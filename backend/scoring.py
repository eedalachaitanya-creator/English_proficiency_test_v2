"""
Scoring module — turns a submitted invitation into a Score row.

Pipeline overview (called once from routes/submit.py once the candidate
has submitted everything):

    score_invitation(inv, db)
        ├── score_reading(inv, db)          # deterministic, no API calls
        ├── _run_writing_eval(inv, db)      # GPT-4o on the essay
        ├── _run_speaking_eval(inv, db)     # Whisper + Azure + GPT-4o on audio
        ├── compute_total(...)              # weighted average of available sections
        └── derive_rating(...)              # final hire/no-hire band

Each section can be excluded from a given invitation (HR picks at invite
time which of reading/writing/speaking to include). Excluded sections
return None scores. compute_total() then redistributes weights across
whatever IS scored, so a 2-section test still produces a 0–100 total.

Rating bands (applied to total_score 0-100):
    - 75-100: recommended
    - 60-74:  borderline
    - 0-59:   not_recommended

Conditional speaking floor (added 2026-05):
    When speaking is a SIGNIFICANT share of the test (i.e. fewer than 3
    sections were included), a speaking_score below 50 forces
    "not_recommended" regardless of the weighted total. Rationale:
        - 3-section test: speaking is 33% — a weak speaker who aced the
          other two is still likely competent overall, don't auto-fail.
        - 2-section test with speaking: speaking is 50% — a fail here
          is structurally a much bigger problem, so the floor applies.
        - Speaking-only test: speaking is 100% — floor obviously applies.

Failure handling:
    Each evaluator (writing, speaking) is wrapped in try/except. A single
    bad invitation can't poison the whole submission flow — on failure
    the evaluator returns a stub result with total=None and an error
    message in the feedback field, and scoring continues for the other
    sections.
"""

import logging
from sqlalchemy.orm import Session
from models import Invitation, MCQAnswer, Question, Score

log = logging.getLogger("scoring")

# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------
def score_reading(inv: Invitation, db: Session) -> tuple[int | None, int | None, int | None]:
    """
    Score the reading section. Deterministic — no API calls, runs in milliseconds.

    Compares each MCQAnswer's selected_option against the Question's correct_answer.

    Returns (score_0_to_100, num_correct, num_total).
        - num_total is what was assigned, not what they answered. So unanswered
          questions count against the candidate (12 right out of 15 = 80, not 100).
        - score is rounded to nearest integer.

    Concrete example:
        Candidate was assigned 15 questions, answered 12 of them, got 10 right.
        Returns (round(10/15 * 100), 10, 15) = (67, 10, 15).

    For invitations where HR excluded reading, returns (None, None, None) so
    compute_total() redistributes weight to only the included sections — and
    the dashboard shows "—" rather than a misleading 0.
    """
    if not inv.include_reading:
        return None, None, None
    answers = db.query(MCQAnswer).filter(MCQAnswer.invitation_id == inv.id).all()
    if not answers:
        return 0, 0, len(inv.assigned_question_ids or [])

    # Bulk-fetch the questions (with their correct_answer) so we don't query in a loop
    qids = [a.question_id for a in answers]
    qmap = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()}

    correct = sum(
        1 for a in answers
        if a.question_id in qmap
        and a.selected_option == qmap[a.question_id].correct_answer
    )
    total = len(inv.assigned_question_ids or [])  # total assigned, not just answered
    score = round((correct / total) * 100) if total > 0 else 0
    return score, correct, total

# ------------------------------------------------------------------
# Writing — evaluation via GPT-4o (writing_eval.score_writing).
# Lazy-imports writing_eval so import-time failures (e.g. openai not installed)
# don't break the rest of the app — they get surfaced at scoring time.
# ------------------------------------------------------------------
def score_writing_stub() -> dict:
    """
    Fallback only. Used when the real evaluator can't run (e.g., OPENAI_API_KEY
    missing in dev). Same shape as the real return so the pipeline is consistent.
    """
    return {
        "breakdown": None,
        "total": None,
        "feedback": (
            "Essay received. AI grading could not run "
            "(missing OPENAI_API_KEY or evaluator failure)."
        ),
    }


def _run_writing_eval(inv: Invitation, db: Session) -> dict:
    """
    Try to import and run the real writing evaluator. On any unexpected
    failure, fall back to the stub so a single bad invitation doesn't
    poison the whole submission flow.

    Short-circuits with all-None when HR excluded the writing section — the
    eval can't run without an essay/topic, and compute_total() needs None
    (not 0) so the writing weight gets redistributed.
    """
    if not inv.include_writing:
        return {"breakdown": None, "total": None, "feedback": None}
    try:
        from writing_eval import score_writing
        return score_writing(inv, db)
    except Exception as e:
        log.exception("Writing evaluation pipeline crashed for invitation %s", inv.id)
        return {
            "breakdown": None,
            "total": None,
            "feedback": f"Writing evaluation failed: {type(e).__name__}. Manual review needed.",
        }


# ------------------------------------------------------------------
# Speaking — evaluation via Whisper + Azure + GPT-4o.
# Lazy-imports speaking_eval so that import-time failures (missing Azure SDK,
# etc.) don't break the rest of the app — they get surfaced at scoring time.
# ------------------------------------------------------------------
def score_speaking_stub() -> dict:
    """
    Fallback only. Used when the real evaluator can't run (e.g., missing API keys
    in dev). Same shape as the real return so the pipeline is consistent.
    """
    return {
        "breakdown": None,
        "total": None,
        "feedback": (
            "Speaking section recorded successfully. "
            "AI evaluation could not run (missing API credentials)."
        ),
    }


def _run_speaking_eval(inv: Invitation, db: Session) -> dict:
    """
    Try to import and run the real speaking evaluator. On any unexpected
    failure, fall back to the stub so a single bad invitation doesn't
    poison the whole submission flow.

    Short-circuits with all-None when HR excluded the speaking section — no
    audio means no eval input, and compute_total() needs None (not 0) so
    the speaking weight gets redistributed.
    """
    if not inv.include_speaking:
        return {"breakdown": None, "total": None, "feedback": None}
    try:
        from speaking_eval import score_speaking
        return score_speaking(inv, db)
    except Exception as e:
        log.exception("Speaking evaluation pipeline crashed for invitation %s", inv.id)
        return {
            "breakdown": None,
            "total": None,
            "feedback": f"Speaking evaluation failed: {type(e).__name__}. Manual review needed.",
        }

# ------------------------------------------------------------------
# Combined score + rating
# ------------------------------------------------------------------
def derive_rating(
    total_score: int,
    speaking_score: int | None = None,
    sections_included: int = 3,
) -> str:
    """
    Convert the weighted total into a hire/no-hire band.

    Speaking floor (conditional): when speaking is a significant share of the
    test, a sub-50 speaking score forces "not_recommended" regardless of how
    strong the other sections were. The floor only fires when:

      1. speaking was actually evaluated (speaking_score is not None), AND
      2. speaking_score < 50, AND
      3. fewer than 3 sections were included — i.e. speaking is at least 50%
         of the candidate's total assessment.

    Why the 3rd condition matters:
      - Full 3-section test: speaking is 33%. A weak speaker who aced reading
        and writing is still likely competent overall — don't auto-fail.
      - 2-section test (speaking + reading OR speaking + writing): speaking
        is 50% of the score. A failed speaking section is a much bigger
        problem here, so the floor applies.
      - Speaking-only test: speaking is 100%. Floor obviously applies.

    The floor is also skipped when speaking_score is None, which happens when
    HR excluded speaking entirely or speaking eval failed. In both cases the
    rule has no signal to apply, so normal bands run.
    """
    if (
        speaking_score is not None
        and speaking_score < 50
        and sections_included < 3
    ):
        return "not_recommended"
    if total_score >= 75:
        return "recommended"
    if total_score >= 60:
        return "borderline"
    return "not_recommended"



# Section weights — must sum to 1.0. Sourced from config.py so the client
# can tune the relative importance of reading / writing / speaking without
# editing scoring code.
from config import W_READING, W_WRITING, W_SPEAKING


def compute_total(
    reading_score: int | None,
    writing_score: int | None,
    speaking_score: int | None,
) -> int:
    """
    Combine the per-section scores into a single 0–100 number.

    Default weights are equal (1/3 each), set in config.py. If a section
    came back as None (HR excluded it, or eval failed), its weight is
    redistributed proportionally across whatever IS scored. This way a
    2-section test still produces a 0–100 total, not e.g. 0–67.

    Concrete examples:
        Full test, all scored: reading=80, writing=70, speaking=60
            → (80×0.33 + 70×0.33 + 60×0.33) / 1.00 = 70

        Reading + writing only (speaking excluded): reading=80, writing=70
            → (80×0.33 + 70×0.33) / 0.66 = 75
            The 0.33 speaking weight gets redistributed.

        All None (no sections scored): returns 0 as a safe default.
    """
    pairs = []
    if reading_score is not None:
        pairs.append((reading_score, W_READING))
    if writing_score is not None:
        pairs.append((writing_score, W_WRITING))
    if speaking_score is not None:
        pairs.append((speaking_score, W_SPEAKING))

    if not pairs:
        return 0

    total_weight = sum(w for _, w in pairs)
    weighted_sum = sum(s * w for s, w in pairs)
    return round(weighted_sum / total_weight)


# ------------------------------------------------------------------
# Top-level entry point — call this from the submit route
# ------------------------------------------------------------------
def score_invitation(inv: Invitation, db: Session) -> Score:
    """
    Top-level scoring entry point. Called once from routes/submit.py after
    the candidate has submitted the test (submitted_at must be set first).

    Runs all three section evaluators, computes the total, picks a rating,
    and inserts a Score row. The caller is responsible for db.commit().

    Pipeline:
        1. score_reading()       → fast, deterministic, no API calls
        2. _run_writing_eval()   → GPT-4o on essay (or stub on failure)
        3. _run_speaking_eval()  → Whisper + Azure + GPT-4o (or stub)
        4. compute_total()       → weighted average (None sections excluded)
        5. derive_rating()       → applies bands + conditional <50 speaking floor

    NOT idempotent — calling twice creates two Score rows. The caller MUST
    ensure submitted_at is set first AND that this is only called once per
    invitation. The submit route guarantees both.

    AI feedback paragraphs from writing + speaking get joined into a single
    `ai_feedback` field on the Score row, so HR sees one combined paragraph
    in the dashboard rather than two separate ones.
    """
    reading_score, reading_correct, reading_total = score_reading(inv, db)

    writing = _run_writing_eval(inv, db)
    writing_total = writing["total"]

    speaking = _run_speaking_eval(inv, db)
    speaking_total = speaking["total"]

    total_score = compute_total(reading_score, writing_total, speaking_total)
    # Count how many sections were actually scored (None = excluded or failed).
    # The conditional floor in derive_rating uses this to decide whether the
    # <50 speaking rule should fire — it should NOT fire when all 3 sections
    # are included (speaking is only 33% of the total in that case).
    sections_included = sum(
        1 for s in (reading_score, writing_total, speaking_total) if s is not None
    )
    rating = derive_rating(
        total_score,
        speaking_score=speaking_total,
        sections_included=sections_included,
    )

    # Combine the two pending-feedback notes into one paragraph for HR.
    feedback = "\n\n".join(filter(None, [writing["feedback"], speaking["feedback"]]))

    score = Score(
        invitation_id=inv.id,
        reading_score=reading_score,
        reading_correct=reading_correct,
        reading_total=reading_total,
        writing_breakdown=writing["breakdown"],
        writing_score=writing_total,
        speaking_breakdown=speaking["breakdown"],
        speaking_score=speaking_total,
        total_score=total_score,
        rating=rating,
        ai_feedback=feedback,
    )
    db.add(score)
    return score
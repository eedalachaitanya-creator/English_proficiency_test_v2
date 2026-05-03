"""
Scoring module.

Reading scoring is deterministic (count correct ÷ total) — runs immediately
on submission, no API calls, free.

Writing scoring delegates to writing_eval.score_writing(), which sends the
candidate's essay + the assigned prompt to GPT-4o and gets back a rubric
breakdown (Task Response / Grammar / Vocabulary / Coherence, each 0..25)
plus a short feedback paragraph for HR. A stub fallback runs if the call
can't import or fails.

Speaking scoring delegates to speaking_eval.score_speaking(), which runs:
  Whisper (transcribe) -> Azure (pronunciation) -> GPT-4o (grammar/vocab)
  + Python (confidence from filler/pause/restart signals).
A stub fallback runs if the evaluator can't import or crashes.

Rating bands (applied to total_score 0-100):
  - 75-100: recommended
  - 60-74:  borderline
  - 0-59:   not_recommended
"""

import logging
from sqlalchemy.orm import Session
from models import Invitation, MCQAnswer, Question, Score

log = logging.getLogger("scoring")

# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------
def score_reading(inv: Invitation, db: Session) -> tuple[int, int, int]:
    """
    Compare each MCQAnswer's selected_option against the Question's correct_answer.
    Returns (score_0_to_100, num_correct, num_total).
    """
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
    """
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
    """
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
def derive_rating(total_score: int) -> str:
    if total_score >= 75:
        return "recommended"
    if total_score >= 60:
        return "borderline"
    return "not_recommended"



# Section weights (must sum to 1.0). Equal weighting across all three sections.
# Use 1/3 (not 0.3333) so floating-point round-off does not push the sum off 1.0.
W_READING = 1 / 3
W_WRITING = 1 / 3
W_SPEAKING = 1 / 3


def compute_total(
    reading_score: int | None,
    writing_score: int | None,
    speaking_score: int | None,
) -> int:
    """
    Weighted total: equal 33.33% weighting across reading, writing, and speaking.
    If a section isn't scored yet (None), its weight is redistributed proportionally
    across whatever IS scored, so the displayed total reflects the available data.
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
    Compute reading + writing + speaking scores for this invitation, persist a Score row.
    Reading is deterministic. Writing runs GPT-4o on the candidate's essay (or stub
    fallback on failure). Speaking runs the Whisper + Azure + GPT-4o pipeline (or
    stub fallback on failure).
    Idempotent in the sense that calling twice creates two scores (don't do that);
    the caller should ensure submitted_at is set first and only call once.
    """
    reading_score, reading_correct, reading_total = score_reading(inv, db)

    writing = _run_writing_eval(inv, db)
    writing_total = writing["total"]

    speaking = _run_speaking_eval(inv, db)
    speaking_total = speaking["total"]

    total_score = compute_total(reading_score, writing_total, speaking_total)
    rating = derive_rating(total_score)

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

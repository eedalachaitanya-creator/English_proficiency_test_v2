"""
Scoring module.

Reading scoring is deterministic (count correct ÷ total) — runs immediately
on submission, no API calls, free.

Speaking scoring delegates to speaking_eval.score_speaking(), which runs:
  Whisper (transcribe) -> Azure (pronunciation) -> GPT-4o (grammar/vocab)
  + Python (confidence from filler/pause/restart signals).

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


def compute_total(reading_score: int, speaking_score: int | None) -> int:
    """
    Weighted total. v1: 50% reading + 50% speaking.
    If speaking isn't scored yet (None), total = reading only — the table will
    be updated once the speaking AI scoring runs.
    """
    if speaking_score is None:
        return reading_score
    return round(reading_score * 0.5 + speaking_score * 0.5)


# ------------------------------------------------------------------
# Top-level entry point — call this from the submit route
# ------------------------------------------------------------------
def score_invitation(inv: Invitation, db: Session) -> Score:
    """
    Compute reading + speaking scores for this invitation, persist a Score row.
    Idempotent in the sense that calling twice creates two scores (don't do that);
    the caller should ensure submitted_at is set first and only call once.
    """
    reading_score, reading_correct, reading_total = score_reading(inv, db)

    speaking = _run_speaking_eval(inv, db)
    speaking_total = speaking["total"]

    total_score = compute_total(reading_score, speaking_total)
    rating = derive_rating(total_score)

    score = Score(
        invitation_id=inv.id,
        reading_score=reading_score,
        reading_correct=reading_correct,
        reading_total=reading_total,
        speaking_breakdown=speaking["breakdown"],
        speaking_score=speaking_total,
        total_score=total_score,
        rating=rating,
        ai_feedback=speaking["feedback"],
    )
    db.add(score)
    return score

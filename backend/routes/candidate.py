"""
Candidate-facing routes.

Two endpoints:
  GET /exam/{token}    -> validate token, lock content, set session, redirect to /instructions.html
  GET /api/test-content -> the test pages call this to load passage + questions + topics

Authentication for candidates is the URL token + the resulting session cookie.
There is no candidate password — the URL itself is the credential.

Content assignment happens once on the FIRST visit to /exam/{token}. After
that, refresh just reloads the same passage/questions/topics — there's no
way to re-roll for easier content.
"""
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Invitation, Passage, Question, SpeakingTopic, WritingTopic
from schemas import (
    TestContent,
    PassagePublic,
    QuestionPublic,
    SpeakingTopicPublic,
    WritingTopicPublic,
    ExamCodeVerifyRequest,
    ExamCodeVerifyResponse,
)


router = APIRouter(tags=["candidate"])

# Code-entry security
MAX_CODE_ATTEMPTS = 3  # after this many wrong codes, invitation is locked

# v1 constants — match the locked scope.
WRITTEN_QUESTIONS_PER_TEST = 15
SPEAKING_QUESTIONS_PER_TEST = 3
WRITTEN_DURATION_SECONDS = 30 * 60    # 30 minutes for the reading MCQs
WRITING_DURATION_SECONDS = 20 * 60    # 20 minutes for the essay
SPEAKING_DURATION_SECONDS = 10 * 60   # 10 minutes for 3 speaking prompts


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _check_invitation_active(inv: Invitation):
    """Raise 410 Gone if expired or already submitted. 404 if missing handled by caller."""
    now = _utcnow_naive()
    if inv.submitted_at is not None:
        raise HTTPException(status_code=410, detail="This test has already been submitted.")
    if inv.expires_at < now:
        raise HTTPException(status_code=410, detail="This test link has expired (24-hour limit).")


def _assign_content(inv: Invitation, db: Session):
    """
    First-visit content lock-in. Picks 1 passage, 15 questions
    (RC about that passage + grammar/vocab fill-in), and 3 speaking topics —
    all matching the candidate's difficulty. Stores the IDs on the Invitation row.
    """
    # 1) Pick a random passage of matching difficulty
    passages = db.query(Passage).filter(Passage.difficulty == inv.difficulty).all()
    if not passages:
        raise HTTPException(
            status_code=500,
            detail=f"No passages have been seeded for difficulty='{inv.difficulty}'. "
                   f"Run seed.py first.",
        )
    passage = random.choice(passages)
    inv.passage_id = passage.id

    # 2) RC questions tied to this passage (all of them — typically 4-5)
    rc_questions = (
        db.query(Question)
        .filter(
            Question.passage_id == passage.id,
            Question.question_type == "reading_comp",
        )
        .all()
    )

    # 3) Fill the rest from standalone grammar/vocab questions.
    # If a passage somehow has more RC questions than the whole test allows,
    # subsample so we still produce exactly WRITTEN_QUESTIONS_PER_TEST.
    if len(rc_questions) > WRITTEN_QUESTIONS_PER_TEST:
        rc_questions = random.sample(rc_questions, WRITTEN_QUESTIONS_PER_TEST)
    needed = WRITTEN_QUESTIONS_PER_TEST - len(rc_questions)
    standalone = (
        db.query(Question)
        .filter(
            Question.difficulty == inv.difficulty,
            Question.passage_id.is_(None),
            Question.question_type.in_(["grammar", "vocabulary", "fill_blank"]),
        )
        .all()
    )
    if needed > len(standalone):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Question bank too small for {inv.difficulty}: "
                f"need {needed} standalone questions, have {len(standalone)}. "
                f"Seed more questions."
            ),
        )

    selected_others = random.sample(standalone, needed)
    selected = rc_questions + selected_others
    random.shuffle(selected)  # interleave question types so they're not all clumped
    inv.assigned_question_ids = [q.id for q in selected]

    # 4) Speaking topics
    topics = db.query(SpeakingTopic).filter(SpeakingTopic.difficulty == inv.difficulty).all()
    if len(topics) < SPEAKING_QUESTIONS_PER_TEST:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Not enough speaking topics for {inv.difficulty}: "
                f"need {SPEAKING_QUESTIONS_PER_TEST}, have {len(topics)}."
            ),
        )
    selected_topics = random.sample(topics, SPEAKING_QUESTIONS_PER_TEST)
    inv.assigned_topic_ids = [t.id for t in selected_topics]

    # 5) Writing topic (one essay prompt)
    writing_topics = db.query(WritingTopic).filter(WritingTopic.difficulty == inv.difficulty).all()
    if not writing_topics:
        raise HTTPException(
            status_code=500,
            detail=f"No writing prompts seeded for difficulty='{inv.difficulty}'. "
                   f"Run `alembic upgrade head` (if needed) and then `python3 seed.py`.",
        )
    inv.assigned_writing_topic_id = random.choice(writing_topics).id


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@router.get("/exam/{token}")
def open_exam(token: str, db: Session = Depends(get_db)):
    """
    Candidate's URL handler.

    Validates the token exists and the invitation is active. Does NOT set a
    session cookie yet — that happens only after the access code is verified.
    Redirects to the code-entry page.

    Security note: a leaked URL alone now yields nothing useful — the attacker
    still needs the 6-digit code (sent to candidate's email separately by HR).
    """
    inv = db.query(Invitation).filter(Invitation.token == token).first()
    if not inv:
        raise HTTPException(status_code=404, detail="This test link is invalid.")

    _check_invitation_active(inv)

    # If the invitation is locked from too many failed code attempts, we still
    # show the code page — we just won't accept any code there. The page will
    # display the lockout message. Better UX than a hard error here.
    return RedirectResponse(url=f"/exam-code.html?token={token}", status_code=302)


@router.post("/api/exam/verify-code", response_model=ExamCodeVerifyResponse)
def verify_code(
    payload: ExamCodeVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Candidate enters their 6-digit code on /exam-code.html. This endpoint:
      1. Looks up the invitation by token.
      2. Rejects if invitation is expired/submitted/locked (no code is checked).
      3. Compares submitted code to stored code (constant-time-ish via ==).
      4. On wrong code, increments failed_code_attempts. If we hit
         MAX_CODE_ATTEMPTS, sets code_locked=True and refuses further attempts.
      5. On correct code: resets the counter, sets the session cookie,
         locks content if first visit, and tells frontend to redirect.

    We DO return attempts_remaining on wrong code so the candidate isn't
    fumbling in the dark. The token itself is the secret that gates this
    endpoint — without it, an attacker can't even try.
    """
    inv = db.query(Invitation).filter(Invitation.token == payload.token).first()
    if not inv:
        # Same generic-ish error whether token doesn't exist or is malformed —
        # don't leak which one. 404 because that's the most useful semantics.
        raise HTTPException(status_code=404, detail="This test link is invalid.")

    # Active checks (expired, already submitted)
    _check_invitation_active(inv)

    # Once the test has been started, the URL is "consumed" — the candidate
    # cannot reopen the link to start over (or even resume from a fresh
    # browser). In-session continuation is unaffected because that path
    # uses the session cookie set on first entry, not this endpoint. A
    # candidate who experienced a real technical failure must contact HR
    # to request a fresh invitation.
    if inv.started_at is not None:
        raise HTTPException(
            status_code=410,
            detail=(
                "This test has already been started and cannot be reopened. "
                "If you experienced a technical issue, please contact your "
                "HR manager to request a new invitation."
            ),
        )

    # Locked-out check — refuse before checking the code
    if inv.code_locked:
        raise HTTPException(
            status_code=423,  # Locked
            detail=(
                "Too many wrong attempts. This test link has been locked. "
                "Please contact your HR manager to receive a new code."
            ),
        )

    # Code comparison. Plain == is fine for a 6-digit numeric code; brute-force
    # is already prevented by the attempt counter. Strip whitespace in case the
    # candidate pasted with extra spaces.
    submitted_code = payload.code.strip()
    if submitted_code != inv.access_code:
        inv.failed_code_attempts = (inv.failed_code_attempts or 0) + 1
        if inv.failed_code_attempts >= MAX_CODE_ATTEMPTS:
            inv.code_locked = True
            db.commit()
            raise HTTPException(
                status_code=423,
                detail=(
                    f"Too many wrong attempts ({MAX_CODE_ATTEMPTS}). "
                    "This test link has been locked. Please contact your HR "
                    "manager to receive a new code."
                ),
            )
        attempts_left = MAX_CODE_ATTEMPTS - inv.failed_code_attempts
        db.commit()
        # 401 because the credential (code) was wrong
        raise HTTPException(
            status_code=401,
            detail=f"Wrong code. {attempts_left} attempt{'s' if attempts_left != 1 else ''} remaining.",
        )

    # Code is correct — reset counter, lock content if first visit, set session.
    inv.failed_code_attempts = 0

    if inv.assigned_question_ids is None:
        _assign_content(inv, db)
        inv.started_at = _utcnow_naive()

    db.commit()
    request.session["invitation_id"] = inv.id

    return ExamCodeVerifyResponse(
        success=True,
        redirect_to="/instructions.html",
    )


@router.get("/api/test-content", response_model=TestContent)
def get_test_content(request: Request, db: Session = Depends(get_db)):
    """
    Frontend pages (instructions, reading, speaking) call this on load to fetch
    the passage, questions, and topics assigned to this candidate.
    Crucially: returns QuestionPublic, which has NO `correct_answer` field.
    """
    inv_id = request.session.get("invitation_id")
    if not inv_id:
        raise HTTPException(
            status_code=401,
            detail="No active test session. Please open the exam URL again.",
        )

    inv = db.query(Invitation).filter(Invitation.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=401, detail="Test session is invalid.")

    _check_invitation_active(inv)

    if inv.passage_id is None or not inv.assigned_question_ids:
        # Should not happen — open_exam assigns these. Defensive.
        raise HTTPException(
            status_code=500,
            detail="Test content not assigned. Re-open the exam URL.",
        )

    passage = db.query(Passage).filter(Passage.id == inv.passage_id).first()

    # Load all assigned questions in one query, then re-order to match assignment
    qmap = {
        q.id: q
        for q in db.query(Question).filter(Question.id.in_(inv.assigned_question_ids)).all()
    }
    questions_ordered = [qmap[qid] for qid in inv.assigned_question_ids if qid in qmap]

    tmap = {
        t.id: t
        for t in db.query(SpeakingTopic)
        .filter(SpeakingTopic.id.in_(inv.assigned_topic_ids))
        .all()
    }
    topics_ordered = [tmap[tid] for tid in inv.assigned_topic_ids if tid in tmap]

    # Writing topic (single, may be None for older invitations created before the
    # writing section was added — defensive guard so we don't crash for those).
    writing_topic = (
        db.query(WritingTopic).filter(WritingTopic.id == inv.assigned_writing_topic_id).first()
        if inv.assigned_writing_topic_id else None
    )
    if writing_topic is None:
        raise HTTPException(
            status_code=500,
            detail="No writing topic assigned. This invitation pre-dates the writing section. "
                   "Generate a fresh invitation.",
        )

    return TestContent(
        candidate_name=inv.candidate_name,
        difficulty=inv.difficulty,
        duration_written_seconds=WRITTEN_DURATION_SECONDS,
        duration_writing_seconds=WRITING_DURATION_SECONDS,
        duration_speaking_seconds=SPEAKING_DURATION_SECONDS,
        passage=PassagePublic(id=passage.id, title=passage.title, body=passage.body),
        questions=[
            QuestionPublic(
                id=q.id,
                question_type=q.question_type,
                stem=q.stem,
                options=q.options,
            )
            for q in questions_ordered
        ],
        writing_topic=WritingTopicPublic(
            id=writing_topic.id,
            prompt_text=writing_topic.prompt_text,
            min_words=writing_topic.min_words,
            max_words=writing_topic.max_words,
        ),
        speaking_topics=[
            SpeakingTopicPublic(id=t.id, prompt_text=t.prompt_text)
            for t in topics_ordered
        ],
    )
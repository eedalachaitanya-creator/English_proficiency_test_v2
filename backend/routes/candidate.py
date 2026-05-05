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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Invitation, Passage, Question, SpeakingTopic, WritingTopic, SupportedTimezone
from schemas import (
    TestContent,
    SectionFlags,
    PassagePublic,
    QuestionPublic,
    SpeakingTopicPublic,
    WritingTopicPublic,
    ExamCodeVerifyRequest,
    ExamCodeVerifyResponse,
)


router = APIRouter(tags=["candidate"])

# Code-entry security
from config import (
    MAX_CODE_ATTEMPTS,
    WRITTEN_QUESTIONS_PER_TEST,
    SPEAKING_QUESTIONS_PER_TEST,
)
# Section duration constants are no longer module-level — each Invitation
# now carries its own snapshotted reading_seconds / writing_seconds /
# speaking_seconds, sourced from system_settings at creation time. Edit
# system_settings (or the migration's seed values) to change defaults.


def _can_start(start_count: int, max_starts: int) -> bool:
    """
    Counter-based replacement for the old single-use rule. Returns True if
    the candidate is allowed to verify the access code one more time.
    Pure function — easy to unit-test.

    The candidate's start_count starts at 0 and is incremented on each
    successful verification. The URL is locked when start_count reaches
    max_starts. max_starts of 1 reproduces the old behavior; 0 locks the
    URL from creation (HR misconfiguration — accepted, see spec).
    """
    return start_count < max_starts


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _format_opens_at(db: Session, valid_from: datetime, display_timezone: str) -> str:
    """
    Format the "test opens on..." time for the candidate-facing 425 error.

    Renders in the invitation's display_timezone (the same zone HR picked
    when creating the invitation) and appends the short label like "(IST)"
    or "(ET)" so the candidate knows which timezone the time is in.

    Why the label matters: a candidate scheduled in ET who clicks the link
    while traveling — or any third party (HR self-testing from a different
    zone, someone forwarded the link, etc.) — would otherwise read the
    bare time as "their" time and get confused. The label is a 4-character
    anchor that removes that ambiguity.

    The label comes from the supported_timezones table. We don't enforce
    is_active here — even if HR soft-deleted the zone after creating this
    invitation, we still want to render the candidate's email-time-equivalent
    string correctly.

    Failure modes (all safe — never raises, always produces a string):
      - display_timezone is "UTC" or unknown -> render in UTC with "(UTC)" label
      - tzdata not installed -> render the naive UTC value with "(UTC)" label
      - supported_timezones row missing -> use the IANA name as the label

    Returns a string like: "May 5, 2026 at 3:57 PM (IST)"
    """
    # Look up the short label from supported_timezones. Falls back to the
    # IANA name if the row is gone (e.g. someone hard-deleted it instead
    # of soft-deleting). Ugly but functional — at least the candidate sees
    # SOME zone identifier.
    tz_row = db.query(SupportedTimezone).filter(
        SupportedTimezone.iana_name == display_timezone
    ).first()
    label = tz_row.short_label if tz_row else display_timezone

    # Convert UTC -> target zone. Same pattern as email_service._format_window:
    # explicitly attach UTC tzinfo before astimezone() because Python 3.12+
    # deprecates implicit UTC assumption on naive datetimes.
    try:
        target_tz = ZoneInfo(display_timezone)
        local = valid_from.replace(tzinfo=timezone.utc).astimezone(target_tz)
    except ZoneInfoNotFoundError:
        # Bad zone name OR (on Windows) tzdata not installed. Fall back
        # to UTC display — both the time AND the label become UTC so they
        # match. Better than rendering a non-UTC time with a UTC label.
        print(
            f"[candidate] WARN: timezone {display_timezone!r} unavailable. "
            f"Falling back to UTC. On Windows: pip install tzdata"
        )
        local = valid_from
        label = "UTC"

    # Format with .replace(' 0', ' ') to drop the leading zero on the hour
    # (so "03:57 PM" becomes "3:57 PM"). strftime has no portable flag.
    formatted = local.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    return f"{formatted} ({label})"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _check_invitation_active(inv: Invitation):
    """Raise 410 Gone if expired or already submitted. 404 if missing handled by caller."""
    now = _utcnow_naive()
    if inv.submitted_at is not None:
        raise HTTPException(status_code=410, detail="This test has already been submitted.")
    if inv.expires_at < now:
        raise HTTPException(
            status_code=410,
            detail="This test link's scheduled window has ended.",
        )


def _assign_content(inv: Invitation, db: Session):
    """
    First-visit content lock-in. For each section the HR included in this
    invitation, picks the matching content (passage + 15 questions for
    reading, one essay prompt for writing, 3 prompts for speaking).
    Excluded sections leave their assignment fields as None — the explicit
    "this section was not part of the test" signal that scoring + the
    frontend both rely on.
    """
    # ---- Reading: passage + question set ----
    if inv.include_reading:
        passages = db.query(Passage).filter(Passage.difficulty == inv.difficulty).all()
        if not passages:
            raise HTTPException(
                status_code=500,
                detail=f"No passages have been seeded for difficulty='{inv.difficulty}'. "
                       f"Run seed.py first.",
            )
        passage = random.choice(passages)
        inv.passage_id = passage.id

        # RC questions tied to this passage (all of them — typically 4-5)
        rc_questions = (
            db.query(Question)
            .filter(
                Question.passage_id == passage.id,
                Question.question_type == "reading_comp",
            )
            .all()
        )

        # Fill the rest from standalone grammar/vocab questions.
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

    # ---- Speaking: 3 prompts ----
    if inv.include_speaking:
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

    # ---- Writing: one essay prompt ----
    if inv.include_writing:
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

    # Scheduled-window check — HR may have set a future start time. 425 (Too
    # Early) is the standard HTTP code for "the request is correct but the
    # server isn't ready to accept it yet" — distinct from 410 (gone forever).
    if inv.valid_from > _utcnow_naive():
        # Render the open time in the invitation's display_timezone (same
        # zone the candidate saw in their invitation email), so they don't
        # have to do timezone math at the moment they're trying to start
        # the test. Falls back to UTC if the zone or its label isn't
        # available — the message is still informative either way.
        opens_at = _format_opens_at(db, inv.valid_from, inv.display_timezone)
        raise HTTPException(
            status_code=425,
            detail=(
                f"Your test hasn't started yet. It opens on {opens_at}. "
                f"Please return at the scheduled time."
            ),
        )

    # Counter-based start gate. Replaces the old binary "started_at != None"
    # rule with a configurable max-starts limit (snapshotted onto the
    # invitation from system_settings at creation time). Default is 1, which
    # exactly reproduces the previous single-use behavior.
    if not _can_start(inv.start_count, inv.max_starts):
        raise HTTPException(
            status_code=410,
            detail=(
                "This test has reached its allowed number of opens and "
                "cannot be reopened. If you experienced a technical issue, "
                "please contact your HR manager to request a fresh invitation."
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

    # First-visit gate. Use started_at (rather than assigned_question_ids)
    # because reading-excluded invitations never set assigned_question_ids,
    # which would otherwise re-randomize writing/speaking on every visit.
    if inv.started_at is None:
        _assign_content(inv, db)

    # Counter increment + first-start timestamp. start_count tracks every
    # successful verification so the next attempt can be compared against
    # max_starts. started_at records ONLY the first start (preserved for HR
    # display).
    #
    # TODO: this whole verify_code path is not race-safe. Two browser tabs
    # racing the access-code POST can both pass _can_start() AND both call
    # _assign_content() (which re-randomizes passage_id /
    # assigned_question_ids / assigned_writing_topic_id /
    # assigned_topic_ids — overwriting whatever the first call assigned).
    # Net effect: start_count consumes 2× budget on a single click AND
    # the two tabs may each have set sessions pointing at the same row
    # while the row's content was rewritten between them. Acceptable for
    # the current trusted-HR / max_starts=1-by-default environment per
    # the spec
    # (docs/superpowers/specs/2026-05-04-system-settings-runtime-config-design.md).
    # The right fix is a single SELECT ... FOR UPDATE on the invitation
    # row at the top of this handler, gating BOTH _assign_content and
    # the start_count increment. Or move the entire transition into a
    # database-level atomic UPDATE ... RETURNING.
    inv.start_count = (inv.start_count or 0) + 1
    if inv.started_at is None:
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

    # Each section block is gated on inv.include_*. Excluded sections return
    # null/empty so the frontend's per-section guards skip those routes
    # entirely. See spec.

    passage_payload = None
    questions_payload: list[QuestionPublic] = []
    if inv.include_reading:
        if inv.passage_id is None or not inv.assigned_question_ids:
            raise HTTPException(
                status_code=500,
                detail="Reading content not assigned. Re-open the exam URL.",
            )
        passage = db.query(Passage).filter(Passage.id == inv.passage_id).first()
        qmap = {
            q.id: q
            for q in db.query(Question).filter(Question.id.in_(inv.assigned_question_ids)).all()
        }
        questions_ordered = [qmap[qid] for qid in inv.assigned_question_ids if qid in qmap]
        passage_payload = PassagePublic(id=passage.id, title=passage.title, body=passage.body)
        questions_payload = [
            QuestionPublic(
                id=q.id,
                question_type=q.question_type,
                stem=q.stem,
                options=q.options,
            )
            for q in questions_ordered
        ]

    writing_topic_payload = None
    if inv.include_writing:
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
        writing_topic_payload = WritingTopicPublic(
            id=writing_topic.id,
            prompt_text=writing_topic.prompt_text,
            min_words=writing_topic.min_words,
            max_words=writing_topic.max_words,
        )

    speaking_topics_payload: list[SpeakingTopicPublic] = []
    if inv.include_speaking:
        if not inv.assigned_topic_ids:
            raise HTTPException(
                status_code=500,
                detail="Speaking topics not assigned. Re-open the exam URL.",
            )
        tmap = {
            t.id: t
            for t in db.query(SpeakingTopic)
            .filter(SpeakingTopic.id.in_(inv.assigned_topic_ids))
            .all()
        }
        topics_ordered = [tmap[tid] for tid in inv.assigned_topic_ids if tid in tmap]
        speaking_topics_payload = [
            SpeakingTopicPublic(id=t.id, prompt_text=t.prompt_text)
            for t in topics_ordered
        ]

    return TestContent(
        candidate_name=inv.candidate_name,
        difficulty=inv.difficulty,
        # Per-invitation durations — snapshotted from system_settings at
        # invitation creation. Setting changes do not affect existing
        # invitations; each one carries the values it was created with.
        duration_written_seconds=inv.reading_seconds,
        duration_writing_seconds=inv.writing_seconds,
        duration_speaking_seconds=inv.speaking_seconds,
        # ISO-8601 UTC with 'Z' suffix so the Angular frontend can `new Date(iso)`
        # and schedule the window-end setTimeout against the wall clock.
        valid_until_iso=inv.expires_at.isoformat() + "Z",
        sections=SectionFlags(
            reading=inv.include_reading,
            writing=inv.include_writing,
            speaking=inv.include_speaking,
        ),
        passage=passage_payload,
        questions=questions_payload,
        writing_topic=writing_topic_payload,
        speaking_topics=speaking_topics_payload,
    )
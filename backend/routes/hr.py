"""
HR-facing routes.

All routes here mounted under /api/hr/* via the prefix on the APIRouter.
Every route except /login is protected by `Depends(require_hr)` —
that dependency reads the session cookie and returns the HRAdmin row,
or raises 401 if no valid session.

Multi-tenancy guarantee: results endpoints filter by hr_admin_id so
HR-A can never see HR-B's candidates, even by guessing IDs.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import HRAdmin, Invitation, AudioRecording, SpeakingTopic, WritingResponse, WritingTopic, SystemSettings
from schemas import (
    HRLoginRequest,
    HRLoginResponse,
    ChangePasswordRequest,
    InviteCreateRequest,
    InviteCreateResponse,
    InvitationDetails,
    ResendEmailResponse,
    ScoreSummary,
    ScoreDetail,
    AudioRecordingPublic,
)
from auth import (
    hash_password,
    verify_password,
    generate_token,
    generate_access_code,
    require_hr,
)
from email_service import send_invitation_email, send_regenerated_code_email


router = APIRouter(prefix="/api/hr", tags=["hr"])

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


def _utcnow_naive() -> datetime:
    """Match models.py's _utcnow — naive UTC for cross-DB consistency."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------
# Scheduled URL validity window — HR picks a [valid_from, valid_until]
# range when creating an invitation. The candidate's URL is active only
# during that window. PAST_GRACE_SECONDS and MIN_WINDOW_SECONDS live in
# config.py so the client can tune them without code changes here.
# ------------------------------------------------------------------
from config import PAST_GRACE_SECONDS, MIN_WINDOW_SECONDS


# ------------------------------------------------------------------
# System settings snapshot — operational config that HR can change in
# the DB without redeploying. Each invitation snapshots these values at
# creation; changes here do NOT affect existing or in-flight invitations.
# ------------------------------------------------------------------
# Defensive fallback used only when the system_settings row is missing
# (fresh DB never migrated, or row deleted). Migration seeds the row, so
# this branch is rare. Values are sourced from config.py so the fallback
# can never silently drift away from the migration's seed defaults.
from config import (
    FALLBACK_MAX_STARTS,
    FALLBACK_READING_SECONDS,
    FALLBACK_WRITING_SECONDS,
    FALLBACK_SPEAKING_SECONDS,
)
_FALLBACK_SETTINGS = {
    "max_starts": FALLBACK_MAX_STARTS,
    "reading_seconds": FALLBACK_READING_SECONDS,
    "writing_seconds": FALLBACK_WRITING_SECONDS,
    "speaking_seconds": FALLBACK_SPEAKING_SECONDS,
}


def _settings_to_dict(row) -> dict:
    """
    Convert a SystemSettings ORM row (or None) to a settings dict for
    snapshotting onto a new Invitation. Pure function — no I/O.

    Why a dict not a transient SystemSettings instance: SQLAlchemy column
    defaults only run at INSERT time. An unpersisted SystemSettings()
    would have None for every field, not the column defaults.
    """
    if row is None:
        return dict(_FALLBACK_SETTINGS)
    return {
        "max_starts": row.max_starts,
        "reading_seconds": row.reading_seconds,
        "writing_seconds": row.writing_seconds,
        "speaking_seconds": row.speaking_seconds,
    }


def _to_naive_utc(dt: datetime) -> datetime:
    """
    Normalize a datetime to naive UTC. The frontend sends ISO strings with
    'Z' suffix → Pydantic parses them as timezone-aware. The rest of the
    codebase uses naive UTC. Convert at the boundary.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _validate_window(valid_from: datetime, valid_until: datetime) -> None:
    """
    Raise 400 HTTPException if the [valid_from, valid_until] window is
    invalid. Pure function so it's easy to unit-test.
    """
    valid_from = _to_naive_utc(valid_from)
    valid_until = _to_naive_utc(valid_until)
    now = _utcnow_naive()
    if valid_from < now - timedelta(seconds=PAST_GRACE_SECONDS):
        raise HTTPException(
            status_code=400,
            detail="Start time cannot be in the past.",
        )
    if valid_until <= valid_from:
        raise HTTPException(
            status_code=400,
            detail="End time must be after start time.",
        )
    if (valid_until - valid_from).total_seconds() < MIN_WINDOW_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="Window must be at least 60 minutes — the test takes about an hour.",
        )


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
@router.post("/login", response_model=HRLoginResponse)
def login(payload: HRLoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Validate email + password, set session cookie, return HR profile.
    Same generic 401 message for every failure mode ("no such user",
    "wrong password", "credentials are correct but account is admin not
    HR") — don't leak which one failed (slows enumeration attacks AND
    avoids hinting that the email belongs to an admin).
    """
    hr = db.query(HRAdmin).filter(HRAdmin.email == payload.email.lower()).first()
    if (
        not hr
        or hr.role != "hr"
        or not verify_password(payload.password, hr.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    request.session["hr_admin_id"] = hr.id
    return HRLoginResponse(id=hr.id, name=hr.name, email=hr.email)


@router.post("/logout")
def logout(request: Request):
    """Clear the session. Idempotent — safe to call when not logged in."""
    request.session.pop("hr_admin_id", None)
    return {"status": "logged_out"}


@router.get("/me", response_model=HRLoginResponse)
def me(hr: HRAdmin = Depends(require_hr)):
    """Returns the currently logged-in HR. Frontend uses this to confirm session is alive."""
    return HRLoginResponse(id=hr.id, name=hr.name, email=hr.email)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Change the logged-in HR's password. Requires the CURRENT password
    even though we already have a valid session — same defense Gmail and
    GitHub use, mitigates session-hijack-then-takeover and drive-by
    changes (someone walking up to an unattended browser).

    Same generic 401 message as login on a wrong current_password (no
    role-leak via distinct error text). Pydantic enforces the new-
    password length floor (≥6 chars); a finer-grained policy can be
    added in a future feature.

    Session is preserved on success — the HR keeps working without a
    forced re-login. The session cookie is signed with SESSION_SECRET,
    not derived from the password, so no cookie rotation is needed.
    """
    if not verify_password(payload.current_password, hr.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Current password is incorrect.",
        )

    hr.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"status": "password_changed"}

@router.get("/session-status")
def session_status(request: Request, db: Session = Depends(get_db)):
    """
    Silent probe used by the login page on mount. Always returns 200 to
    avoid red 401s in the browser console; returns `logged_in: false` for
    any non-HR situation (no session, deleted user, OR admin session).
    The admin equivalent lives at /api/admin/session-status.
    """
    hr_id = request.session.get("hr_admin_id")
    if not hr_id:
        return {"logged_in": False, "user": None}

    hr = db.query(HRAdmin).filter(HRAdmin.id == hr_id).first()
    if not hr:
        # Session has an hr_admin_id but the user was deleted from the DB.
        # Clear the stale session and report logged-out.
        request.session.clear()
        return {"logged_in": False, "user": None}

    if hr.role != "hr":
        # An admin session exists; from the HR portal's perspective the
        # user is logged-out. Don't clear the cookie — the admin portal
        # needs it. The login page also probes /api/admin/session-status,
        # which will pick this up.
        return {"logged_in": False, "user": None}

    return {
        "logged_in": True,
        "user": {"id": hr.id, "name": hr.name, "email": hr.email},
    }


# ------------------------------------------------------------------
# Invitations
# ------------------------------------------------------------------
@router.post("/invite", response_model=InviteCreateResponse)
def create_invite(
    payload: InviteCreateRequest,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Generate a candidate test URL.
    Token is unique 32-byte URL-safe random — collision risk is negligible,
    but we retry up to 5 times to be safe.
    Email delivery is stubbed in v1 (Day 3 will wire SMTP); the URL is
    returned in the response so HR can copy/paste it for now.
    """
    token = None
    for _ in range(5):
        candidate_token = generate_token()
        if not db.query(Invitation).filter(Invitation.token == candidate_token).first():
            token = candidate_token
            break
    if token is None:
        raise HTTPException(status_code=500, detail="Could not allocate unique invitation token.")

    # Pydantic's min_length runs before our strip, so a whitespace-only name
    # passes validation. Re-check after stripping.
    candidate_name = payload.candidate_name.strip()
    if not candidate_name:
        raise HTTPException(status_code=422, detail="candidate_name cannot be blank.")

    # Validate HR-chosen scheduling window. _validate_window raises 400 with
    # a friendly message if the window is in the past, end <= start, or shorter
    # than 60 min (the test budget). Frontend pre-flights the same checks but
    # we re-validate here as the source of truth.
    _validate_window(payload.valid_from, payload.valid_until)
    # Persist as naive UTC to match the column type and rest of the codebase.
    valid_from = _to_naive_utc(payload.valid_from)
    expires_at = _to_naive_utc(payload.valid_until)
    access_code = generate_access_code()

    # Snapshot the operational settings onto this invitation. Future setting
    # changes won't affect this row — see spec for the rationale.
    settings_row = db.query(SystemSettings).filter(SystemSettings.id == 1).first()
    settings = _settings_to_dict(settings_row)

    inv = Invitation(
        token=token,
        candidate_email=payload.candidate_email.lower(),
        candidate_name=candidate_name,
        difficulty=payload.difficulty,
        hr_admin_id=hr.id,
        valid_from=valid_from,
        expires_at=expires_at,
        access_code=access_code,
        max_starts=settings["max_starts"],
        reading_seconds=settings["reading_seconds"],
        writing_seconds=settings["writing_seconds"],
        speaking_seconds=settings["speaking_seconds"],
        # Snapshot HR's chosen timezone onto the row. Resends and
        # regenerate-code calls later read it from here, so the candidate
        # always sees their original local-time window even if HR's
        # browser/preferences change between sends.
        display_timezone=payload.timezone,
        # HR's per-invitation section selection. Schema defaults each to
        # True so older clients that don't send these fields preserve the
        # full-test behavior. Validator already rejected all-three-false.
        include_reading=payload.include_reading,
        include_writing=payload.include_writing,
        include_speaking=payload.include_speaking,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    exam_url = f"{APP_BASE_URL}/exam/{token}"

    # Send invitation email (best-effort — the invite is already saved in the DB
    # above, so HR can fall back to copy/paste from the dashboard popup if SMTP
    # fails). All failure modes are logged with [smtp] prefix in the server log.
    email_ok, email_err = send_invitation_email(
        candidate_email=inv.candidate_email,
        candidate_name=inv.candidate_name,
        exam_url=exam_url,
        access_code=access_code,
        valid_from=inv.valid_from,
        valid_until=inv.expires_at,
        hr_name=hr.name,
        display_timezone=inv.display_timezone,
        include_reading=inv.include_reading,
        include_writing=inv.include_writing,
        include_speaking=inv.include_speaking,
    )

    # Persist the email send result so the dashboard can surface failures to HR
    # (e.g. "Email failed to send — copy URL manually"). Done as a second commit
    # because the SMTP send happens AFTER the row is saved — so HR keeps a
    # usable invitation even if the email send hangs or crashes mid-process.
    if email_ok:
        inv.email_status = "sent"
        inv.email_error = None
    else:
        inv.email_status = "failed"
        inv.email_error = email_err
    db.commit()

    # Audit log line — useful for debugging and proves the invite was created
    # even when email delivery silently fails.
    print(
        f"[invite] {hr.email} invited {inv.candidate_email} ({inv.difficulty}) "
        f"-> {exam_url}  code={access_code}  "
        f"email={'sent' if email_ok else 'FAILED: ' + (email_err or 'unknown')}"
    )

    return InviteCreateResponse(
        invitation_id=inv.id,
        token=token,
        candidate_name=inv.candidate_name,
        candidate_email=inv.candidate_email,
        difficulty=inv.difficulty,
        exam_url=exam_url,
        access_code=access_code,
        expires_at=inv.expires_at,
        email_status=inv.email_status,
        email_error=inv.email_error,
    )


# ------------------------------------------------------------------
# Regenerate access code (after lockout)
# ------------------------------------------------------------------
@router.post("/invite/{invitation_id}/regenerate-code", response_model=InviteCreateResponse)
def regenerate_code(
    invitation_id: int,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    HR can regenerate a candidate's access code, e.g. after they got locked out
    from too many wrong attempts. Resets the failed_code_attempts counter and
    clears the code_locked flag.

    Tenancy check: 404 (not 403) if the invitation belongs to a different HR.
    """
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not inv or inv.hr_admin_id != hr.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    if inv.submitted_at is not None:
        raise HTTPException(
            status_code=410,
            detail="This test has already been submitted. Cannot regenerate code.",
        )

    inv.access_code = generate_access_code()
    inv.failed_code_attempts = 0
    inv.code_locked = False
    db.commit()
    db.refresh(inv)

    exam_url = f"{APP_BASE_URL}/exam/{inv.token}"

    # Notify the candidate via email that their access code was reset. Same
    # best-effort policy: regen is recorded in the DB regardless of SMTP outcome.
    email_ok, email_err = send_regenerated_code_email(
        candidate_email=inv.candidate_email,
        candidate_name=inv.candidate_name,
        exam_url=exam_url,
        access_code=inv.access_code,
        valid_from=inv.valid_from,
        valid_until=inv.expires_at,
        hr_name=hr.name,
        display_timezone=inv.display_timezone,
        include_reading=inv.include_reading,
        include_writing=inv.include_writing,
        include_speaking=inv.include_speaking,
    )

    # Update email tracking. Regenerate replaces the previous status entirely:
    # if a previous send succeeded but the new one fails, HR should see the
    # latest attempt's status (not stale "sent" from a code that no longer works).
    if email_ok:
        inv.email_status = "sent"
        inv.email_error = None
    else:
        inv.email_status = "failed"
        inv.email_error = email_err
    db.commit()

    print(
        f"[regenerate] {hr.email} regenerated code for {inv.candidate_email} "
        f"-> code={inv.access_code}  "
        f"email={'sent' if email_ok else 'FAILED: ' + (email_err or 'unknown')}"
    )

    return InviteCreateResponse(
        invitation_id=inv.id,
        token=inv.token,
        candidate_name=inv.candidate_name,
        candidate_email=inv.candidate_email,
        difficulty=inv.difficulty,
        exam_url=exam_url,
        access_code=inv.access_code,
        expires_at=inv.expires_at,
        email_status=inv.email_status,
        email_error=inv.email_error,
    )


# ------------------------------------------------------------------
# Invitation details (for pending candidates) — view URL + access code
# ------------------------------------------------------------------
@router.get("/invitation/{invitation_id}/details", response_model=InvitationDetails)
def invitation_details(
    invitation_id: int,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Return the full invitation state for a candidate. Used by the candidate
    detail page to render the "INVITATION DETAILS" card showing URL, access
    code, email status, and expiry — even AFTER the post-invite popup has
    been dismissed.

    Returns valid data for both pending and submitted invitations. The
    frontend decides whether to render the card (skipped for submitted).

    Tenancy check: 404 (not 403) if the invitation belongs to a different
    HR, to avoid leaking which invitation IDs exist.
    """
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not inv or inv.hr_admin_id != hr.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    return InvitationDetails(
        invitation_id=inv.id,
        candidate_name=inv.candidate_name,
        candidate_email=inv.candidate_email,
        difficulty=inv.difficulty,
        created_at=inv.created_at,
        valid_from=inv.valid_from,
        expires_at=inv.expires_at,
        started_at=inv.started_at,
        submitted_at=inv.submitted_at,
        exam_url=f"{APP_BASE_URL}/exam/{inv.token}",
        access_code=inv.access_code,
        email_status=inv.email_status,
        email_error=inv.email_error,
        code_locked=inv.code_locked,
        failed_code_attempts=inv.failed_code_attempts,
    )


# ------------------------------------------------------------------
# Resend invitation email — same URL + access code, just send again
# ------------------------------------------------------------------
@router.post("/invite/{invitation_id}/resend-email", response_model=ResendEmailResponse)
def resend_invitation_email(
    invitation_id: int,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Resend the invitation email to the candidate WITHOUT regenerating the
    access code. Use cases:
      - Initial email failed (e.g. SMTP timeout) — HR retries
      - Candidate says they didn't receive it — HR resends same code

    Distinct from /regenerate-code: this does NOT change the access_code
    so a candidate who already received the original email can still use
    the code if they find it later.

    Refuses to resend after submission (test is over, pointless to resend).
    Tenancy: 404 if not owned by this HR.
    """
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not inv or inv.hr_admin_id != hr.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    if inv.submitted_at is not None:
        raise HTTPException(
            status_code=410,
            detail="This test has already been submitted. No need to resend.",
        )

    exam_url = f"{APP_BASE_URL}/exam/{inv.token}"
    email_ok, email_err = send_invitation_email(
        candidate_email=inv.candidate_email,
        candidate_name=inv.candidate_name,
        exam_url=exam_url,
        access_code=inv.access_code,
        valid_from=inv.valid_from,
        valid_until=inv.expires_at,
        hr_name=hr.name,
        display_timezone=inv.display_timezone,
        include_reading=inv.include_reading,
        include_writing=inv.include_writing,
        include_speaking=inv.include_speaking,
    )

    # Update tracking columns (same logic as create_invite). Resend replaces
    # the previous status — if a previous attempt succeeded but this one
    # fails, HR needs to see the latest failure, not stale "sent".
    if email_ok:
        inv.email_status = "sent"
        inv.email_error = None
    else:
        inv.email_status = "failed"
        inv.email_error = email_err
    db.commit()

    print(
        f"[resend] {hr.email} resent invite to {inv.candidate_email} "
        f"(invitation_id={inv.id})  "
        f"email={'sent' if email_ok else 'FAILED: ' + (email_err or 'unknown')}"
    )

    return ResendEmailResponse(
        email_status=inv.email_status,
        email_error=inv.email_error,
    )


# ------------------------------------------------------------------
# Results
# ------------------------------------------------------------------
@router.get("/results", response_model=list[ScoreSummary])
def list_results(hr: HRAdmin = Depends(require_hr), db: Session = Depends(get_db)):
    """
    All invitations sent by this HR, newest first. Score fields are None
    until the candidate submits and Day 2 scoring fills them in.
    """
    invitations = (
        db.query(Invitation)
        .filter(Invitation.hr_admin_id == hr.id)
        .order_by(Invitation.created_at.desc())
        .all()
    )

    out = []
    for inv in invitations:
        s = inv.score  # SQLAlchemy returns None if no score row yet
        out.append(
            ScoreSummary(
                invitation_id=inv.id,
                candidate_name=inv.candidate_name,
                candidate_email=inv.candidate_email,
                difficulty=inv.difficulty,
                submitted_at=inv.submitted_at,
                reading_score=s.reading_score if s else None,
                writing_score=s.writing_score if s else None,
                speaking_score=s.speaking_score if s else None,
                total_score=s.total_score if s else None,
                rating=s.rating if s else None,
                include_reading=inv.include_reading,
                include_writing=inv.include_writing,
                include_speaking=inv.include_speaking,
                # Pass email columns straight through. Only include the error
                # string when status is "failed" — for "sent" or "pending"
                # rows the error column should already be null, but we belt-
                # and-braces it here so a stale error never leaks.
                email_status=inv.email_status,
                email_error=inv.email_error if inv.email_status == "failed" else None,
            )
        )
    return out


@router.get("/results/{invitation_id}", response_model=ScoreDetail)
def result_detail(
    invitation_id: int,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Detail view for one candidate. Tenancy check: 404 (not 403) if the
    invitation belongs to a different HR — don't leak existence.
    """
    inv = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if not inv or inv.hr_admin_id != hr.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    s = inv.score

    # Audio recordings — sorted by file index (q0, q1, q2). Each one carries the
    # speaking topic prompt so HR knows what the candidate was responding to.
    audio_pubs: list[AudioRecordingPublic] = []
    if inv.audio_recordings:
        # Build a topic-id → prompt map so we don't N+1 query
        topic_ids = list({r.topic_id for r in inv.audio_recordings})
        topic_map = {
            t.id: t.prompt_text
            for t in db.query(SpeakingTopic).filter(SpeakingTopic.id.in_(topic_ids)).all()
        }
        # Pair each recording with its position in the candidate's assigned topic order.
        # If assigned_topic_ids is set, we use it; otherwise fall back to recording order.
        assigned = inv.assigned_topic_ids or []
        for rec in sorted(inv.audio_recordings, key=lambda r: r.id):
            try:
                qi = assigned.index(rec.topic_id)
            except ValueError:
                qi = -1
            audio_pubs.append(AudioRecordingPublic(
                id=rec.id,
                question_index=qi,
                topic_prompt=topic_map.get(rec.topic_id, "(topic missing)"),
                duration_seconds=rec.duration_seconds,
                transcript=rec.transcript,
            ))

    # Essay (writing response) — pulled from the relationship for HR review
    wr: WritingResponse | None = inv.writing_response
    writing_topic_text = None
    essay_text = None
    essay_word_count = None
    if wr:
        essay_text = wr.essay_text
        essay_word_count = wr.word_count
        topic = db.query(WritingTopic).filter(WritingTopic.id == wr.topic_id).first()
        if topic:
            writing_topic_text = topic.prompt_text

    return ScoreDetail(
        invitation_id=inv.id,
        candidate_name=inv.candidate_name,
        candidate_email=inv.candidate_email,
        difficulty=inv.difficulty,
        submitted_at=inv.submitted_at,
        reading_score=s.reading_score if s else None,
        reading_correct=s.reading_correct if s else None,
        reading_total=s.reading_total if s else None,
        writing_topic_text=writing_topic_text,
        essay_text=essay_text,
        essay_word_count=essay_word_count,
        writing_breakdown=s.writing_breakdown if s else None,
        writing_score=s.writing_score if s else None,
        speaking_breakdown=s.speaking_breakdown if s else None,
        speaking_score=s.speaking_score if s else None,
        total_score=s.total_score if s else None,
        rating=s.rating if s else None,
        ai_feedback=s.ai_feedback if s else None,
        include_reading=inv.include_reading,
        include_writing=inv.include_writing,
        include_speaking=inv.include_speaking,
        tab_switches_count=inv.tab_switches_count or 0,
        tab_switches_total_seconds=inv.tab_switches_total_seconds or 0,
        submission_reason=inv.submission_reason,
        audio_recordings=audio_pubs,
    )


# ------------------------------------------------------------------
# Audio streaming
# ------------------------------------------------------------------
@router.get("/audio/{audio_recording_id}")
def get_audio(
    audio_recording_id: int,
    hr: HRAdmin = Depends(require_hr),
    db: Session = Depends(get_db),
):
    """
    Stream a candidate's audio recording back to HR's <audio> element.
    Tenancy check: only the HR who invited the candidate can access the audio.
    Anyone else (including other HRs) gets 404 — don't leak existence.
    """
    rec = db.query(AudioRecording).filter(AudioRecording.id == audio_recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found.")

    # Walk back to the invitation and verify the HR owns it
    inv = db.query(Invitation).filter(Invitation.id == rec.invitation_id).first()
    if not inv or inv.hr_admin_id != hr.id:
        raise HTTPException(status_code=404, detail="Recording not found.")

    file_path = Path(rec.file_path)
    if not file_path.is_file():
        # File removed from disk but DB row remains — log and 404
        raise HTTPException(
            status_code=404,
            detail="Audio file is no longer available on the server.",
        )

    return FileResponse(
        path=str(file_path),
        media_type=rec.mime_type or "audio/webm",
        filename=f"candidate_{inv.id}_q{audio_recording_id}.webm",
    )
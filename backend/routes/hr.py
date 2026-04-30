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
from models import HRAdmin, Invitation, AudioRecording, SpeakingTopic
from schemas import (
    HRLoginRequest,
    HRLoginResponse,
    InviteCreateRequest,
    InviteCreateResponse,
    ScoreSummary,
    ScoreDetail,
    AudioRecordingPublic,
)
from auth import verify_password, generate_token, require_hr


router = APIRouter(prefix="/api/hr", tags=["hr"])

INVITATION_TTL_HOURS = int(os.getenv("INVITATION_TTL_HOURS", "24"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


def _utcnow_naive() -> datetime:
    """Match models.py's _utcnow — naive UTC for cross-DB consistency."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
@router.post("/login", response_model=HRLoginResponse)
def login(payload: HRLoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Validate email + password, set session cookie, return HR profile.
    Same generic 401 message for "no such user" and "wrong password" — don't
    leak which one failed (slows down enumeration attacks).
    """
    hr = db.query(HRAdmin).filter(HRAdmin.email == payload.email.lower()).first()
    if not hr or not verify_password(payload.password, hr.password_hash):
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

    expires_at = _utcnow_naive() + timedelta(hours=INVITATION_TTL_HOURS)

    inv = Invitation(
        token=token,
        candidate_email=payload.candidate_email.lower(),
        candidate_name=candidate_name,
        difficulty=payload.difficulty,
        hr_admin_id=hr.id,
        expires_at=expires_at,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    exam_url = f"{APP_BASE_URL}/exam/{token}"

    # TODO Day 3: send email to inv.candidate_email with exam_url here.
    print(f"[invite] {hr.email} invited {inv.candidate_email} ({inv.difficulty}) -> {exam_url}")

    return InviteCreateResponse(
        invitation_id=inv.id,
        token=token,
        exam_url=exam_url,
        expires_at=inv.expires_at,
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
                speaking_score=s.speaking_score if s else None,
                total_score=s.total_score if s else None,
                rating=s.rating if s else None,
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

    return ScoreDetail(
        invitation_id=inv.id,
        candidate_name=inv.candidate_name,
        candidate_email=inv.candidate_email,
        difficulty=inv.difficulty,
        submitted_at=inv.submitted_at,
        reading_score=s.reading_score if s else None,
        reading_correct=s.reading_correct if s else None,
        reading_total=s.reading_total if s else None,
        speaking_breakdown=s.speaking_breakdown if s else None,
        speaking_score=s.speaking_score if s else None,
        total_score=s.total_score if s else None,
        rating=s.rating if s else None,
        ai_feedback=s.ai_feedback if s else None,
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

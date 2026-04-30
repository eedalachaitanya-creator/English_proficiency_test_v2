"""
Candidate test submission.

POST /api/submit accepts multipart/form-data:
  - answers      : JSON-encoded {"<question_id>": <selected_option>, ...}
  - topic_ids    : JSON-encoded [<topic_id>, <topic_id>, <topic_id>]
  - audio_0..N   : audio file blobs, one per speaking question

Auth is the same candidate session cookie set when they opened /exam/{token}.

Side effects:
  1. Insert MCQAnswer rows (only for questions actually assigned to this invitation).
  2. Save audio files to backend/audio_uploads/inv_<id>_q<i>.webm and insert AudioRecording rows.
  3. Set invitations.submitted_at — invitation token now treated as expired (one-time-use).
  4. Compute reading score deterministically and write a Score row. Speaking score
     is stubbed for now (next batch wires Whisper + Claude).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, File, Form, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models import Invitation, MCQAnswer, AudioRecording
from schemas import SubmitResponse
from scoring import score_invitation


router = APIRouter(tags=["submit"])

# Where uploaded audio files live on disk. Created on first upload if missing.
# This folder is gitignored — never commit candidate recordings.
AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio_uploads"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/api/submit", response_model=SubmitResponse)
async def submit_test(
    request: Request,
    answers: str = Form(...),
    topic_ids: str = Form("[]"),
    audio_0: UploadFile | None = File(None),
    audio_1: UploadFile | None = File(None),
    audio_2: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    # ---- 1. Validate session ----
    inv_id = request.session.get("invitation_id")
    if not inv_id:
        raise HTTPException(401, "No active test session.")

    inv = db.query(Invitation).filter(Invitation.id == inv_id).first()
    if not inv:
        raise HTTPException(401, "Test session is invalid.")
    if inv.submitted_at is not None:
        raise HTTPException(410, "This test has already been submitted.")
    if inv.expires_at < _utcnow_naive():
        raise HTTPException(410, "This test link has expired.")

    # ---- 2. Parse JSON inputs (validate before touching DB) ----
    try:
        answer_dict = json.loads(answers)
        topic_id_list = json.loads(topic_ids)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"Could not parse JSON fields: {e}")

    if not isinstance(answer_dict, dict):
        raise HTTPException(422, "answers must be a JSON object.")
    if not isinstance(topic_id_list, list):
        raise HTTPException(422, "topic_ids must be a JSON array.")

    assigned_q_ids = set(inv.assigned_question_ids or [])
    assigned_t_ids = set(inv.assigned_topic_ids or [])

    # ---- 3. Save MCQ answers ----
    # Only accept answers for questions that were actually assigned to this candidate.
    # Anything else is silently dropped (don't trust the client).
    for qid_str, selected in answer_dict.items():
        try:
            qid = int(qid_str)
            sel = int(selected)
        except (TypeError, ValueError):
            continue
        if qid not in assigned_q_ids:
            continue
        if sel < 0 or sel > 3:
            continue
        db.add(MCQAnswer(
            invitation_id=inv.id,
            question_id=qid,
            selected_option=sel,
        ))

    # ---- 4. Save audio files to disk + insert AudioRecording rows ----
    AUDIO_DIR.mkdir(exist_ok=True)
    audio_uploads = [audio_0, audio_1, audio_2]
    for i, audio in enumerate(audio_uploads):
        if audio is None:
            continue
        if i >= len(topic_id_list):
            continue
        topic_id = topic_id_list[i]
        try:
            topic_id = int(topic_id)
        except (TypeError, ValueError):
            continue
        if topic_id not in assigned_t_ids:
            continue

        # Read into memory then write — fine for ≤2 MB audio files. For larger
        # files, stream chunk-by-chunk instead.
        contents = await audio.read()
        if not contents:
            continue
        file_path = AUDIO_DIR / f"inv_{inv.id}_q{i}.webm"
        with open(file_path, "wb") as f:
            f.write(contents)

        db.add(AudioRecording(
            invitation_id=inv.id,
            topic_id=topic_id,
            file_path=str(file_path),
            mime_type=audio.content_type or "audio/webm",
            duration_seconds=None,  # could probe via ffprobe; not critical for v1
        ))

    # ---- 5. Mark submitted (single-use enforcement) ----
    inv.submitted_at = _utcnow_naive()
    db.flush()  # ensure MCQAnswer rows are visible to score_invitation

    # ---- 6. Score (reading deterministic; speaking stubbed for now) ----
    score_invitation(inv, db)

    db.commit()

    ref_id = f"EPT-{inv.id:05d}-{inv.token[:6].upper()}"
    return SubmitResponse(ref_id=ref_id, status="submitted")

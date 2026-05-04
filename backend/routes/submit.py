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
from models import Invitation, MCQAnswer, AudioRecording, WritingResponse, WritingTopic
from schemas import SubmitResponse
from scoring import score_invitation


router = APIRouter(tags=["submit"])

# Where uploaded audio files live on disk. Created on first upload if missing.
# This folder is gitignored — never commit candidate recordings.
AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio_uploads"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


from config import HARD_FLOOR_WORDS


def _word_count(text: str) -> int:
    """Crude but consistent word counter — splits on whitespace, ignores empties."""
    return len([w for w in text.strip().split() if w])


# ------------------------------------------------------------------
# submission_reason — recorded on Invitation to explain why the test ended.
# ------------------------------------------------------------------
_ALLOWED_SUBMISSION_REASONS = {
    "candidate_finished",
    "reading_timer_expired",
    "writing_timer_expired",
    "speaking_timer_expired",
    "tab_switch_termination",
    "window_expired",
}
_DEFAULT_SUBMISSION_REASON = "candidate_finished"


def _validate_submission_reason(value) -> str:
    """
    Map a form value to a canonical submission_reason. A bad value silently
    coerces to the default — refusing a real test submission because the
    frontend sent a typo would be much worse than recording the wrong reason.
    """
    if value is None:
        return _DEFAULT_SUBMISSION_REASON
    candidate = str(value).strip()
    if candidate in _ALLOWED_SUBMISSION_REASONS:
        return candidate
    return _DEFAULT_SUBMISSION_REASON


@router.post("/api/submit", response_model=SubmitResponse)
async def submit_test(
    request: Request,
    answers: str = Form(...),
    topic_ids: str = Form("[]"),
    essay_text: str = Form(""),         # the candidate's written essay
    tab_switches_count: str = Form("0"),         # number of tab switches during test
    tab_switches_total_seconds: str = Form("0"), # cumulative seconds away
    submission_reason: str = Form(""),           # why the test ended; see _validate_submission_reason
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

    # ---- 2b. Parse tab-switching telemetry early ----
    # We need is_terminated up front because it loosens validation rules
    # below (terminated submissions accept empty essays, partial audio, etc.).
    # Untrusted client input — clamp to safe ranges. Cap seconds at 24h
    # so a malicious client can't poison the row with absurd values.
    try:
        ts_count = max(0, int(tab_switches_count))
    except (TypeError, ValueError):
        ts_count = 0
    try:
        ts_seconds = max(0, min(int(tab_switches_total_seconds), 86_400))
    except (TypeError, ValueError):
        ts_seconds = 0
    # 3 strikes = test was force-terminated by the frontend tracker.
    # The frontend only triggers force-submit when this threshold is reached,
    # so seeing >=3 here means partial-data submission is expected.
    is_terminated = ts_count >= 3

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

    # ---- 3b. Save the writing essay ----
    # The candidate's frontend enforces the soft word range, but the server is the
    # security boundary — a non-browser client (curl/Postman) could bypass the UI.
    # If a writing topic was assigned, an essay is REQUIRED at submit time —
    # UNLESS this is a force-terminated submission (3+ tab switches), in which
    # case we accept whatever the candidate had typed (including nothing) so
    # we don't lose all their data over a strict validation.
    essay_clean = (essay_text or "").strip()
    word_count = _word_count(essay_clean)
    if inv.assigned_writing_topic_id and not is_terminated:
        # Normal submission — enforce minimum length
        if word_count < HARD_FLOOR_WORDS:
            raise HTTPException(
                422,
                f"Essay too short ({word_count} words). Minimum {HARD_FLOOR_WORDS} words required.",
            )
        topic = db.query(WritingTopic).filter(WritingTopic.id == inv.assigned_writing_topic_id).first()
        # Hard ceiling: 2x the topic's max — prevents pasting books
        if topic and word_count > topic.max_words * 2:
            raise HTTPException(
                422,
                f"Essay too long ({word_count} words). Hard limit is {topic.max_words * 2}.",
            )

        db.add(WritingResponse(
            invitation_id=inv.id,
            topic_id=inv.assigned_writing_topic_id,
            essay_text=essay_clean,
            word_count=word_count,
        ))
    elif inv.assigned_writing_topic_id and is_terminated and word_count > 0:
        # Terminated mid-test, but they had typed some essay text. Save it
        # (even if it's under HARD_FLOOR_WORDS) so HR can review what they had.
        # Skip the upper-bound check too — if the data is here, just store it.
        db.add(WritingResponse(
            invitation_id=inv.id,
            topic_id=inv.assigned_writing_topic_id,
            essay_text=essay_clean,
            word_count=word_count,
        ))
    # If terminated AND no essay text: don't create a WritingResponse row at all.
    # The candidate was terminated before they wrote anything. The dashboard
    # will show "no essay" and the tab_switches_count tells HR why.

    # ---- 4. Save audio files to disk + insert AudioRecording rows ----
    print("\n" + "=" * 70, flush=True)
    print(f"[SUBMIT] Invitation {inv.id} ({inv.candidate_name}) submitting", flush=True)
    print(f"[SUBMIT] Topic IDs received from frontend: {topic_id_list}", flush=True)
    print(f"[SUBMIT] Assigned topic IDs for this candidate: {sorted(assigned_t_ids)}", flush=True)
    print("=" * 70, flush=True)

    AUDIO_DIR.mkdir(exist_ok=True)
    audio_uploads = [audio_0, audio_1, audio_2]
    for i, audio in enumerate(audio_uploads):
        if audio is None:
            print(f"[SUBMIT] Slot audio_{i}: empty (no file uploaded)", flush=True)
            continue
        if i >= len(topic_id_list):
            print(f"[SUBMIT] Slot audio_{i}: skipped (no matching topic_id at index {i})", flush=True)
            continue
        topic_id = topic_id_list[i]
        try:
            topic_id = int(topic_id)
        except (TypeError, ValueError):
            print(f"[SUBMIT] Slot audio_{i}: skipped (topic_id '{topic_id}' is not an int)", flush=True)
            continue
        if topic_id not in assigned_t_ids:
            print(f"[SUBMIT] Slot audio_{i}: REJECTED (topic_id {topic_id} not assigned to this candidate)", flush=True)
            continue

        # Read into memory then write — fine for ≤2 MB audio files. For larger
        # files, stream chunk-by-chunk instead.
        contents = await audio.read()
        if not contents:
            print(f"[SUBMIT] Slot audio_{i}: skipped (empty body)", flush=True)
            continue
        file_path = AUDIO_DIR / f"inv_{inv.id}_q{i}.webm"
        with open(file_path, "wb") as f:
            f.write(contents)

        size_kb = len(contents) / 1024
        print(
            f"[SUBMIT] Slot audio_{i}: SAVED  "
            f"topic_id={topic_id}  "
            f"size={size_kb:.1f} KB  "
            f"mime={audio.content_type or '(none)'}  "
            f"path={file_path.name}",
            flush=True,
        )

        db.add(AudioRecording(
            invitation_id=inv.id,
            topic_id=topic_id,
            file_path=str(file_path),
            mime_type=audio.content_type or "audio/webm",
            duration_seconds=None,  # could probe via ffprobe; not critical for v1
        ))

    print("=" * 70, flush=True)
    print(f"[SUBMIT] All audio files saved. Starting scoring...", flush=True)
    print("=" * 70 + "\n", flush=True)

    # ---- 4b. Save tab-switching telemetry to the invitation row ----
    # Values were already parsed and clamped at the top of this function
    # (we needed them early to set the is_terminated flag). Now just store.
    inv.tab_switches_count = ts_count
    inv.tab_switches_total_seconds = ts_seconds
    if is_terminated:
        print(
            f"[SUBMIT] *** TERMINATED *** Tab switches: count={ts_count}, "
            f"total_seconds={ts_seconds}",
            flush=True,
        )
    else:
        print(
            f"[SUBMIT] Tab switches: count={ts_count}, total_seconds={ts_seconds}",
            flush=True,
        )

    # ---- 4c. Record submission reason ----
    # is_terminated (3+ tab switches) wins over whatever the frontend sent —
    # this keeps stale clients from mislabeling a tab-switch termination as
    # "candidate_finished". For timer-expiry the frontend's value is honored.
    if is_terminated:
        inv.submission_reason = "tab_switch_termination"
    else:
        inv.submission_reason = _validate_submission_reason(submission_reason)
    print(f"[SUBMIT] submission_reason={inv.submission_reason}", flush=True)

    # ---- 5. Mark submitted (single-use enforcement) ----
    inv.submitted_at = _utcnow_naive()
    db.flush()  # ensure MCQAnswer rows are visible to score_invitation

    # ---- 6. Score (reading deterministic; speaking stubbed for now) ----
    score_invitation(inv, db)

    db.commit()

    ref_id = f"EPT-{inv.id:05d}-{inv.token[:6].upper()}"
    return SubmitResponse(ref_id=ref_id, status="submitted")
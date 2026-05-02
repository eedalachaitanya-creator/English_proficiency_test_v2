"""
Pydantic request/response shapes.

These are what FastAPI uses to validate incoming JSON and serialize outgoing JSON.
They're intentionally separate from SQLAlchemy models so we can choose exactly
what fields to expose to the client (e.g., never expose `correct_answer`).
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field


# ============================================================
# HR auth
# ============================================================
class HRLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class HRLoginResponse(BaseModel):
    id: int
    name: str
    email: EmailStr


# ============================================================
# Invitation creation (HR side)
# ============================================================
class InviteCreateRequest(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    candidate_email: EmailStr
    difficulty: Literal["intermediate", "expert"]


class InviteCreateResponse(BaseModel):
    """
    Response from POST /api/hr/invite (and POST /api/hr/invite/{id}/regenerate-code).

    Includes everything the frontend needs to:
      - Display "Invitation sent to <name>" toast on success
      - Display the URL + access code in a recovery modal on email failure
      - Refresh the dashboard table without an extra round-trip
    """
    invitation_id: int
    token: str
    candidate_name: str
    candidate_email: str
    difficulty: str
    exam_url: str
    access_code: str       # 6-digit code candidate enters after opening URL
    expires_at: datetime
    # Email delivery state — drives the dashboard's UX after Generate Link.
    #   "sent"    → frontend shows success toast, closes modal
    #   "failed"  → frontend keeps modal open, shows error + URL/code as fallback
    #   "pending" → SMTP not configured at all (treat like "failed" in UI)
    email_status: str
    email_error: Optional[str] = None    # short reason if email_status == "failed"


class InvitationDetails(BaseModel):
    """
    Response from GET /api/hr/invitation/{id}/details.

    Used by the Candidate Detail page to render the "INVITATION DETAILS" card
    for pending (not-yet-submitted) candidates. HR uses this view to:
      - Recover the URL + access code if they closed the post-invite modal
      - Resend the invitation email
      - See whether the candidate has started the test or is still pending

    For submitted candidates the frontend will NOT render this card (decision
    locked in during planning) — the page goes straight to score breakdowns.
    The endpoint still returns valid data for submitted invitations so the
    backend stays simple; the frontend decides whether to show it.
    """
    invitation_id: int
    candidate_name: str
    candidate_email: str
    difficulty: str

    # Lifecycle timestamps. submitted_at is None for pending candidates;
    # the frontend uses this to decide whether to render this card at all.
    created_at: datetime
    expires_at: datetime
    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None

    # The URL + 6-digit code HR can copy if they need to share manually.
    exam_url: str
    access_code: str

    # Email delivery state (same meaning as InviteCreateResponse).
    email_status: str
    email_error: Optional[str] = None

    # Lockout state — if True, the candidate hit the 5-wrong-code limit and
    # needs the access code regenerated. Frontend may show a warning banner.
    code_locked: bool = False
    failed_code_attempts: int = 0


class ResendEmailResponse(BaseModel):
    """
    Response from POST /api/hr/invite/{id}/resend-email.

    The frontend uses this to update the Email Status badge on the candidate
    detail page without a full page refresh — and to show a toast saying
    "Email sent" or "Email failed: <reason>".
    """
    email_status: str                    # "sent" | "failed"
    email_error: Optional[str] = None


class ExamCodeVerifyRequest(BaseModel):
    """Request body for POST /api/exam/verify-code — sent by exam-code.html."""
    token: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class ExamCodeVerifyResponse(BaseModel):
    """
    Response from code verification.
    `attempts_remaining` lets the frontend show 'X attempts left' on wrong code.
    `redirect_to` tells the frontend where to send the candidate on success.
    """
    success: bool
    attempts_remaining: Optional[int] = None
    redirect_to: Optional[str] = None
    detail: Optional[str] = None


# ============================================================
# Test content (candidate-facing)
# Crucially: NO `correct_answer` field here. Server keeps that secret.
# ============================================================
class QuestionPublic(BaseModel):
    id: int
    question_type: Literal["reading_comp", "grammar", "vocabulary", "fill_blank"]
    stem: str
    options: list[str]


class PassagePublic(BaseModel):
    id: int
    title: str
    body: str


class SpeakingTopicPublic(BaseModel):
    id: int
    prompt_text: str


class WritingTopicPublic(BaseModel):
    """Essay prompt assigned to the candidate. Word range is shown to the candidate."""
    id: int
    prompt_text: str
    min_words: int
    max_words: int


class TestContent(BaseModel):
    candidate_name: str
    difficulty: str
    duration_written_seconds: int
    duration_writing_seconds: int               # essay time limit
    duration_speaking_seconds: int
    passage: PassagePublic                      # the assigned passage
    questions: list[QuestionPublic]             # the 15 questions (RC + grammar + vocab)
    writing_topic: WritingTopicPublic           # the assigned essay prompt
    speaking_topics: list[SpeakingTopicPublic]  # the 3 assigned speaking topics


# ============================================================
# Submission (candidate side, Day 2)
# ============================================================
class MCQSubmission(BaseModel):
    question_id: int
    # Upper bound enforced in the route handler against the question's actual options length.
    selected_option: int = Field(ge=0)


class SubmitResponse(BaseModel):
    ref_id: str
    status: str


# ============================================================
# HR results dashboard
# ============================================================
class ScoreSummary(BaseModel):
    invitation_id: int
    candidate_name: str
    candidate_email: EmailStr
    difficulty: str
    submitted_at: Optional[datetime]
    reading_score: Optional[int]
    writing_score: Optional[int]
    speaking_score: Optional[int]
    total_score: Optional[int]
    rating: Optional[str]
    # Email delivery state. One of:
    #   "pending" — send not yet attempted (legacy rows, or SMTP not configured)
    #   "sent"    — SMTP accepted the message
    #   "failed"  — SMTP send failed; HR action needed (see email_error)
    email_status: str = "pending"
    email_error: Optional[str] = None    # short reason if email_status == "failed"


class AudioRecordingPublic(BaseModel):
    """Minimal info HR needs to play back a candidate's recording."""
    id: int
    question_index: int        # 0-based: which speaking question this answers (0, 1, 2)
    topic_prompt: str          # the actual prompt the candidate spoke about
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None  # populated once Whisper runs (next batch)


class ScoreDetail(BaseModel):
    invitation_id: int
    candidate_name: str
    candidate_email: EmailStr
    difficulty: str
    submitted_at: Optional[datetime]

    reading_score: Optional[int]
    reading_correct: Optional[int]
    reading_total: Optional[int]

    # Writing — essay text + scoring breakdown
    writing_topic_text: Optional[str] = None
    essay_text: Optional[str] = None
    essay_word_count: Optional[int] = None
    writing_breakdown: Optional[dict] = None
    writing_score: Optional[int] = None

    speaking_breakdown: Optional[dict]
    speaking_score: Optional[int]

    total_score: Optional[int]
    rating: Optional[str]
    ai_feedback: Optional[str]

    # Tab-switching telemetry from the candidate's browser. count is the
    # number of times they switched away (after the 2-second threshold);
    # total_seconds is cumulative time spent away. HR uses these as one
    # signal among many — high values warrant investigation, not auto-rejection.
    tab_switches_count: Optional[int] = 0
    tab_switches_total_seconds: Optional[int] = 0

    # Why the test ended. Null for old rows submitted before this column existed.
    # One of: candidate_finished | reading_timer_expired | writing_timer_expired
    # | speaking_timer_expired | tab_switch_termination.
    submission_reason: Optional[str] = None

    # Per-recording metadata. Frontend uses these IDs to fetch audio bytes
    # via GET /api/hr/audio/{id}. Empty list if candidate hasn't submitted yet.
    audio_recordings: list[AudioRecordingPublic] = []
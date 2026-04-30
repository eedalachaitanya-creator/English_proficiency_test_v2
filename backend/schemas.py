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
    invitation_id: int
    token: str
    exam_url: str
    expires_at: datetime


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

    # Per-recording metadata. Frontend uses these IDs to fetch audio bytes
    # via GET /api/hr/audio/{id}. Empty list if candidate hasn't submitted yet.
    audio_recordings: list[AudioRecordingPublic] = []

"""
SQLAlchemy ORM models — the database schema in Python form.

Each class = one table. Each Column = one column.
Relationships use back_populates so we can navigate both directions.

Schema mirror of docs/requirements.md:
  hr_admins, passages, questions, speaking_topics,
  invitations, mcq_answers, audio_recordings, scores
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, CheckConstraint, Boolean
)
from sqlalchemy.orm import relationship
from database import Base


def _utcnow():
    """
    Naive UTC default. Replaces deprecated datetime.utcnow (Py 3.12+).
    Naive (not tz-aware) on purpose: SQLite drops tz info on round-trip while
    Postgres preserves it, and the resulting offset-naive vs offset-aware
    comparison errors are exactly the bug we want to avoid. Compare these
    columns against datetime.utcnow()-style naive UTC values everywhere.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------
class HRAdmin(Base):
    __tablename__ = "hr_admins"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    # Each HR has many invitations they've sent.
    invitations = relationship("Invitation", back_populates="hr", cascade="all, delete-orphan")


# ------------------------------------------------------------------
# Reading content
# ------------------------------------------------------------------
class Passage(Base):
    __tablename__ = "passages"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    difficulty = Column(String(20), nullable=False, index=True)  # 'intermediate' | 'expert'
    topic = Column(String(100))
    word_count = Column(Integer)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    # RC questions tied to this passage (passage_id IS NOT NULL on those rows).
    questions = relationship(
        "Question", back_populates="passage", cascade="all, delete-orphan"
    )


class Question(Base):
    """
    Standalone OR passage-tied. If passage_id is NULL, it's a grammar/vocab question.
    If passage_id is set, it's a reading-comprehension question about that passage.
    """
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "correct_answer >= 0 AND correct_answer <= 3",
            name="ck_questions_correct_answer_range",
        ),
    )

    id = Column(Integer, primary_key=True)
    passage_id = Column(Integer, ForeignKey("passages.id"), nullable=True, index=True)
    question_type = Column(String(20), nullable=False, index=True)  # reading_comp | grammar | vocabulary | fill_blank
    difficulty = Column(String(20), nullable=False, index=True)     # intermediate | expert
    stem = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)        # list[str], length 4
    correct_answer = Column(Integer, nullable=False)  # 0..3 (index into options)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    passage = relationship("Passage", back_populates="questions")


# ------------------------------------------------------------------
# Speaking content
# ------------------------------------------------------------------
class SpeakingTopic(Base):
    __tablename__ = "speaking_topics"

    id = Column(Integer, primary_key=True)
    prompt_text = Column(Text, nullable=False)
    difficulty = Column(String(20), nullable=False, index=True)  # intermediate | expert
    category = Column(String(100))
    created_at = Column(DateTime, default=_utcnow, nullable=False)


# ------------------------------------------------------------------
# Invitations (one row per candidate per test)
# ------------------------------------------------------------------
class Invitation(Base):
    """
    One invitation = one candidate's chance to take the test.
    The token in the URL points here. All candidate state hangs off this row.
    """
    __tablename__ = "invitations"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    candidate_email = Column(String(255), nullable=False)
    candidate_name = Column(String(100), nullable=False)
    difficulty = Column(String(20), nullable=False)  # intermediate | expert

    hr_admin_id = Column(Integer, ForeignKey("hr_admins.id"), nullable=False, index=True)

    # Assignment (filled when candidate first opens URL — locks the content).
    passage_id = Column(Integer, ForeignKey("passages.id"), nullable=True)
    assigned_question_ids = Column(JSON, nullable=True)   # list[int] — exact 15 questions chosen
    assigned_topic_ids = Column(JSON, nullable=True)      # list[int] — exact 3 speaking topics chosen
    assigned_writing_topic_id = Column(Integer, ForeignKey("writing_topics.id"), nullable=True)

    # Lifecycle
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)         # created_at + 24h
    started_at = Column(DateTime, nullable=True)          # first time URL opened
    submitted_at = Column(DateTime, nullable=True)        # final submission

    # Access code (6-digit) candidate must enter after clicking URL.
    # Lockout: 5 wrong attempts -> code_locked=True, HR must regenerate.
    access_code = Column(String(6), nullable=False)
    failed_code_attempts = Column(Integer, default=0, nullable=False)
    code_locked = Column(Boolean, default=False, nullable=False)

    # Tab-switching telemetry — captured by frontend Page Visibility API and
    # sent at submit time. Used by HR as ONE signal among many for cheating
    # investigation. Not a verdict on its own — a Slack notification can
    # cause a switch too. HR interprets the data, doesn't act blindly.
    tab_switches_count = Column(Integer, default=0, nullable=False)
    tab_switches_total_seconds = Column(Integer, default=0, nullable=False)

    hr = relationship("HRAdmin", back_populates="invitations")
    passage = relationship("Passage")
    writing_topic = relationship("WritingTopic")
    mcq_answers = relationship("MCQAnswer", back_populates="invitation", cascade="all, delete-orphan")
    audio_recordings = relationship("AudioRecording", back_populates="invitation", cascade="all, delete-orphan")
    writing_response = relationship("WritingResponse", back_populates="invitation", uselist=False, cascade="all, delete-orphan")
    score = relationship("Score", back_populates="invitation", uselist=False, cascade="all, delete-orphan")


# ------------------------------------------------------------------
# Submissions
# ------------------------------------------------------------------
class MCQAnswer(Base):
    __tablename__ = "mcq_answers"

    id = Column(Integer, primary_key=True)
    invitation_id = Column(Integer, ForeignKey("invitations.id"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    selected_option = Column(Integer, nullable=False)  # validated against options length in route handler
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    invitation = relationship("Invitation", back_populates="mcq_answers")
    question = relationship("Question")


class AudioRecording(Base):
    __tablename__ = "audio_recordings"

    id = Column(Integer, primary_key=True)
    invitation_id = Column(Integer, ForeignKey("invitations.id"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("speaking_topics.id"), nullable=False)
    file_path = Column(String(500), nullable=False)        # disk path under audio_uploads/
    mime_type = Column(String(50), nullable=False)
    duration_seconds = Column(Integer)
    transcript = Column(Text, nullable=True)               # filled by Whisper on Day 2
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    invitation = relationship("Invitation", back_populates="audio_recordings")
    topic = relationship("SpeakingTopic")


# ------------------------------------------------------------------
# Writing content + responses
# ------------------------------------------------------------------
class WritingTopic(Base):
    """An essay prompt. Each candidate gets one assigned at test start."""
    __tablename__ = "writing_topics"

    id = Column(Integer, primary_key=True)
    prompt_text = Column(Text, nullable=False)
    difficulty = Column(String(20), nullable=False, index=True)  # intermediate | expert
    min_words = Column(Integer, nullable=False, default=200)
    max_words = Column(Integer, nullable=False, default=300)
    category = Column(String(100))
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class WritingResponse(Base):
    """The candidate's essay text. One row per invitation (uselist=False on Invitation)."""
    __tablename__ = "writing_responses"

    id = Column(Integer, primary_key=True)
    invitation_id = Column(Integer, ForeignKey("invitations.id"), unique=True, nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("writing_topics.id"), nullable=False)
    essay_text = Column(Text, nullable=False)
    word_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    invitation = relationship("Invitation", back_populates="writing_response")
    topic = relationship("WritingTopic")


# ------------------------------------------------------------------
# Scores
# ------------------------------------------------------------------
class Score(Base):
    """
    One row per submitted invitation. Holds the final report HR sees.
    """
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True)
    invitation_id = Column(Integer, ForeignKey("invitations.id"), unique=True, nullable=False, index=True)

    # Reading
    reading_score = Column(Integer)      # 0..100, normalized
    reading_correct = Column(Integer)
    reading_total = Column(Integer)

    # Writing — JSON breakdown per rubric dimension
    writing_breakdown = Column(JSON)     # {"task_response": 22, "grammar": 21, "vocabulary": 20, "coherence": 18}
    writing_score = Column(Integer)      # 0..100, normalized

    # Speaking — JSON breakdown per rubric dimension
    speaking_breakdown = Column(JSON)    # {"fluency": 22, "pronunciation": 18, "grammar": 17, "vocabulary": 13, "coherence": 17}
    speaking_score = Column(Integer)     # 0..100, normalized

    # Combined — weighted: 25% reading + 35% writing + 40% speaking
    total_score = Column(Integer)        # 0..100
    rating = Column(String(30))          # 'recommended' | 'borderline' | 'not_recommended'
    ai_feedback = Column(Text)           # paragraph from Claude

    scored_at = Column(DateTime, default=_utcnow, nullable=False)

    invitation = relationship("Invitation", back_populates="score")
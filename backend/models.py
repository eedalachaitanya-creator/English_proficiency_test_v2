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
    """
    Account row for both HR users and admins. The role column distinguishes:
      - 'hr'    — uses the candidate dashboard, creates invitations, views results.
      - 'admin' — uses the admin portal only, creates HR accounts.
    These are strictly disjoint roles, not a privilege hierarchy. See
    docs/superpowers/specs/2026-05-04-admin-portal-design.md.
    """
    __tablename__ = "hr_admins"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'hr')",
            name="ck_hr_admins_role",
        ),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(10), default="hr", nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # Bumped on password rotation (login + change-password). Used to
    # invalidate other sessions for the same user — see auth.py
    # _resolve_user_with_role and routes/hr.py change_password.
    password_changed_at = Column(DateTime, default=_utcnow, nullable=False)

    # Each HR has many invitations they've sent. Admins won't have any.
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
    disabled_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

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
    # When set, row is "disabled" — visible to HR but excluded from new
    # invitations. Toggleable via /api/hr-content/<resource>/{id}/toggle-disabled.
    disabled_at = Column(DateTime, nullable=True)
    # When set, row is "soft-deleted" — hidden from HR list and excluded
    # from new invitations. Existing invitations snapshot IDs at creation
    # time, so they continue to work even after a content row is deleted.
    deleted_at = Column(DateTime, nullable=True)

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
   
    disabled_at = Column(DateTime, nullable=True)
   
    deleted_at = Column(DateTime, nullable=True)


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
    # Scheduled URL validity window. The candidate's exam URL is active only
    # between [valid_from, expires_at]. Both are required and HR-chosen at
    # invitation creation. expires_at was historically created_at + 24h; now
    # it is HR's chosen window-end with no fixed relation to created_at.
    valid_from = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    started_at = Column(DateTime, nullable=True)          # first time URL opened
    submitted_at = Column(DateTime, nullable=True)        # final submission
    # Why the test was submitted. One of: candidate_finished | reading_timer_expired
    # | writing_timer_expired | speaking_timer_expired | tab_switch_termination.
    # Nullable for old rows submitted before this column existed.
    submission_reason = Column(String(40), nullable=True)

    # Snapshotted from system_settings at invitation creation. Each invitation
    # carries the operational config it was created with — changing the global
    # setting later does NOT affect existing invitations. See
    # docs/superpowers/specs/2026-05-04-system-settings-runtime-config-design.md.
    # Defaults match the historical hardcoded values so pre-feature rows
    # backfill correctly.
    max_starts = Column(Integer, default=1, nullable=False)
    start_count = Column(Integer, default=0, nullable=False)
    reading_seconds = Column(Integer, default=30 * 60, nullable=False)
    writing_seconds = Column(Integer, default=20 * 60, nullable=False)
    speaking_seconds = Column(Integer, default=10 * 60, nullable=False)

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

    # Email delivery tracking. We send an invitation email when HR clicks
    # "Generate Link", and the dashboard surfaces the outcome so HR can act
    # on failures (resend manually, fix the address, etc.).
    #
    # Three states:
    #   "pending" — send not yet attempted (default for new rows)
    #   "sent"    — SMTP accepted the message
    #   "failed"  — SMTP send failed; email_error explains why
    #
    # Note: SMTP-accepted does NOT mean delivered to the recipient inbox.
    # Bounces (mailbox doesn't exist, marked as spam, etc.) come back
    # asynchronously to the SMTP_FROM_EMAIL inbox — we don't parse those
    # here. HR checks the sender mailbox for bounce reports.
    email_status = Column(String(20), default="pending", nullable=False)
    email_error = Column(String(255), nullable=True)

    # IANA timezone name (e.g. "Asia/Kolkata", "America/New_York") that the
    # HR picked when creating the invitation. Used ONLY for rendering the
    # scheduled window in the candidate's invitation email — the database
    # itself stays in UTC for valid_from/expires_at and all internal logic.
    #
    # Default "UTC" exists so legacy rows (created before this column) and
    # any future code path that forgets to pass a timezone still produce
    # a valid email — it just shows the time in UTC like the old behavior.
    display_timezone = Column(String(64), default="UTC", nullable=False)

    # Per-invitation section selection. HR picks at invite-creation time
    # which sections this candidate's exam will include (any non-empty
    # subset of reading/writing/speaking). Default True so backfilled rows
    # behave as the old "all three sections" tests. See
    # docs/superpowers/specs/2026-05-04-per-invitation-section-selection-design.md.
    include_reading = Column(Boolean, default=True, nullable=False)
    include_writing = Column(Boolean, default=True, nullable=False)
    include_speaking = Column(Boolean, default=True, nullable=False)

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
    
    disabled_at = Column(DateTime, nullable=True)
   
    deleted_at = Column(DateTime, nullable=True)


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
    writing_breakdown = Column(JSON)     # {"grammar": 17, "vocabulary": 16, "comprehension": 18, "writing_quality": 17, "professional_communication": 16}
    writing_score = Column(Integer)      # 0..100, normalized

    # Speaking — JSON breakdown per rubric dimension
    speaking_breakdown = Column(JSON)    # {"fluency": 22, "pronunciation": 18, "grammar": 17, "vocabulary": 13, "coherence": 17}
    speaking_score = Column(Integer)     # 0..100, normalized

    # Combined — weighted via config.W_READING/W_WRITING/W_SPEAKING (currently 1/3 each).
    total_score = Column(Integer)        # 0..100
    rating = Column(String(30))          # 'recommended' | 'borderline' | 'not_recommended'
    ai_feedback = Column(Text)           # paragraph from Claude

    scored_at = Column(DateTime, default=_utcnow, nullable=False)

    invitation = relationship("Invitation", back_populates="score")


class SystemSettings(Base):
    """
    Singleton row (id=1) carrying the operational config knobs HR can change
    without redeploying. CHECK constraint enforces the singleton — only ever
    one row in this table.

    On each invitation creation, the values here are SNAPSHOTTED onto the
    new Invitation row. Changing values here does NOT affect existing or
    in-flight invitations — only ones created afterwards.

    HR (or an engineer with DB access) edits this with plain SQL:
        UPDATE system_settings SET writing_seconds = 900 WHERE id = 1;

    See docs/superpowers/specs/2026-05-04-system-settings-runtime-config-design.md.
    """
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True)
    max_starts = Column(Integer, default=1, nullable=False)
    reading_seconds = Column(Integer, default=30 * 60, nullable=False)
    writing_seconds = Column(Integer, default=20 * 60, nullable=False)
    speaking_seconds = Column(Integer, default=10 * 60, nullable=False)
    __table_args__ = (
        CheckConstraint("id = 1", name="system_settings_singleton"),
    )

# ------------------------------------------------------------------
# Supported timezones — runtime-editable list of zones HR can pick
# from when creating an invitation.
# ------------------------------------------------------------------
class SupportedTimezone(Base):
    """
    The list of timezones available in the HR invitation form's dropdown.
    Replaces the old hardcoded ALLOWED_TIMEZONES (schemas.py) and
    _TZ_LABELS (email_service.py).

    Adding/removing zones is a SQL operation — no code change needed:

        INSERT INTO supported_timezones
            (iana_name, display_label, short_label, sort_order, is_active)
        VALUES
            ('America/Phoenix', 'US Arizona Time (MST)', 'MST', 65, TRUE);

        UPDATE supported_timezones
        SET is_active = FALSE
        WHERE iana_name = 'America/Anchorage';

    NEVER DELETE rows that have been used by an invitation. Soft-delete
    via is_active=FALSE so old invitations referencing the iana_name via
    Invitation.display_timezone still render emails correctly.

    The relationship between this table and Invitation.display_timezone is
    by name only — there is NO foreign key. That's deliberate so the
    column accepts legacy values like 'UTC' (rows created before this
    feature existed) and so soft-deleting a zone doesn't cascade.
    """
    __tablename__ = "supported_timezones"

    id = Column(Integer, primary_key=True)
    # IANA timezone name — must match an entry in the IANA database
    # (e.g. "Asia/Kolkata"). Validated at the API layer when creating
    # invitations; not enforced at the DB level so we accept legacy
    # values for backward compatibility.
    iana_name = Column(String(64), unique=True, nullable=False)
    # Friendly label HR sees in the dropdown ("India Standard Time (IST)").
    display_label = Column(String(100), nullable=False)
    # Short label the candidate sees in the email ("IST"). Kept separate
    # from display_label because emails have less space and a different
    # tone than UI dropdowns.
    short_label = Column(String(20), nullable=False)
    # Lower numbers appear first in the dropdown. Spaced by 10 in seed
    # data so new zones can slot between existing ones.
    sort_order = Column(Integer, default=0, nullable=False)
    # Soft-delete flag. False = hidden from dropdown but still resolvable
    # for old invitations that already reference this iana_name.
    is_active = Column(Boolean, default=True, nullable=False)
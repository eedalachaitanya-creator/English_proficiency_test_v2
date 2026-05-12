"""
SQLAlchemy ORM models — the database schema in Python form.

Each class = one table. Each Column = one column.
Relationships use back_populates so we can navigate both directions.

Schema mirror of docs/requirements.md + multi-tenancy migrations
(2026-05-12, blocks 1-5):
  organizations (NEW),
  hr_admins, passages, questions, speaking_topics,
  invitations, mcq_answers, audio_recordings, scores,
  writing_topics, writing_responses, supported_timezones, system_settings.

Multi-tenancy notes (see docs/superpowers/specs/2026-05-12-multi-tenancy.md):
  - organizations is the new tenant table.
  - hr_admins.organization_id is NULL only when role='super'.
    Enforced by ck_hr_admins_role_org_consistency at the DB layer.
  - invitations.organization_id is NOT NULL — every invitation belongs
    to exactly one org. Denormalized from the inviting HR's org so we
    don't need a JOIN on every tenant-scoped query.
  - The four content tables (passages, questions, writing_topics,
    speaking_topics) have nullable organization_id. NULL = global
    content, visible to every org, editable only by super. Non-NULL
    = private to that org.
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
# Organizations — the tenant table introduced in 2026-05-12.
# ------------------------------------------------------------------
class Organization(Base):
    """
    A customer company using the platform. Every HR/admin user, every
    invitation, and (optionally) every content row belongs to one
    organization. The first row, id=1 'Stixis' (slug 'stixis'), is
    seeded by the migration as the home for all pre-multi-tenancy data.

    Soft-disable vs soft-delete:
      disabled_at — temporarily suspend the org. HRs/admins of this
                    org get 401 on next request via require_principal,
                    but in-flight candidate tests continue running
                    (the candidate flow doesn't authenticate as an HR;
                    it uses the invitation token + session). This means
                    a disable doesn't kick a candidate mid-test.
      deleted_at  — soft-delete. The route layer refuses to delete an
                    org that still has any non-soft-deleted hr_admins.
                    Force the cleanup explicitly so nothing is lost by
                    accident.

    Slug is the url-safe lowercase identifier (e.g. 'stixis', 'acme-corp')
    used in super-facing UI and logs. Pattern enforced at the schema
    layer: ^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$. Not exposed to candidates.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # Soft-disable: org's users can't log in but candidate tests
    # continue. Distinct from deleted_at — recoverable by clearing
    # the column.
    disabled_at = Column(DateTime, nullable=True)
    # Soft-delete: cascade refusal at route layer (refuse if any
    # active hr_admins.organization_id = this.id). Set ONLY by super
    # via /api/super/organizations/{id}/delete (introduced in Step E).
    deleted_at = Column(DateTime, nullable=True)

    # Users belonging to this org. Cascade is intentionally NOT set:
    # we soft-delete via deleted_at, never hard-delete rows that have
    # children. If a hard-delete ever becomes necessary (e.g. GDPR),
    # the caller handles cascade explicitly to avoid surprise wipes.
    hr_admins = relationship("HRAdmin", back_populates="organization")
    invitations = relationship("Invitation", back_populates="organization")


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------
class HRAdmin(Base):
    """
    Account row for super, admin, and HR users. The role column
    distinguishes:
      - 'super' — Stixis-internal god-mode account. Sees data across all
                  orgs. organization_id IS NULL for these (no org).
                  Creates/disables orgs, creates admins for orgs, manages
                  other supers.
      - 'admin' — Per-org administrator. Manages HRs in their org and
                  authors org-private content. Sees all invitations in
                  their org. organization_id IS NOT NULL.
      - 'hr'    — Per-org HR user. Creates invitations and views their
                  own candidates' results. organization_id IS NOT NULL.

    The three roles are strictly disjoint, not a privilege hierarchy.
    See docs/superpowers/specs/2026-05-12-multi-tenancy.md.

    Database-level safety nets (added in migration block 5):
      ck_hr_admins_role
          role IN ('super', 'admin', 'hr')
      ck_hr_admins_role_org_consistency
          (role = 'super' AND organization_id IS NULL)
          OR (role IN ('admin', 'hr') AND organization_id IS NOT NULL)

    These mean a bug in application code that tries to insert a super
    with an org, or an admin without one, fails at the DB rather than
    silently corrupting tenancy.
    """
    __tablename__ = "hr_admins"
    __table_args__ = (
        # Mirror of the DB-side CHECK constraints. SQLAlchemy enforces
        # these on create_all but the production schema is managed by
        # the SQL we ran in pgAdmin (Block 5) — these definitions stay
        # in sync as documentation and for tests that use create_all.
        CheckConstraint(
            "role IN ('super', 'admin', 'hr')",
            name="ck_hr_admins_role",
        ),
        CheckConstraint(
            "(role = 'super' AND organization_id IS NULL) "
            "OR (role IN ('admin', 'hr') AND organization_id IS NOT NULL)",
            name="ck_hr_admins_role_org_consistency",
        ),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(10), default="hr", nullable=False)
    # The tenant pointer. NULL for super, set for admin/hr. The CHECK
    # constraint above keeps this honest at the DB level. Indexed
    # because every admin-level query filters on it.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # Bumped on password rotation (login + change-password). Used to
    # invalidate other sessions for the same user — see auth.py
    # _resolve_user_with_role and routes/hr.py change_password.
    password_changed_at = Column(DateTime, default=_utcnow, nullable=False)
    # TRUE while the user is using a temp password emailed by
    # /forgot-password. Set on reset; cleared on successful
    # /change-password. Frontend guard + backend strict-auth dep both
    # consult this flag to lock everything except the change-password
    # screen. See docs/superpowers/specs/2026-05-06-admin-forgot-password-design.md.
    must_change_password = Column(Boolean, default=False, nullable=False)
    # Soft-delete timestamp. NULL = active. When an admin deletes an
    # HR via DELETE /api/admin/users/{id} we set this to utcnow()
    # rather than removing the row, so the HR's invitations and the
    # candidate results attached to them are preserved for audits.
    # Login + JWT-resolution + the All Users listing all filter
    # `deleted_at IS NULL` to hide soft-deleted accounts.
    deleted_at = Column(DateTime, nullable=True)

    # Each HR has many invitations they've sent. Admins and supers
    # won't have any (admins manage HRs, supers manage orgs).
    invitations = relationship("Invitation", back_populates="hr", cascade="all, delete-orphan")
    # The org this user belongs to. NULL only when role='super'.
    organization = relationship("Organization", back_populates="hr_admins")


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
    # Tenant pointer for content. NULL = global content (seeded by
    # Stixis, visible to every org, editable only by super). Non-NULL
    # = private to that org (editable by admin/HR of that org).
    # Pre-multi-tenancy content stays NULL after migration block 3.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
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
    # See Passage.organization_id — same semantics. NULL = global,
    # non-NULL = private to that org.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
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
    # See Passage.organization_id — same semantics.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
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

    Tenancy: organization_id is denormalized from the inviting HR's org
    at create_invite time and is NOT NULL on every row. Tenant-scoped
    queries filter on this column directly — never JOIN to hr_admins to
    derive it on the fly. The denormalization invariant
    (invitations.organization_id == hr_admins.organization_id for the
    inviting HR) is maintained by the route layer.
    """
    __tablename__ = "invitations"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    candidate_email = Column(String(255), nullable=False)
    candidate_name = Column(String(100), nullable=False)
    difficulty = Column(String(20), nullable=False)  # intermediate | expert

    hr_admin_id = Column(Integer, ForeignKey("hr_admins.id"), nullable=False, index=True)
    # Tenancy pointer. Required (NOT NULL after migration block 4).
    # Denormalized from hr_admin → hr_admins.organization_id.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

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
    # Lockout: MAX_CODE_ATTEMPTS wrong attempts -> code_locked=True,
    # HR must regenerate. (See config.MAX_CODE_ATTEMPTS — currently 3,
    # not 5 as an older comment claimed.)
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

    # Teams meeting fields. See migration 9b4e7f1a2c3d for the full
    # rationale. All three are nullable so legacy rows created before
    # this feature stay valid. New rows are guaranteed to populate
    # them because the route handler fails the invite if Teams API
    # errors — but historical invitations leave them NULL, and the
    # dashboard / email templates check for NULL before rendering
    # any Teams-specific UI.
    #
    #   teams_meeting_id      Graph API meeting object id (used for
    #                         future lookups, recording retrieval,
    #                         deletion). Up to ~512 chars to be safe.
    #   teams_join_url        URL the candidate and HR click to join.
    #                         Microsoft URLs can be quite long with
    #                         tenant + meeting + context query strings.
    #   teams_meeting_status  NULL = not attempted (legacy row),
    #                         'created' = Teams call succeeded,
    #                         'failed'  = Teams call errored (kept
    #                         here for dashboards / future retry,
    #                         even though current behavior fails the
    #                         invitation entirely on Teams error).
    teams_meeting_id = Column(String(512), nullable=True)
    teams_join_url = Column(String(2048), nullable=True)
    teams_meeting_status = Column(String(20), nullable=True)

    hr = relationship("HRAdmin", back_populates="invitations")
    organization = relationship("Organization", back_populates="invitations")
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
    # See Passage.organization_id — same semantics.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
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


# ------------------------------------------------------------------
# Audit log — append-only ledger of privileged actions
# ------------------------------------------------------------------
class AuditLog(Base):
    """
    One row per privileged action (super-admin org CRUD, super logins,
    etc.). Rows are append-only — application code never updates or
    deletes audit_log rows.

    Denormalized columns (actor_*, target_organization_id) are
    snapshotted at write time so the entry stays meaningful even after
    rows it references are renamed, soft-deleted, or hard-deleted.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)

    # Actor: who performed the action. All nullable so a future
    # failed-login audit (no resolved user yet) can still write a row.
    actor_id = Column(
        Integer, ForeignKey("hr_admins.id"), nullable=True, index=True
    )
    actor_role = Column(String(10), nullable=True)
    actor_email = Column(String(255), nullable=True)
    actor_organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )

    # What happened. SCREAMING_SNAKE_CASE convention; see audit.py for the
    # canonical list of action labels.
    action = Column(String(64), nullable=False, index=True)

    # Subject of the action. Both nullable for actions that don't have a
    # subject (e.g. SUPER_LOGIN). target_type uses singular lowercase
    # nouns matching the model name ('organization', 'hr_admin').
    target_type = Column(String(40), nullable=True)
    target_id = Column(Integer, nullable=True)
    target_organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )

    # Action-specific structured details. Never store secrets here.
    # For renames: {"before": "...", "after": "..."}.
    payload = Column(JSON, nullable=True)

    # Request fingerprint. Nullable so background jobs can write rows.
    ip_address = Column(String(45), nullable=True)   # IPv6 max length
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)


# ------------------------------------------------------------------
# Per-org disable of global content
# ------------------------------------------------------------------
class OrganizationContentDisable(Base):
    """
    Per-organization override for global content. One row = "this org
    has hidden this specific global passage/question/topic from its own
    candidates".

    Independent from the content table's own `disabled_at` column —
    that one is a global flag, set by super (or the content author) to
    disable a row for EVERY org. This table is the per-org layer above
    that, set by HR/admin of one org to hide a row from their org only.

    content_type values: 'passage', 'question', 'speaking_topic',
    'writing_topic'. There is no FK on content_id because it points to
    different tables depending on content_type — we validate the
    target exists at the route layer when an HR triggers the toggle.

    Composite PK (organization_id, content_type, content_id) prevents
    duplicate rows for the same (org, content) pair — toggling
    'disable' twice is idempotent.
    """
    __tablename__ = "organization_content_disable"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('passage', 'question', 'speaking_topic', 'writing_topic')",
            name="ck_org_content_disable_content_type",
        ),
    )

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        primary_key=True,
    )
    content_type = Column(String(20), primary_key=True)
    content_id = Column(Integer, primary_key=True)

    disabled_at = Column(DateTime, default=_utcnow, nullable=False)
    # Who flipped it. Nullable for migration safety and for back-fill
    # scenarios. Always set in normal route-handler operation.
    disabled_by = Column(
        Integer, ForeignKey("hr_admins.id"), nullable=True
    )
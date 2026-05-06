"""
Pydantic request/response shapes.

These are what FastAPI uses to validate incoming JSON and serialize outgoing JSON.
They're intentionally separate from SQLAlchemy models so we can choose exactly
what fields to expose to the client (e.g., never expose `correct_answer`).
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ============================================================
# HR auth
# ============================================================
class HRLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class HRLoginResponse(BaseModel):
    """Returned by /api/hr/login (full token pair) AND /api/hr/me
    (identity only — no tokens). The token fields are Optional so /me
    doesn't have to mint fresh credentials just to satisfy the schema.

    Mirrors AdminLoginResponse for consistency."""
    id: int
    name: str
    email: EmailStr
    # JWT tokens. Populated on /login; None on /me — /me is an identity
    # check, not a credential issuance, so it shouldn't be minting new
    # tokens server-side just to fit the response shape.
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = "bearer"
    expires_in: int | None = None
    # TRUE while the user is using a temp password from /forgot-password.
    # Frontend AuthService reads this and the route guard locks all
    # authenticated routes to /change-password-required until /change-
    # password is called. Default false on the schema so an old frontend
    # that doesn't know about this field interprets it as "no action
    # needed" — backwards-compat with pre-flag clients.
    must_change_password: bool = False


class RefreshTokenRequest(BaseModel):
    """POST /api/hr/refresh — trade a refresh token for a new access token."""
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Response from /api/hr/refresh. Same shape as the token-pair fields in HRLoginResponse."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ForgotPasswordRequest(BaseModel):
    """POST /api/hr/forgot-password. Anonymous endpoint — only the
    email address is needed. The endpoint always returns the same
    generic 200 message regardless of whether the email exists, was
    sent successfully, or belongs to an admin (security best-practice
    to prevent email enumeration)."""
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    """POST /api/hr/change-password (and the parallel /api/admin/...).
    The session cookie identifies the user; current_password is required
    to defend against session-hijack and drive-by changes (the same
    defense Gmail/GitHub use). new_password has the same min-length floor
    as create_hr.py CLI for consistency.

    No max_length on current_password — Pydantic shouldn't be the
    gatekeeper here, bcrypt's verify is. Capping it would lock out any
    user who originally chose a >128-char password (rare but real for
    passphrase users)."""
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=128)


# ============================================================
# Admin portal — auth + HR management
# See docs/superpowers/specs/2026-05-04-admin-portal-design.md.
# ============================================================
class AdminLoginRequest(BaseModel):
    """POST /api/admin/login. Same shape as HRLoginRequest; kept as a
    distinct class so future admin-only fields (e.g., 2FA token) don't
    require touching the HR login schema."""
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminLoginResponse(BaseModel):
    """Returned by /api/admin/login and /api/admin/me. Includes role so
    the frontend can sanity-check (defense-in-depth — backend auth is the
    real boundary).

    JWT token fields added for the new auth path. /me returns these as
    None since /me doesn't mint new tokens (you'd never want it to —
    /me is an idempotent identity check, not a credential issuance)."""
    id: int
    name: str
    email: EmailStr
    role: str  # always "admin" — the route returns 401 for any other role
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = "bearer"
    expires_in: int | None = None
    # See HRLoginResponse.must_change_password — same semantics, default
    # false for backwards-compat with pre-flag frontends.
    must_change_password: bool = False


class AdminRefreshTokenRequest(BaseModel):
    """POST /api/admin/refresh — trade a refresh token for a new access token."""
    refresh_token: str


class AdminRefreshTokenResponse(BaseModel):
    """Response from /api/admin/refresh. Same shape as the token-pair fields in AdminLoginResponse."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AdminUserSummary(BaseModel):
    """One row in GET /api/admin/users — the admin dashboard's top-level
    table. Lists every row in hr_admins (both roles) with a count of how
    many invitations they've sent. Admins always have candidate_count=0
    since they can't create invitations."""
    id: int
    name: str
    email: EmailStr
    role: str  # "hr" or "admin" — same allowed values as HRAdmin.role
    candidate_count: int  # 0 for admins; total invitations sent for HRs
    created_at: datetime


class HRCreateByAdminRequest(BaseModel):
    """POST /api/admin/hrs. Admin types the password directly; we pass
    the plaintext to bcrypt server-side. Min password length here mirrors
    the create_hr.py CLI rule for consistency."""
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class HRCreateByAdminResponse(BaseModel):
    """Response after creating an HR. Includes email_status so the admin
    UI can surface SMTP failures the same way candidate-invite does."""
    id: int
    name: str
    email: EmailStr
    email_status: str  # "sent" | "failed" | "pending"
    email_error: Optional[str] = None


# ============================================================
# Invitation creation (HR side)
# ============================================================
class InviteCreateRequest(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    candidate_email: EmailStr
    difficulty: Literal["intermediate", "expert"]
    # Scheduled URL validity window. Both required — see
    # docs/superpowers/specs/2026-05-04-scheduled-url-validity-window-design.md.
    # Values are sent as ISO-8601 UTC strings from the Angular form.
    valid_from: datetime
    valid_until: datetime
    # IANA timezone name HR selected. The list of accepted values is now
    # in the supported_timezones table — validated in the route handler
    # against active rows. We don't validate here because Pydantic field
    # validators can't access the DB session, and DB lookup is the source
    # of truth (allows zones to be added at runtime without a code change).
    # min/max length still enforced as a basic sanity check against
    # obviously malformed input.
    timezone: str = Field(min_length=1, max_length=64)

    # Per-invitation section selection. HR picks any non-empty subset of the
    # three sections. Defaults to all-true so older clients that don't send
    # these fields preserve pre-feature behavior. See
    # docs/superpowers/specs/2026-05-04-per-invitation-section-selection-design.md.
    include_reading: bool = True
    include_writing: bool = True
    include_speaking: bool = True

    @model_validator(mode="after")
    def _check_at_least_one_section(self) -> "InviteCreateRequest":
        """A test with zero sections is meaningless. Reject explicitly so HR
        gets a clear error instead of accidentally generating a no-op URL."""
        if not (self.include_reading or self.include_writing or self.include_speaking):
            raise ValueError(
                "At least one section (reading, writing, or speaking) must be selected."
            )
        return self


class SupportedTimezoneOut(BaseModel):
    """
    One row of the timezone dropdown, returned from GET /api/hr/timezones.

    The endpoint returns these as a list, sorted by sort_order, filtered
    to is_active=TRUE rows only. The frontend populates the invite-modal
    dropdown from the response and sends `iana_name` back as the `timezone`
    field on the invite create request.
    """
    iana_name: str       # "Asia/Kolkata" — what gets stored on Invitation
    display_label: str   # "India Standard Time (IST)" — what HR sees
    short_label: str     # "IST" — what the candidate sees in emails

    class Config:
        from_attributes = True


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
    valid_from: datetime         # window start — when the URL becomes active
    expires_at: datetime         # window end — when the URL stops working
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


class SectionFlags(BaseModel):
    """Which sections this candidate's exam includes. The frontend uses
    these to drive routing — sections set to False are skipped entirely."""
    reading: bool
    writing: bool
    speaking: bool


class TestContent(BaseModel):
    candidate_name: str
    difficulty: str
    duration_written_seconds: int
    duration_writing_seconds: int               # essay time limit
    duration_speaking_seconds: int
    # Window end as ISO-8601 UTC string (suffix "Z"). Frontend reads this and
    # schedules a setTimeout in each test page so the test auto-submits at the
    # window end even if the candidate is mid-section. See spec.
    valid_until_iso: str
    # Per-invitation section selection. The frontend reads this to decide
    # which section pages to walk the candidate through. For excluded
    # sections the corresponding content fields below come back null/empty.
    sections: SectionFlags
    passage: Optional[PassagePublic] = None              # null when reading is excluded
    questions: list[QuestionPublic] = []                 # empty when reading is excluded
    writing_topic: Optional[WritingTopicPublic] = None   # null when writing is excluded
    speaking_topics: list[SpeakingTopicPublic] = []      # empty when speaking is excluded


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
    # Per-invitation section selection — drives the small "R · W · S" chip
    # in the dashboard so HR can see at a glance what test the candidate
    # took. Defaults to True for legacy rows (pre-feature, all-three-test).
    include_reading: bool = True
    include_writing: bool = True
    include_speaking: bool = True
    # Email delivery state. One of:
    #   "pending" — send not yet attempted (legacy rows, or SMTP not configured)
    #   "sent"    — SMTP accepted the message
    #   "failed"  — SMTP send failed; HR action needed (see email_error)
    email_status: str = "pending"
    email_error: Optional[str] = None    # short reason if email_status == "failed"


class PaginatedScoreSummary(BaseModel):
    """Wrapper used by GET /api/admin/hrs/{hr_id}/candidates so an admin
    looking at a busy HR doesn't ship hundreds of rows at once. The
    backend slices via SQL LIMIT/OFFSET; the client renders one page at
    a time and re-requests for prev/next."""
    items: list[ScoreSummary]
    total: int      # total candidates for this HR (across all pages)
    page: int       # 1-indexed page being returned
    page_size: int  # actual page size after server-side cap


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

    # Per-invitation section selection — lets the candidate-detail page
    # render "Not included in this test" instead of a misleading "Not yet
    # submitted" or "AI scoring pending" for sections HR opted out of.
    # Defaults to True for legacy rows (pre-feature, full test).
    include_reading: bool = True
    include_writing: bool = True
    include_speaking: bool = True

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

# ============================================================
# HR CONTENT AUTHORING (Day 2 evening)
#
# These schemas back the new /api/hr/content/* CRUD endpoints in
# routes/hr_content.py. Naming convention: *Out for HR-facing reads
# (which DO include correct_answer for questions, unlike *Public),
# *Create for POST bodies, *Update for PATCH bodies (all fields optional).
# ============================================================


class PassageOut(BaseModel):
    """HR view of a passage. Includes everything a candidate-facing
    PassagePublic would NOT — difficulty, topic, word_count."""
    id: int
    title: str
    body: str
    difficulty: str
    topic: Optional[str] = None
    word_count: int
    disabled_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PassageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    difficulty: Literal["intermediate", "expert"]
    topic: Optional[str] = None


class PassageUpdate(BaseModel):
    """All fields optional — PATCH semantics, send only what you want to change."""
    title: Optional[str] = None
    body: Optional[str] = None
    difficulty: Optional[Literal["intermediate", "expert"]] = None
    topic: Optional[str] = None


class QuestionOut(BaseModel):
    """HR view of a question. Crucially DOES include correct_answer
    (the candidate-facing QuestionPublic does not)."""
    id: int
    question_type: Literal["reading_comp", "grammar", "vocabulary", "fill_blank"]
    difficulty: str
    stem: str
    options: list[str]
    correct_answer: int
    passage_id: Optional[int] = None
    disabled_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QuestionCreate(BaseModel):
    question_type: Literal["reading_comp", "grammar", "vocabulary", "fill_blank"]
    difficulty: Literal["intermediate", "expert"]
    stem: str = Field(min_length=1)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_answer: int = Field(ge=0, le=3)
    # Required only when question_type == "reading_comp" — enforced in the route.
    passage_id: Optional[int] = None


class QuestionUpdate(BaseModel):
    stem: Optional[str] = None
    difficulty: Optional[Literal["intermediate", "expert"]] = None
    options: Optional[list[str]] = None
    correct_answer: Optional[int] = None
    # NOTE: question_type and passage_id are deliberately NOT updatable.
    # Changing them post-creation would invalidate the question's
    # relationship to whichever passage it belongs to and break invitations
    # that already reference it.


class WritingTopicOut(BaseModel):
    """HR view of a writing topic. Includes difficulty + category that
    the candidate-facing WritingTopicPublic doesn't expose."""
    id: int
    prompt_text: str
    difficulty: str
    min_words: int
    max_words: int
    category: Optional[str] = None
    disabled_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WritingTopicCreate(BaseModel):
    prompt_text: str = Field(min_length=1)
    difficulty: Literal["intermediate", "expert"]
    min_words: int = Field(ge=50, le=1000)
    max_words: int = Field(ge=50, le=1000)
    category: Optional[str] = None


class WritingTopicUpdate(BaseModel):
    prompt_text: Optional[str] = None
    difficulty: Optional[Literal["intermediate", "expert"]] = None
    min_words: Optional[int] = None
    max_words: Optional[int] = None
    category: Optional[str] = None


class SpeakingTopicOut(BaseModel):
    """HR view of a speaking topic. Includes difficulty + category."""
    id: int
    prompt_text: str
    difficulty: str
    category: Optional[str] = None
    disabled_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SpeakingTopicCreate(BaseModel):
    prompt_text: str = Field(min_length=1)
    difficulty: Literal["intermediate", "expert"]
    category: Optional[str] = None


class SpeakingTopicUpdate(BaseModel):
    prompt_text: Optional[str] = None
    difficulty: Optional[Literal["intermediate", "expert"]] = None
    category: Optional[str] = None


class BulkImportResult(BaseModel):
    """Response from any /bulk endpoint — how many rows succeeded, plus
    a row-by-row error list for the ones that didn't. Frontend renders
    this as 'Imported X items, Y errors:' followed by the error strings."""
    created: int
    errors: list[str] = []
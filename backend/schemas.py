"""
Pydantic request/response shapes.

These are what FastAPI uses to validate incoming JSON and serialize outgoing JSON.
They're intentionally separate from SQLAlchemy models so we can choose exactly
what fields to expose to the client (e.g., never expose `correct_answer`).
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


# ============================================================
# Shared password-policy helper
# ------------------------------------------------------------
# Any "set a new password" surface (HR/admin change-password,
# admin-creates-user) routes through this single function so the
# rules stay in one place. The frontend's hint message and
# client-side validator mirror these exact rules.
# ============================================================
def _enforce_password_complexity(v: str) -> str:
    """Reject passwords that miss any of the four character classes.

    Rules: at least 1 uppercase, 1 lowercase, 1 digit, 1 non-alphanumeric
    (excluding whitespace, which is rejected by a separate validator
    that runs before this one). Builds the error message dynamically
    so the user sees only the rules they're missing — clearer than a
    blanket "password doesn't meet policy".
    """
    missing: list[str] = []
    if not any(c.isupper() for c in v):
        missing.append("1 uppercase letter")
    if not any(c.islower() for c in v):
        missing.append("1 lowercase letter")
    if not any(c.isdigit() for c in v):
        missing.append("1 number")
    if not any((not c.isalnum()) and (not c.isspace()) for c in v):
        missing.append("1 special character")
    if missing:
        raise PydanticCustomError(
            "password_complexity",
            "Password must contain at least " + ", ".join(missing) + ".",
        )
    return v


# ============================================================
# Organizations — multi-tenancy tenant table.
# See docs/superpowers/specs/2026-05-12-multi-tenancy.md.
# ============================================================
class OrganizationOut(BaseModel):
    """Public shape of an organization. Used in login responses (so the
    frontend knows which org the user belongs to) and in super-facing
    endpoints that list orgs.

    `disabled_at` is intentionally exposed: org-admins of a disabled
    org should see WHY their account can't do anything. The login
    endpoint won't return this body for a disabled org's users (they
    get 401), but super-facing org listings need it."""
    id: int
    name: str
    slug: str
    disabled_at: Optional[datetime] = None

    class Config:
        from_attributes = True


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
    # Role of the logged-in user. Always 'hr' for this response shape
    # (the route enforces it), but exposing it makes the frontend's
    # type discriminator simpler — one Principal type can hold HR,
    # admin, or super shape.
    role: str = "hr"
    # The organization this user belongs to. None only if role='super';
    # for HR it's always set. Frontend can show "Logged in as HR @ Org"
    # in the topbar. Optional on the schema so older /me responses
    # that don't include it stay parseable on rolling deploys.
    organization: Optional[OrganizationOut] = None
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

    @field_validator("new_password")
    @classmethod
    def reject_whitespace(cls, v: str) -> str:
        """Whitespace is not allowed anywhere in the password.

        Rationale: silent-padding bugs are a common source of "I can't
        log in" tickets — a user types 'mypass ' (trailing space) and
        the next time they sign in they type 'mypass' (no space) and
        it fails. Banning whitespace entirely eliminates that whole
        class of issue, plus closes the original '      ' (6 spaces)
        bypass that satisfies min_length but produces a useless
        credential. Validator lives on the schema so every endpoint
        accepting a ChangePasswordRequest is protected without each
        route re-checking."""
        if any(c.isspace() for c in v):
            # PydanticCustomError keeps the user-facing `msg` exactly as
            # given, without Pydantic's automatic "Value error, " prefix
            # that would otherwise surface in the FastAPI 422 response.
            raise PydanticCustomError(
                "password_whitespace",
                "Password cannot contain spaces.",
            )
        return v

    @field_validator("new_password")
    @classmethod
    def enforce_complexity(cls, v: str) -> str:
        """Apply the shared complexity policy. Runs after reject_whitespace
        because Pydantic V2 short-circuits on the first failing validator,
        which keeps "Password cannot contain spaces." as the most specific
        message when both rules fail."""
        return _enforce_password_complexity(v)


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
    role: str  # "super" or "admin" — the route returns 401 for any other role
    # The org this admin belongs to. None when role='super' (supers have
    # no org). Set when role='admin'. Frontend shows org name in the
    # admin-dashboard header.
    organization: Optional[OrganizationOut] = None
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
    table. Lists every row in hr_admins with a count of how many
    invitations they've sent. Admins and supers always have
    candidate_count=0 since they can't create invitations.

    organization_id: the user's org. None for super. Required for
    admin/hr but Optional on the schema so legacy callers can ignore
    it. The frontend's super dashboard reads it to show org names
    alongside each user."""
    id: int
    name: str
    email: EmailStr
    role: str  # "super", "admin", or "hr"
    organization_id: Optional[int] = None
    candidate_count: int  # 0 for admins/supers; total invitations sent for HRs
    created_at: datetime


class UserCreateByAdminRequest(BaseModel):
    """POST /api/admin/users. Admin types the password directly; we pass
    the plaintext to bcrypt server-side. Min password length here mirrors
    the create_hr.py CLI rule for consistency.

    `role` decides whether the new account is a peer admin or an HR.
    Multi-tenancy adds 'super' — only super can create supers; this is
    enforced at the route layer in Step D, not here.

    `organization_id` is the org to place the new user in. Only super
    can target an arbitrary org; admin's own org is filled in by the
    route layer (admins can't create users in other orgs). For role='super'
    the field must be None — enforced at the route layer.
    """
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: Literal["super", "admin", "hr"] = "hr"
    # Optional: omit when admin is creating a user in their own org
    # (route fills it in). Required when super is creating a user in
    # a specific org. None means "global" (only valid for role='super').
    organization_id: Optional[int] = None

    @field_validator("password")
    @classmethod
    def reject_whitespace(cls, v: str) -> str:
        """Same protection as ChangePasswordRequest.new_password —
        whitespace is not allowed anywhere in the password."""
        if any(c.isspace() for c in v):
            # PydanticCustomError keeps the user-facing `msg` exactly as
            # given, without Pydantic's automatic "Value error, " prefix
            # that would otherwise surface in the FastAPI 422 response.
            raise PydanticCustomError(
                "password_whitespace",
                "Password cannot contain spaces.",
            )
        return v

    @field_validator("password")
    @classmethod
    def enforce_complexity(cls, v: str) -> str:
        """Same complexity policy as ChangePasswordRequest.new_password."""
        return _enforce_password_complexity(v)


class UserCreateByAdminResponse(BaseModel):
    """Response after creating an HR or admin. Includes email_status so
    the admin UI can surface SMTP failures the same way candidate-invite
    does, and role so the UI can show the right success copy."""
    id: int
    name: str
    email: EmailStr
    role: Literal["super", "admin", "hr"]
    organization_id: Optional[int] = None
    email_status: str  # "sent" | "failed" | "pending"
    email_error: Optional[str] = None

class UserUpdateByAdminRequest(BaseModel):
    """PATCH /api/admin/users/{user_id}. All fields optional — admin
    edits only what they want to change. Sending `password=null` means
    "leave password alone" (NOT "clear it"). Email is re-validated for
    uniqueness if changed. Role is intentionally NOT editable here —
    promoting/demoting between HR and admin has knock-on effects on
    candidate ownership that v1 doesn't address.
    """
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)

    @field_validator("password")
    @classmethod
    def reject_whitespace(cls, v: Optional[str]) -> Optional[str]:
        """Same protection as UserCreateByAdminRequest.password."""
        if v is None:
            return v
        if not v.strip() or v != v.strip():
            raise ValueError(
                "Password cannot start with, end with, or be only whitespace."
            )
        return v


class UserUpdateByAdminResponse(BaseModel):
    """Response after updating an HR or admin. No email field since
    update doesn't send a new welcome email — admin shares the new
    password manually if they reset it."""
    id: int
    name: str
    email: EmailStr
    role: Literal["super", "admin", "hr"]
    organization_id: Optional[int] = None


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
    # Teams meeting URL — populated when the invite handler successfully
    # created a Teams meeting alongside the invitation. Optional for
    # backward compatibility with the regenerate-code path (which doesn't
    # create a new Teams meeting), and so older clients that don't read
    # this field continue to work. Frontend displays it in the post-invite
    # modal so HR can copy/paste as a backup to the email.
    teams_join_url: Optional[str] = None


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

    # IANA timezone the original invitation was scheduled in. The
    # candidate-detail page uses this to pre-fill the timezone dropdown
    # in the resend-invitation modal so HR doesn't have to repick it
    # for the same candidate. Older rows that pre-date the column
    # default to "UTC" via the model column default.
    display_timezone: str = "UTC"


class ResendEmailRequest(BaseModel):
    """
    Body for POST /api/hr/invite/{id}/resend-email. The HR picks a NEW
    window when resending — the old one has often expired by the time
    they decide to resend, which was why the candidate's URL was dead.
    Same field names + types as InviteCreateRequest's window so the
    server-side _validate_window() helper applies unchanged.

    The token, access_code, and exam URL are NOT regenerated by this
    endpoint — only the window columns + display_timezone change. HR
    has a separate /regenerate-code endpoint if they want a new code.
    """
    valid_from: datetime
    valid_until: datetime
    timezone: str = Field(min_length=1, max_length=64)


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
    # Window expiry — exposed so the frontend can compute "Not Attended"
    # status for unsubmitted invitations whose window has passed.
    expires_at: datetime

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


    # ============================================================
# Super-admin auth (added in Step E)
# ============================================================
class SuperLoginRequest(BaseModel):
    """POST /api/super/login. Mirrors HRLoginRequest and AdminLoginRequest;
    kept as a distinct class so future super-only fields (e.g. 2FA,
    higher-privilege MFA) don't pollute the lower-tier login schemas."""
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
 
 
class SuperLoginResponse(BaseModel):
    """Returned by /api/super/login. Includes JWT pair + must_change_password.
    Always returns role='super' since the route enforces it.
 
    No `organization` field because super has no org (organization_id IS NULL
    by ck_hr_admins_role_org_consistency)."""
    id: int
    name: str
    email: EmailStr
    role: str  # always "super"
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = "bearer"
    expires_in: int | None = None
    must_change_password: bool = False
 
 
class SuperMeResponse(BaseModel):
    """GET /api/super/me. Identity-only response (no tokens minted)."""
    id: int
    name: str
    email: EmailStr
    role: str  # always "super"
    must_change_password: bool = False
 
 
# ============================================================
# Organization management (added in Step E)
# ============================================================
class OrganizationCreateRequest(BaseModel):
    """POST /api/super/organizations.
 
    Only `name` is taken from the client — slug is auto-derived server-side
    (see _derive_unique_slug in routes/super.py). This prevents super from
    creating an org with slug='api', 'login', etc. that would collide with
    URL paths, and keeps slugs predictable across the system.
 
    Name is min 1 char post-trim (validator enforced server-side after
    .strip()), max 150 to match the column."""
    name: str = Field(min_length=1, max_length=150)
 
 
class OrganizationRenameRequest(BaseModel):
    """PATCH /api/super/organizations/{id}.
 
    Renames only the display name. Slug is immutable for the lifetime of
    the org (decision E2) so any downstream system that references the
    slug (e.g. URL paths, external integrations) doesn't break on rename."""
    name: str = Field(min_length=1, max_length=150)
 
 
class OrganizationDetail(BaseModel):
    """GET /api/super/organizations/{id}. Org row plus light usage stats.
 
    Counts:
      admin_count                  — non-soft-deleted users with role='admin'
      hr_count                     — non-soft-deleted users with role='hr'
      invitation_count             — every invitation ever for this org
      submitted_invitation_count   — subset with submitted_at NOT NULL
 
    Why expose these here instead of separate endpoints? Super's dashboard
    needs them on the org-detail page anyway, and computing four scalar
    counts in one round-trip is far cheaper than four endpoint calls."""
    id: int
    name: str
    slug: str
    disabled_at: Optional[datetime] = None
    created_at: datetime
    admin_count: int
    hr_count: int
    invitation_count: int
    submitted_invitation_count: int
 
    class Config:
        from_attributes = True
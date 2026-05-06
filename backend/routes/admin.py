"""
Admin portal routes.

The admin portal is for managing HR accounts. Admins do NOT have access
to the candidate dashboard, content authoring, or any HR-facing endpoint
— see docs/superpowers/specs/2026-05-04-admin-portal-design.md for the
strict-separation rationale.

Authenticated routes use one of two deps:
  - `Depends(require_admin)` — allow-list (/me, /change-password). Lets
    a user with must_change_password=True through.
  - `Depends(require_admin_strict)` — everything else (HR list/create).
    403s with code='must_change_password' when the flag is set.

/login, /forgot-password, /refresh, /logout, and /session-status are
anonymous. The session cookie is the same one HR uses (key:
`hr_admin_id`); role enforcement happens inside the dependency.
"""
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from auth import (
    hash_password,
    require_admin,           # allow-list: /me, /change-password
    require_admin_strict,    # everything else — blocks must_change_password=True
    verify_password,
)
from database import get_db
from email_service import send_temp_password_email
from models import HRAdmin
from password_reset import (
    FORGOT_PASSWORD_GENERIC_RESPONSE,
    is_recently_reset,
    sleep_to_latency_floor,
    generate_temp_password,
)
from schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminRefreshTokenRequest,
    AdminRefreshTokenResponse,
    AdminUserSummary,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    HRCreateByAdminRequest,
    HRCreateByAdminResponse,
    PaginatedScoreSummary,
    ScoreSummary,
)

from jwt_service import (
    create_token_pair,
    create_access_token,
    decode_token,
    InvalidTokenError,
)

log = logging.getLogger("admin.forgot_password")


# Read APP_BASE_URL at module load — same pattern as routes/hr.py to avoid
# the lazy `from main import APP_BASE_URL` inside the handler, which works
# today but would deadlock if anyone ever moves it to module scope (admin
# is imported by main, and a top-level back-import would cycle). The
# production guard in main.py still enforces that this env var is set in
# IS_PRODUCTION mode.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")


router = APIRouter(prefix="/api/admin", tags=["admin"])


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
@router.post("/login", response_model=AdminLoginResponse)
def login(payload: AdminLoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Validate admin email + password, set the session cookie, return the
    admin's profile + JWT tokens. Same generic 401 message for every
    failure mode ("no such user", "wrong password", "account exists but
    is HR not admin") — don't leak which one failed.

    Both auth mechanisms are issued on success: session cookie (existing)
    and JWT access+refresh tokens (new). Frontend uses JWT going forward.
    """
    user = db.query(HRAdmin).filter(HRAdmin.email == payload.email.lower()).first()
    if (
        not user
        or user.role != "admin"
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    request.session["hr_admin_id"] = user.id
    # Pin the session to the admin's current password_changed_at — same
    # pattern as HR login, enables session invalidation on rotation.
    request.session["pw_v"] = user.password_changed_at.isoformat()

    # Mint JWT tokens with role="admin" so require_jwt_admin accepts them
    # but require_jwt_hr rejects them (cross-role token misuse defense).
    tokens = create_token_pair(user_id=user.id, role="admin")

    return AdminLoginResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        must_change_password=user.must_change_password,
    )


@router.post("/logout")
def logout(request: Request):
    """Clear the session. Idempotent — same key as HR logout uses."""
    request.session.pop("hr_admin_id", None)
    return {"status": "logged_out"}


@router.post("/refresh", response_model=AdminRefreshTokenResponse)
def refresh_access_token(payload: AdminRefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Trade a valid admin refresh token for a new admin access token.
    Mirror of /api/hr/refresh but enforces role="admin" — an HR refresh
    token cannot be used here, even if it's valid otherwise.
    """
    GENERIC_401 = "Invalid or expired refresh token. Please log in again."

    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail=GENERIC_401)

    try:
        user_id = int(decoded.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail=GENERIC_401)

    user = db.query(HRAdmin).filter(HRAdmin.id == user_id).first()
    if not user or user.role != "admin" or decoded.get("role") != "admin":
        raise HTTPException(status_code=401, detail=GENERIC_401)

    new_access = create_access_token(user_id=user.id, role="admin")
    return AdminRefreshTokenResponse(
        access_token=new_access,
        expires_in=int(os.getenv("JWT_ACCESS_MINUTES", "30")) * 60,
    )


@router.get("/me", response_model=AdminLoginResponse)
def me(admin: HRAdmin = Depends(require_admin)):
    """Returns the currently logged-in admin. Frontend uses this to
    confirm the admin session is alive on page load AND to refresh
    must_change_password (e.g. after a forced-change reset triggered
    from another tab)."""
    return AdminLoginResponse(
        id=admin.id,
        name=admin.name,
        email=admin.email,
        role=admin.role,
        must_change_password=admin.must_change_password,
    )


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    admin: HRAdmin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Change the logged-in admin's password. Mirrors POST /api/hr/change-
    password (same schema, same bcrypt re-hash, same session-preservation
    semantics) but gated on require_admin so HR sessions can't reach it.

    Admins start with a CLI-set password (create_admin.py); rotating it
    in-product avoids the "first admin gets stuck with whatever the
    deploy script gave them" trap.

    Bumps password_changed_at to invalidate any other live admin
    sessions for this account, then re-pins the current request's
    session so the active tab keeps working.
    """
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Current password is incorrect.",
        )

    admin.password_hash = hash_password(payload.new_password)
    admin.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # Clear the temp-password flag set by /api/admin/forgot-password.
    # After this the route guard / strict-auth dep stop locking the
    # admin to /change-password-required.
    admin.must_change_password = False
    db.commit()
    db.refresh(admin)
    request.session["pw_v"] = admin.password_changed_at.isoformat()
    return {"status": "password_changed"}


@router.post("/forgot-password")
def admin_forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Anonymous endpoint. Mirror of /api/hr/forgot-password but only
    rotates accounts whose role == 'admin'. ALWAYS returns 200 with the
    same generic message regardless of:
      - whether the email exists
      - whether the email belongs to an HR (HR resets via /api/hr/...)
      - whether SMTP succeeded
      - whether the cooldown is active

    Why: prevents enumeration of valid admin emails. The cooldown +
    latency floor close two abuse vectors:
      - Spam-a-victim (1 reset per email per minute)
      - Timing oracle (every response takes ~1.2s minimum)

    Cross-role isolation: an HR email submitted here goes down the
    same constant-time fake-hash path as an unknown email. The
    rate-limit dict is shared with the HR endpoint via password_reset
    so an attacker can't bypass the cooldown by alternating endpoints.

    Atomicity: the password_hash is only updated AFTER the email send
    succeeds. If SMTP fails the user keeps their existing password.

    Successful resets bump password_changed_at (invalidates other live
    sessions) AND set must_change_password=True (locks the UI to
    /change-password-required until a permanent password is set).
    """
    started_at = time.monotonic()
    email_lower = payload.email.lower()

    if is_recently_reset(email_lower):
        sleep_to_latency_floor(started_at)
        return FORGOT_PASSWORD_GENERIC_RESPONSE

    user = db.query(HRAdmin).filter(HRAdmin.email == email_lower).first()

    if user is None or user.role != "admin":
        # Same constant-time padding path the HR endpoint uses — without
        # this, the bcrypt latency on the real-admin branch would be a
        # secondary timing oracle on top of the latency floor.
        hash_password(generate_temp_password())
        sleep_to_latency_floor(started_at)
        return FORGOT_PASSWORD_GENERIC_RESPONSE

    temp_password = generate_temp_password()
    email_ok, _email_err = send_temp_password_email(
        hr_email=user.email,
        hr_name=user.name,
        login_url=f"{APP_BASE_URL}/login",
        temp_password=temp_password,
    )
    if not email_ok:
        sleep_to_latency_floor(started_at)
        return FORGOT_PASSWORD_GENERIC_RESPONSE

    # Email sent — commit the rotation. Wrapped in try/except for the
    # same reason as routes/hr.py: if the commit fails AFTER the email
    # went out, the user is holding new credentials that don't work.
    # Log loudly; the user just gets the generic response and can retry
    # after the cooldown.
    user.password_hash = hash_password(temp_password)
    user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    user.must_change_password = True
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.exception(
            "[admin-forgot-password] DB commit failed AFTER temp-password email "
            "was sent for admin_id=%s — user may be locked out, ops should "
            "investigate.", user.id
        )
    sleep_to_latency_floor(started_at)
    return FORGOT_PASSWORD_GENERIC_RESPONSE


@router.get("/session-status")
def session_status(request: Request, db: Session = Depends(get_db)):
    """
    Silent admin-session probe — same shape and rationale as
    /api/hr/session-status (always 200, no console noise on logged-out).
    Returns `logged_in: false` for any non-admin situation, including the
    case where the cookie maps to an HR account.
    """
    user_id = request.session.get("hr_admin_id")
    if not user_id:
        return {"logged_in": False, "user": None}

    user = db.query(HRAdmin).filter(HRAdmin.id == user_id).first()
    if not user:
        request.session.clear()
        return {"logged_in": False, "user": None}

    if user.role != "admin":
        # HR session — from the admin portal's perspective, logged-out.
        # Don't clear the cookie; the HR portal needs it.
        return {"logged_in": False, "user": None}

    return {
        "logged_in": True,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            # Same rationale as the HR session-status — frontend
            # AuthService reads this on app boot to populate its
            # mustChangePassword signal, so a refresh during the
            # forced-change flow doesn't silently bypass the guard.
            "must_change_password": user.must_change_password,
        },
    }


# ------------------------------------------------------------------
# User management
# ------------------------------------------------------------------
@router.get("/users", response_model=list[AdminUserSummary])
def list_users(_admin: HRAdmin = Depends(require_admin_strict), db: Session = Depends(get_db)):
    """
    Every row in hr_admins (both 'hr' and 'admin' roles), newest first,
    each annotated with how many invitations they've sent. Admins
    always have count=0 (they can't create invitations); HRs get an
    accurate aggregate via a single LEFT JOIN with a COUNT subquery —
    no per-row N+1 lookups.
    """
    # Aggregate invitation counts per HR in a single grouped subquery.
    # Imported lazily to avoid pulling Invitation/SQLAlchemy func into
    # the module top-level just for this one endpoint.
    from sqlalchemy import func
    from models import Invitation
    invite_counts = (
        db.query(
            Invitation.hr_admin_id.label("hr_admin_id"),
            func.count(Invitation.id).label("count"),
        )
        .group_by(Invitation.hr_admin_id)
        .subquery()
    )

    rows = (
        db.query(HRAdmin, invite_counts.c.count)
        .outerjoin(invite_counts, HRAdmin.id == invite_counts.c.hr_admin_id)
        .order_by(HRAdmin.created_at.desc())
        .all()
    )
    return [
        AdminUserSummary(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            # COALESCE the NULL from the LEFT JOIN to 0 — happens for
            # every admin (no invitations) and for HRs who haven't sent
            # any yet.
            candidate_count=count or 0,
            created_at=user.created_at,
        )
        for user, count in rows
    ]


@router.get("/hrs/{hr_id}/candidates", response_model=PaginatedScoreSummary)
def list_hr_candidates(
    hr_id: int,
    page: int = 1,
    page_size: int = 25,
    _admin: HRAdmin = Depends(require_admin_strict),
    db: Session = Depends(get_db),
):
    """
    Paginated candidate-results for the given HR. Mirrors /api/hr/results
    but accepts an hr_id (admin can see ANY HR's candidates) and slices
    server-side via SQL LIMIT/OFFSET so a busy HR doesn't ship hundreds
    of rows in one request.

    Returns 404 if hr_id doesn't match any user OR matches an admin.
    Admins don't have candidates, and treating them as "not found" keeps
    the URL space tidy — same pattern the candidate-detail endpoint
    uses for HR vs admin sessions.

    page is 1-indexed. page_size is capped server-side at 100 to defend
    against a misbehaving client requesting half a million rows.
    """
    # Lazy imports: Invitation pulled in only here, mirrors list_users.
    from models import Invitation

    # Cap the page_size BEFORE doing any DB work — a tiny defense
    # against accidental DDoS via large page_size values.
    page_size = max(1, min(page_size, 100))
    page = max(1, page)

    target = db.query(HRAdmin).filter(HRAdmin.id == hr_id).first()
    if target is None or target.role != "hr":
        # Same generic 404 shape FastAPI uses elsewhere. Doesn't
        # distinguish "no such id" from "id belongs to admin" — neither
        # case is actionable from the admin's perspective.
        raise HTTPException(status_code=404, detail="HR not found.")

    # COUNT(*) for the total — matches the WHERE clause of the slice
    # query so the pagination math the frontend does (ceil(total/page_size))
    # is accurate.
    total = (
        db.query(Invitation)
        .filter(Invitation.hr_admin_id == hr_id)
        .count()
    )

    invitations = (
        db.query(Invitation)
        .filter(Invitation.hr_admin_id == hr_id)
        .order_by(Invitation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for inv in invitations:
        s = inv.score  # None if Day-2 scoring hasn't filled it in yet
        items.append(
            ScoreSummary(
                invitation_id=inv.id,
                candidate_name=inv.candidate_name,
                candidate_email=inv.candidate_email,
                difficulty=inv.difficulty,
                submitted_at=inv.submitted_at,
                reading_score=s.reading_score if s else None,
                writing_score=s.writing_score if s else None,
                speaking_score=s.speaking_score if s else None,
                total_score=s.total_score if s else None,
                rating=s.rating if s else None,
                include_reading=inv.include_reading,
                include_writing=inv.include_writing,
                include_speaking=inv.include_speaking,
                email_status=inv.email_status,
                email_error=inv.email_error if inv.email_status == "failed" else None,
            )
        )

    return PaginatedScoreSummary(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/hrs", response_model=HRCreateByAdminResponse, status_code=201)
def create_hr(
    payload: HRCreateByAdminRequest,
    _admin: HRAdmin = Depends(require_admin_strict),
    db: Session = Depends(get_db),
):
    """
    Create an HR account. Admin-typed password (hashed server-side via
    bcrypt). After insert, send a welcome email containing the login URL
    + email + plaintext password — best-effort: the row is committed
    even if SMTP fails so the admin can share credentials manually.

    Refuses to create an HR with an email that's already in use, even if
    the existing row is an admin (would silently demote that admin
    otherwise — same guard the create_hr.py CLI has).
    """
    email = payload.email.lower()
    name = payload.name.strip()

    existing = db.query(HRAdmin).filter(HRAdmin.email == email).first()
    if existing:
        if existing.role == "admin":
            raise HTTPException(
                status_code=409,
                detail=(
                    "That email is already in use by an admin account. "
                    "Pick a different email or remove the admin first."
                ),
            )
        raise HTTPException(
            status_code=409,
            detail="An HR account with that email already exists.",
        )

    hr = HRAdmin(
        name=name,
        email=email,
        password_hash=hash_password(payload.password),
        role="hr",
    )
    db.add(hr)
    db.commit()
    db.refresh(hr)

    # Send welcome email (best-effort — same pattern as candidate invitations).
    # Lazy import of email_service only (SMTP libs are big); APP_BASE_URL
    # comes from the module-level read above.
    from email_service import send_hr_welcome_email

    email_ok, email_err = send_hr_welcome_email(
        hr_email=hr.email,
        hr_name=hr.name,
        login_url=f"{APP_BASE_URL}/login",
        plaintext_password=payload.password,
    )

    return HRCreateByAdminResponse(
        id=hr.id,
        name=hr.name,
        email=hr.email,
        email_status="sent" if email_ok else "failed",
        email_error=email_err,
    )

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
    require_admin,           # allow-list: /me, /change-password (legacy wrapper, kept for backward compat)
    require_admin_strict,    # everything else — blocks must_change_password=True (legacy wrapper)
    require_principal,       # NEW: unified principal-returning dep used on tenant-scoped routes
    Principal,               # NEW: typed (user, role, organization_id) bundle
    verify_password,
)
from database import get_db
from email_service import send_temp_password_email
from models import HRAdmin, Organization
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
    OrganizationOut,         # NEW: included on AdminLoginResponse so frontend knows admin's org
    UserCreateByAdminRequest,
    UserCreateByAdminResponse,
    UserUpdateByAdminRequest,
    UserUpdateByAdminResponse,
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


# ============================================================
# Multi-tenancy helpers (Step D)
# ============================================================

def _pw_changed_at_iso(user: HRAdmin):
    """Return user.password_changed_at as an ISO-8601 string, or None.
    Embedded in JWTs so token replay after password rotation is rejected.
    Mirrors the cookie session's pw_v field."""
    return user.password_changed_at.isoformat() if user.password_changed_at else None


def _count_active_admins_in_org(db: Session, organization_id: int) -> int:
    """
    How many non-soft-deleted admins exist in this org right now?

    Used by the destructive-action guard "every org must have at least one
    admin." Called BEFORE soft-deleting an admin to refuse the operation
    when it would drop the count to zero.

    Returns 0 for orgs that have only HRs (or are empty) — those orgs are
    in a broken state from the multi-tenancy invariant's perspective but
    not something this helper tries to recover from; it just reports.
    """
    from sqlalchemy import func
    return (
        db.query(func.count(HRAdmin.id))
        .filter(
            HRAdmin.organization_id == organization_id,
            HRAdmin.role == "admin",
            HRAdmin.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )


def _principal_can_access_user(target: HRAdmin, p: Principal) -> bool:
    """
    Returns True if the principal `p` is allowed to view/edit/delete the
    user `target`. Centralized so list_users, update_user, delete_user
    all agree.

    Rules:
      super → can access anyone (including other supers, admins in any
              org, HRs in any org). The cross-org god mode of super.
      admin → can access users in their own org only. Cross-org → False.
              Super-role targets → False (admin cannot touch super).
      hr    → not allowed to call admin endpoints in the first place,
              but we still answer False if somehow asked.

    Returning False = generic 404 at the caller, matching the
    "don't leak existence" pattern used elsewhere.
    """
    if p.role == "super":
        return True

    if p.role == "admin":
        # Admin cannot touch a super (super is above admin in the hierarchy).
        if target.role == "super":
            return False
        # Cross-org targets are invisible. Same org → allowed.
        return target.organization_id == p.organization_id

    return False


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
    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.email == payload.email.lower(), HRAdmin.deleted_at.is_(None))
        .first()
    )
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

    # Mint JWT tokens. Embed pw_changed_at_iso so token replay after
    # password rotation is rejected (mirror of cookie pw_v). Role stays
    # 'admin' — super uses its own /api/super/login endpoint and would
    # be rejected by the role check above anyway.
    tokens = create_token_pair(
        user_id=user.id,
        role="admin",
        pw_changed_at_iso=_pw_changed_at_iso(user),
    )

    # Multi-tenancy: include the admin's organization on the response so
    # the frontend can render "Logged in as admin @ {Org Name}" without
    # a follow-up request. Admin always has a non-NULL org (CHECK
    # constraint); serialized defensively in case of unexpected NULL.
    org_out = None
    if user.organization is not None:
        org_out = OrganizationOut(
            id=user.organization.id,
            name=user.organization.name,
            slug=user.organization.slug,
            disabled_at=user.organization.disabled_at,
        )

    return AdminLoginResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        organization=org_out,
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

    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == user_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if not user or user.role != "admin" or decoded.get("role") != "admin":
        raise HTTPException(status_code=401, detail=GENERIC_401)

    new_access = create_access_token(
        user_id=user.id,
        role="admin",
        pw_changed_at_iso=_pw_changed_at_iso(user),
    )
    return AdminRefreshTokenResponse(
        access_token=new_access,
        expires_in=int(os.getenv("JWT_ACCESS_MINUTES", "30")) * 60,
    )


@router.get("/me", response_model=AdminLoginResponse)
def me(admin: HRAdmin = Depends(require_admin)):
    """Returns the currently logged-in admin. Frontend uses this to
    confirm the admin session is alive on page load AND to refresh
    must_change_password (e.g. after a forced-change reset triggered
    from another tab).

    Multi-tenancy: includes the admin's organization so the topbar can
    render org context without a follow-up call."""
    org_out = None
    if admin.organization is not None:
        org_out = OrganizationOut(
            id=admin.organization.id,
            name=admin.organization.name,
            slug=admin.organization.slug,
            disabled_at=admin.organization.disabled_at,
        )
    return AdminLoginResponse(
        id=admin.id,
        name=admin.name,
        email=admin.email,
        role=admin.role,
        organization=org_out,
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

    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.email == email_lower, HRAdmin.deleted_at.is_(None))
        .first()
    )

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

    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == user_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
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
def list_users(
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
    organization_id: int | None = None,
):
    """
    Multi-tenancy: returns users scoped to the caller.

      super → all users across all orgs (every admin + HR in every org,
              PLUS other super accounts). Can optionally narrow to a single
              org via the ?organization_id=N query param.
      admin → users in their own org only (admin + HR rows). Super
              accounts are never returned to admin callers (admin can't
              touch super). The ?organization_id= param is ignored for
              admin — they can't see other orgs anyway.

    Each row is annotated with how many invitations the user has sent.
    Admin/super rows always have count=0; HRs get an accurate aggregate
    via a single LEFT JOIN with a COUNT subquery — no per-row N+1 lookups.

    Ordering: super first, then admins, then HRs. Within each group,
    newest-first by created_at. Grouping high-privilege accounts first
    keeps the admin section of the table easy to spot at a glance.
    """
    # Aggregate invitation counts per HR in a single grouped subquery.
    # Imported lazily to avoid pulling Invitation/SQLAlchemy func into
    # the module top-level just for this one endpoint.
    from sqlalchemy import case, func
    from models import Invitation
    invite_counts = (
        db.query(
            Invitation.hr_admin_id.label("hr_admin_id"),
            func.count(Invitation.id).label("count"),
        )
        .group_by(Invitation.hr_admin_id)
        .subquery()
    )

    # super=0, admin=1, hr=2 — ascending sort puts super at the top, then
    # admins, then HRs. created_at descending is the secondary key so
    # within each group the newest user is at the top.
    role_rank = case(
        (HRAdmin.role == "super", 0),
        (HRAdmin.role == "admin", 1),
        else_=2,
    )

    q = (
        db.query(HRAdmin, invite_counts.c.count)
        .outerjoin(invite_counts, HRAdmin.id == invite_counts.c.hr_admin_id)
        .filter(HRAdmin.deleted_at.is_(None))
    )

    # Tenancy scoping. Admin sees only their own org and only non-super
    # rows. Super sees everything, optionally filtered to one org.
    if p.role == "admin":
        q = q.filter(
            HRAdmin.organization_id == p.organization_id,
            # Super-role users are invisible to admin even if a future
            # multi-super-per-org world ever happens. Today supers have
            # organization_id IS NULL so they'd already be excluded by
            # the org filter above, but the explicit role check makes
            # the intent obvious and survives future schema changes.
            HRAdmin.role != "super",
        )
    elif p.role == "super" and organization_id is not None:
        # Super opted into filtering by a specific org.
        q = q.filter(HRAdmin.organization_id == organization_id)

    rows = q.order_by(role_rank, HRAdmin.created_at.desc()).all()

    return [
        AdminUserSummary(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            organization_id=user.organization_id,  # None for super
            # COALESCE the NULL from the LEFT JOIN to 0 — happens for
            # every admin/super (no invitations) and for HRs who haven't
            # sent any yet.
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
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Paginated candidate-results for the given HR. Mirrors /api/hr/results
    but accepts an hr_id so admin/super can see any HR's candidates.

    Tenancy:
      super → can fetch any HR's candidates across any org.
      admin → can only fetch HRs that belong to admin's own org. Cross-org
              hr_id returns 404, same as a missing HR — don't leak existence.

    Returns 404 if hr_id doesn't match any user OR matches an admin/super.
    Admins/supers don't have candidates, and treating them as "not found"
    keeps the URL space tidy.

    page is 1-indexed. page_size is capped server-side at 100 to defend
    against a misbehaving client requesting half a million rows.
    """
    # Lazy imports: Invitation pulled in only here, mirrors list_users.
    from models import Invitation

    # Cap the page_size BEFORE doing any DB work — a tiny defense
    # against accidental DDoS via large page_size values.
    page_size = max(1, min(page_size, 100))
    page = max(1, page)

    target = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == hr_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if target is None or target.role != "hr":
        raise HTTPException(status_code=404, detail="HR not found.")

    # Cross-org check for admin callers. Super skips this — they see all.
    if p.role == "admin" and target.organization_id != p.organization_id:
        # Same 404 as missing — don't leak which orgs have which HRs.
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
                expires_at=inv.expires_at,
            )
        )

    return PaginatedScoreSummary(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=UserCreateByAdminResponse, status_code=201)
def create_user(
    payload: UserCreateByAdminRequest,
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Create an HR, admin, or super account. Admin-typed password (hashed
    server-side via bcrypt). After insert, send a welcome email
    containing the login URL + email + plaintext password — best-effort:
    the row is committed even if SMTP fails so the admin can share
    credentials manually.

    Refuses to create a user whose email is already in use, regardless of
    the existing row's role. Silent role-flips would be a security
    surprise (a freshly-created "HR" that's actually an admin would
    inherit admin privileges).

    Multi-tenancy rules (Step D):
      D8. Admin can only create users in their own org. payload.role is
          restricted to 'admin' or 'hr'. payload.organization_id is
          IGNORED if sent — caller's own org is always used.
      D9. Super can create users in any org, including new supers.
          - For role='super': organization_id MUST be None (CHECK
            constraint enforces this at the DB; we validate it here for
            a better error message).
          - For role='admin' or 'hr': organization_id MUST be a valid,
            non-deleted org id. Required field (no default to caller's
            org because super has no org).
    """
    email = payload.email.lower()
    name = payload.name.strip()

    # Tenancy: validate and normalize organization_id based on caller's role.
    target_role = payload.role
    if p.role == "admin":
        # Admin creating non-admin/non-hr role → refuse. Admins cannot
        # mint supers.
        if target_role not in ("admin", "hr"):
            raise HTTPException(
                status_code=403,
                detail="Admins can only create 'admin' or 'hr' accounts.",
            )
        # Admin's organization_id is the only valid target; we override
        # whatever the payload requested rather than erroring, since the
        # frontend doesn't send this field for admin callers.
        target_org_id = p.organization_id
    else:  # super
        if target_role == "super":
            # Super accounts have no org. Reject any attempt to set one.
            if payload.organization_id is not None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "role='super' accounts must have organization_id=null "
                        "(CHECK ck_hr_admins_role_org_consistency)."
                    ),
                )
            target_org_id = None
        else:
            # role in ('admin', 'hr'): super must specify which org.
            if payload.organization_id is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "organization_id is required when creating an "
                        "'admin' or 'hr' account."
                    ),
                )
            # Validate the org exists and isn't soft-deleted.
            org = (
                db.query(Organization)
                .filter(
                    Organization.id == payload.organization_id,
                    Organization.deleted_at.is_(None),
                )
                .first()
            )
            if org is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Organization id={payload.organization_id} not found.",
                )
            target_org_id = org.id

    # Soft-deleted accounts don't count as collisions — the partial unique
    # index on email already permits reuse, and re-creating an account
    # under an old email is one of the recovery paths that motivated soft
    # delete in the first place.
    existing = (
        db.query(HRAdmin)
        .filter(HRAdmin.email == email, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if existing:
        # Role-aware error so the caller knows whether to pick a different
        # email or remove the existing account first. Note: "in your org"
        # vs "globally" is irrelevant here — email is globally unique.
        existing_label = {
            "super": "super",
            "admin": "admin",
            "hr": "HR",
        }.get(existing.role, existing.role)
        raise HTTPException(
            status_code=409,
            detail=f"That email is already in use by a {existing_label} account.",
        )

    user = HRAdmin(
        name=name,
        email=email,
        password_hash=hash_password(payload.password),
        role=target_role,
        organization_id=target_org_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Send welcome email (best-effort — same pattern as candidate invitations).
    # Lazy import of email_service only (SMTP libs are big); APP_BASE_URL
    # comes from the module-level read above.
    from email_service import send_user_welcome_email

    email_ok, email_err = send_user_welcome_email(
        user_email=user.email,
        user_name=user.name,
        role=user.role,
        login_url=f"{APP_BASE_URL}/login",
        plaintext_password=payload.password,
    )

    return UserCreateByAdminResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        organization_id=user.organization_id,
        email_status="sent" if email_ok else "failed",
        email_error=email_err,
    )

@router.patch("/users/{user_id}", response_model=UserUpdateByAdminResponse)
def update_user(
    user_id: int,
    payload: UserUpdateByAdminRequest,
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Update an account's name, email, or password. Multi-role: works on
    HR, admin, and (for super callers) super accounts. Role changes
    are NOT supported in v1 (decision D6) — admins/HRs/supers stay in
    their original role for the lifetime of the account.

    Partial update — only fields the caller changes get applied. Sending
    `null` for a field means "don't touch it" (NOT "clear it").

    Tenancy (Step D):
      super → can update any user, including other supers.
      admin → can update users in their OWN ORG only. Cross-org → 404.
              Super-role targets → 404 (admin can't touch super).

    Constraints:
      - 404 if user_id doesn't exist or is soft-deleted
      - 404 if user is outside caller's tenancy (don't leak existence)
      - 409 if changing email to one that's already in use by another
        active user
      - Password is re-hashed if provided; old hash is replaced

    Does NOT send a welcome email on password reset — admin shares the
    new password manually. Email change does NOT notify the user either —
    they'll see it next time they log in.
    """
    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == user_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Tenancy check. 404 (not 403) so cross-tenant access doesn't leak
    # existence. _principal_can_access_user handles the per-role rules.
    if not _principal_can_access_user(user, p):
        raise HTTPException(status_code=404, detail="User not found.")

    if payload.email is not None:
        new_email = payload.email.lower()
        if new_email != user.email:
            # Email is being changed — check for collisions against other
            # active users. Soft-deleted accounts don't count.
            collision = (
                db.query(HRAdmin)
                .filter(
                    HRAdmin.email == new_email,
                    HRAdmin.deleted_at.is_(None),
                    HRAdmin.id != user.id,
                )
                .first()
            )
            if collision:
                collision_label = {
                    "super": "super",
                    "admin": "admin",
                    "hr": "HR",
                }.get(collision.role, collision.role)
                raise HTTPException(
                    status_code=409,
                    detail=f"That email is already in use by a {collision_label} account.",
                )
            user.email = new_email

    if payload.name is not None:
        user.name = payload.name.strip()

    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        # Bump password_changed_at so any active session for this user is
        # invalidated on its next request. Same defense as the user's own
        # change-password flow — without this, an admin-forced password
        # reset wouldn't kick the affected user's open sessions.
        user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(user)

    return UserUpdateByAdminResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        organization_id=user.organization_id,
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    p: Principal = Depends(require_principal(allow=("super", "admin"), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Soft-delete a user account. Sets `deleted_at = utcnow()` on the
    `hr_admins` row; the row stays in the DB so the user's invitations
    and any candidate results attached to them are preserved for audits.

    Multi-tenancy rules (Step D):
      D3. Org must always have ≥1 active admin. If deleting this user
          would drop the org's admin count to zero, refuse with 422.
          Applies whether the deletion is admin self-delete OR cross-user.
      D4. Admin CAN delete themselves IF rule D3 holds.
      D5. Admin CAN delete peer admins in same org. Cross-org → 404.
      D6. (out of scope) Role changes — not relevant here.

    Authorization:
      super → can delete anyone, including other supers. Rule D3 still
              applies for any non-super target (preserves org invariant).
      admin → can delete users in their own org (any role, including peer
              admins and themselves, subject to D3).

    The user's session immediately becomes invalid: the auth dependencies
    + login query all filter `deleted_at IS NULL`, so any in-flight
    request from a deleted user's JWT/session token gets a 401 on its
    next call.
    """
    target = db.query(HRAdmin).filter(HRAdmin.id == user_id).first()
    if target is None or target.deleted_at is not None:
        # Already-deleted is treated as "not found" — same surface to
        # the client either way, and it makes the endpoint idempotent
        # in the practical sense (a second click doesn't error).
        raise HTTPException(status_code=404, detail="User not found.")

    # Tenancy check. Admin can't see cross-org users or super users; both
    # return 404 (don't leak existence). Super has no restriction.
    if not _principal_can_access_user(target, p):
        raise HTTPException(status_code=404, detail="User not found.")

    # Rule D3: every org must always have at least one active admin.
    # This applies when deleting any admin, including self-delete.
    # Super accounts have organization_id=None, so the rule doesn't apply
    # when deleting a super (no org to keep an invariant on).
    if target.role == "admin" and target.organization_id is not None:
        remaining_admins = (
            _count_active_admins_in_org(db, target.organization_id) - 1
        )
        if remaining_admins < 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Cannot delete the last active admin in this organization. "
                    "Create another admin first, or contact a super-admin to "
                    "assist."
                ),
            )

    # Rule D4: admin self-delete is allowed (subject to D3 above, which
    # already passed if we got here). No special handling needed — just
    # mark deleted_at. The admin's NEXT request will 401 because
    # _resolve_cookie_user / _resolve_jwt_user filter `deleted_at IS NULL`.
    #
    # Note: we DON'T clear the admin's session cookie server-side here.
    # The cookie is invalidated on next request anyway, and clearing it
    # from this handler would require special-case logic that adds risk
    # for marginal benefit (the admin probably closed the tab right after
    # confirming the delete).

    target.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return None
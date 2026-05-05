"""
Admin portal routes.

The admin portal is for managing HR accounts. Admins do NOT have access
to the candidate dashboard, content authoring, or any HR-facing endpoint
— see docs/superpowers/specs/2026-05-04-admin-portal-design.md for the
strict-separation rationale.

Every route except /login is protected by `Depends(require_admin)`. The
session cookie is the same one HR uses (key: `hr_admin_id`); role
enforcement happens inside the dependency.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from auth import hash_password, require_admin, verify_password
from database import get_db
from models import HRAdmin
from schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    ChangePasswordRequest,
    HRCreateByAdminRequest,
    HRCreateByAdminResponse,
    HRSummary,
)


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
    admin's profile. Same generic 401 message for every failure mode
    ("no such user", "wrong password", "account exists but is HR not admin")
    — don't leak which one failed.
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
    return AdminLoginResponse(
        id=user.id, name=user.name, email=user.email, role=user.role
    )


@router.post("/logout")
def logout(request: Request):
    """Clear the session. Idempotent — same key as HR logout uses."""
    request.session.pop("hr_admin_id", None)
    return {"status": "logged_out"}


@router.get("/me", response_model=AdminLoginResponse)
def me(admin: HRAdmin = Depends(require_admin)):
    """Returns the currently logged-in admin. Frontend uses this to
    confirm the admin session is alive on page load."""
    return AdminLoginResponse(
        id=admin.id, name=admin.name, email=admin.email, role=admin.role
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
    from datetime import datetime, timezone
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Current password is incorrect.",
        )

    admin.password_hash = hash_password(payload.new_password)
    admin.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(admin)
    request.session["pw_v"] = admin.password_changed_at.isoformat()
    return {"status": "password_changed"}


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
        },
    }


# ------------------------------------------------------------------
# HR account management
# ------------------------------------------------------------------
@router.get("/hrs", response_model=list[HRSummary])
def list_hrs(_admin: HRAdmin = Depends(require_admin), db: Session = Depends(get_db)):
    """
    All HR accounts, newest first. Excludes admin accounts — admins
    manage HRs, not other admins (admin creation is CLI-only).
    """
    rows = (
        db.query(HRAdmin)
        .filter(HRAdmin.role == "hr")
        .order_by(HRAdmin.created_at.desc())
        .all()
    )
    return [
        HRSummary(id=r.id, name=r.name, email=r.email, created_at=r.created_at)
        for r in rows
    ]


@router.post("/hrs", response_model=HRCreateByAdminResponse, status_code=201)
def create_hr(
    payload: HRCreateByAdminRequest,
    _admin: HRAdmin = Depends(require_admin),
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

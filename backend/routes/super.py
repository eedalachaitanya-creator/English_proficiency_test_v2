"""
Super-admin routes — the Stixis-internal portal for managing organizations.

This is the bootstrap layer of multi-tenancy: super accounts (NO organization_id)
create, rename, disable, enable, and delete tenant organizations. After super
creates a new org, the first per-org admin for it is created via the EXISTING
POST /api/admin/users endpoint (which Step D widens to accept super callers).
No org-bootstrap-with-initial-admin atomicity here — two API calls by design
(see spec decision E3).

What lives in this file:

  Auth (narrow surface for now, by user choice):
    POST /api/super/login        — credentials → session cookie + JWT pair
    POST /api/super/logout       — clear session
    GET  /api/super/me           — current super identity
    NOT in this file (intentional):
      refresh, change-password, forgot-password — to be added later if needed.
      Super accounts are bootstrapped via SQL only and have no temp-password
      flow, so the absence of /forgot-password is not a gap a real super
      would hit in normal operation.

  Organization management (the actual super work):
    GET    /api/super/organizations           — list (with filter flags)
    GET    /api/super/organizations/{id}      — single org + stats
    POST   /api/super/organizations           — create (auto-slug from name)
    PATCH  /api/super/organizations/{id}      — rename
    POST   /api/super/organizations/{id}/disable
    POST   /api/super/organizations/{id}/enable
    DELETE /api/super/organizations/{id}      — soft-delete with safety guard

What lives OUTSIDE this file:

  User management — super uses POST /api/admin/users etc. with an explicit
  organization_id to seed an org's first admin. Those endpoints become
  super-aware in Step D via require_principal(allow=("super","admin")).
  Putting the duplicate under /api/super/users/ would just be code that
  drifts from /api/admin/users over time.

Auth dependency choice:
  Every endpoint here uses require_principal(allow=("super",), strict=True).
  No legacy wrapper — super is a new role with no historical callers, so we
  go straight to the Step A unified dependency.

  strict=True means a super whose must_change_password flag is set CANNOT
  call these endpoints. They'd need a /change-password route to clear the
  flag, which we deferred — so the bootstrap super (must_change_password=
  FALSE in the seed SQL) is fine. If we ever add /forgot-password for super,
  /change-password must come with it (and be marked strict=False on the
  dependency for that single endpoint, mirroring the HR/admin pattern).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    Principal,
    require_principal,
    verify_password,
)
from database import get_db
from jwt_service import create_token_pair
from models import HRAdmin, Invitation, Organization
from schemas import (
    OrganizationOut,
    SuperLoginRequest,
    SuperLoginResponse,
    SuperMeResponse,
    OrganizationCreateRequest,
    OrganizationRenameRequest,
    OrganizationDetail,
)


log = logging.getLogger("super")
router = APIRouter(prefix="/api/super", tags=["super"])


# ============================================================
# Helpers
# ============================================================
def _utcnow_naive() -> datetime:
    """Naive UTC. Matches models.py _utcnow and the rest of the codebase
    so DateTime comparisons work consistently across SQLite (test) and
    Postgres (prod)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _org_to_out(org: Organization) -> OrganizationOut:
    """Serialize an Organization row to its public shape. Used in list +
    detail responses. Single helper keeps the serialization consistent
    if we add a field later."""
    return OrganizationOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        disabled_at=org.disabled_at,
    )


# ------------------------------------------------------------------
# Slug derivation
# ------------------------------------------------------------------
# Pattern: lowercase, replace non-alphanumeric runs with single hyphens,
# strip leading/trailing hyphens. Examples:
#   "Acme Corp Inc."   → "acme-corp-inc"
#   "ACME"             → "acme"
#   "Foo & Bar"        → "foo-bar"
#   "  spaces  "       → "spaces"
#   "123 Industries"   → "123-industries"
#
# Hard cap at 58 chars to leave room for a "-NNN" collision suffix while
# staying under the 60-char column limit on organizations.slug.
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_BASE = 58


def _derive_slug_base(name: str) -> str:
    """
    Lowercase + normalize. Returns the base candidate WITHOUT collision
    handling. Empty result (all non-alphanumeric input) raises 422 — we
    refuse to fabricate a slug from nothing because that would silently
    accept garbage like "🚀🌟" as a valid org name.
    """
    base = name.strip().lower()
    base = _NON_SLUG_CHARS.sub("-", base).strip("-")
    if not base:
        raise HTTPException(
            status_code=422,
            detail=(
                "Organization name must contain at least one alphanumeric "
                "character (letter or number) so we can derive a URL slug."
            ),
        )
    return base[:_SLUG_MAX_BASE]


def _derive_unique_slug(db: Session, name: str) -> str:
    """
    Derive a slug from `name` that doesn't collide with any existing
    organizations.slug (including soft-deleted orgs — their rows still
    hold the slug because organizations.slug is a UNIQUE column).

    Collision handling: append "-2", "-3", etc. until a free slug is
    found. In the (very rare) event that 100+ collisions occur, we
    give up rather than loop forever — that probably indicates a bug
    or a malicious caller pasting the same name 100 times.

    We don't catch IntegrityError on the eventual INSERT because the
    pre-check below makes simultaneous collisions astronomically
    unlikely (the bottleneck for super is a single person clicking
    "create org" in a UI, not a concurrent write storm).
    """
    base = _derive_slug_base(name)
    candidate = base
    suffix = 1
    while True:
        existing = (
            db.query(Organization.id)
            .filter(Organization.slug == candidate)
            .first()
        )
        if existing is None:
            return candidate
        suffix += 1
        if suffix > 100:
            # Defensive fallback. In practice this never triggers.
            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not allocate a unique slug for this organization "
                    "name. Try a different name."
                ),
            )
        candidate = f"{base}-{suffix}"


# ============================================================
# Auth: login
# ============================================================
@router.post("/login", response_model=SuperLoginResponse)
def super_login(
    payload: SuperLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Super login. Mirrors /api/hr/login and /api/admin/login but accepts
    only role='super'.

    Returns the session cookie (set on the response) PLUS a JWT token pair
    in the body, so frontend can pick either transport. Same dual-track
    auth model as HR and admin.

    Error handling: invalid email, deleted user, wrong-role user, and
    wrong password all return the SAME generic 401 "Invalid credentials"
    message. Different messages would let an attacker enumerate super
    emails by probing.
    """
    GENERIC_401 = "Invalid credentials."

    email = payload.email.strip().lower()
    user = (
        db.query(HRAdmin)
        .filter(HRAdmin.email == email, HRAdmin.deleted_at.is_(None))
        .first()
    )

    # Generic 401 for: no user, wrong role, wrong password. All look the
    # same on the wire to prevent role/email enumeration.
    if user is None or user.role != "super":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )

    # Stamp the session cookie. Mirrors the HR/admin login.
    request.session["hr_admin_id"] = user.id
    # Pin the session to the current password_changed_at. If the password
    # is rotated later, every old session is invalidated (auth.py
    # _resolve_cookie_user checks this).
    request.session["pw_v"] = user.password_changed_at.isoformat()

    # Mint JWT tokens too. Embed pw_changed_at_iso so the JWT path also
    # invalidates on password rotation (mirror of the cookie path).
    tokens = create_token_pair(
        user_id=user.id,
        role="super",
        pw_changed_at_iso=(
            user.password_changed_at.isoformat()
            if user.password_changed_at
            else None
        ),
    )

    return SuperLoginResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,  # always "super" by the filter above
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        must_change_password=user.must_change_password,
    )


# ============================================================
# Auth: logout
# ============================================================
@router.post("/logout")
def super_logout(request: Request):
    """
    Clear the session. Idempotent — calling when not logged in succeeds
    silently (200 OK with {"status": "logged_out"}). Frontend can call
    this from "logout" buttons without checking auth state first.

    Note: this clears the cookie session but does NOT invalidate the JWT
    pair that may also be on the client. If JWT-only frontends need
    immediate revocation, they should drop the tokens from their store.
    Server-side blacklisting of JWTs isn't implemented for any role
    here — short access-token lifetime (30 min default) is the mitigation.
    """
    request.session.pop("hr_admin_id", None)
    request.session.pop("pw_v", None)
    return {"status": "logged_out"}


# ============================================================
# Auth: /me
# ============================================================
@router.get("/me", response_model=SuperMeResponse)
def super_me(
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
):
    """
    Returns the currently logged-in super. Frontend calls this on app
    boot to confirm the session is alive and pull the user's name/email
    for the topbar.

    No organization on this response because super has no org (always
    NULL organization_id, enforced by ck_hr_admins_role_org_consistency).
    """
    return SuperMeResponse(
        id=p.user.id,
        name=p.user.name,
        email=p.user.email,
        role=p.role,
        must_change_password=p.user.must_change_password,
    )


# ============================================================
# Organization management
# ============================================================

# ------------------------------------------------------------------
# GET /organizations — list
# ------------------------------------------------------------------
@router.get("/organizations", response_model=List[OrganizationOut])
def list_organizations(
    include_disabled: bool = True,
    include_deleted: bool = False,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    List all organizations.

    Query params:
      include_disabled (default TRUE)  — include orgs whose disabled_at is set.
                                          Super usually wants to see these in
                                          the management UI so they can re-enable.
      include_deleted  (default FALSE) — include soft-deleted orgs. OFF by
                                          default because a deleted org is
                                          almost never something you want to
                                          act on; explicit opt-in to view.

    Ordered by id ascending so id=1 (Stixis) always appears first — gives a
    stable, predictable order in the super UI.
    """
    q = db.query(Organization)
    if not include_deleted:
        q = q.filter(Organization.deleted_at.is_(None))
    if not include_disabled:
        q = q.filter(Organization.disabled_at.is_(None))
    orgs = q.order_by(Organization.id.asc()).all()
    return [_org_to_out(o) for o in orgs]


# ------------------------------------------------------------------
# GET /organizations/{id} — single org with stats
# ------------------------------------------------------------------
@router.get("/organizations/{org_id}", response_model=OrganizationDetail)
def get_organization(
    org_id: int,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Returns the org row plus light usage stats:
      - admin_count: non-soft-deleted admins in this org
      - hr_count:    non-soft-deleted HRs in this org
      - invitation_count: total invitations ever sent for this org
      - submitted_invitation_count: invitations that have a submitted_at

    The counts use aggregate queries (single SQL statement each), not
    Python-side counting, so this endpoint stays cheap even for orgs
    with thousands of invitations.

    404 on soft-deleted orgs too — they're hidden from the regular list
    unless include_deleted=TRUE was passed. If super wants to inspect a
    deleted org, they can list with include_deleted=TRUE to find the id,
    but the detail endpoint pretends it doesn't exist either way for
    consistency.
    """
    org = (
        db.query(Organization)
        .filter(
            Organization.id == org_id,
            Organization.deleted_at.is_(None),
        )
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    # Aggregate counts. Each is a one-row scalar query.
    admin_count = (
        db.query(func.count(HRAdmin.id))
        .filter(
            HRAdmin.organization_id == org_id,
            HRAdmin.role == "admin",
            HRAdmin.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    hr_count = (
        db.query(func.count(HRAdmin.id))
        .filter(
            HRAdmin.organization_id == org_id,
            HRAdmin.role == "hr",
            HRAdmin.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    invitation_count = (
        db.query(func.count(Invitation.id))
        .filter(Invitation.organization_id == org_id)
        .scalar()
        or 0
    )
    submitted_invitation_count = (
        db.query(func.count(Invitation.id))
        .filter(
            Invitation.organization_id == org_id,
            Invitation.submitted_at.isnot(None),
        )
        .scalar()
        or 0
    )

    return OrganizationDetail(
        id=org.id,
        name=org.name,
        slug=org.slug,
        disabled_at=org.disabled_at,
        created_at=org.created_at,
        admin_count=admin_count,
        hr_count=hr_count,
        invitation_count=invitation_count,
        submitted_invitation_count=submitted_invitation_count,
    )


# ------------------------------------------------------------------
# POST /organizations — create
# ------------------------------------------------------------------
@router.post(
    "/organizations",
    response_model=OrganizationOut,
    status_code=201,
)
def create_organization(
    payload: OrganizationCreateRequest,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Create a new organization. Slug is auto-derived from name; super
    cannot override it (decision E2). This keeps slugs predictable and
    prevents shenanigans like creating an org with slug="api" or "login"
    that would collide with URL paths.

    Behavior:
      - Name normalization: strip whitespace, then validate non-empty.
      - Name uniqueness: enforced by the UNIQUE constraint on
        organizations.name. We don't pre-check in Python because the DB
        check is the source of truth and pre-checking would add a
        race window.
      - Slug derivation: see _derive_unique_slug. Collisions get -2, -3, etc.
      - On IntegrityError (duplicate name OR duplicate slug we didn't
        catch), return 409 with a clear message.

    Does NOT create an initial admin (decision E3). Caller follows up with
    POST /api/admin/users to seed the first admin for the new org.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail="Organization name cannot be blank.",
        )

    # Derive slug. Failures raise inside the helper with a clear message.
    slug = _derive_unique_slug(db, name)

    org = Organization(
        name=name,
        slug=slug,
    )
    db.add(org)
    try:
        db.commit()
    except IntegrityError:
        # Most likely: duplicate name (UNIQUE on organizations.name).
        # Slug collisions are pre-checked but a race could in principle
        # produce one; same 409 either way.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "An organization with this name already exists. "
                "Pick a different name."
            ),
        )
    db.refresh(org)

    log.info(
        "[super] org created id=%s name=%r slug=%r by user_id=%s",
        org.id, org.name, org.slug, p.user.id,
    )
    return _org_to_out(org)


# ------------------------------------------------------------------
# PATCH /organizations/{id} — rename
# ------------------------------------------------------------------
@router.patch(
    "/organizations/{org_id}",
    response_model=OrganizationOut,
)
def rename_organization(
    org_id: int,
    payload: OrganizationRenameRequest,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Rename an organization. ONLY the display name changes — slug is
    preserved (decision E2: slugs are immutable, since other systems
    may reference them).

    Refuses to rename soft-deleted orgs. Disabled orgs CAN be renamed
    (you might want to update the display name as part of preparing
    to re-enable).
    """
    org = (
        db.query(Organization)
        .filter(
            Organization.id == org_id,
            Organization.deleted_at.is_(None),
        )
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(
            status_code=422,
            detail="Organization name cannot be blank.",
        )

    if new_name == org.name:
        # No-op — return the row as-is. Idempotent.
        return _org_to_out(org)

    org.name = new_name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another organization already has this name.",
        )
    db.refresh(org)

    log.info(
        "[super] org renamed id=%s new_name=%r by user_id=%s",
        org.id, org.name, p.user.id,
    )
    return _org_to_out(org)


# ------------------------------------------------------------------
# POST /organizations/{id}/disable
# ------------------------------------------------------------------
@router.post(
    "/organizations/{org_id}/disable",
    response_model=OrganizationOut,
)
def disable_organization(
    org_id: int,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Soft-disable an organization. All admins and HRs in this org get 401
    on their next request (the org-disabled check in auth.require_principal
    blocks them). In-flight candidate tests CONTINUE — those are
    session-keyed, not auth-keyed, so a disable mid-test doesn't kick
    the candidate.

    Idempotent: disabling an already-disabled org is a no-op (200 OK,
    same response). Mirroring a "you're already in the state you wanted"
    philosophy — there's no harm in calling this twice.

    Cannot disable a soft-deleted org (404).
    """
    org = (
        db.query(Organization)
        .filter(
            Organization.id == org_id,
            Organization.deleted_at.is_(None),
        )
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    if org.disabled_at is None:
        org.disabled_at = _utcnow_naive()
        db.commit()
        db.refresh(org)
        log.info(
            "[super] org disabled id=%s name=%r by user_id=%s",
            org.id, org.name, p.user.id,
        )
    # else: already disabled — no-op, fall through to return current state

    return _org_to_out(org)


# ------------------------------------------------------------------
# POST /organizations/{id}/enable
# ------------------------------------------------------------------
@router.post(
    "/organizations/{org_id}/enable",
    response_model=OrganizationOut,
)
def enable_organization(
    org_id: int,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Re-enable a previously-disabled organization. Clears disabled_at.

    Idempotent (mirror of /disable). Cannot enable a soft-deleted org
    (404) — soft-delete is more permanent than disable; to restore a
    deleted org, undo the deleted_at directly via SQL or add a
    /restore endpoint later.
    """
    org = (
        db.query(Organization)
        .filter(
            Organization.id == org_id,
            Organization.deleted_at.is_(None),
        )
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    if org.disabled_at is not None:
        org.disabled_at = None
        db.commit()
        db.refresh(org)
        log.info(
            "[super] org enabled id=%s name=%r by user_id=%s",
            org.id, org.name, p.user.id,
        )
    # else: already active — no-op

    return _org_to_out(org)


# ------------------------------------------------------------------
# DELETE /organizations/{id} — soft-delete with active-user guard
# ------------------------------------------------------------------
@router.delete(
    "/organizations/{org_id}",
    status_code=204,
)
def delete_organization(
    org_id: int,
    p: Principal = Depends(require_principal(allow=("super",), strict=True)),
    db: Session = Depends(get_db),
):
    """
    Soft-delete an organization. Sets deleted_at on the row.

    Safety guard: refuses (409) if ANY non-soft-deleted hr_admins still
    have organization_id = this org. Super must explicitly delete each
    user (or transfer them) before deleting the org. This avoids the
    silent foreign-data-loss scenario where deleting an org would
    orphan its users without warning.

    Soft-delete vs hard-delete: hard-delete would cascade through
    invitations, scores, audio recordings, etc. — months of candidate
    data gone. Soft-delete preserves all of it for audit purposes.
    If a hard-delete is ever needed (GDPR, etc.), do it manually with
    explicit SQL after all the cascade implications are understood.

    Cannot delete the same org twice (404 on already-deleted).
    """
    org = (
        db.query(Organization)
        .filter(
            Organization.id == org_id,
            Organization.deleted_at.is_(None),
        )
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    # Active-user guard. Count non-soft-deleted users in this org. We
    # include all roles (admin + hr) because every one of them would
    # become inaccessible after the deletion. Disabled users count —
    # they're still real accounts.
    active_user_count = (
        db.query(func.count(HRAdmin.id))
        .filter(
            HRAdmin.organization_id == org_id,
            HRAdmin.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    if active_user_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete organization '{org.name}': it still has "
                f"{active_user_count} active user(s). Delete or transfer "
                f"all admins and HRs first."
            ),
        )

    org.deleted_at = _utcnow_naive()
    db.commit()

    log.info(
        "[super] org soft-deleted id=%s name=%r by user_id=%s",
        org.id, org.name, p.user.id,
    )
    # 204 — empty response body
    return None
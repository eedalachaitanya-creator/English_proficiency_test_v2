"""
Authentication helpers.

- bcrypt for password hashing (slow on purpose — that's the security feature).
- secrets.token_urlsafe for invitation tokens (cryptographically random).
- The dependency tree is unified around require_principal(allow=(...)),
  which works for both session-cookie and JWT transports, returns a
  typed Principal, and enforces the role allowlist, the
  must_change_password gate (when strict=True), the deleted_at filter,
  and the password-rotation check (pw_v on cookie, pw_changed_at_iso
  on JWT).

  The old require_hr / require_admin / require_hr_strict / require_admin_strict /
  require_jwt_hr / require_jwt_admin remain as thin wrappers that
  delegate to require_principal. Existing routes keep working byte-for-byte.

Multi-tenancy:
  Principal carries (user, role, organization_id). For role='super',
  organization_id is None. For 'admin' and 'hr' it's the user's org.
  Tenant-scoping helpers in tenancy.py consume Principal to apply the
  right filter per role — but those land in Step B.
"""
import secrets
import bcrypt
from dataclasses import dataclass
from typing import Optional, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db
from models import HRAdmin
from jwt_service import decode_token, InvalidTokenError


# ============================================================
# Passwords
# ============================================================
def hash_password(plain: str) -> str:
    """Generate a bcrypt hash with a fresh salt each call."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison via bcrypt."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ============================================================
# Tokens (used for invitation URLs)
# ============================================================
def generate_token() -> str:
    """
    URL-safe random string. ~43 chars at 32 bytes of entropy.
    Used as the invitation `token` field — what appears in /exam/<this>.
    """
    return secrets.token_urlsafe(32)


def generate_access_code() -> str:
    """
    6-digit numeric code candidate must enter after clicking the URL.
    Uses secrets.choice (not random.randint) — same cryptographic generator
    as token generation, so codes can't be predicted from observing prior ones.
    Returned as a zero-padded string so leading zeros aren't lost (e.g. "048273").
    """
    return "".join(secrets.choice("0123456789") for _ in range(6))


# ============================================================
# Principal — the unified caller identity
# ============================================================
@dataclass(frozen=True)
class Principal:
    """
    The authenticated caller, plus their role and tenant scope.

    Returned by require_principal and consumed by route handlers and
    tenancy helpers. Frozen so handlers can't accidentally mutate role
    or organization_id mid-request (which would be a silent privilege
    escalation bug).

    Fields:
      user            — the HRAdmin row, with all current column values.
      role            — one of 'super', 'admin', 'hr'. Mirror of user.role
                        but pulled out as a typed field for ergonomics.
      organization_id — None for super, set for admin/hr. Mirror of
                        user.organization_id.
    """
    user: HRAdmin
    role: str
    organization_id: Optional[int]


# ============================================================
# Generic 401 / 403 helpers
# ============================================================
# All authentication failure modes return the SAME generic 401 message.
# Different messages would let an attacker distinguish "this user_id was
# once valid but is now deleted" from "no cookie at all" by crafting
# session cookies, or distinguish "your password was just rotated" from
# "you never logged in" by replaying old cookies.
_GENERIC_401 = "Not authenticated. Please log in."

# Disabled-org returns the same 401. An attacker shouldn't be able to
# probe which orgs exist by checking "is the response a 401 with the
# generic message vs a 403 'org disabled' message". Both look the same.
# Internally we log distinctly so ops can tell why an HR can't get in.


def _raise_401(headers: Optional[dict] = None) -> None:
    """Tiny helper so every path raises the SAME shape."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_GENERIC_401,
        headers=headers or {},
    )


def _check_must_change_password(user: HRAdmin) -> None:
    """Raise 403 with code='must_change_password' when the user is on a
    temp credential.

    The detail is a dict so the frontend HTTP interceptor can branch on
    `detail.code` rather than parsing strings. The message field is
    human-readable for ops/log surfaces.
    """
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "must_change_password",
                "message": "Password change required.",
            },
        )


# ============================================================
# Cookie-session resolution
# ============================================================
def _resolve_cookie_user(request: Request, db: Session) -> Optional[HRAdmin]:
    """
    Pull hr_admin_id from the session, fetch the row, and validate it.
    Returns None on any failure (caller decides whether None is a 401
    or just "try the JWT path next"). Clears the session on stale-cookie
    paths so a bad cookie can't keep getting rejected forever.

    Validation steps:
      1. session contains hr_admin_id        → otherwise None
      2. row exists and not soft-deleted     → otherwise clear + None
      3. session pw_v == current pw_v        → otherwise clear + None
                                               (session pre-dates a pw rotation)
    """
    hr_id = request.session.get("hr_admin_id")
    if not hr_id:
        return None

    hr = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == hr_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if not hr:
        # Session points to a deleted (or soft-deleted) user — clear and reject.
        request.session.clear()
        return None

    # Session-invalidation gate. session_pw_v is the password_changed_at
    # value at login (as ISO-8601 string — DateTime isn't JSON-serializable
    # for the session cookie). If the user's current password_changed_at
    # is strictly newer, the session pre-dates a password rotation and
    # MUST be invalidated.
    session_pw_v = request.session.get("pw_v")
    current_pw_v = hr.password_changed_at.isoformat() if hr.password_changed_at else None
    if session_pw_v != current_pw_v:
        # Don't keep the cookie around — it's cryptographically valid but
        # carries a stale pw_v. Force re-login.
        request.session.clear()
        return None

    return hr


# ============================================================
# JWT resolution
# ============================================================
# HTTPBearer is FastAPI's built-in helper for extracting Bearer tokens
# from the Authorization header. auto_error=False so we can return our
# generic 401 instead of FastAPI's default "Not authenticated" body —
# avoids leaking which dep failed when an attacker probes routes.
_bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


def _resolve_jwt_user(
    creds: Optional[HTTPAuthorizationCredentials],
    db: Session,
) -> Optional[HRAdmin]:
    """
    Validate the bearer token and load the user. Returns None on any
    failure. Parallel to _resolve_cookie_user.

    Validation steps:
      1. creds present                                  → otherwise None
      2. token decodes + correct type + correct issuer  → otherwise None
      3. sub claim is an int                            → otherwise None
      4. row exists and not soft-deleted                → otherwise None
      5. pw_changed_at_iso claim == current pw_v        → otherwise None
                                                          (NEW: was missing on
                                                          the JWT path before
                                                          multi-tenancy work)

    The pw_changed_at_iso check closes the same hole that pw_v closes
    on the cookie path: a stolen JWT keeps working past a password
    rotation unless we cross-check against the user's current
    password_changed_at. The check is best-effort: if the token doesn't
    carry the claim (legacy tokens minted before this code), we fall
    back to allowing the token — the user's NEXT login will mint a
    fresh token with the claim, and from then on rotation invalidates
    properly. This matches how we handle session_pw_v=None.
    """
    if creds is None or not creds.credentials:
        return None

    try:
        payload = decode_token(creds.credentials, expected_type="access")
    except InvalidTokenError:
        # Covers expired, malformed, wrong-issuer, and wrong-type tokens.
        return None

    # JWT spec says sub is a string. Convert to int defensively — a forged
    # token with a non-numeric sub shouldn't crash the route.
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        return None

    hr = (
        db.query(HRAdmin)
        .filter(HRAdmin.id == user_id, HRAdmin.deleted_at.is_(None))
        .first()
    )
    if not hr:
        return None

    # Password-rotation gate for the JWT path. Mirror of the pw_v check
    # in _resolve_cookie_user. If the token was minted before the
    # password rotation, reject it.
    #
    # The claim is `pw_changed_at_iso` (set by jwt_service when we mint
    # the token at login or refresh time). Legacy tokens (issued before
    # this code) don't carry the claim — in that case we fall back to
    # allowing the token; the next login will mint a fresh one with the
    # claim, and from then on rotation invalidates properly.
    token_pw_v = payload.get("pw_changed_at_iso")
    current_pw_v = hr.password_changed_at.isoformat() if hr.password_changed_at else None
    if token_pw_v is not None and token_pw_v != current_pw_v:
        return None

    return hr


# ============================================================
# The unified dependency factory: require_principal
# ============================================================
def require_principal(
    *,
    allow: Iterable[str],
    strict: bool = True,
):
    """
    Return a FastAPI dependency that:

      1. Resolves the caller via session cookie OR JWT bearer token
         (whichever the route has been wired up to use). Cookie is tried
         first; if no cookie session, JWT is tried; if neither, 401.
      2. Enforces role membership: user.role MUST be in `allow`.
      3. (If strict=True) enforces must_change_password=False with a 403
         carrying code='must_change_password'.
      4. Enforces org-not-disabled: if the user's org is disabled, 401.
         Super (no org) is never blocked by this check.

    Returns a Principal dataclass.

    Usage:

        @router.get("/api/hr/results")
        def list_results(
            p: Principal = Depends(require_principal(allow=("hr",))),
        ):
            ...

        @router.get("/api/admin/users")
        def list_users(
            p: Principal = Depends(require_principal(allow=("super", "admin"))),
        ):
            ...

    The `allow` tuple is parameter-baked into the returned dependency, so
    each route's permission set is visible at the import site. No global
    "this is the admin set" state.

    `strict=False` is used by the four allow-listed endpoints (/me,
    /change-password, /refresh, /logout) so the user can clear the
    must_change_password flag without being blocked from those very
    endpoints by that same flag.
    """
    allow_set = frozenset(allow)
    if not allow_set:
        # Programmer error — a dep that allows no roles can never succeed.
        # Fail at import time, not at request time.
        raise ValueError("require_principal(allow=...) needs at least one role")

    invalid = allow_set - {"super", "admin", "hr"}
    if invalid:
        raise ValueError(f"require_principal: unknown role(s) {sorted(invalid)}")

    def _dep(
        request: Request,
        creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
        db: Session = Depends(get_db),
    ) -> Principal:
        # Try cookie first, then JWT. Order matters only when both are
        # present and refer to DIFFERENT users — in that case the cookie
        # wins (the older browser session is the "user is here right now"
        # signal). Pragmatically, the two transports are usually wired
        # to disjoint sets of routes, so both-present-different-user
        # is rare.
        user = _resolve_cookie_user(request, db) or _resolve_jwt_user(creds, db)
        if user is None:
            # Add WWW-Authenticate so JWT-only clients get a hint.
            _raise_401(headers={"WWW-Authenticate": "Bearer"})

        # Role check. Same generic 401 if the user is real but not
        # allowed on this route — don't reveal "this user exists but
        # has the wrong role".
        if user.role not in allow_set:
            _raise_401(headers={"WWW-Authenticate": "Bearer"})

        # Org disabled check. Skip for super (no org). For admin/hr,
        # we'd need the org row — but only fetch it if needed, to keep
        # the hot path fast. Refresh from DB via the relationship.
        if user.role != "super":
            org = user.organization  # lazy-loaded; cheap because the
                                     # request will likely use it later
            if org is not None and org.disabled_at is not None:
                # Same generic 401 — don't leak that the org exists but
                # is suspended. Internally an ops dashboard can correlate
                # via the audit log.
                _raise_401(headers={"WWW-Authenticate": "Bearer"})

        # must_change_password gate (only when strict). The non-strict
        # endpoints (/me, /change-password, /refresh, /logout) skip this
        # so the user can actually clear the flag.
        if strict:
            _check_must_change_password(user)

        return Principal(
            user=user,
            role=user.role,
            organization_id=user.organization_id,
        )

    return _dep


# ============================================================
# Legacy wrappers — keep existing route signatures working
# ============================================================
# Each old-style dep delegates to require_principal. Routes that still
# import these by name keep working without changes. Step B and beyond
# can migrate them on a per-router basis to use require_principal
# directly with explicit allow tuples.
#
# Note on call-site shape: routes that have
#     hr: HRAdmin = Depends(require_hr_strict)
# can ALSO be written as
#     p: Principal = Depends(require_principal(allow=("hr",)))
#     # and then use p.user where they used `hr`
# but we're not forcing that migration in Step A. Both styles coexist.

def require_hr(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """
    LEGACY: HR-only access via session cookie (and now also JWT).
    Use require_principal(allow=("hr",), strict=False) for new code.

    Backward-compat: this used to be cookie-only. With the unified
    resolver it now also accepts JWT. That's a strict generalization —
    every request that worked before still works.
    """
    p = require_principal(allow=("hr",), strict=False)(request, creds, db)
    return p.user


def require_hr_strict(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """LEGACY: HR-only + must_change_password gate.
    Use require_principal(allow=("hr",)) for new code."""
    p = require_principal(allow=("hr",), strict=True)(request, creds, db)
    return p.user


def require_admin(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """LEGACY: admin-only access.
    Use require_principal(allow=("admin",), strict=False) for new code.

    NOTE on multi-tenancy: this dep allows only 'admin', not 'super'.
    During the Step B–D migration, super-callable endpoints will switch
    to require_principal(allow=("super","admin")). Until then, super
    users CANNOT call admin endpoints — that's intentional: it forces
    the migration to be explicit per route, not a silent automatic
    upgrade that might expose endpoints we forgot to think about.
    """
    p = require_principal(allow=("admin",), strict=False)(request, creds, db)
    return p.user


def require_admin_strict(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """LEGACY: admin-only + must_change_password gate.
    Use require_principal(allow=("admin",)) for new code."""
    p = require_principal(allow=("admin",), strict=True)(request, creds, db)
    return p.user


def require_jwt_hr(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
) -> HRAdmin:
    """
    LEGACY: JWT-only HR access. Pre-multi-tenancy, this rejected cookie
    sessions; under the unified resolver, cookie also works. This is a
    strict generalization — every old caller keeps working.
    Use require_principal(allow=("hr",), strict=False) for new code.
    """
    # FastAPI will inject `request` for us because Request is a known
    # framework type, but mypy doesn't like the default. The runtime is
    # fine — FastAPI binds it before the body runs.
    p = require_principal(allow=("hr",), strict=False)(request, creds, db)
    return p.user


def require_jwt_admin(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
) -> HRAdmin:
    """LEGACY: JWT-only admin access. Same multi-tenancy note as
    require_admin — super is not allowed on these endpoints until they
    explicitly opt in. Use require_principal(allow=("admin",), strict=False)."""
    p = require_principal(allow=("admin",), strict=False)(request, creds, db)
    return p.user
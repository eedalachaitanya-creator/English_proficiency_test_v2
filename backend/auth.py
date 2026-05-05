"""
Authentication helpers.

- bcrypt for password hashing (slow on purpose — that's the security feature).
- secrets.token_urlsafe for invitation tokens (cryptographically random).
- require_hr() dependency rejects requests without a valid session cookie.
"""
import secrets
import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models import HRAdmin


# ------------------------------------------------------------------
# Passwords
# ------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Generate a bcrypt hash with a fresh salt each call."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison via bcrypt."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------
# Tokens (used for invitation URLs)
# ------------------------------------------------------------------
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


# ------------------------------------------------------------------
# Session dependencies
# ------------------------------------------------------------------
def _resolve_user_with_role(
    request: Request,
    db: Session,
    expected_role: str,
) -> HRAdmin:
    """
    Shared lookup: pull hr_admin_id from the session, fetch the row, and
    enforce the given role. Centralized so role enforcement is identical
    across require_hr and require_admin and a new role can be added in
    one place. Always raises 401 on any failure (no role-leak via
    distinct error messages — admins and HRs both get the same generic
    "not authenticated" response if they try to use the wrong endpoint).

    Also enforces session invalidation on password rotation: each session
    cookie carries `pw_v` (the user's password_changed_at timestamp at
    login). If the user's current password_changed_at is newer, the
    session was issued before a password change and is rejected. This
    is what makes the change-password endpoint actually defend against
    an active session-hijack — without this check, an attacker who
    captured the cookie keeps working until the cookie's natural expiry.
    """
    # All four failure modes (no cookie / deleted user / wrong role /
    # stale-after-password-change) return the SAME generic 401 message.
    # Different messages would let an attacker distinguish "this user_id
    # was once valid but is now deleted" from "no cookie at all" by
    # crafting session cookies, or distinguish "your password was just
    # rotated" from "you never logged in" by replaying old cookies.
    GENERIC_401 = "Not authenticated. Please log in."

    hr_id = request.session.get("hr_admin_id")
    if not hr_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )

    hr = db.query(HRAdmin).filter(HRAdmin.id == hr_id).first()
    if not hr:
        # Session points to a deleted user — clear and reject.
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )

    if hr.role != expected_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )

    # Session-invalidation gate. session_pw_v is the password_changed_at
    # value at login (as ISO-8601 string — DateTime isn't JSON-serializable
    # for the session cookie). If the user's current password_changed_at
    # is strictly newer, the session pre-dates a password rotation and
    # MUST be invalidated.
    session_pw_v = request.session.get("pw_v")
    current_pw_v = hr.password_changed_at.isoformat() if hr.password_changed_at else None
    if session_pw_v != current_pw_v:
        # Don't clear: the cookie itself is still cryptographically valid,
        # but its embedded timestamp is stale. Forcing the user to re-log
        # in is the whole point.
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
        )

    return hr


def require_hr(request: Request, db: Session = Depends(get_db)) -> HRAdmin:
    """
    FastAPI dependency. Add to any route that requires HR login:

        @router.get("/some-protected-thing")
        def handler(hr: HRAdmin = Depends(require_hr)):
            ...

    Returns the logged-in HRAdmin (with role='hr') or raises 401.
    Admin accounts are explicitly NOT accepted — see _resolve_user_with_role.
    """
    return _resolve_user_with_role(request, db, expected_role="hr")


def require_admin(request: Request, db: Session = Depends(get_db)) -> HRAdmin:
    """
    FastAPI dependency. Add to admin-portal routes:

        @router.get("/api/admin/some-thing")
        def handler(admin: HRAdmin = Depends(require_admin)):
            ...

    Returns the logged-in admin (role='admin') or raises 401. HR accounts
    are explicitly NOT accepted.
    """
    return _resolve_user_with_role(request, db, expected_role="admin")

# ------------------------------------------------------------------
# JWT dependencies
# ------------------------------------------------------------------

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from jwt_service import decode_token, InvalidTokenError

# HTTPBearer is FastAPI's built-in helper for extracting Bearer tokens
# from the Authorization header. auto_error=False so we can return our
# generic 401 instead of FastAPI's default "Not authenticated" body —
# avoids leaking which dep failed when an attacker probes routes.
_bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


def _resolve_jwt_user_with_role(
    creds: HTTPAuthorizationCredentials | None,
    db: Session,
    expected_role: str,
) -> HRAdmin:
    """
    Shared logic: extract the JWT, validate it, look up the HRAdmin row,
    enforce the role. Same generic-401 policy as the cookie path — no
    distinct error messages, even for "expired token" vs "wrong role"
    vs "deleted user". Don't help an attacker enumerate.
    """
    GENERIC_401 = "Not authenticated. Please log in."

    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(creds.credentials, expected_type="access")
    except InvalidTokenError:
        # Covers expired, malformed, wrong-issuer, and wrong-type tokens.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # JWT payload uses string `sub` per the JWT spec. Convert back to int
    # for the DB query, but defensively — if someone forged a token with
    # a non-numeric sub it shouldn't crash the route.
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    hr = db.query(HRAdmin).filter(HRAdmin.id == user_id).first()
    if not hr:
        # Token points to a deleted user — reject.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Cross-check: the role claim in the token must match what we expect
    # AND match the user's current DB role. If a user was demoted from
    # admin to hr after their token was issued, their old admin token
    # should stop working.
    token_role = payload.get("role")
    if token_role != expected_role or hr.role != expected_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return hr


def require_jwt_hr(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """
    JWT-protected counterpart to require_hr. Use on routes that should
    accept the new Authorization: Bearer <token> header.

        @router.get("/some-jwt-protected-thing")
        def handler(hr: HRAdmin = Depends(require_jwt_hr)):
            ...
    """
    return _resolve_jwt_user_with_role(creds, db, expected_role="hr")


def require_jwt_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> HRAdmin:
    """
    JWT-protected counterpart to require_admin. For admin-portal routes
    that authenticate via JWT instead of the session cookie.
    """
    return _resolve_jwt_user_with_role(creds, db, expected_role="admin")
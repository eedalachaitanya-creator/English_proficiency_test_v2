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
# Session dependency
# ------------------------------------------------------------------
def require_hr(request: Request, db: Session = Depends(get_db)) -> HRAdmin:
    """
    FastAPI dependency. Add to any route that requires HR login:

        @router.get("/some-protected-thing")
        def handler(hr: HRAdmin = Depends(require_hr)):
            ...

    Returns the logged-in HRAdmin or raises 401.
    """
    hr_id = request.session.get("hr_admin_id")
    if not hr_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    hr = db.query(HRAdmin).filter(HRAdmin.id == hr_id).first()
    if not hr:
        # Session points to a deleted user — clear and reject.
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session. Please log in again.",
        )

    return hr
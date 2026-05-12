"""
JWT service — token creation, decoding, and validation.

This is the single source of truth for everything JWT in the backend.
Routes and dependencies should call into this module rather than touching
python-jose directly. Keeps the auth surface small and easy to audit.

Token shape (payload):
    {
        "sub": <user_id>,           # subject — the HRAdmin.id (as string)
        "role": "super" | "admin" | "hr",
        "type": "access" | "refresh",
        "iat": <unix-ts>,           # issued-at
        "exp": <unix-ts>,           # expiry
        "iss": "ept-backend",
        "pw_changed_at_iso":        # OPTIONAL on legacy tokens; required on
            "<iso8601>" | null      # any token minted by this code path.
                                    # Mirrors the cookie session's `pw_v` field
                                    # and is used by auth.py to invalidate tokens
                                    # after a password change. See
                                    # auth._resolve_jwt_user for the check.
    }

Signing: HS256 with JWT_SECRET_KEY. Symmetric — the same key that signs
also verifies. We're a single backend with no third-party verifiers, so
HS256 is the right choice (RS256 would be overkill).

Lifetimes (configurable via env, see .env.example):
    JWT_ACCESS_MINUTES   default 30 — used on every API call
    JWT_REFRESH_DAYS     default 1  — used only to mint new access tokens

Why two token types:
    - Access token is short-lived. Stolen access tokens expire fast.
    - Refresh token is longer-lived but used rarely (only at the
      /refresh endpoint), reducing exposure.
    The `type` claim prevents misuse — a refresh token cannot be passed
    where an access token is expected, and vice versa.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError

load_dotenv()


# ---------------- Config (read once at import time) ----------------

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "ept-backend"

# Fail fast if the key is missing or too short. Without this, tokens get
# signed with empty string -> anyone can forge tokens that "validate".
# Catastrophic and silent. Better to refuse to start.
if not JWT_SECRET_KEY or len(JWT_SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY env var is missing or shorter than 32 chars. "
        "Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
        "and put it in backend/.env"
    )

ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_MINUTES", "30"))
REFRESH_TOKEN_DAYS = int(os.getenv("JWT_REFRESH_DAYS", "1"))


# ---------------- Public API ----------------

# Expanded for multi-tenancy. 'super' tokens grant the Stixis-internal
# god-mode access; 'admin' tokens are per-org admins; 'hr' tokens are
# per-org HR users. require_principal in auth.py enforces what each role
# can do on each endpoint.
Role = Literal["super", "admin", "hr"]
TokenType = Literal["access", "refresh"]


def create_access_token(
    user_id: int,
    role: Role,
    pw_changed_at_iso: Optional[str] = None,
) -> str:
    """
    Mint a short-lived access token. Sent on every API call.

    pw_changed_at_iso: the user's current password_changed_at as an
    ISO-8601 string. Embedded in the token so auth._resolve_jwt_user
    can reject tokens minted before a password rotation. Optional only
    for backward compatibility — every call site in routes/hr.py and
    routes/admin.py is being updated to pass it.
    """
    return _create_token(
        user_id, role, "access",
        timedelta(minutes=ACCESS_TOKEN_MINUTES),
        pw_changed_at_iso,
    )


def create_refresh_token(
    user_id: int,
    role: Role,
    pw_changed_at_iso: Optional[str] = None,
) -> str:
    """Mint a longer-lived refresh token. Used only by /refresh endpoint."""
    return _create_token(
        user_id, role, "refresh",
        timedelta(days=REFRESH_TOKEN_DAYS),
        pw_changed_at_iso,
    )


def create_token_pair(
    user_id: int,
    role: Role,
    pw_changed_at_iso: Optional[str] = None,
) -> dict:
    """
    Convenience: mint both tokens in one call. This is what login endpoints
    return — the response body shape matches the OAuth2 password-grant
    convention so any standard JWT client library can consume it.

    pw_changed_at_iso: see create_access_token. Pass the user's current
    password_changed_at so the token can be invalidated on rotation.
    """
    return {
        "access_token": create_access_token(user_id, role, pw_changed_at_iso),
        "refresh_token": create_refresh_token(user_id, role, pw_changed_at_iso),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,  # seconds, OAuth2 convention
    }


class InvalidTokenError(Exception):
    """Raised when a token is malformed, expired, or has the wrong type."""


def decode_token(token: str, expected_type: TokenType) -> dict:
    """
    Decode and verify a token. Returns the payload dict on success.

    Raises InvalidTokenError if:
        - signature is bad (forged or wrong key)
        - token has expired
        - issuer is not "ept-backend"
        - the `type` claim doesn't match expected_type

    The expected_type check is critical — it prevents a refresh token
    from being used to authenticate an API call (and vice versa).

    Note: pw_changed_at_iso is NOT checked here. That belongs in auth.py
    where the live DB row is available for comparison.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
        )
    except ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired") from exc
    except JWTError as exc:
        raise InvalidTokenError(f"Invalid token: {exc}") from exc

    actual_type = payload.get("type")
    if actual_type != expected_type:
        raise InvalidTokenError(
            f"Wrong token type — expected {expected_type!r}, got {actual_type!r}"
        )

    return payload


# ---------------- Internal ----------------

def _create_token(
    user_id: int,
    role: Role,
    token_type: TokenType,
    lifetime: timedelta,
    pw_changed_at_iso: Optional[str],
) -> str:
    """Shared token creation logic. Not for external use — call the named helpers."""
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": str(user_id),  # JWT spec says sub is a string; jose enforces this
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "iss": JWT_ISSUER,
    }
    # Only include the claim when given. Legacy callers that don't yet
    # pass it produce tokens without the claim, which auth.py treats as
    # "fall back to allowing" — that's the safe default for an in-flight
    # rolling upgrade where some sessions are mid-existence.
    if pw_changed_at_iso is not None:
        payload["pw_changed_at_iso"] = pw_changed_at_iso

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
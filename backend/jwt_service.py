"""
JWT service — token creation, decoding, and validation.

This is the single source of truth for everything JWT in the backend.
Routes and dependencies should call into this module rather than touching
python-jose directly. Keeps the auth surface small and easy to audit.

Token shape (payload):
    {
        "sub": <user_id>,         # subject — the HRAdmin.id
        "role": "hr" | "admin",   # which routes this token can hit
        "type": "access" | "refresh",  # which kind of token this is
        "iat": <unix-ts>,         # issued-at
        "exp": <unix-ts>,         # expiry
        "iss": "ept-backend"      # issuer — sanity check on validation
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
from typing import Literal

from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError


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

Role = Literal["hr", "admin"]
TokenType = Literal["access", "refresh"]


def create_access_token(user_id: int, role: Role) -> str:
    """Mint a short-lived access token. Sent on every API call."""
    return _create_token(user_id, role, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES))


def create_refresh_token(user_id: int, role: Role) -> str:
    """Mint a longer-lived refresh token. Used only by /refresh endpoint."""
    return _create_token(user_id, role, "refresh", timedelta(days=REFRESH_TOKEN_DAYS))


def create_token_pair(user_id: int, role: Role) -> dict:
    """
    Convenience: mint both tokens in one call. This is what login endpoints
    return — the response body shape matches the OAuth2 password-grant
    convention so any standard JWT client library can consume it.
    """
    return {
        "access_token": create_access_token(user_id, role),
        "refresh_token": create_refresh_token(user_id, role),
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

def _create_token(user_id: int, role: Role, token_type: TokenType, lifetime: timedelta) -> str:
    """Shared token creation logic. Not for external use — call the named helpers."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # JWT spec says sub is a string; jose enforces this
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
"""
Shared helpers for HR + Admin forgot-password endpoints.

Extracted from routes/hr.py so the new /api/admin/forgot-password
endpoint can reuse the same rate-limit dict, latency-floor padding,
generic response, and temp-password generator without duplicating
logic. The in-memory rate-limit state is intentionally module-level
here, so a single email's cooldown applies regardless of which
endpoint received it (HR or admin).

Per-worker caveat: in a multi-worker uvicorn deploy each worker has
its own dict. Combined with the latency floor an attacker still can't
distinguish rate-limited from unknown-email by timing, so this is
acceptable. For a hard cross-worker guarantee, move to Redis.
"""
import secrets
import string
import time


# Generic message returned for ALL outcomes — unknown email, wrong
# role, rate-limited, SMTP failure, real reset. Identical wording is
# defended in tests; keep it stable.
FORGOT_PASSWORD_GENERIC_RESPONSE = {
    "status": "ok",
    "message": "If an account exists for that email, a temporary password has been sent.",
}

# A single email can only trigger one reset every N seconds. Closes
# email-bombing and SMTP quota burn.
RESET_COOLDOWN_SECONDS = 60

# Every code path takes at least this many seconds to respond, masking
# the timing channel between "real user" (~SMTP latency) and "unknown
# email" (~5ms).
LATENCY_FLOOR_SECONDS = 1.2

# email_lower → expires_at unix timestamp. Public (no leading
# underscore) because tests legitimately clear it between cases.
recent_resets: dict[str, float] = {}


def is_recently_reset(email_lower: str) -> bool:
    """True if this email triggered a reset within RESET_COOLDOWN_SECONDS.
    Side effect: stamps the email with a fresh expiry on the FIRST hit
    (subsequent calls within the window all return True). Garbage-
    collects expired entries lazily so the dict doesn't grow forever."""
    now = time.time()
    # Sweep expired keys (cheap — typically <100 items).
    for k in [k for k, v in recent_resets.items() if v < now]:
        del recent_resets[k]
    expires = recent_resets.get(email_lower)
    if expires and now < expires:
        return True
    recent_resets[email_lower] = now + RESET_COOLDOWN_SECONDS
    return False


def sleep_to_latency_floor(started_at: float) -> None:
    """Pad the response time so every code path takes at least
    LATENCY_FLOOR_SECONDS. Sync sleep is fine — sync routes run on the
    threadpool and don't block the event loop."""
    elapsed = time.monotonic() - started_at
    remaining = LATENCY_FLOOR_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)


def generate_temp_password(length: int = 12) -> str:
    """Cryptographically random temp password. Mix of upper/lower/digits
    (no special chars — easier to type from email; we trade a tiny bit
    of entropy for fewer "weird-character" support tickets). 12 chars
    of [A-Za-z0-9] = ~71 bits of entropy, well above brute-force range."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

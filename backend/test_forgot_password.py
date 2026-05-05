"""
Tests for POST /api/hr/forgot-password.

Security model: the endpoint always returns 200 with the SAME generic
success message, regardless of whether the email was found, the SMTP
send succeeded, or the email belongs to an admin instead of an HR.
This prevents enumeration of valid HR emails via the forgot-password
flow. The actual SMTP outcome is logged server-side for ops to debug.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auth import hash_password, verify_password
from database import SessionLocal
from main import app
from models import HRAdmin


_GENERIC_MESSAGE = "If an account exists for that email, a temporary password has been sent."


def _make_hr(password: str = "originalPass1") -> HRAdmin:
    db = SessionLocal()
    hr = HRAdmin(
        name="Forgot Test HR",
        email=f"forgot-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role="hr",
    )
    db.add(hr)
    db.commit()
    db.refresh(hr)
    db.close()
    return hr


def _make_admin(password: str = "originalPass1") -> HRAdmin:
    db = SessionLocal()
    admin = HRAdmin(
        name="Forgot Test Admin",
        email=f"forgot-admin-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.close()
    return admin


def _drop(uid: int) -> None:
    db = SessionLocal()
    db.query(HRAdmin).filter(HRAdmin.id == uid).delete()
    db.commit()
    db.close()


def _hash_for(uid: int) -> str:
    db = SessionLocal()
    hr = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
    db.close()
    return hr.password_hash


# ----------------------------------------------------------------------
# Happy path — HR exists, email sends OK
# ----------------------------------------------------------------------

def test_forgot_password_happy_path_replaces_password():
    """For a real HR email + working SMTP: the user's password_hash is
    replaced with a hash of the temp password, password_changed_at is
    bumped, and the same generic 200 message is returned."""
    hr = _make_hr(password="originalPass1")
    captured = {}

    def fake_send(*, hr_email, hr_name, login_url, temp_password):
        captured["temp_password"] = temp_password
        captured["hr_email"] = hr_email
        return (True, None)

    try:
        original_hash = _hash_for(hr.id)
        original_pw_changed = hr.password_changed_at

        with patch("routes.hr.send_temp_password_email", side_effect=fake_send):
            c = TestClient(app)
            r = c.post("/api/hr/forgot-password", json={"email": hr.email})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "ok"
            assert r.json()["message"] == _GENERIC_MESSAGE

        # Hash actually changed in the DB
        new_hash = _hash_for(hr.id)
        assert new_hash != original_hash, "password_hash must have been replaced"

        # The temp password our patched helper saw must verify against
        # the new hash — proves the route generated, hashed, AND saved
        # the SAME password it sent in the email.
        assert verify_password(captured["temp_password"], new_hash)

        # Original password no longer works
        assert not verify_password("originalPass1", new_hash)

        # password_changed_at bumped — invalidates other sessions
        db = SessionLocal()
        refreshed = db.query(HRAdmin).filter(HRAdmin.id == hr.id).first()
        db.close()
        assert refreshed.password_changed_at > original_pw_changed
    finally:
        _drop(hr.id)


# ----------------------------------------------------------------------
# Email enumeration defenses — same response in every "no send" case
# ----------------------------------------------------------------------

def test_forgot_password_unknown_email_returns_generic_message():
    """No HR with that email exists. Endpoint returns the SAME 200 +
    generic message — does NOT 404, does NOT say 'no such user', does
    NOT call SMTP. This is the email-enumeration defense."""
    sent = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return (True, None)

    with patch("routes.hr.send_temp_password_email", side_effect=fake_send):
        c = TestClient(app)
        # Use a clearly-unique-but-format-valid email. "example.com" is
        # the IETF reserved test domain, and Pydantic EmailStr accepts it.
        r = c.post(
            "/api/hr/forgot-password",
            json={"email": f"nobody-{int(datetime.now(timezone.utc).timestamp())}@example.com"},
        )
        assert r.status_code == 200
        assert r.json()["message"] == _GENERIC_MESSAGE

    # Critical: SMTP was NOT called for an unknown email.
    assert len(sent) == 0


def test_forgot_password_admin_email_returns_generic_message():
    """Email belongs to an admin, not an HR. Same generic 200 response,
    NO email sent (admins reset via CLI). This both prevents enumeration
    AND ensures admins can't be reset via the HR endpoint."""
    admin = _make_admin(password="adminPass1")
    sent = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return (True, None)

    try:
        original_hash = _hash_for(admin.id)
        with patch("routes.hr.send_temp_password_email", side_effect=fake_send):
            c = TestClient(app)
            r = c.post("/api/hr/forgot-password", json={"email": admin.email})
            assert r.status_code == 200
            assert r.json()["message"] == _GENERIC_MESSAGE

        # Admin's password must NOT have changed.
        assert _hash_for(admin.id) == original_hash
        # SMTP must NOT have been called.
        assert len(sent) == 0
    finally:
        _drop(admin.id)


def test_forgot_password_smtp_failure_returns_generic_message():
    """SMTP fails (network down, auth, etc.). The user's password is
    NOT updated (atomicity — don't lock them out of their account just
    because we couldn't email the new password). Endpoint still returns
    the same generic 200 (don't leak SMTP state to a probing attacker)."""
    hr = _make_hr(password="originalPass1")

    def fake_send(**kwargs):
        return (False, "SMTPConnectError: simulated")

    try:
        original_hash = _hash_for(hr.id)
        with patch("routes.hr.send_temp_password_email", side_effect=fake_send):
            c = TestClient(app)
            r = c.post("/api/hr/forgot-password", json={"email": hr.email})
            assert r.status_code == 200, r.text
            assert r.json()["message"] == _GENERIC_MESSAGE

        # Hash must NOT have changed — the user can still log in with
        # their original password. If we'd updated the hash and then
        # failed to email, they'd be permanently locked out.
        assert _hash_for(hr.id) == original_hash
    finally:
        _drop(hr.id)


# ----------------------------------------------------------------------
# Validator
# ----------------------------------------------------------------------

def test_forgot_password_invalid_email_format_rejected():
    """Pydantic EmailStr rejects malformed input with 422."""
    c = TestClient(app)
    r = c.post("/api/hr/forgot-password", json={"email": "not-an-email"})
    assert r.status_code == 422


# ----------------------------------------------------------------------
# Session invalidation — cross-tab story
# ----------------------------------------------------------------------

def test_forgot_password_invalidates_existing_sessions():
    """A successful reset bumps password_changed_at, so any session
    that pre-dates the reset is invalidated by the pw_v check in
    _resolve_user_with_role. Symmetric to the change-password fix."""
    hr = _make_hr(password="originalPass1")

    def fake_send(**kwargs):
        return (True, None)

    try:
        # Tab A: log in, confirm session works
        tabA = TestClient(app)
        tabA.post("/api/hr/login", json={"email": hr.email, "password": "originalPass1"})
        assert tabA.get("/api/hr/me").status_code == 200

        # Forgot-password (no session needed — anonymous endpoint)
        with patch("routes.hr.send_temp_password_email", side_effect=fake_send):
            anon = TestClient(app)
            r = anon.post("/api/hr/forgot-password", json={"email": hr.email})
            assert r.status_code == 200

        # Tab A's stale session must now 401.
        assert tabA.get("/api/hr/me").status_code == 401
    finally:
        _drop(hr.id)

"""
Tests for POST /api/hr/change-password.

Uses FastAPI's TestClient so we exercise the full route + dependency
chain (auth.require_hr, password verification, bcrypt re-hash). Each
test creates and tears down its own HR user so they can run in any
order without state leak.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from auth import hash_password, verify_password
from database import SessionLocal
from main import app
from models import HRAdmin


def _make_hr(password: str = "currentPass123") -> HRAdmin:
    """Insert a fresh HR row and return it. Caller deletes via _drop()."""
    db = SessionLocal()
    hr = HRAdmin(
        name="Pwd Test HR",
        email=f"pwd-test-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role="hr",
    )
    db.add(hr)
    db.commit()
    db.refresh(hr)
    db.close()
    return hr


def _drop(hr_id: int) -> None:
    db = SessionLocal()
    db.query(HRAdmin).filter(HRAdmin.id == hr_id).delete()
    db.commit()
    db.close()


def _login_client(email: str, password: str) -> TestClient:
    """Return a TestClient with an HR session cookie set."""
    c = TestClient(app)
    r = c.post("/api/hr/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return c


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------

def test_change_password_happy_path():
    """Correct current password + valid new password → 200, hash updated."""
    hr = _make_hr(password="currentPass123")
    try:
        c = _login_client(hr.email, "currentPass123")
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "currentPass123", "new_password": "newPass456"},
        )
        assert r.status_code == 200, r.text

        # Verify the hash actually changed: re-fetch and bcrypt-check.
        db = SessionLocal()
        refreshed = db.query(HRAdmin).filter(HRAdmin.id == hr.id).first()
        db.close()
        assert verify_password("newPass456", refreshed.password_hash)
        assert not verify_password("currentPass123", refreshed.password_hash)
    finally:
        _drop(hr.id)


def test_change_password_session_survives():
    """After a successful change, the session cookie still works — no
    forced re-login. (Convenience: HR shouldn't get bounced mid-task.)"""
    hr = _make_hr(password="currentPass123")
    try:
        c = _login_client(hr.email, "currentPass123")
        c.post(
            "/api/hr/change-password",
            json={"current_password": "currentPass123", "new_password": "newPass456"},
        )
        # Use the same client (same cookie jar) — should still be authed.
        r = c.get("/api/hr/me")
        assert r.status_code == 200
    finally:
        _drop(hr.id)


# ----------------------------------------------------------------------
# Failure modes
# ----------------------------------------------------------------------

def test_change_password_wrong_current_rejected():
    """Wrong current password → 401, hash unchanged."""
    hr = _make_hr(password="currentPass123")
    try:
        c = _login_client(hr.email, "currentPass123")
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "WRONG", "new_password": "newPass456"},
        )
        assert r.status_code == 401, r.text

        # Hash must still verify against the original.
        db = SessionLocal()
        refreshed = db.query(HRAdmin).filter(HRAdmin.id == hr.id).first()
        db.close()
        assert verify_password("currentPass123", refreshed.password_hash)
    finally:
        _drop(hr.id)


def test_change_password_too_short_rejected():
    """New password < 6 chars → 422 (Pydantic field validation)."""
    hr = _make_hr(password="currentPass123")
    try:
        c = _login_client(hr.email, "currentPass123")
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "currentPass123", "new_password": "abc"},
        )
        assert r.status_code == 422, r.text
    finally:
        _drop(hr.id)


def test_change_password_whitespace_only_rejected():
    """New password of all whitespace → 422 (custom field validator).
    Min-length=6 alone is satisfied by '      ' (6 spaces); the
    schema's reject_blank_or_whitespace_only validator closes that
    gap. Without this guard, an HR could 'change' to a useless
    blank-string password."""
    hr = _make_hr(password="currentPass123")
    try:
        c = _login_client(hr.email, "currentPass123")
        # Six spaces — passes min_length but is empty after .strip()
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "currentPass123", "new_password": "      "},
        )
        assert r.status_code == 422, r.text
        # Mostly whitespace — also rejected because stripped length is 3
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "currentPass123", "new_password": "abc   "},
        )
        assert r.status_code == 422, r.text
    finally:
        _drop(hr.id)


def test_change_password_no_session_rejected():
    """Anonymous request → 401 from require_hr."""
    c = TestClient(app)
    r = c.post(
        "/api/hr/change-password",
        json={"current_password": "x", "new_password": "newPass456"},
    )
    assert r.status_code == 401


def test_change_password_admin_cannot_use_hr_endpoint():
    """An admin session must NOT be able to change password via the HR
    endpoint — admin password change goes through a separate route.
    Regression for the role-enforcement work."""
    db = SessionLocal()
    admin = HRAdmin(
        name="Pwd Admin",
        email=f"pwd-admin-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("adminpass"),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    aid = admin.id
    db.close()
    try:
        c = TestClient(app)
        # Log in via admin endpoint to set the session cookie.
        r = c.post("/api/admin/login", json={"email": admin.email, "password": "adminpass"})
        assert r.status_code == 200
        # Try to use the HR change-password endpoint with the admin session.
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "adminpass", "new_password": "newPass456"},
        )
        assert r.status_code == 401, r.text
    finally:
        _drop(aid)


# ----------------------------------------------------------------------
# Admin /api/admin/change-password — mirrors HR tests, must reject HRs
# ----------------------------------------------------------------------

def _make_admin(password: str = "adminPass123") -> HRAdmin:
    """Insert a fresh admin row. Caller deletes via _drop()."""
    db = SessionLocal()
    admin = HRAdmin(
        name="Pwd Admin",
        email=f"pwd-admin-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.close()
    return admin


def _login_admin(email: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return c


def test_admin_change_password_happy_path():
    """Admin can change their own password via /api/admin/change-password."""
    admin = _make_admin(password="adminOriginal1")
    try:
        c = _login_admin(admin.email, "adminOriginal1")
        r = c.post(
            "/api/admin/change-password",
            json={"current_password": "adminOriginal1", "new_password": "adminNew456"},
        )
        assert r.status_code == 200, r.text
        # Verify hash actually changed
        db = SessionLocal()
        refreshed = db.query(HRAdmin).filter(HRAdmin.id == admin.id).first()
        db.close()
        assert verify_password("adminNew456", refreshed.password_hash)
        assert not verify_password("adminOriginal1", refreshed.password_hash)
    finally:
        _drop(admin.id)


def test_admin_change_password_wrong_current_rejected():
    """Wrong current password → 401, hash unchanged."""
    admin = _make_admin(password="adminOriginal1")
    try:
        c = _login_admin(admin.email, "adminOriginal1")
        r = c.post(
            "/api/admin/change-password",
            json={"current_password": "WRONG", "new_password": "adminNew456"},
        )
        assert r.status_code == 401
    finally:
        _drop(admin.id)


def test_admin_change_password_no_session_rejected():
    """Anonymous → 401."""
    c = TestClient(app)
    r = c.post(
        "/api/admin/change-password",
        json={"current_password": "x", "new_password": "adminNew456"},
    )
    assert r.status_code == 401


def test_admin_change_password_hr_cannot_use_admin_endpoint():
    """An HR session must NOT be able to change password via the admin
    endpoint — symmetric to test_change_password_admin_cannot_use_hr_endpoint."""
    hr = _make_hr(password="hrpass123")
    try:
        c = _login_client(hr.email, "hrpass123")
        r = c.post(
            "/api/admin/change-password",
            json={"current_password": "hrpass123", "new_password": "newPass456"},
        )
        assert r.status_code == 401, r.text
    finally:
        _drop(hr.id)


# ----------------------------------------------------------------------
# must_change_password flag is cleared by a successful change-password
# (paired with forgot-password setting it TRUE; together they unlock
# the rest of the app after the user picks a permanent password).
# ----------------------------------------------------------------------

def _set_must_change(uid: int, value: bool) -> None:
    """Force the must_change_password flag without going through the
    forgot-password endpoint. Faster than running the real reset flow
    just to set up the precondition."""
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
    user.must_change_password = value
    db.commit()
    db.close()


def _must_change_for(uid: int) -> bool:
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
    db.close()
    return user.must_change_password


def test_change_password_clears_must_change_password_flag_hr():
    """A successful HR change-password must set must_change_password
    back to FALSE, so the route guard + strict-auth dep stop locking
    the UI."""
    hr = _make_hr(password="tempFromEmail1")
    _set_must_change(hr.id, True)
    try:
        assert _must_change_for(hr.id) is True  # precondition

        c = _login_client(hr.email, "tempFromEmail1")
        r = c.post(
            "/api/hr/change-password",
            json={"current_password": "tempFromEmail1", "new_password": "permPass789"},
        )
        assert r.status_code == 200, r.text
        assert _must_change_for(hr.id) is False, (
            "must_change_password must be cleared after a successful "
            "change — without this, the route guard would keep the "
            "user on /change-password-required forever."
        )
    finally:
        _drop(hr.id)


def test_change_password_clears_must_change_password_flag_admin():
    """A successful admin change-password must clear the flag — same
    reason as the HR test above, mirrored for the admin endpoint."""
    admin = _make_admin(password="tempAdminFromEmail1")
    _set_must_change(admin.id, True)
    try:
        assert _must_change_for(admin.id) is True

        c = _login_admin(admin.email, "tempAdminFromEmail1")
        r = c.post(
            "/api/admin/change-password",
            json={"current_password": "tempAdminFromEmail1", "new_password": "permAdmin789"},
        )
        assert r.status_code == 200, r.text
        assert _must_change_for(admin.id) is False
    finally:
        _drop(admin.id)

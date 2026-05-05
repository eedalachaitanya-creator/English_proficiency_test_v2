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
    endpoint — admin password change goes through a separate route (or
    CLI, today). Regression for the role-enforcement work."""
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

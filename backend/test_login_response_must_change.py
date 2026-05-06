"""
Tests for must_change_password being surfaced in login and /me responses.

The frontend AuthService reads this field from the login response (and
later from /me, on app boot / refresh) to populate its mustChangePassword
signal. Without the field on the wire, the route guard never engages and
the forced-change UX silently regresses.

Both roles tested in both endpoints (HR + admin × login + /me).
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from auth import hash_password
from database import SessionLocal
from main import app
from models import HRAdmin


def _make_user(role: str, password: str, must_change: bool) -> HRAdmin:
    db = SessionLocal()
    user = HRAdmin(
        name=f"MCP {role} test",
        email=f"mcp-{role}-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def _drop(uid: int) -> None:
    db = SessionLocal()
    db.query(HRAdmin).filter(HRAdmin.id == uid).delete()
    db.commit()
    db.close()


# ----------------------------------------------------------------------
# HR
# ----------------------------------------------------------------------

def test_hr_login_response_includes_must_change_password_false_by_default():
    hr = _make_user("hr", "testpass1", must_change=False)
    try:
        c = TestClient(app)
        r = c.post("/api/hr/login", json={"email": hr.email, "password": "testpass1"})
        assert r.status_code == 200
        body = r.json()
        assert "must_change_password" in body
        assert body["must_change_password"] is False
    finally:
        _drop(hr.id)


def test_hr_login_response_must_change_password_true_after_reset():
    hr = _make_user("hr", "tempPass1", must_change=True)
    try:
        c = TestClient(app)
        r = c.post("/api/hr/login", json={"email": hr.email, "password": "tempPass1"})
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is True
    finally:
        _drop(hr.id)


def test_hr_me_response_includes_must_change_password():
    hr = _make_user("hr", "tempPass1", must_change=True)
    try:
        c = TestClient(app)
        login = c.post("/api/hr/login", json={"email": hr.email, "password": "tempPass1"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        me = c.get("/api/hr/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["must_change_password"] is True
    finally:
        _drop(hr.id)


# ----------------------------------------------------------------------
# Admin
# ----------------------------------------------------------------------

def test_admin_login_response_includes_must_change_password_false_by_default():
    admin = _make_user("admin", "adminpass1", must_change=False)
    try:
        c = TestClient(app)
        r = c.post("/api/admin/login", json={"email": admin.email, "password": "adminpass1"})
        assert r.status_code == 200
        body = r.json()
        assert "must_change_password" in body
        assert body["must_change_password"] is False
    finally:
        _drop(admin.id)


def test_admin_login_response_must_change_password_true_after_reset():
    admin = _make_user("admin", "adminTempPass1", must_change=True)
    try:
        c = TestClient(app)
        r = c.post(
            "/api/admin/login",
            json={"email": admin.email, "password": "adminTempPass1"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is True
    finally:
        _drop(admin.id)


def test_admin_me_response_includes_must_change_password():
    admin = _make_user("admin", "adminTempPass1", must_change=True)
    try:
        c = TestClient(app)
        login = c.post(
            "/api/admin/login",
            json={"email": admin.email, "password": "adminTempPass1"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        me = c.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["must_change_password"] is True
    finally:
        _drop(admin.id)

"""
Tests for POST /api/admin/users (renamed and generalized from
POST /api/admin/hrs). The endpoint can now create either an HR or
an admin; the role is supplied in the request body.

Coverage:
  - happy path: create HR
  - happy path: create admin
  - role defaults to "hr" if omitted (back-compat with the old shape)
  - duplicate email collision regardless of which role currently owns
    the email — neither HR-over-admin nor admin-over-HR is allowed
  - validation: invalid role string is rejected by Pydantic (422)
  - auth: unauthenticated request gets 401
  - auth: HR-token request gets 401 (only admins can create users)
  - auth: must_change_password admin gets 403 (strict-auth dependency)

The welcome-email helper is patched out; we assert it was called with
the right role keyword without actually hitting SMTP.
"""
import secrets
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from auth import hash_password
from database import SessionLocal
from main import app
from models import HRAdmin


def _make_user(
    role: str,
    password: str = "testpass123",
    must_change: bool = False,
) -> HRAdmin:
    db = SessionLocal()
    user = HRAdmin(
        name=f"Setup {role}",
        email=f"setup-{role}-{secrets.token_hex(4)}@example.com",
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def _drop_user(uid: int) -> None:
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
    if user is not None:
        db.delete(user)
        db.commit()
    db.close()


def _drop_user_by_email(email: str) -> None:
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.email == email).first()
    if user is not None:
        db.delete(user)
        db.commit()
    db.close()


def _login_admin_client(email: str, password: str = "testpass123") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"admin login failed: {r.text}"
    return c


def _login_hr_client(email: str, password: str = "testpass123") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/hr/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"hr login failed: {r.text}"
    return c


# ----------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------

def test_create_hr_via_users_endpoint():
    admin = _make_user("admin")
    new_email = f"created-hr-{secrets.token_hex(4)}@example.com"
    try:
        c = _login_admin_client(admin.email)
        with patch("email_service.send_user_welcome_email", return_value=(True, None)) as mock_email:
            r = c.post("/api/admin/users", json={
                "name": "Alice HR",
                "email": new_email,
                "password": "Alicepw1!",
                "role": "hr",
            })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Alice HR"
        assert body["email"] == new_email
        assert body["role"] == "hr"
        assert body["email_status"] == "sent"
        assert body["email_error"] is None
        # Email helper got the role kwarg.
        assert mock_email.called
        kwargs = mock_email.call_args.kwargs
        assert kwargs["role"] == "hr"
        assert kwargs["user_email"] == new_email
        assert kwargs["plaintext_password"] == "Alicepw1!"
        # DB row exists with role=hr.
        db = SessionLocal()
        row = db.query(HRAdmin).filter(HRAdmin.email == new_email).first()
        assert row is not None
        assert row.role == "hr"
        db.close()
    finally:
        _drop_user_by_email(new_email)
        _drop_user(admin.id)


def test_create_admin_via_users_endpoint():
    admin = _make_user("admin")
    new_email = f"created-admin-{secrets.token_hex(4)}@example.com"
    try:
        c = _login_admin_client(admin.email)
        with patch("email_service.send_user_welcome_email", return_value=(True, None)) as mock_email:
            r = c.post("/api/admin/users", json={
                "name": "Bob Admin",
                "email": new_email,
                "password": "Bobpw1234!",
                "role": "admin",
            })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["role"] == "admin"
        assert mock_email.call_args.kwargs["role"] == "admin"
        # DB row exists with role=admin.
        db = SessionLocal()
        row = db.query(HRAdmin).filter(HRAdmin.email == new_email).first()
        assert row is not None
        assert row.role == "admin"
        db.close()
    finally:
        _drop_user_by_email(new_email)
        _drop_user(admin.id)


def test_role_defaults_to_hr_when_omitted():
    """Back-compat: callers that don't send `role` get an HR account
    (matches the old POST /hrs behavior)."""
    admin = _make_user("admin")
    new_email = f"created-default-{secrets.token_hex(4)}@example.com"
    try:
        c = _login_admin_client(admin.email)
        with patch("email_service.send_user_welcome_email", return_value=(True, None)):
            r = c.post("/api/admin/users", json={
                "name": "Default Role",
                "email": new_email,
                "password": "Defaultpw1!",
            })
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "hr"
    finally:
        _drop_user_by_email(new_email)
        _drop_user(admin.id)


# ----------------------------------------------------------------------
# Email collisions (symmetric)
# ----------------------------------------------------------------------

def test_create_user_rejects_email_already_used_by_hr():
    admin = _make_user("admin")
    existing_hr = _make_user("hr")
    try:
        c = _login_admin_client(admin.email)
        r = c.post("/api/admin/users", json={
            "name": "Dup HR",
            "email": existing_hr.email,
            "password": "Duppw1234!",
            "role": "admin",
        })
        assert r.status_code == 409
        assert "HR account" in r.json()["detail"]
    finally:
        _drop_user(existing_hr.id)
        _drop_user(admin.id)


def test_create_user_rejects_email_already_used_by_admin():
    admin = _make_user("admin")
    existing_admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.post("/api/admin/users", json={
            "name": "Dup Admin",
            "email": existing_admin.email,
            "password": "Duppw1234!",
            "role": "hr",
        })
        assert r.status_code == 409
        assert "admin account" in r.json()["detail"]
    finally:
        _drop_user(existing_admin.id)
        _drop_user(admin.id)


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

def test_create_user_rejects_invalid_role():
    admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.post("/api/admin/users", json={
            "name": "Bad Role",
            "email": f"bad-role-{secrets.token_hex(4)}@example.com",
            "password": "Badrole12!",
            "role": "superuser",
        })
        assert r.status_code == 422
    finally:
        _drop_user(admin.id)


def test_create_user_rejects_short_password():
    admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.post("/api/admin/users", json={
            "name": "Short Pw",
            "email": f"short-pw-{secrets.token_hex(4)}@example.com",
            "password": "x",  # below the 6-char minimum
            "role": "hr",
        })
        assert r.status_code == 422
    finally:
        _drop_user(admin.id)


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------

def test_create_user_unauthenticated_returns_401():
    c = TestClient(app)
    r = c.post("/api/admin/users", json={
        "name": "No Auth",
        "email": f"no-auth-{secrets.token_hex(4)}@example.com",
        "password": "Noauth123!",
        "role": "hr",
    })
    assert r.status_code == 401


def test_create_user_hr_token_returns_401():
    """An HR session can read /me but cannot create users — that's an
    admin-only endpoint."""
    hr = _make_user("hr")
    try:
        c = _login_hr_client(hr.email)
        r = c.post("/api/admin/users", json={
            "name": "HR Bypass",
            "email": f"hr-bypass-{secrets.token_hex(4)}@example.com",
            "password": "Hrbypass1!",
            "role": "hr",
        })
        # require_admin_strict treats a non-admin session the same as no
        # session — 401, not 403.
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)


def test_create_user_must_change_admin_returns_403():
    """An admin still on a temp password gets a 403 with the
    must_change_password code (require_admin_strict guard)."""
    admin = _make_user("admin", must_change=True)
    try:
        c = _login_admin_client(admin.email)
        r = c.post("/api/admin/users", json={
            "name": "Forced Change",
            "email": f"forced-change-{secrets.token_hex(4)}@example.com",
            "password": "Forced123!",
            "role": "hr",
        })
        assert r.status_code == 403
        body = r.json()["detail"]
        assert body["code"] == "must_change_password"
    finally:
        _drop_user(admin.id)

"""
Tests for DELETE /api/admin/users/{user_id} (soft-delete an HR).

Coverage:
  - happy path: HR is soft-deleted (deleted_at set, row still in DB)
  - the HR vanishes from GET /api/admin/users
  - the HR cannot log in after deletion (generic creds error)
  - existing JWT tokens issued before deletion stop working
  - admin targets are refused with 400
  - already-deleted targets return 404 (idempotent surface)
  - non-existent ids return 404
  - HR token cannot call this endpoint (admin-only)
  - unauthenticated requests get 401
  - email becomes reusable after deletion (partial unique index)
  - the HR's invitations still exist in the DB (preservation)
"""
import secrets
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from auth import hash_password
from database import SessionLocal
from main import app
from models import HRAdmin, Invitation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_user(role: str, password: str = "testpass123") -> HRAdmin:
    db = SessionLocal()
    user = HRAdmin(
        name=f"Soft {role}",
        email=f"soft-{role}-{secrets.token_hex(4)}@example.com",
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def _make_invitation(hr_id: int) -> int:
    db = SessionLocal()
    now = _utcnow()
    inv = Invitation(
        token=secrets.token_urlsafe(24),
        candidate_email=f"cand-{secrets.token_hex(4)}@example.com",
        candidate_name="Test Cand",
        difficulty="intermediate",
        hr_admin_id=hr_id,
        valid_from=now,
        expires_at=now,
        access_code="123456",
    )
    db.add(inv)
    db.commit()
    inv_id = inv.id
    db.close()
    return inv_id


def _drop_user(uid: int) -> None:
    """Hard-delete for test cleanup. Goes through ORM so cascade fires
    on invitations etc."""
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
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

def test_delete_hr_soft_deletes_and_preserves_invitations():
    admin = _make_user("admin")
    hr = _make_user("hr")
    inv_id = _make_invitation(hr.id)
    try:
        c = _login_admin_client(admin.email)
        r = c.delete(f"/api/admin/users/{hr.id}")
        assert r.status_code == 204, r.text

        # Row is still in the DB but flagged.
        db = SessionLocal()
        row = db.query(HRAdmin).filter(HRAdmin.id == hr.id).first()
        assert row is not None
        assert row.deleted_at is not None
        # Invitation is preserved — that's the whole point of soft delete.
        inv = db.query(Invitation).filter(Invitation.id == inv_id).first()
        assert inv is not None
        db.close()
    finally:
        _drop_user(hr.id)
        _drop_user(admin.id)


def test_deleted_hr_hidden_from_users_list():
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        c = _login_admin_client(admin.email)
        # Pre-delete: HR appears.
        rows = c.get("/api/admin/users").json()
        ids = [r["id"] for r in rows]
        assert hr.id in ids

        c.delete(f"/api/admin/users/{hr.id}")

        # Post-delete: HR is gone.
        rows = c.get("/api/admin/users").json()
        ids = [r["id"] for r in rows]
        assert hr.id not in ids
    finally:
        _drop_user(hr.id)
        _drop_user(admin.id)


def test_deleted_hr_cannot_login():
    admin = _make_user("admin")
    hr = _make_user("hr", password="hrpass1234")
    try:
        admin_client = _login_admin_client(admin.email)
        admin_client.delete(f"/api/admin/users/{hr.id}")

        # Login with the (correct) credentials must now fail with the
        # same generic error a non-existent email would produce.
        c = TestClient(app)
        r = c.post("/api/hr/login", json={
            "email": hr.email,
            "password": "hrpass1234",
        })
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)
        _drop_user(admin.id)


def test_existing_jwt_invalidated_after_delete():
    """A JWT that was minted before the HR was deleted should stop
    working immediately — auth.py filters deleted rows."""
    admin = _make_user("admin")
    hr = _make_user("hr", password="hrpass1234")
    try:
        # HR logs in and gets a fresh JWT.
        hr_client = _login_hr_client(hr.email, password="hrpass1234")
        # Confirm the token works before deletion.
        r = hr_client.get("/api/hr/me")
        assert r.status_code == 200

        # Admin deletes the HR.
        admin_client = _login_admin_client(admin.email)
        admin_client.delete(f"/api/admin/users/{hr.id}")

        # The HR's already-issued JWT now fails with 401.
        r = hr_client.get("/api/hr/me")
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)
        _drop_user(admin.id)


def test_email_reusable_after_soft_delete():
    """Partial unique index lets us POST /api/admin/users with the
    same email after the original was soft-deleted."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    reused_email = hr.email
    new_user_id = None
    try:
        c = _login_admin_client(admin.email)
        c.delete(f"/api/admin/users/{hr.id}")

        # Re-create with the same email — should succeed (201, not 409).
        from unittest.mock import patch
        with patch("email_service.send_user_welcome_email", return_value=(True, None)):
            r = c.post("/api/admin/users", json={
                "name": "Reborn HR",
                "email": reused_email,
                "password": "rebornpw",
                "role": "hr",
            })
        assert r.status_code == 201, r.text
        new_user_id = r.json()["id"]
        assert new_user_id != hr.id  # fresh row
    finally:
        if new_user_id:
            _drop_user(new_user_id)
        _drop_user(hr.id)
        _drop_user(admin.id)


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------

def test_delete_admin_target_returns_400():
    admin = _make_user("admin")
    other_admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.delete(f"/api/admin/users/{other_admin.id}")
        assert r.status_code == 400
    finally:
        _drop_user(other_admin.id)
        _drop_user(admin.id)


def test_delete_already_deleted_returns_404():
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        c = _login_admin_client(admin.email)
        # First delete — succeeds.
        r1 = c.delete(f"/api/admin/users/{hr.id}")
        assert r1.status_code == 204
        # Second delete — 404 (idempotent surface).
        r2 = c.delete(f"/api/admin/users/{hr.id}")
        assert r2.status_code == 404
    finally:
        _drop_user(hr.id)
        _drop_user(admin.id)


def test_delete_non_existent_returns_404():
    admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.delete("/api/admin/users/999999999")
        assert r.status_code == 404
    finally:
        _drop_user(admin.id)


def test_delete_hr_token_returns_401():
    """An HR session can't soft-delete anyone — admin-only endpoint."""
    hr = _make_user("hr")
    try:
        c = _login_hr_client(hr.email)
        r = c.delete(f"/api/admin/users/{hr.id}")
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)


def test_delete_unauthenticated_returns_401():
    c = TestClient(app)
    r = c.delete("/api/admin/users/1")
    assert r.status_code == 401

"""
Tests for the strict-auth dependencies that reject users on a temp
credential (must_change_password=True).

These deps wrap the existing require_hr / require_admin and add a 403
gate. They're applied to every authenticated route EXCEPT the allow-
list — /me, /change-password, /refresh, /logout — so that a user
holding a temp password from the email cannot access the rest of the
API even if they bypass the frontend's route guard.

Tests call the dep functions directly (the standard FastAPI test
pattern) rather than mounting routes; the strict variants are pure
wrappers and don't introduce any new I/O to integration-test.
"""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from auth import (
    hash_password,
    require_hr,
    require_admin,
    require_hr_strict,
    require_admin_strict,
)
from database import SessionLocal
from models import HRAdmin


@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def hr_user(db_session):
    hr = HRAdmin(
        name="Strict HR",
        email=f"strict-hr-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("testpass123"),
        role="hr",
        must_change_password=False,
    )
    db_session.add(hr)
    db_session.commit()
    db_session.refresh(hr)
    yield hr
    db_session.query(HRAdmin).filter(HRAdmin.id == hr.id).delete()
    db_session.commit()


@pytest.fixture
def admin_user(db_session):
    admin = HRAdmin(
        name="Strict Admin",
        email=f"strict-admin-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("testpass123"),
        role="admin",
        must_change_password=False,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    yield admin
    db_session.query(HRAdmin).filter(HRAdmin.id == admin.id).delete()
    db_session.commit()


# ----------------------------------------------------------------------
# Strict deps reject when must_change_password=True
# ----------------------------------------------------------------------

def test_require_hr_strict_rejects_when_must_change(hr_user, db_session):
    hr_user.must_change_password = True
    db_session.commit()
    db_session.refresh(hr_user)

    with pytest.raises(HTTPException) as exc:
        # Call directly with an already-resolved user (skip the Depends
        # chain — that's tested separately by require_hr's own tests).
        require_hr_strict(hr_user)
    assert exc.value.status_code == 403
    assert isinstance(exc.value.detail, dict), (
        "Detail must be a dict so the frontend can read .code; got "
        f"{type(exc.value.detail).__name__}"
    )
    assert exc.value.detail.get("code") == "must_change_password"


def test_require_admin_strict_rejects_when_must_change(admin_user, db_session):
    admin_user.must_change_password = True
    db_session.commit()
    db_session.refresh(admin_user)

    with pytest.raises(HTTPException) as exc:
        require_admin_strict(admin_user)
    assert exc.value.status_code == 403
    assert exc.value.detail.get("code") == "must_change_password"


# ----------------------------------------------------------------------
# Strict deps allow when flag is False
# ----------------------------------------------------------------------

def test_require_hr_strict_allows_when_flag_false(hr_user):
    """Happy path: flag is false → strict dep returns the user unchanged."""
    result = require_hr_strict(hr_user)
    assert result.id == hr_user.id


def test_require_admin_strict_allows_when_flag_false(admin_user):
    result = require_admin_strict(admin_user)
    assert result.id == admin_user.id


# ----------------------------------------------------------------------
# Allow-list: NON-strict deps still let the user through even when
# must_change_password=True. This is what keeps /me, /change-password,
# /refresh, and /logout reachable so the user can actually clear the
# flag.
# ----------------------------------------------------------------------

def test_require_hr_still_allows_when_must_change(hr_user, db_session):
    """The non-strict dep is the allow-list dep. With must_change=True
    it MUST still return the user — otherwise /change-password would
    also be locked and the user could never clear the flag, creating
    a permanent lockout."""
    hr_user.must_change_password = True
    db_session.commit()
    db_session.refresh(hr_user)

    # Use the same _stub_request helper pattern as test_admin_portal.py.
    from types import SimpleNamespace
    request = SimpleNamespace(session={
        "hr_admin_id": hr_user.id,
        "pw_v": hr_user.password_changed_at.isoformat(),
    })
    result = require_hr(request, db_session)
    assert result.id == hr_user.id
    assert result.must_change_password is True


def test_require_admin_still_allows_when_must_change(admin_user, db_session):
    admin_user.must_change_password = True
    db_session.commit()
    db_session.refresh(admin_user)

    from types import SimpleNamespace
    request = SimpleNamespace(session={
        "hr_admin_id": admin_user.id,
        "pw_v": admin_user.password_changed_at.isoformat(),
    })
    result = require_admin(request, db_session)
    assert result.id == admin_user.id
    assert result.must_change_password is True


# ----------------------------------------------------------------------
# Integration: real routes return 403 when the flag is set
# ----------------------------------------------------------------------

def test_real_hr_route_returns_403_when_must_change(hr_user, db_session):
    """End-to-end check that a real (strict-protected) HR route blocks
    a session whose user has must_change_password=True. /api/hr/results
    is the canary — it's a typical HR endpoint not on the allow-list.

    Asserts the response shape too: 403 with body
    {"detail": {"code": "must_change_password", "message": ...}}.
    The frontend interceptor reads detail.code to detect this state."""
    from fastapi.testclient import TestClient
    from main import app

    # Seed the user with the flag set.
    hr_user.must_change_password = True
    db_session.commit()

    # Log in to get a session cookie. The flag does NOT block /login —
    # /login is anonymous.
    c = TestClient(app)
    r = c.post("/api/hr/login", json={"email": hr_user.email, "password": "testpass123"})
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is True

    # Now hit a strict-protected route — must be blocked.
    r2 = c.get("/api/hr/results")
    assert r2.status_code == 403, r2.text
    body = r2.json()
    # FastAPI wraps HTTPException(detail=dict) into top-level "detail".
    assert "detail" in body
    assert body["detail"].get("code") == "must_change_password"


def test_real_hr_me_route_still_works_when_must_change(hr_user, db_session):
    """The allow-list route /api/hr/me MUST still return 200 even when
    must_change_password=True — otherwise the frontend can't load
    enough state to render the change-password screen."""
    from fastapi.testclient import TestClient
    from main import app

    hr_user.must_change_password = True
    db_session.commit()

    c = TestClient(app)
    r = c.post("/api/hr/login", json={"email": hr_user.email, "password": "testpass123"})
    assert r.status_code == 200

    me = c.get("/api/hr/me")
    assert me.status_code == 200, me.text
    assert me.json()["must_change_password"] is True


def test_real_admin_route_returns_403_when_must_change(admin_user, db_session):
    """End-to-end: a typical admin-portal route (/api/admin/users) must
    block a session whose admin has must_change_password=True."""
    from fastapi.testclient import TestClient
    from main import app

    admin_user.must_change_password = True
    db_session.commit()

    c = TestClient(app)
    r = c.post("/api/admin/login", json={"email": admin_user.email, "password": "testpass123"})
    assert r.status_code == 200

    r2 = c.get("/api/admin/users")
    assert r2.status_code == 403, r2.text
    assert r2.json()["detail"].get("code") == "must_change_password"

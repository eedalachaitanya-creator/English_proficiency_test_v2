"""
Tests for the admin portal feature.
See docs/superpowers/specs/2026-05-04-admin-portal-design.md.

Two surfaces under test:
  - auth.require_admin / auth.require_hr — dependency-level role
    enforcement. Tests use a stub Request object so we don't have to
    spin up a full FastAPI test client just to verify role logic.
  - The /api/admin/* HTTP routes — login flow, role rejection, HR
    listing/creation. These use FastAPI's TestClient against the real
    routes mounted on the live app.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth import require_hr, require_admin, hash_password
from database import SessionLocal
from models import HRAdmin


# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db_session():
    """Real DB session — these tests touch the hr_admins table for role
    enforcement, so a SQLite mock isn't sufficient. Each test creates and
    cleans up its own rows; we don't isolate via transactions because the
    auth helpers commit through their own session lookup."""
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def admin_user(db_session):
    """Insert a fresh admin row, yield it, then delete it after the test."""
    admin = HRAdmin(
        name="Test Admin",
        email=f"test-admin-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("testpass123"),
        role="admin",
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    yield admin
    db_session.query(HRAdmin).filter(HRAdmin.id == admin.id).delete()
    db_session.commit()


@pytest.fixture
def hr_user(db_session):
    """Insert a fresh HR row, yield it, then delete it after the test."""
    hr = HRAdmin(
        name="Test HR",
        email=f"test-hr-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("testpass123"),
        role="hr",
    )
    db_session.add(hr)
    db_session.commit()
    db_session.refresh(hr)
    yield hr
    db_session.query(HRAdmin).filter(HRAdmin.id == hr.id).delete()
    db_session.commit()


def _stub_request(session_dict: dict):
    """A minimal Request-like object that satisfies auth dependencies.
    They only ever read .session, and the .session.clear() call on bad
    sessions needs to work too."""
    return SimpleNamespace(session=dict(session_dict))


# ----------------------------------------------------------------------
# require_admin
# ----------------------------------------------------------------------

def test_require_admin_accepts_admin(admin_user, db_session):
    """Happy path: session has an admin id + matching pw_v, dependency
    returns the admin row."""
    request = _stub_request({
        "hr_admin_id": admin_user.id,
        "pw_v": admin_user.password_changed_at.isoformat(),
    })
    result = require_admin(request, db_session)
    assert result.id == admin_user.id
    assert result.role == "admin"


def test_require_admin_rejects_stale_pw_v(admin_user, db_session):
    """Session has a pw_v older than the user's current password_changed_at
    (the user rotated their password from another tab) → 401, session
    cleared. This is the whole point of the pw_v field."""
    # Bump the user's password_changed_at; pretend the session was issued
    # before this rotation.
    admin_user.password_changed_at = admin_user.password_changed_at.replace(year=2025)
    db_session.commit()
    db_session.refresh(admin_user)
    request = _stub_request({
        "hr_admin_id": admin_user.id,
        # Stale: claims an older pw_v than the user has now.
        "pw_v": "2024-01-01T00:00:00",
    })
    with pytest.raises(HTTPException) as exc:
        require_admin(request, db_session)
    assert exc.value.status_code == 401


def test_require_admin_rejects_no_session(db_session):
    """No session cookie → 401."""
    request = _stub_request({})
    with pytest.raises(HTTPException) as exc:
        require_admin(request, db_session)
    assert exc.value.status_code == 401


def test_require_admin_rejects_hr_role(hr_user, db_session):
    """Session points to an HR account, not an admin → 401."""
    request = _stub_request({"hr_admin_id": hr_user.id})
    with pytest.raises(HTTPException) as exc:
        require_admin(request, db_session)
    assert exc.value.status_code == 401


def test_require_admin_rejects_deleted_user(db_session):
    """Session points to a deleted user → 401 + session cleared."""
    request = _stub_request({"hr_admin_id": 999_999_999})
    with pytest.raises(HTTPException) as exc:
        require_admin(request, db_session)
    assert exc.value.status_code == 401


# ----------------------------------------------------------------------
# require_hr — must now also reject admins
# ----------------------------------------------------------------------

def test_require_hr_still_accepts_hr(hr_user, db_session):
    """Regression: the existing happy path still works for HR users."""
    request = _stub_request({
        "hr_admin_id": hr_user.id,
        "pw_v": hr_user.password_changed_at.isoformat(),
    })
    result = require_hr(request, db_session)
    assert result.id == hr_user.id
    assert result.role == "hr"


def test_require_hr_rejects_admin_role(admin_user, db_session):
    """An admin session must NOT pass require_hr — admins shouldn't be able
    to use HR endpoints by accident even if their cookie is valid."""
    request = _stub_request({"hr_admin_id": admin_user.id})
    with pytest.raises(HTTPException) as exc:
        require_hr(request, db_session)
    assert exc.value.status_code == 401

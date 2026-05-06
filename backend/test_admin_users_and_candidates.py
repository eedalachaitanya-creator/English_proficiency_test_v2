"""
Tests for the admin-dashboard data endpoints:
  - GET /api/admin/users          (renamed from /api/admin/hrs)
  - GET /api/admin/hrs/{hr_id}/candidates

The first replaces the old HR-only listing with a full user list that
includes admins and an aggregate candidate_count per row. The second
mirrors /api/hr/results but takes any hr_id and slices server-side.
"""
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from auth import hash_password
from database import SessionLocal
from main import app
from models import HRAdmin, Invitation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_user(role: str, password: str = "testpass123", name_prefix: str = "U") -> HRAdmin:
    db = SessionLocal()
    user = HRAdmin(
        name=f"{name_prefix} {role}",
        email=f"adm-users-{role}-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def _make_invitation(hr_id: int, candidate_email: str | None = None) -> int:
    """Insert a minimal Invitation row for the given HR. Returns its id."""
    db = SessionLocal()
    now = _utcnow()
    inv = Invitation(
        token=secrets.token_urlsafe(24),
        candidate_email=candidate_email or f"cand-{secrets.token_hex(4)}@example.com",
        candidate_name="Test Candidate",
        difficulty="intermediate",
        hr_admin_id=hr_id,
        valid_from=now,
        expires_at=now + timedelta(hours=24),
        access_code="123456",
    )
    db.add(inv)
    db.commit()
    inv_id = inv.id
    db.close()
    return inv_id


def _drop_user(uid: int) -> None:
    """Delete the user AND any rows referencing them (invitations,
    scores, audio recordings, etc.). Uses session.delete() rather than
    a bulk-delete query so SQLAlchemy's cascade='all, delete-orphan'
    on HRAdmin.invitations actually fires — bulk deletes bypass the
    ORM and trigger the FK constraint."""
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


# ----------------------------------------------------------------------
# GET /api/admin/users
# ----------------------------------------------------------------------

def test_admin_users_endpoint_returns_admins_and_hrs():
    """Both roles must be visible to the admin. The old /api/admin/hrs
    only returned HRs; the new endpoint returns every hr_admins row."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/users")
        assert r.status_code == 200, r.text
        body = r.json()
        roles_by_id = {row["id"]: row["role"] for row in body}
        assert roles_by_id.get(admin.id) == "admin"
        assert roles_by_id.get(hr.id) == "hr"
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)


def test_admin_users_endpoint_includes_candidate_count():
    """For an HR with N invitations, candidate_count is N. For an admin,
    candidate_count is always 0."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    inv_ids = []
    try:
        # 3 invitations for the HR.
        for _ in range(3):
            inv_ids.append(_make_invitation(hr.id))

        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/users")
        assert r.status_code == 200
        rows_by_id = {row["id"]: row for row in r.json()}
        assert rows_by_id[hr.id]["candidate_count"] == 3
        assert rows_by_id[admin.id]["candidate_count"] == 0
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)  # cascade drops the invitations


def test_admin_users_endpoint_hr_with_zero_candidates_shows_zero_not_null():
    """A LEFT JOIN can return NULL for users with no invitations. The
    handler must COALESCE to 0 — otherwise Pydantic would reject the
    None and the whole response would 500."""
    admin = _make_user("admin")
    hr = _make_user("hr")  # no invitations
    try:
        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/users")
        assert r.status_code == 200
        rows_by_id = {row["id"]: row for row in r.json()}
        assert rows_by_id[hr.id]["candidate_count"] == 0
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)


def test_admin_users_endpoint_orders_admins_first_then_hrs():
    """The dashboard groups admin rows above HR rows so admins are easy
    to spot at a glance. Within each group the order is still newest-
    first by created_at."""
    # Insert two HRs and one admin in a deliberate order so chronological
    # ordering would interleave them. With proper grouping, the admin
    # row must appear ABOVE both HR rows even though it was inserted
    # in the middle.
    hr_first = _make_user("hr", name_prefix="HrFirst")
    admin = _make_user("admin", name_prefix="Adm")
    hr_second = _make_user("hr", name_prefix="HrSecond")
    try:
        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/users")
        assert r.status_code == 200
        rows = r.json()

        # Find the indices of our three test users in the response.
        # Other rows (real users from prior tests / dev data) may exist
        # in between; we only care about the relative order of OUR three.
        ids_in_order = [row["id"] for row in rows]
        idx_admin = ids_in_order.index(admin.id)
        idx_hr_first = ids_in_order.index(hr_first.id)
        idx_hr_second = ids_in_order.index(hr_second.id)

        # Admin must come above both HRs.
        assert idx_admin < idx_hr_first, (
            "admin row must precede HR rows; "
            f"got admin@{idx_admin}, hr_first@{idx_hr_first}"
        )
        assert idx_admin < idx_hr_second
        # Within HRs, hr_second was inserted later so it should appear
        # before hr_first (newest-first).
        assert idx_hr_second < idx_hr_first
    finally:
        _drop_user(admin.id)
        _drop_user(hr_first.id)
        _drop_user(hr_second.id)


def test_admin_users_endpoint_requires_admin_session():
    """Anonymous → 401."""
    c = TestClient(app)
    r = c.get("/api/admin/users")
    assert r.status_code == 401


def test_admin_users_endpoint_rejects_hr_session():
    """HR sessions must NOT be able to use this endpoint."""
    hr = _make_user("hr")
    try:
        c = TestClient(app)
        login = c.post("/api/hr/login", json={"email": hr.email, "password": "testpass123"})
        assert login.status_code == 200
        # HR sessions reach the admin endpoint via the same cookie key,
        # but require_admin_strict checks role and returns 401.
        r = c.get("/api/admin/users")
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)


# ----------------------------------------------------------------------
# GET /api/admin/hrs/{hr_id}/candidates
# ----------------------------------------------------------------------

def test_admin_hr_candidates_returns_paginated_results():
    """Happy path: an HR with 5 candidates, page_size=2 → 3 pages.
    Verifies total, page slicing, and item count."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        for _ in range(5):
            _make_invitation(hr.id)

        c = _login_admin_client(admin.email)
        r = c.get(f"/api/admin/hrs/{hr.id}/candidates?page=1&page_size=2")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert len(body["items"]) == 2

        r3 = c.get(f"/api/admin/hrs/{hr.id}/candidates?page=3&page_size=2")
        assert r3.status_code == 200
        # Last page has 1 item (5 % 2 = 1 trailing).
        assert len(r3.json()["items"]) == 1
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)


def test_admin_hr_candidates_default_page_size_is_25():
    """Both query params should be optional. Defaults: page=1, page_size=25."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        # 10 invitations — well under the 25 default.
        for _ in range(10):
            _make_invitation(hr.id)

        c = _login_admin_client(admin.email)
        r = c.get(f"/api/admin/hrs/{hr.id}/candidates")
        assert r.status_code == 200
        body = r.json()
        assert body["page"] == 1
        assert body["page_size"] == 25
        assert body["total"] == 10
        assert len(body["items"]) == 10
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)


def test_admin_hr_candidates_caps_page_size_at_100():
    """A misbehaving client requesting page_size=999 must get capped to
    100 — defends against accidental megabyte responses."""
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        c = _login_admin_client(admin.email)
        r = c.get(f"/api/admin/hrs/{hr.id}/candidates?page_size=999")
        assert r.status_code == 200
        assert r.json()["page_size"] == 100
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)


def test_admin_hr_candidates_404_for_unknown_hr():
    admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/hrs/999999999/candidates")
        assert r.status_code == 404
    finally:
        _drop_user(admin.id)


def test_admin_hr_candidates_404_for_admin_id():
    """Admins don't have candidates. Hitting /candidates with an admin
    id returns 404 (same shape as unknown id) — admin existence is
    not actionable for the candidate listing."""
    admin = _make_user("admin")
    other_admin = _make_user("admin")
    try:
        c = _login_admin_client(admin.email)
        r = c.get(f"/api/admin/hrs/{other_admin.id}/candidates")
        assert r.status_code == 404
    finally:
        _drop_user(admin.id)
        _drop_user(other_admin.id)


def test_admin_hr_candidates_requires_admin_session():
    c = TestClient(app)
    r = c.get("/api/admin/hrs/1/candidates")
    assert r.status_code == 401


def test_admin_hr_candidates_rejects_hr_session():
    """An HR cannot see another HR's candidates via this endpoint —
    even if they guess the path. require_admin_strict gates the route."""
    hr = _make_user("hr")
    try:
        c = TestClient(app)
        c.post("/api/hr/login", json={"email": hr.email, "password": "testpass123"})
        r = c.get(f"/api/admin/hrs/{hr.id}/candidates")
        assert r.status_code == 401
    finally:
        _drop_user(hr.id)


# ----------------------------------------------------------------------
# Strict-auth gate
# ----------------------------------------------------------------------

def test_admin_users_403_when_must_change_password():
    """Defense-in-depth: even with a valid admin session, must_change=True
    locks the data endpoint."""
    admin = _make_user("admin")
    try:
        db = SessionLocal()
        target = db.query(HRAdmin).filter(HRAdmin.id == admin.id).first()
        target.must_change_password = True
        db.commit()
        db.close()

        c = _login_admin_client(admin.email)
        r = c.get("/api/admin/users")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "must_change_password"
    finally:
        _drop_user(admin.id)


def test_admin_hr_candidates_403_when_must_change_password():
    admin = _make_user("admin")
    hr = _make_user("hr")
    try:
        db = SessionLocal()
        target = db.query(HRAdmin).filter(HRAdmin.id == admin.id).first()
        target.must_change_password = True
        db.commit()
        db.close()

        c = _login_admin_client(admin.email)
        r = c.get(f"/api/admin/hrs/{hr.id}/candidates")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "must_change_password"
    finally:
        _drop_user(admin.id)
        _drop_user(hr.id)

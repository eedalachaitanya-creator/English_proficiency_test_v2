"""
Tests for POST /api/hr/invite/{id}/resend-email.

The endpoint is now a window-aware resend: HR picks a new
{valid_from, valid_until, timezone} and the invitation row's window
columns are updated in place. Token, access_code, lockout state, and
start counters are deliberately preserved — they belong to the
candidate, not the email-send action.
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


def _login_hr_client(email: str, password: str = "testpass123") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/hr/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


def _make_hr() -> HRAdmin:
    db = SessionLocal()
    hr = HRAdmin(
        name="Resend HR",
        email=f"resend-hr-{datetime.now(timezone.utc).timestamp()}@example.com",
        password_hash=hash_password("testpass123"),
        role="hr",
    )
    db.add(hr)
    db.commit()
    db.refresh(hr)
    db.close()
    return hr


def _make_invitation(
    hr_id: int,
    *,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    submitted: bool = False,
    failed_attempts: int = 0,
    start_count: int = 0,
) -> Invitation:
    """Insert a minimal Invitation row. Returns the persisted row.

    Defaults to a window starting 1 day ago and ending 23 hours later
    (i.e. expired) — matches the real-world scenario the resend feature
    exists to fix."""
    now = _utcnow()
    valid_from = valid_from or (now - timedelta(days=1))
    valid_until = valid_until or (now - timedelta(hours=1))

    db = SessionLocal()
    inv = Invitation(
        token=secrets.token_urlsafe(24),
        candidate_email=f"cand-{secrets.token_hex(4)}@example.com",
        candidate_name="Resend Candidate",
        difficulty="intermediate",
        hr_admin_id=hr_id,
        valid_from=valid_from,
        expires_at=valid_until,
        access_code="246810",
        failed_code_attempts=failed_attempts,
        start_count=start_count,
        display_timezone="Asia/Kolkata",
    )
    if submitted:
        inv.submitted_at = now
    db.add(inv)
    db.commit()
    db.refresh(inv)
    db.close()
    return inv


def _refresh_invitation(inv_id: int) -> Invitation:
    db = SessionLocal()
    inv = db.query(Invitation).filter(Invitation.id == inv_id).first()
    db.close()
    return inv


def _drop_user_with_invs(uid: int) -> None:
    db = SessionLocal()
    user = db.query(HRAdmin).filter(HRAdmin.id == uid).first()
    if user is not None:
        db.delete(user)  # cascade clears invitations
        db.commit()
    db.close()


def _future_window(start_in: timedelta = timedelta(hours=2),
                   length: timedelta = timedelta(hours=2)):
    """Build a (start_iso, end_iso) pair safely in the future. Uses 2h
    start so even with the 60s past-grace + the test runtime there's
    no risk of the start landing in the past mid-test."""
    start = datetime.now(timezone.utc) + start_in
    end = start + length
    return start.isoformat(), end.isoformat()


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------

def test_resend_with_new_window_updates_invitation_columns():
    """Posting a new window updates valid_from / expires_at /
    display_timezone on the same row. The token + access_code stay."""
    hr = _make_hr()
    inv = _make_invitation(hr.id)
    original_token = inv.token
    original_code = inv.access_code

    start_iso, end_iso = _future_window()
    body = {
        "valid_from": start_iso,
        "valid_until": end_iso,
        "timezone": "Asia/Kolkata",
    }

    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(f"/api/hr/invite/{inv.id}/resend-email", json=body)
    try:
        assert r.status_code == 200, r.text
        assert r.json()["email_status"] == "sent"

        refreshed = _refresh_invitation(inv.id)
        # Columns updated.
        assert refreshed.valid_from is not None
        assert refreshed.expires_at is not None
        assert refreshed.valid_from > _utcnow()
        assert refreshed.expires_at > refreshed.valid_from
        assert refreshed.display_timezone == "Asia/Kolkata"
        # Identity columns preserved.
        assert refreshed.token == original_token
        assert refreshed.access_code == original_code
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_does_not_change_token_or_access_code():
    """Even with a different timezone supplied, the token and access
    code are untouched — those stay with the candidate's URL."""
    hr = _make_hr()
    inv = _make_invitation(hr.id)
    original_token = inv.token
    original_code = inv.access_code

    start_iso, end_iso = _future_window()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "America/Los_Angeles"},
        )
    try:
        assert r.status_code == 200, r.text
        refreshed = _refresh_invitation(inv.id)
        assert refreshed.token == original_token
        assert refreshed.access_code == original_code
        assert refreshed.display_timezone == "America/Los_Angeles"
    finally:
        _drop_user_with_invs(hr.id)


# ----------------------------------------------------------------------
# Window validation — reuses _validate_window from invite-create
# ----------------------------------------------------------------------

def test_resend_rejects_past_start():
    hr = _make_hr()
    inv = _make_invitation(hr.id)

    # Start 1 hour in the past — beyond the 60s grace.
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)) as send_mock:
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start, "valid_until": end, "timezone": "Asia/Kolkata"},
        )
    try:
        assert r.status_code == 400
        assert "past" in r.json()["detail"].lower()
        # Email must NOT have been sent on a validation failure.
        assert not send_mock.called
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_rejects_inverted_window():
    hr = _make_hr()
    inv = _make_invitation(hr.id)

    # End before start.
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start, "valid_until": end, "timezone": "Asia/Kolkata"},
        )
    try:
        assert r.status_code == 400
        assert "after" in r.json()["detail"].lower()
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_rejects_too_short_window():
    hr = _make_hr()
    inv = _make_invitation(hr.id)

    # 30-minute window — under the 60-min floor.
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)).isoformat()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start, "valid_until": end, "timezone": "Asia/Kolkata"},
        )
    try:
        assert r.status_code == 400
        assert "60 minutes" in r.json()["detail"]
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_rejects_invalid_timezone():
    hr = _make_hr()
    inv = _make_invitation(hr.id)

    start_iso, end_iso = _future_window()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "Mars/Olympus"},
        )
    try:
        # _resolve_timezone returns 400 with the list of allowed values.
        assert r.status_code == 400
        assert "timezone" in r.json()["detail"].lower() or "Mars/Olympus" in r.json()["detail"]
    finally:
        _drop_user_with_invs(hr.id)


# ----------------------------------------------------------------------
# Existing guards
# ----------------------------------------------------------------------

def test_resend_after_submission_returns_410_and_does_not_mutate():
    """Existing 410 guard expanded: even with a valid new window,
    a submitted test cannot be resent and the row is untouched."""
    hr = _make_hr()
    inv = _make_invitation(hr.id, submitted=True)
    original_window_start = inv.valid_from
    original_tz = inv.display_timezone

    start_iso, end_iso = _future_window()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)) as send_mock:
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "America/New_York"},
        )
    try:
        assert r.status_code == 410
        # No email send for a submitted test.
        assert not send_mock.called
        # Window columns must be untouched.
        refreshed = _refresh_invitation(inv.id)
        assert refreshed.valid_from == original_window_start
        assert refreshed.display_timezone == original_tz
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_404_for_other_hrs_invitation():
    """Tenancy: HR-A cannot resend HR-B's invitations."""
    hr_a = _make_hr()
    hr_b = _make_hr()
    inv = _make_invitation(hr_b.id)  # belongs to hr_b

    start_iso, end_iso = _future_window()
    c = _login_hr_client(hr_a.email)
    r = c.post(
        f"/api/hr/invite/{inv.id}/resend-email",
        json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "Asia/Kolkata"},
    )
    try:
        assert r.status_code == 404
    finally:
        _drop_user_with_invs(hr_a.id)
        _drop_user_with_invs(hr_b.id)


# ----------------------------------------------------------------------
# State preservation
# ----------------------------------------------------------------------

def test_resend_preserves_start_count_and_failed_code_attempts():
    """The candidate's lockout / start counters belong to the candidate
    flow, not the email-send action. Resending must NOT silently reset
    them — that would be a way for HR to undo a code-locked state."""
    hr = _make_hr()
    inv = _make_invitation(hr.id, failed_attempts=3, start_count=1)

    start_iso, end_iso = _future_window()
    with patch("routes.hr.send_invitation_email", return_value=(True, None)):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "Asia/Kolkata"},
        )
    try:
        assert r.status_code == 200
        refreshed = _refresh_invitation(inv.id)
        assert refreshed.failed_code_attempts == 3
        assert refreshed.start_count == 1
    finally:
        _drop_user_with_invs(hr.id)


def test_resend_email_body_reflects_new_window():
    """The email helper receives the NEW dates, not the stale ones from
    the row. This is the whole feature — without it, candidates would
    keep getting emails advertising the old (expired) window."""
    hr = _make_hr()
    inv = _make_invitation(hr.id)

    start_iso, end_iso = _future_window(timedelta(hours=4), timedelta(hours=3))
    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return (True, None)

    with patch("routes.hr.send_invitation_email", side_effect=fake_send):
        c = _login_hr_client(hr.email)
        r = c.post(
            f"/api/hr/invite/{inv.id}/resend-email",
            json={"valid_from": start_iso, "valid_until": end_iso, "timezone": "America/Los_Angeles"},
        )
    try:
        assert r.status_code == 200
        # The send_invitation_email kwargs should carry the new window.
        assert captured["display_timezone"] == "America/Los_Angeles"
        # valid_from and valid_until are the naive UTC datetimes; verify
        # they're consistent with what we sent (timezone-aware → naive).
        sent_start = captured["valid_from"]
        sent_end = captured["valid_until"]
        assert sent_start > _utcnow()
        assert sent_end > sent_start
        assert (sent_end - sent_start).total_seconds() >= 60 * 60
    finally:
        _drop_user_with_invs(hr.id)


# ----------------------------------------------------------------------
# Body required (regression — old endpoint took no body, new one does)
# ----------------------------------------------------------------------

def test_resend_rejects_request_with_no_body():
    """Old endpoint behavior was a no-body POST; that no longer works.
    A missing body is a 422 (Pydantic validation), not silently doing
    the old behavior."""
    hr = _make_hr()
    inv = _make_invitation(hr.id)
    try:
        c = _login_hr_client(hr.email)
        r = c.post(f"/api/hr/invite/{inv.id}/resend-email")
        assert r.status_code == 422
    finally:
        _drop_user_with_invs(hr.id)

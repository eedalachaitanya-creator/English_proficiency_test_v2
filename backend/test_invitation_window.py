"""
Tests for routes.hr._validate_window — the pure helper that validates a
candidate's exam URL scheduling window before creating an Invitation.

See docs/superpowers/specs/2026-05-04-scheduled-url-validity-window-design.md.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from routes.hr import _validate_window


def _now() -> datetime:
    """Naive UTC matching backend's _utcnow_naive() helper convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_valid_60_minute_window_accepted():
    start = _now() + timedelta(minutes=5)
    end = start + timedelta(minutes=60)
    # Should not raise
    _validate_window(start, end)


def test_valid_long_window_accepted():
    start = _now() + timedelta(hours=2)
    end = start + timedelta(days=7)
    _validate_window(start, end)


def test_rejects_start_in_the_past():
    start = _now() - timedelta(hours=1)
    end = start + timedelta(hours=2)
    with pytest.raises(HTTPException) as exc:
        _validate_window(start, end)
    assert exc.value.status_code == 400
    assert "past" in exc.value.detail.lower()


def test_allows_start_within_grace_window_for_clock_skew():
    """1-minute grace lets a 'now' from the browser still be accepted even
    if the backend clock is a few seconds ahead. Without grace, the user
    would see a confusing rejection of what they just clicked."""
    start = _now() - timedelta(seconds=30)
    end = start + timedelta(hours=2)
    _validate_window(start, end)  # should not raise


def test_rejects_end_before_or_equal_to_start():
    start = _now() + timedelta(hours=1)
    # end == start
    with pytest.raises(HTTPException) as exc:
        _validate_window(start, start)
    assert exc.value.status_code == 400
    assert "after" in exc.value.detail.lower()
    # end < start
    with pytest.raises(HTTPException) as exc:
        _validate_window(start, start - timedelta(minutes=10))
    assert exc.value.status_code == 400


def test_rejects_window_shorter_than_60_minutes():
    """Test budget is 30+20+10 = 60 min total. A window shorter than that
    can't possibly fit the test, so HR can't accidentally schedule one."""
    start = _now() + timedelta(hours=1)
    end = start + timedelta(minutes=59)
    with pytest.raises(HTTPException) as exc:
        _validate_window(start, end)
    assert exc.value.status_code == 400
    assert "60 minutes" in exc.value.detail


def test_accepts_exactly_60_minute_window_at_boundary():
    """Boundary check — exactly 60 min should pass, not get rejected by
    a strict <= comparison."""
    start = _now() + timedelta(hours=1)
    end = start + timedelta(minutes=60)
    _validate_window(start, end)

"""
Tests for SystemSettings snapshot logic.

Two pure helpers tested here:
  - _settings_to_dict(row | None) → dict — converts a SystemSettings ORM
    row (or None for fresh-DB fallback) into a settings dict for snapshotting.
  - _can_start(start_count, max_starts) → bool — returns True if the
    candidate is allowed to verify the access code one more time.

See docs/superpowers/specs/2026-05-04-system-settings-runtime-config-design.md.
"""
from unittest.mock import MagicMock

from routes.hr import _settings_to_dict, _FALLBACK_SETTINGS
from routes.candidate import _can_start


# ------------------------------------------------------------------
# _settings_to_dict
# ------------------------------------------------------------------
def test_returns_fallback_when_row_is_none():
    """Defensive — fresh DB without the seed row, or row deleted manually."""
    out = _settings_to_dict(None)
    assert out == _FALLBACK_SETTINGS


def test_extracts_all_four_fields_from_row():
    row = MagicMock()
    row.max_starts = 3
    row.reading_seconds = 1500
    row.writing_seconds = 900
    row.speaking_seconds = 720
    out = _settings_to_dict(row)
    assert out == {
        "max_starts": 3,
        "reading_seconds": 1500,
        "writing_seconds": 900,
        "speaking_seconds": 720,
    }


def test_fallback_matches_historical_hardcoded_defaults():
    """If the migration is ever lost, the fallback must produce the same
    behavior as before the SystemSettings feature shipped."""
    assert _FALLBACK_SETTINGS["max_starts"] == 1
    assert _FALLBACK_SETTINGS["reading_seconds"] == 30 * 60
    assert _FALLBACK_SETTINGS["writing_seconds"] == 20 * 60
    assert _FALLBACK_SETTINGS["speaking_seconds"] == 10 * 60


# ------------------------------------------------------------------
# _can_start (counter-based start gate)
# ------------------------------------------------------------------
def test_first_start_allowed_when_max_is_one():
    """The pre-feature default: max_starts=1, start_count=0 → allow first start."""
    assert _can_start(start_count=0, max_starts=1) is True


def test_second_start_blocked_when_max_is_one():
    """After the candidate verifies once, start_count=1 == max_starts=1 → block."""
    assert _can_start(start_count=1, max_starts=1) is False


def test_allows_up_to_max_then_blocks():
    """max_starts=3 → 3 successful starts, then block on the 4th."""
    assert _can_start(0, 3) is True
    assert _can_start(1, 3) is True
    assert _can_start(2, 3) is True
    assert _can_start(3, 3) is False
    assert _can_start(4, 3) is False  # defensive — never below max post-clamp


def test_zero_max_starts_blocks_everything():
    """If HR sets max_starts=0 (self-inflicted misconfiguration), the URL is
    locked from the moment of invitation creation. Spec accepts this — HR
    has direct DB access and is trusted."""
    assert _can_start(0, 0) is False

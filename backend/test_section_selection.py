"""
Tests for per-invitation section selection (HR picks Reading/Writing/Speaking).
See docs/superpowers/specs/2026-05-04-per-invitation-section-selection-design.md.

Two surfaces under test here:
  - InviteCreateRequest validator: must reject "all three sections false"
    while accepting any other combination, and default to all-true if the
    fields are omitted (backwards compat).
  - compute_total scoring math: a partial test (only some sections taken)
    should report a final score normalized over the chosen sections, so
    rating bands keep meaning.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from schemas import InviteCreateRequest
from scoring import compute_total


# ----------------------------------------------------------------------
# Validator tests
# ----------------------------------------------------------------------

def _base_payload(**overrides) -> dict:
    """Minimal valid InviteCreateRequest payload + per-test overrides."""
    now = datetime.now(timezone.utc)
    payload = {
        "candidate_name": "Test Candidate",
        "candidate_email": "test@example.com",
        "difficulty": "intermediate",
        "valid_from": (now + timedelta(minutes=5)).isoformat(),
        "valid_until": (now + timedelta(hours=2)).isoformat(),
        "timezone": "Asia/Kolkata",
    }
    payload.update(overrides)
    return payload


def test_validator_accepts_all_three_omitted_defaults_to_all_true():
    """
    Old clients that don't send the section flags must still work.
    All three default to True so behavior is unchanged from pre-feature.
    """
    req = InviteCreateRequest(**_base_payload())
    assert req.include_reading is True
    assert req.include_writing is True
    assert req.include_speaking is True


def test_validator_accepts_two_true_one_false():
    """The common case: HR de-selects one section."""
    req = InviteCreateRequest(
        **_base_payload(
            include_reading=True,
            include_writing=True,
            include_speaking=False,
        )
    )
    assert req.include_speaking is False


def test_validator_accepts_only_one_section():
    """Reading-only is allowed (e.g., quick MCQ screen)."""
    req = InviteCreateRequest(
        **_base_payload(
            include_reading=True,
            include_writing=False,
            include_speaking=False,
        )
    )
    assert (req.include_reading, req.include_writing, req.include_speaking) == (True, False, False)


def test_validator_rejects_all_three_false():
    """
    A test with zero sections is meaningless. Validator must reject so
    HR can't accidentally generate a no-op URL.
    """
    with pytest.raises(ValidationError) as exc_info:
        InviteCreateRequest(
            **_base_payload(
                include_reading=False,
                include_writing=False,
                include_speaking=False,
            )
        )
    # Error message should make the failure mode obvious to HR.
    assert "at least one section" in str(exc_info.value).lower()


# ----------------------------------------------------------------------
# Scoring tests — partial-section totals
# ----------------------------------------------------------------------

def test_compute_total_one_section_only_reading():
    """
    HR selects only Reading. Candidate scores 60. Final total is 60 —
    weight redistribution gives that single section the full 100% weight.
    """
    total = compute_total(reading_score=60, writing_score=None, speaking_score=None)
    assert total == 60


def test_compute_total_two_sections_reading_and_writing():
    """
    HR selects Reading + Writing. Speaking is None (excluded).
    With 1/3 weights, each chosen section's effective weight is 0.5.
    Reading 60 + Writing 80 → 70.
    """
    total = compute_total(reading_score=60, writing_score=80, speaking_score=None)
    assert total == 70


def test_compute_total_two_sections_writing_and_speaking():
    """Symmetric — confirms redistribution is order-independent."""
    total = compute_total(reading_score=None, writing_score=70, speaking_score=90)
    assert total == 80


def test_compute_total_all_three_sections_unchanged():
    """Sanity check: full-test math still works (regression guard)."""
    # 60+75+90 averaged with 1/3 each = 75
    total = compute_total(reading_score=60, writing_score=75, speaking_score=90)
    assert total == 75

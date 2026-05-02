"""
Tests for routes.submit._validate_submission_reason — the pure helper that
maps an arbitrary form value into one of the canonical submission_reason
strings (or the default).

See docs/superpowers/specs/2026-05-01-global-test-timer-autosubmit-design.md.
"""
import pytest

from routes.submit import _validate_submission_reason


def test_known_value_passes_through_unchanged():
    assert _validate_submission_reason("reading_timer_expired") == "reading_timer_expired"
    assert _validate_submission_reason("writing_timer_expired") == "writing_timer_expired"
    assert _validate_submission_reason("speaking_timer_expired") == "speaking_timer_expired"
    assert _validate_submission_reason("tab_switch_termination") == "tab_switch_termination"
    assert _validate_submission_reason("candidate_finished") == "candidate_finished"


def test_none_defaults_to_candidate_finished():
    """No value sent (e.g. old client) → safe default, not an error."""
    assert _validate_submission_reason(None) == "candidate_finished"


def test_empty_string_defaults_to_candidate_finished():
    assert _validate_submission_reason("") == "candidate_finished"
    assert _validate_submission_reason("   ") == "candidate_finished"


def test_unknown_value_silently_coerces_to_default():
    """A bad value (typo, stale client, attacker) must NOT cause the submit
    endpoint to reject a real test submission. Coerce silently to default."""
    assert _validate_submission_reason("nonsense_value") == "candidate_finished"
    assert _validate_submission_reason("READING_TIMER_EXPIRED") == "candidate_finished"  # case-sensitive
    assert _validate_submission_reason("'; DROP TABLE invitations; --") == "candidate_finished"


def test_surrounding_whitespace_is_stripped_before_match():
    """Defensive — most form parsers don't leave whitespace, but we don't want
    a stray newline to silently coerce a real reason to default."""
    assert _validate_submission_reason("  reading_timer_expired  ") == "reading_timer_expired"
    assert _validate_submission_reason("\nspeaking_timer_expired\n") == "speaking_timer_expired"

# Property 5: Alert suppression idempotence
# Validates: Requirements 7.6

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from src.alert_manager import _is_suppressed


@given(
    window_hours=st.floats(min_value=0.1, max_value=48.0, allow_nan=False, allow_infinity=False),
    seconds_ago=st.floats(min_value=0.0, max_value=200000.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_suppression_within_window(window_hours, seconds_ago):
    """If last_sent was seconds_ago seconds ago, suppression matches whether seconds_ago < window_hours * 3600."""
    last_sent = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    result = _is_suppressed("key", last_sent, window_hours)
    expected = seconds_ago < window_hours * 3600
    assert result == expected


def test_suppression_none_last_sent():
    assert _is_suppressed("key", None, 4.0) is False


@given(
    window_hours=st.floats(min_value=0.1, max_value=48.0, allow_nan=False, allow_infinity=False),
    seconds_ago=st.floats(min_value=0.0, max_value=200000.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50)
def test_suppression_deterministic(window_hours, seconds_ago):
    last_sent = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    result1 = _is_suppressed("key", last_sent, window_hours)
    result2 = _is_suppressed("key", last_sent, window_hours)
    assert result1 == result2

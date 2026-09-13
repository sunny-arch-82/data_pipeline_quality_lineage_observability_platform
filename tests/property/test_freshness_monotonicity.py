# Property 2: Freshness state monotonicity
# Validates: Requirements 5.2, 5.3, 5.5

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.sla_monitor import classify_freshness


@given(
    elapsed=st.floats(min_value=0.001, max_value=1e9, allow_nan=False, allow_infinity=False),
    sla=st.floats(min_value=0.001, max_value=1e9, allow_nan=False, allow_infinity=False),
    warning_threshold=st.floats(
        min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=100)
def test_freshness_state_monotonicity(elapsed: float, sla: float, warning_threshold: float) -> None:
    """Property 2: For any valid (elapsed, sla, warning_threshold) triple the returned
    state must match the expected region and be exactly one of the three valid states."""
    state = classify_freshness(elapsed, sla, warning_threshold)

    # Assert correct region
    if elapsed <= sla * warning_threshold:
        assert state == "FRESH"
    elif elapsed <= sla:
        assert state == "WARNING"
    else:
        assert state == "SLA_BREACHED"

    # Assert mutual exclusivity — state is exactly one of the three
    assert state in ("FRESH", "WARNING", "SLA_BREACHED")


# ---------------------------------------------------------------------------
# Invalid-input tests
# ---------------------------------------------------------------------------


def test_classify_freshness_invalid_sla() -> None:
    with pytest.raises(ValueError):
        classify_freshness(100, sla=0)


def test_classify_freshness_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        classify_freshness(100, sla=3600, warning_threshold=0.0)
    with pytest.raises(ValueError):
        classify_freshness(100, sla=3600, warning_threshold=1.0)

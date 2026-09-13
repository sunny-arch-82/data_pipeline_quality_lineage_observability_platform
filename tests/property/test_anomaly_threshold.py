# Property 6: Anomaly threshold invariant
# Validates: Requirements 4.2, 4.4, 4.5

from hypothesis import given, settings
from hypothesis import strategies as st

from src.anomaly_detector import classify_anomaly


@given(
    history=st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=14,
        max_size=50,
    ),
    observed=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    threshold=st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_anomaly_threshold_invariant(history, observed, threshold):
    """For series >= 14 points, classification matches |observed - mean| > k * std."""
    result = classify_anomaly(observed, history, threshold)

    mean = sum(history) / len(history)
    variance = sum((v - mean) ** 2 for v in history) / len(history)
    std = variance ** 0.5

    expected = abs(observed - mean) > threshold * std
    assert result == expected


@given(
    history=st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=13,
    ),
    observed=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_insufficient_history_no_anomaly(history, observed):
    """Series with < 14 points never raises anomaly when no volume_threshold set."""
    result = classify_anomaly(observed, history, threshold_stddevs=3.0, volume_threshold=None)
    assert result is False


def test_volume_threshold_override():
    """Volume threshold fires even with < 14 data points."""
    # observed is way above threshold
    assert classify_anomaly(1000.0, history=[10.0, 20.0], volume_threshold=50.0) is True
    # observed is within threshold
    assert classify_anomaly(30.0, history=[10.0, 20.0], volume_threshold=50.0) is False

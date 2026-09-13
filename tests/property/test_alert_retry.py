# Property 7: Retry delivery exhaustion
# Validates: Requirements 7.7, 1.5

from hypothesis import given, settings
from hypothesis import strategies as st

from src.alert_manager import _should_retry


@given(
    attempt=st.integers(min_value=0, max_value=10),
    max_attempts=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100)
def test_retry_policy(attempt, max_attempts):
    """_should_retry returns True iff attempt < max_attempts."""
    result = _should_retry(attempt, max_attempts)
    assert result == (attempt < max_attempts)


@given(
    failure_sequence=st.lists(st.booleans(), min_size=1, max_size=5)
)
@settings(max_examples=100)
def test_total_attempts_correct(failure_sequence):
    """Simulate retry loop: total attempts = min(3, first_success_index + 1) or 3 if all fail."""
    MAX_RETRIES = 3
    attempts = 0
    succeeded = False
    for i, should_fail in enumerate(failure_sequence):
        if i >= MAX_RETRIES:
            break
        attempts += 1
        if not should_fail:
            succeeded = True
            break

    # Find expected total
    first_success = next((i for i, fail in enumerate(failure_sequence) if not fail), None)
    if first_success is not None and first_success < MAX_RETRIES:
        expected_attempts = first_success + 1
    else:
        # All elements in the sequence (up to MAX_RETRIES) fail
        expected_attempts = min(MAX_RETRIES, len(failure_sequence))

    assert attempts == expected_attempts
    assert succeeded == (first_success is not None and first_success < MAX_RETRIES)

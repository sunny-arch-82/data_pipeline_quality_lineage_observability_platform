"""Pure anomaly detection helpers for the Data Pipeline Quality, Lineage & Observability Platform."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from src.models import Anomaly


def _mean(values: list[float]) -> float:
    """Return arithmetic mean of values. Raises ValueError if empty."""
    if not values:
        raise ValueError("Cannot compute mean of empty list")
    return sum(values) / len(values)


def _std(values: list[float], mean: float) -> float:
    """Return population standard deviation. Returns 0.0 if fewer than 2 values."""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def classify_anomaly(
    observed: float,
    history: list[float],
    threshold_stddevs: float = 3.0,
    volume_threshold: float | None = None,
) -> bool:
    """Return True if observed is an anomaly.

    Rules:
    - If len(history) < 14 AND volume_threshold is None → return False (not enough data)
    - If len(history) < 14 AND volume_threshold is not None:
        → return True if abs(observed - mean(history)) > volume_threshold (if history non-empty)
        → return True if observed > volume_threshold (if history empty)
    - If len(history) >= 14:
        → compute mean and std of history
        → return True if abs(observed - mean) > threshold_stddevs * std
    """
    if len(history) < 14:
        if volume_threshold is None:
            return False
        # volume_threshold override for short history
        if not history:
            return observed > volume_threshold
        mean = _mean(history)
        return abs(observed - mean) > volume_threshold

    mean = _mean(history)
    std = _std(history, mean)
    return abs(observed - mean) > threshold_stddevs * std


def build_anomaly(
    table_name: str,
    column_name: str | None,
    metric_name: str,
    observed: float,
    history: list[float],
    threshold_stddevs: float = 3.0,
    volume_threshold: float | None = None,
) -> Anomaly | None:
    """Return an Anomaly if classify_anomaly returns True, else None.

    expected_min = mean - threshold_stddevs * std
    expected_max = mean + threshold_stddevs * std
    std_deviations = abs(observed - mean) / std if std > 0 else 0.0
    detection_timestamp = datetime.now(timezone.utc)
    """
    if not classify_anomaly(observed, history, threshold_stddevs, volume_threshold):
        return None

    if history:
        mean = _mean(history)
        std = _std(history, mean)
    else:
        mean = 0.0
        std = 0.0

    expected_min = mean - threshold_stddevs * std
    expected_max = mean + threshold_stddevs * std
    std_deviations = abs(observed - mean) / std if std > 0 else 0.0

    return Anomaly(
        table_name=table_name,
        column_name=column_name,
        metric_name=metric_name,
        observed_value=observed,
        expected_min=expected_min,
        expected_max=expected_max,
        std_deviations=std_deviations,
        detection_timestamp=datetime.now(timezone.utc),
    )

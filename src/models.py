"""Shared data models for the Data Pipeline Quality, Lineage & Observability Platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic models (contract definitions)
# ---------------------------------------------------------------------------


class ColumnContract(BaseModel):
    name: str
    type: str
    nullable: bool = True
    unique: bool = False


class DataContract(BaseModel):
    dataset: str
    columns: list[ColumnContract]
    freshness_sla: int | None = None  # seconds


# ---------------------------------------------------------------------------
# Dataclasses (runtime observability state)
# ---------------------------------------------------------------------------


@dataclass
class FreshnessStatus:
    dataset_name: str
    namespace: str
    state: Literal["FRESH", "WARNING", "SLA_BREACHED", "UNKNOWN"]
    last_run_time: datetime | None
    elapsed_seconds: float | None
    sla_seconds: float
    warning_threshold: float  # fraction of sla_seconds (default 0.8)
    breach_time: datetime | None
    recovery_time: datetime | None
    breach_duration_seconds: float | None


@dataclass
class ContractViolation:
    dataset_name: str
    violation_type: Literal["MISSING_COLUMN", "TYPE_MISMATCH", "NULLABILITY_VIOLATION"]
    column_name: str
    expected: str | None
    observed: str | None
    null_count: int | None
    run_timestamp: datetime


@dataclass
class Anomaly:
    table_name: str
    column_name: str | None
    metric_name: str  # row_count | null_rate | distinct_count
    observed_value: float
    expected_min: float
    expected_max: float
    std_deviations: float
    detection_timestamp: datetime


@dataclass
class TestFailure:
    model_name: str
    test_name: str
    failure_count: int
    timestamp: datetime
    message: str | None = None


@dataclass
class SLABreach:
    dataset_name: str
    namespace: str
    freshness_sla_seconds: float
    elapsed_seconds: float
    breach_time: datetime




@dataclass
class Incident:
    incident_id: str
    dataset: str
    failure_type: str
    severity: Literal["LOW", "HIGH", "CRITICAL"]
    affected_rows: int | None
    detected_at: datetime
    resolved_at: datetime | None
    downstream_impact_count: int
    details: dict


@dataclass
class IncidentImpact:
    asset_namespace: str
    asset_name: str
    asset_type: Literal["MODEL", "DATASET", "DASHBOARD"]
    depth: int


@dataclass
class ContractComplianceReport:
    violations: list[ContractViolation]
    run_timestamp: datetime

    def violations_by_dataset(self) -> dict[str, list[ContractViolation]]:
        result: dict[str, list[ContractViolation]] = {}
        for v in self.violations:
            result.setdefault(v.dataset_name, []).append(v)
        return result

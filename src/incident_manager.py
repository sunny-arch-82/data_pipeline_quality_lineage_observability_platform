"""Incident management and lineage-based blast-radius analysis.

The incident layer turns observability signals (quality failures, contract
violations, SLA breaches, anomalies) into durable operational incidents.  It
uses the Marquez lineage graph to identify downstream assets that may be
impacted and persists those assets alongside the incident for triage.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
import sqlalchemy
from sqlalchemy import text

from src.db_migrations import create_observability_schema
from src.models import Incident, IncidentImpact

logger = logging.getLogger(__name__)


_DASHBOARD_MARKERS = (
    "dashboard",
    "metabase",
    "looker",
    "tableau",
    "superset",
    "report",
)


def classify_incident_severity(
    failure_type: str,
    affected_rows: int | None,
    downstream_impact_count: int,
) -> str:
    """Classify an incident as LOW, HIGH, or CRITICAL.

    Rules are intentionally small and explainable:
    - CRITICAL: at least 5 downstream assets, or >= 1,000 affected rows.
    - HIGH: SLA/contract/pipeline failures, at least 2 downstream assets, or
      >= 100 affected rows.
    - LOW: isolated quality/anomaly failures below those thresholds.
    """
    rows = max(int(affected_rows or 0), 0)
    impacts = max(int(downstream_impact_count), 0)
    normalized = failure_type.upper()

    if impacts >= 5 or rows >= 1000:
        return "CRITICAL"
    if (
        normalized in {"SLA_BREACH", "CONTRACT_VIOLATION", "PIPELINE_FAILURE"}
        or impacts >= 2
        or rows >= 100
    ):
        return "HIGH"
    return "LOW"


def _asset_type(node_type: str, asset_name: str) -> str:
    """Map a Marquez node to a portfolio-friendly impact type."""
    lowered = asset_name.lower()
    if any(marker in lowered for marker in _DASHBOARD_MARKERS):
        return "DASHBOARD"
    if node_type.upper() == "JOB":
        return "MODEL"
    return "DATASET"


def _dataset_key(namespace: str, name: str) -> tuple[str, str]:
    return namespace or "", name or ""


def extract_downstream_impacts(
    graph: list[dict[str, Any]],
    root_namespace: str,
    root_dataset: str,
) -> list[IncidentImpact]:
    """Extract downstream assets from a Marquez lineage graph.

    Marquez lineage graph responses contain DATASET and JOB nodes.  JOB nodes
    carry ``inputs`` and ``outputs`` lists.  We build dataset -> job -> dataset
    edges and walk only in the downstream direction from the failed dataset.

    Dashboards can only be reported when they are represented in lineage; a
    simple name-based classifier labels known BI/report nodes as DASHBOARD.
    """
    jobs: list[dict[str, Any]] = []
    for node in graph:
        if str(node.get("type", "")).upper() == "JOB":
            jobs.append(node)

    root = _dataset_key(root_namespace, root_dataset)
    queue: deque[tuple[tuple[str, str], int]] = deque([(root, 0)])
    visited_datasets: set[tuple[str, str]] = {root}
    visited_jobs: set[str] = set()
    impacts: dict[tuple[str, str, str], IncidentImpact] = {}

    while queue:
        current_dataset, depth = queue.popleft()
        for job in jobs:
            data = job.get("data") or {}
            inputs = {
                _dataset_key(str(item.get("namespace", "")), str(item.get("name", "")))
                for item in (data.get("inputs") or [])
            }
            if current_dataset not in inputs:
                continue

            job_name = str(data.get("name") or (data.get("id") or {}).get("name") or "")
            job_namespace = str(
                data.get("namespace") or (data.get("id") or {}).get("namespace") or root_namespace
            )
            job_id = str(job.get("id") or f"job:{job_namespace}:{job_name}")
            next_depth = depth + 1

            if job_name and job_id not in visited_jobs:
                visited_jobs.add(job_id)
                impact = IncidentImpact(
                    asset_namespace=job_namespace,
                    asset_name=job_name,
                    asset_type=_asset_type("JOB", job_name),
                    depth=next_depth,
                )
                impacts[(impact.asset_namespace, impact.asset_name, impact.asset_type)] = impact

            for output in data.get("outputs") or []:
                out_namespace = str(output.get("namespace", ""))
                out_name = str(output.get("name", ""))
                if not out_name:
                    continue
                out_key = _dataset_key(out_namespace, out_name)
                impact = IncidentImpact(
                    asset_namespace=out_namespace,
                    asset_name=out_name,
                    asset_type=_asset_type("DATASET", out_name),
                    depth=next_depth,
                )
                impacts[(impact.asset_namespace, impact.asset_name, impact.asset_type)] = impact
                if out_key not in visited_datasets:
                    visited_datasets.add(out_key)
                    queue.append((out_key, next_depth))

    return sorted(
        impacts.values(),
        key=lambda item: (item.depth, item.asset_type, item.asset_namespace, item.asset_name),
    )


class IncidentManager:
    """Create, enrich, persist, query, and resolve data incidents."""

    def __init__(
        self,
        db_url: str,
        marquez_url: str,
        namespace: str,
        lineage_depth: int = 5,
    ) -> None:
        self._engine = sqlalchemy.create_engine(db_url)
        self.marquez_url = marquez_url.rstrip("/")
        self.namespace = namespace
        self.lineage_depth = max(int(lineage_depth), 1)
        create_observability_schema(self._engine)

    def get_blast_radius(self, dataset: str) -> list[IncidentImpact]:
        """Fetch Marquez lineage and return downstream impacted assets."""
        node_id = f"dataset:{self.namespace}:{dataset}"
        try:
            response = httpx.get(
                f"{self.marquez_url}/api/v1/lineage",
                params={"nodeId": node_id, "depth": self.lineage_depth},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            graph = payload.get("graph", []) if isinstance(payload, dict) else []
            if not isinstance(graph, list):
                return []
            return extract_downstream_impacts(graph, self.namespace, dataset)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Could not calculate blast radius for %s: %s", dataset, exc)
            return []

    def _get_open_incident(self, dataset: str, failure_type: str) -> Incident | None:
        query = text(
            """
            SELECT incident_id, dataset, failure_type, severity, affected_rows,
                   detected_at, resolved_at, downstream_impact_count, details
            FROM observability.incidents
            WHERE dataset = :dataset
              AND failure_type = :failure_type
              AND resolved_at IS NULL
            ORDER BY detected_at DESC
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                query,
                {"dataset": dataset, "failure_type": failure_type},
            ).mappings().first()
        return self._incident_from_row(row) if row else None

    def create_incident(
        self,
        dataset: str,
        failure_type: str,
        affected_rows: int | None = None,
        detected_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> Incident:
        """Create one open incident per dataset/failure type.

        Repeated observations of the same unresolved failure return the existing
        incident instead of opening duplicates on every polling cycle.
        """
        existing = self._get_open_incident(dataset, failure_type)
        if existing is not None:
            return existing

        detected = detected_at or datetime.now(timezone.utc)
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)

        impacts = self.get_blast_radius(dataset)
        severity = classify_incident_severity(
            failure_type,
            affected_rows,
            len(impacts),
        )
        incident = Incident(
            incident_id=str(uuid.uuid4()),
            dataset=dataset,
            failure_type=failure_type,
            severity=severity,  # type: ignore[arg-type]
            affected_rows=affected_rows,
            detected_at=detected,
            resolved_at=None,
            downstream_impact_count=len(impacts),
            details=details or {},
        )

        insert_incident = text(
            """
            INSERT INTO observability.incidents
                (incident_id, dataset, failure_type, severity, affected_rows,
                 detected_at, resolved_at, downstream_impact_count, details)
            VALUES
                (:incident_id, :dataset, :failure_type, :severity, :affected_rows,
                 :detected_at, :resolved_at, :downstream_impact_count,
                 CAST(:details AS JSONB))
            """
        )
        insert_impact = text(
            """
            INSERT INTO observability.incident_impacts
                (incident_id, asset_namespace, asset_name, asset_type, depth)
            VALUES
                (:incident_id, :asset_namespace, :asset_name, :asset_type, :depth)
            ON CONFLICT DO NOTHING
            """
        )

        with self._engine.begin() as conn:
            conn.execute(
                insert_incident,
                {
                    **asdict(incident),
                    "details": json.dumps(incident.details),
                },
            )
            for impact in impacts:
                conn.execute(
                    insert_impact,
                    {"incident_id": incident.incident_id, **asdict(impact)},
                )

        logger.warning(
            "Incident %s opened: dataset=%s type=%s severity=%s downstream=%d",
            incident.incident_id,
            incident.dataset,
            incident.failure_type,
            incident.severity,
            incident.downstream_impact_count,
        )
        return incident

    def resolve_incident(
        self,
        incident_id: str,
        resolved_at: datetime | None = None,
    ) -> Incident | None:
        """Resolve an incident and return its updated record."""
        resolved = resolved_at or datetime.now(timezone.utc)
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE observability.incidents
                    SET resolved_at = COALESCE(resolved_at, :resolved_at)
                    WHERE incident_id = :incident_id
                    """
                ),
                {"incident_id": incident_id, "resolved_at": resolved},
            )
        if result.rowcount == 0:
            return None
        return self.get_incident(incident_id)

    def resolve_open_incidents(self, dataset: str, failure_type: str) -> int:
        """Resolve all open incidents for a recovered dataset/failure type."""
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE observability.incidents
                    SET resolved_at = NOW()
                    WHERE dataset = :dataset
                      AND failure_type = :failure_type
                      AND resolved_at IS NULL
                    """
                ),
                {"dataset": dataset, "failure_type": failure_type},
            )
        return int(result.rowcount or 0)

    def get_incident(self, incident_id: str) -> Incident | None:
        query = text(
            """
            SELECT incident_id, dataset, failure_type, severity, affected_rows,
                   detected_at, resolved_at, downstream_impact_count, details
            FROM observability.incidents
            WHERE incident_id = :incident_id
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(query, {"incident_id": incident_id}).mappings().first()
        return self._incident_from_row(row) if row else None

    def list_incidents(self, open_only: bool = False, limit: int = 100) -> list[Incident]:
        where_clause = "WHERE resolved_at IS NULL" if open_only else ""
        query = text(
            f"""
            SELECT incident_id, dataset, failure_type, severity, affected_rows,
                   detected_at, resolved_at, downstream_impact_count, details
            FROM observability.incidents
            {where_clause}
            ORDER BY detected_at DESC
            LIMIT :limit
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query, {"limit": max(1, min(int(limit), 500))}).mappings().all()
        return [self._incident_from_row(row) for row in rows]

    def get_impacts(self, incident_id: str) -> list[IncidentImpact]:
        query = text(
            """
            SELECT asset_namespace, asset_name, asset_type, depth
            FROM observability.incident_impacts
            WHERE incident_id = :incident_id
            ORDER BY depth, asset_type, asset_namespace, asset_name
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query, {"incident_id": incident_id}).mappings().all()
        return [IncidentImpact(**dict(row)) for row in rows]

    @staticmethod
    def _incident_from_row(row: Any) -> Incident:
        raw_details = row["details"] or {}
        if isinstance(raw_details, str):
            try:
                raw_details = json.loads(raw_details)
            except json.JSONDecodeError:
                raw_details = {"raw": raw_details}
        return Incident(
            incident_id=str(row["incident_id"]),
            dataset=str(row["dataset"]),
            failure_type=str(row["failure_type"]),
            severity=str(row["severity"]),  # type: ignore[arg-type]
            affected_rows=row["affected_rows"],
            detected_at=row["detected_at"],
            resolved_at=row["resolved_at"],
            downstream_impact_count=int(row["downstream_impact_count"] or 0),
            details=dict(raw_details),
        )

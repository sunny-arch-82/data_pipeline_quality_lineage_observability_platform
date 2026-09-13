"""SLA and freshness monitoring for the Data Pipeline Quality, Lineage & Observability Platform."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy
import sqlalchemy.exc
from sqlalchemy import text

from src.models import FreshnessStatus

logger = logging.getLogger(__name__)


def classify_freshness(
    elapsed: float,
    sla: float,
    warning_threshold: float = 0.8,
) -> str:
    """Classify freshness state based on elapsed time vs SLA window.

    Args:
        elapsed: Seconds since last successful run.
        sla: SLA window in seconds (must be > 0).
        warning_threshold: Fraction of SLA at which WARNING is triggered (must be in (0, 1)).

    Returns:
        "FRESH", "WARNING", or "SLA_BREACHED".

    Raises:
        ValueError: If sla <= 0 or warning_threshold not in (0, 1).
    """
    if sla <= 0:
        raise ValueError(f"sla must be positive, got {sla}")
    if not (0 < warning_threshold < 1):
        raise ValueError(
            f"warning_threshold must be in (0, 1), got {warning_threshold}"
        )

    if elapsed <= sla * warning_threshold:
        return "FRESH"
    elif elapsed <= sla:
        return "WARNING"
    else:
        return "SLA_BREACHED"


class SLAMonitor:
    """Evaluates freshness SLA compliance by polling Marquez dataset versions."""

    def __init__(
        self,
        marquez_url: str,
        namespace: str,
        sla_config: list[dict],
        db_url: str,
    ) -> None:
        """Initialise the SLA monitor.

        Args:
            marquez_url: Base URL of the Marquez API (e.g. "http://marquez:5000").
            namespace: OpenLineage namespace for all datasets.
            sla_config: List of dicts with keys "name", "freshness_sla", "warning_threshold".
            db_url: SQLAlchemy-compatible database URL for writing sla_status.
        """
        self.marquez_url = marquez_url.rstrip("/")
        self.namespace = namespace
        self.sla_config = sla_config
        self._engine = sqlalchemy.create_engine(db_url)
        # In-memory cache of most recent evaluations keyed by dataset name
        self._statuses: dict[str, FreshnessStatus] = {}

        from src.db_migrations import create_observability_schema
        create_observability_schema(self._engine)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_last_run_time(self, dataset_name: str) -> datetime | None:
        """Query Marquez for the most recent run timestamp of a dataset.

        Returns the ``createdAt`` timestamp of the latest version, or ``None``
        if no versions exist or the Marquez API is unreachable.
        """
        url = (
            f"{self.marquez_url}/api/v1/namespaces/{self.namespace}"
            f"/datasets/{dataset_name}/versions"
        )
        try:
            response = httpx.get(url, timeout=10.0)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            versions: list[dict] = data.get("versions", [])
            if not versions:
                return None
            # Versions are returned newest-first; take the first entry
            created_at_str: str = versions[0].get("createdAt", "")
            if not created_at_str:
                return None
            return datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except httpx.ConnectError:
            logger.warning(
                "Could not connect to Marquez at %s while fetching versions for %s",
                self.marquez_url,
                dataset_name,
            )
            return None

    def _write_status(self, status: FreshnessStatus) -> None:
        """Upsert a FreshnessStatus record into observability.sla_status."""
        upsert_sql = text(
            """
            INSERT INTO observability.sla_status
                (dataset_name, namespace, state, last_run_time, elapsed_seconds,
                 sla_seconds, evaluated_at)
            VALUES
                (:dataset_name, :namespace, :state, :last_run_time, :elapsed_seconds,
                 :sla_seconds, :evaluated_at)
            ON CONFLICT (dataset_name, namespace)
            DO UPDATE SET
                state            = EXCLUDED.state,
                last_run_time    = EXCLUDED.last_run_time,
                elapsed_seconds  = EXCLUDED.elapsed_seconds,
                sla_seconds      = EXCLUDED.sla_seconds,
                evaluated_at     = EXCLUDED.evaluated_at
            """
        )
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    upsert_sql,
                    {
                        "dataset_name": status.dataset_name,
                        "namespace": status.namespace,
                        "state": status.state,
                        "last_run_time": status.last_run_time,
                        "elapsed_seconds": status.elapsed_seconds,
                        "sla_seconds": status.sla_seconds,
                        "evaluated_at": datetime.now(tz=timezone.utc),
                    },
                )
        except sqlalchemy.exc.OperationalError:
            logger.error(
                "Database error while writing SLA status for %s",
                status.dataset_name,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate_all(self) -> list[FreshnessStatus]:
        """Evaluate freshness for every configured dataset.

        For each dataset:
        1. Fetches the last run time from Marquez.
        2. Computes elapsed seconds (or marks UNKNOWN if no run exists).
        3. Classifies freshness state.
        4. Persists the result to the warehouse.

        Returns:
            List of FreshnessStatus objects for all configured datasets.
        """
        results: list[FreshnessStatus] = []
        now = datetime.now(tz=timezone.utc)

        for cfg in self.sla_config:
            name: str = cfg["name"]
            sla: float = float(cfg["freshness_sla"])
            threshold: float = float(cfg.get("warning_threshold", 0.8))

            last_run = self._get_last_run_time(name)

            if last_run is None:
                state = "UNKNOWN"
                elapsed = None
            else:
                # Ensure both datetimes are timezone-aware for subtraction
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                elapsed = (now - last_run).total_seconds()
                state = classify_freshness(elapsed, sla, threshold)

            freshness_status = FreshnessStatus(
                dataset_name=name,
                namespace=self.namespace,
                state=state,  # type: ignore[arg-type]
                last_run_time=last_run,
                elapsed_seconds=elapsed,
                sla_seconds=sla,
                warning_threshold=threshold,
                breach_time=now if state == "SLA_BREACHED" else None,
                recovery_time=None,
                breach_duration_seconds=None,
            )

            self._write_status(freshness_status)
            self._statuses[name] = freshness_status
            results.append(freshness_status)

        return results

    def get_status(self, dataset_name: str) -> FreshnessStatus | None:
        """Return the most recently evaluated FreshnessStatus for a dataset."""
        return self._statuses.get(dataset_name)

    def get_all_statuses(self) -> list[FreshnessStatus]:
        """Return all most recently evaluated FreshnessStatus objects."""
        return list(self._statuses.values())

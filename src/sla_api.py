"""FastAPI app for freshness monitoring and incident operations."""

from __future__ import annotations

import dataclasses
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query

from src.incident_manager import IncidentManager
from src.sla_monitor import SLAMonitor

logger = logging.getLogger(__name__)

_sla_monitor: SLAMonitor | None = None
_incident_manager: IncidentManager | None = None
_scheduler: BackgroundScheduler | None = None


def _load_sla_config(config_path: Path) -> list[dict]:
    """Load dataset SLA config from a YAML file."""
    if not config_path.exists():
        logger.warning("sla_config.yml not found at %s — using empty config", config_path)
        return []
    with config_path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("datasets", [])


def _evaluate_slas_and_sync_incidents() -> None:
    """Evaluate freshness and keep SLA incidents in sync with recovery state."""
    if _sla_monitor is None or _incident_manager is None:
        return
    statuses = _sla_monitor.evaluate_all()
    for status in statuses:
        if status.state == "SLA_BREACHED":
            _incident_manager.create_incident(
                dataset=status.dataset_name,
                failure_type="SLA_BREACH",
                detected_at=status.breach_time,
                details={
                    "elapsed_seconds": status.elapsed_seconds,
                    "sla_seconds": status.sla_seconds,
                },
            )
        elif status.state in {"FRESH", "WARNING"}:
            _incident_manager.resolve_open_incidents(status.dataset_name, "SLA_BREACH")


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Startup / shutdown lifecycle for the FastAPI app."""
    global _sla_monitor, _incident_manager, _scheduler

    marquez_url = os.environ.get("MARQUEZ_URL", "http://marquez:5000")
    namespace = os.environ.get("OPENLINEAGE_NAMESPACE", "data_pipeline_observability")
    db_url = os.environ.get(
        "WAREHOUSE_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )
    poll_interval = int(os.environ.get("SLA_POLL_INTERVAL_MINUTES", "60"))
    lineage_depth = int(os.environ.get("INCIDENT_LINEAGE_DEPTH", "5"))

    config_path = Path(os.environ.get("SLA_CONFIG_PATH", "sla_config.yml"))
    sla_config = _load_sla_config(config_path)

    _sla_monitor = SLAMonitor(
        marquez_url=marquez_url,
        namespace=namespace,
        sla_config=sla_config,
        db_url=db_url,
    )
    _incident_manager = IncidentManager(
        db_url=db_url,
        marquez_url=marquez_url,
        namespace=namespace,
        lineage_depth=lineage_depth,
    )

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _evaluate_slas_and_sync_incidents,
        trigger="interval",
        minutes=poll_interval,
        id="evaluate_all",
    )
    _scheduler.start()
    logger.info("APScheduler started — polling every %d minutes", poll_interval)

    yield

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


app = FastAPI(
    title="Data Pipeline Observability API",
    description="Freshness status, incidents, severity, and lineage blast-radius analysis.",
    lifespan=lifespan,
)


@app.get("/freshness")
def get_all_freshness() -> list[dict[str, Any]]:
    """Return freshness status for all monitored datasets."""
    if _sla_monitor is None:
        return []
    return [dataclasses.asdict(s) for s in _sla_monitor.get_all_statuses()]


@app.get("/freshness/{name:path}")
def get_freshness(name: str) -> dict[str, Any]:
    """Return freshness status for a single dataset, or 404 if not found."""
    if _sla_monitor is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    status = _sla_monitor.get_status(name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    return dataclasses.asdict(status)


@app.get("/incidents")
def get_incidents(
    open_only: bool = Query(False, description="Return only unresolved incidents"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List incidents newest first."""
    if _incident_manager is None:
        return []
    return [dataclasses.asdict(i) for i in _incident_manager.list_incidents(open_only, limit)]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    """Return one incident together with its persisted blast radius."""
    if _incident_manager is None:
        raise HTTPException(status_code=503, detail="Incident manager unavailable")
    incident = _incident_manager.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return {
        **dataclasses.asdict(incident),
        "downstream_impacts": [
            dataclasses.asdict(i) for i in _incident_manager.get_impacts(incident_id)
        ],
    }


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str) -> dict[str, Any]:
    """Mark an incident resolved and stamp its resolution time."""
    if _incident_manager is None:
        raise HTTPException(status_code=503, detail="Incident manager unavailable")
    incident = _incident_manager.resolve_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return dataclasses.asdict(incident)

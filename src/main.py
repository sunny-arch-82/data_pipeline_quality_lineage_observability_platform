"""Main entry point for the Data Pipeline Quality, Lineage & Observability Platform.

Startup sequence:
1. Validate required environment variables.
2. Create observability schema and incident tables.
3. Instantiate SLA, contract, alert, and incident services.
4. Convert contract/SLA failures into alerts and durable incidents.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from sqlalchemy import create_engine

from src.alert_manager import AlertManager
from src.config import load_config
from src.contract_enforcer import ContractEnforcer
from src.db_migrations import create_observability_schema
from src.incident_manager import IncidentManager
from src.models import SLABreach
from src.sla_monitor import SLAMonitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_sla_config(path: Path) -> list[dict]:
    if not path.exists():
        logger.warning("sla_config.yml not found at %s — SLA monitoring disabled", path)
        return []
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("datasets", [])


def build_services(
    config: dict,
) -> tuple[SLAMonitor, ContractEnforcer, AlertManager, IncidentManager]:
    """Instantiate and return all platform services."""
    db_url = config["WAREHOUSE_URL"]
    engine = create_engine(db_url)
    create_observability_schema(engine)

    sla_config = _load_sla_config(Path(os.environ.get("SLA_CONFIG_PATH", "sla_config.yml")))

    sla_monitor = SLAMonitor(
        marquez_url=config["MARQUEZ_URL"],
        namespace=config["OPENLINEAGE_NAMESPACE"],
        sla_config=sla_config,
        db_url=db_url,
    )

    contract_enforcer = ContractEnforcer(db_url=db_url)
    contract_enforcer.load_contracts(Path("contracts"))

    alert_manager = AlertManager(
        channel=config["ALERT_CHANNEL"],
        slack_webhook_url=config.get("SLACK_WEBHOOK_URL"),
        smtp_host=config.get("SMTP_HOST"),
        smtp_port=int(config.get("SMTP_PORT", 587)),
        alert_email_to=config.get("ALERT_EMAIL_TO"),
        suppression_window_hours=float(config.get("ALERT_SUPPRESSION_WINDOW_HOURS", 4)),
        db_url=db_url,
    )

    incident_manager = IncidentManager(
        db_url=db_url,
        marquez_url=config["MARQUEZ_URL"],
        namespace=config["OPENLINEAGE_NAMESPACE"],
        lineage_depth=int(os.environ.get("INCIDENT_LINEAGE_DEPTH", "5")),
    )

    return sla_monitor, contract_enforcer, alert_manager, incident_manager


def run_contract_checks(
    contract_enforcer: ContractEnforcer,
    alert_manager: AlertManager,
    incident_manager: IncidentManager,
) -> None:
    """Run contract validation and create incidents for violations."""
    report = contract_enforcer.validate_all()
    for violation in report.violations:
        incident_manager.create_incident(
            dataset=violation.dataset_name,
            failure_type="CONTRACT_VIOLATION",
            affected_rows=violation.null_count,
            detected_at=violation.run_timestamp,
            details={
                "violation_type": violation.violation_type,
                "column_name": violation.column_name,
                "expected": violation.expected,
                "observed": violation.observed,
            },
        )
        alert_manager.send_contract_alert(violation)
    if report.violations:
        logger.warning("Contract violations found: %d", len(report.violations))
    else:
        logger.info("All contract checks passed")


def run_sla_checks(
    sla_monitor: SLAMonitor,
    alert_manager: AlertManager,
    incident_manager: IncidentManager,
) -> None:
    """Evaluate freshness, open breach incidents, and resolve recovered ones."""
    statuses = sla_monitor.evaluate_all()
    for status in statuses:
        if status.state == "SLA_BREACHED":
            breach = SLABreach(
                dataset_name=status.dataset_name,
                namespace=status.namespace,
                freshness_sla_seconds=status.sla_seconds,
                elapsed_seconds=status.elapsed_seconds or 0.0,
                breach_time=status.breach_time,
            )
            incident_manager.create_incident(
                dataset=status.dataset_name,
                failure_type="SLA_BREACH",
                detected_at=status.breach_time,
                details={
                    "elapsed_seconds": status.elapsed_seconds,
                    "sla_seconds": status.sla_seconds,
                },
            )
            alert_manager.send_sla_alert(breach)
        elif status.state in {"FRESH", "WARNING"}:
            incident_manager.resolve_open_incidents(status.dataset_name, "SLA_BREACH")


def main() -> None:
    config = load_config()
    sla_monitor, contract_enforcer, alert_manager, incident_manager = build_services(config)
    run_contract_checks(contract_enforcer, alert_manager, incident_manager)
    run_sla_checks(sla_monitor, alert_manager, incident_manager)
    logger.info("Platform checks complete")


if __name__ == "__main__":
    main()

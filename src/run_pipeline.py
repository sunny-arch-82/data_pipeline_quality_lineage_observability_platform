"""Pipeline orchestration script.

Invokes dbt-ol run -> dbt test -> contract enforcement -> Elementary report.
Observability failures are also converted into durable incidents with a
lineage-derived downstream blast radius.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from src.alert_manager import AlertManager
from src.config import load_config
from src.contract_enforcer import ContractEnforcer
from src.db_migrations import create_observability_schema
from src.incident_manager import IncidentManager
from src.lineage_tracker import build_run_event, emit_event
from src.models import TestFailure

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DBT_PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR", "dbt_project")
DBT_PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", "dbt_project")


def _run_cmd(cmd: list[str]) -> int:
    """Run a subprocess command, stream output, return exit code."""
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def _emit_fail_event(config: dict, job_name: str, error_msg: str) -> None:
    """Emit a FAIL lineage event to Marquez."""
    import uuid

    event = build_run_event(
        job_name=job_name,
        run_id=str(uuid.uuid4()),
        inputs=[],
        outputs=[],
        state="FAIL",
        namespace=config["OPENLINEAGE_NAMESPACE"],
        error_msg=error_msg,
    )
    try:
        emit_event(event, config["MARQUEZ_URL"])
    except Exception as exc:
        logger.warning("Could not emit FAIL lineage event: %s", exc)


def _get_quality_failures(db_url: str) -> list[TestFailure]:
    """Query Elementary results table for recent test failures."""
    engine = create_engine(db_url)
    query = text(
        """
        SELECT model_id, test_unique_id, failures, detected_at
        FROM elementary.dbt_test_results
        WHERE status = 'fail'
          AND detected_at >= NOW() - INTERVAL '1 hour'
        ORDER BY detected_at DESC
        LIMIT 100
        """
    )
    failures = []
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
            for row in rows:
                failures.append(
                    TestFailure(
                        model_name=row[0] or "unknown",
                        test_name=row[1] or "unknown",
                        failure_count=int(row[2] or 0),
                        timestamp=row[3] or datetime.now(timezone.utc),
                    )
                )
    except Exception as exc:
        logger.warning("Could not query Elementary results: %s", exc)
    return failures


def main() -> None:
    config = load_config()
    db_url = config["WAREHOUSE_URL"]

    engine = create_engine(db_url)
    create_observability_schema(engine)

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

    # Step 1: dbt-ol run (lineage emission + dbt run)
    dbt_run_rc = _run_cmd(
        [
            "dbt-ol",
            "run",
            "--project-dir",
            DBT_PROJECT_DIR,
            "--profiles-dir",
            DBT_PROFILES_DIR,
        ]
    )

    if dbt_run_rc != 0:
        error_msg = f"dbt-ol run exited with code {dbt_run_rc}"
        logger.error("dbt-ol run failed with exit code %d", dbt_run_rc)
        _emit_fail_event(config, "dbt.run", error_msg)
        incident_manager.create_incident(
            dataset="dbt.run",
            failure_type="PIPELINE_FAILURE",
            details={"exit_code": dbt_run_rc, "message": error_msg},
        )
        sys.exit(dbt_run_rc)

    # Step 2: dbt tests
    dbt_test_rc = _run_cmd(
        [
            "dbt",
            "test",
            "--project-dir",
            DBT_PROJECT_DIR,
            "--profiles-dir",
            DBT_PROFILES_DIR,
        ]
    )

    # Step 3: Turn quality failures into alerts + incidents
    failures = _get_quality_failures(db_url)
    for failure in failures:
        incident_manager.create_incident(
            dataset=failure.model_name,
            failure_type="QUALITY_TEST",
            affected_rows=failure.failure_count,
            detected_at=failure.timestamp,
            details={"test_name": failure.test_name, "message": failure.message},
        )
        alert_manager.send_quality_alert(failure)

    # Step 4: Contract enforcement -> alerts + incidents
    contract_enforcer = ContractEnforcer(db_url=db_url)
    contract_enforcer.load_contracts(Path("contracts"))
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

    # Step 5: Generate Elementary report
    _run_cmd(
        [
            "edr",
            "report",
            "generate",
            "--project-dir",
            DBT_PROJECT_DIR,
            "--profiles-dir",
            DBT_PROFILES_DIR,
        ]
    )

    logger.info("Pipeline complete. dbt test exit code: %d", dbt_test_rc)
    sys.exit(dbt_test_rc)


if __name__ == "__main__":
    main()

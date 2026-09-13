"""Alert_Manager — suppression, retry, and multi-channel delivery."""

from __future__ import annotations

import logging
import smtplib
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.mime.text import MIMEText

import httpx
import sqlalchemy
import sqlalchemy.exc
from sqlalchemy import text

from src.models import Anomaly, ContractViolation, SLABreach, TestFailure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure helper functions (module-level, for property testing)
# ---------------------------------------------------------------------------


def _is_suppressed(alert_key: str, last_sent: datetime | None, window_hours: float) -> bool:
    """Return True if the alert should be suppressed.

    Suppressed if last_sent is not None AND
    (now - last_sent).total_seconds() < window_hours * 3600.
    """
    if last_sent is None:
        return False
    now = datetime.now(timezone.utc)
    elapsed = (now - last_sent).total_seconds()
    return elapsed < window_hours * 3600


def _should_retry(attempt: int, max_attempts: int) -> bool:
    """Return True if another retry attempt should be made.

    True if attempt < max_attempts (0-indexed attempt number).
    """
    return attempt < max_attempts


# ---------------------------------------------------------------------------
# AlertManager class
# ---------------------------------------------------------------------------


class AlertManager:
    def __init__(
        self,
        channel: str,
        slack_webhook_url: str | None,
        smtp_host: str | None,
        smtp_port: int,
        alert_email_to: str | None,
        suppression_window_hours: float,
        db_url: str,
    ) -> None:
        self.channel = channel
        self.slack_webhook_url = slack_webhook_url
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.alert_email_to = alert_email_to
        self.suppression_window_hours = suppression_window_hours
        self._suppression_cache: dict[str, datetime] = {}
        self._engine = sqlalchemy.create_engine(db_url)

    # ------------------------------------------------------------------
    # Delivery primitives
    # ------------------------------------------------------------------

    def _deliver_slack(self, payload: dict) -> None:
        """POST payload as JSON to the Slack webhook URL."""
        response = httpx.post(self.slack_webhook_url, json=payload)
        response.raise_for_status()

    def _deliver_email(self, subject: str, body: str) -> None:
        """Send an email via SMTP."""
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = "data-observability@platform.local"
        msg["To"] = self.alert_email_to
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as smtp:
            smtp.sendmail(msg["From"], [self.alert_email_to], msg.as_string())

    # ------------------------------------------------------------------
    # Retry / suppression core
    # ------------------------------------------------------------------

    def _send_with_retry(
        self,
        alert_key: str,
        deliver_fn: Callable,
        payload: dict,
        alert_type: str,
    ) -> None:
        """Attempt delivery up to 3 times with exponential backoff.

        Checks suppression before the first attempt. On exhaustion writes
        a record to observability.alert_delivery_failures.
        """
        if _is_suppressed(
            alert_key,
            self._suppression_cache.get(alert_key),
            self.suppression_window_hours,
        ):
            logger.info("Alert suppressed (within window): %s", alert_key)
            return

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                deliver_fn(payload)
                self._suppression_cache[alert_key] = datetime.now(timezone.utc)
                return
            except (httpx.HTTPError, smtplib.SMTPException) as exc:
                logger.warning(
                    "Alert delivery attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    max_attempts,
                    alert_key,
                    exc,
                )
                if _should_retry(attempt + 1, max_attempts):
                    time.sleep(2.0**attempt)

        # All attempts exhausted — write to dead-letter table
        logger.error("All %d delivery attempts failed for alert: %s", max_attempts, alert_key)
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO observability.alert_delivery_failures
                            (alert_type, payload, attempts, last_attempt_at)
                        VALUES
                            (:alert_type, :payload::jsonb, :attempts, :last_attempt_at)
                        """
                    ),
                    {
                        "alert_type": alert_type,
                        "payload": str(payload),
                        "attempts": max_attempts,
                        "last_attempt_at": datetime.now(timezone.utc),
                    },
                )
        except sqlalchemy.exc.OperationalError as exc:
            logger.error("Failed to write delivery failure record to DB: %s", exc)

    # ------------------------------------------------------------------
    # Public alert methods
    # ------------------------------------------------------------------

    def send_quality_alert(self, failure: TestFailure) -> None:
        alert_key = f"quality:{failure.model_name}:{failure.test_name}"
        payload = {
            "text": (
                f"Quality test FAILED: {failure.model_name}.{failure.test_name}"
                f" — {failure.failure_count} failures"
            )
        }
        self._dispatch(alert_key, payload, "quality_alert")

    def send_anomaly_alert(self, anomaly: Anomaly) -> None:
        alert_key = f"anomaly:{anomaly.table_name}:{anomaly.metric_name}:{anomaly.column_name}"
        payload = {
            "text": (
                f"Anomaly detected in {anomaly.table_name}.{anomaly.column_name}:"
                f" {anomaly.metric_name}={anomaly.observed_value}"
                f" (expected {anomaly.expected_min:.2f}–{anomaly.expected_max:.2f})"
            )
        }
        self._dispatch(alert_key, payload, "anomaly_alert")

    def send_sla_alert(self, breach: SLABreach) -> None:
        alert_key = f"sla:{breach.dataset_name}"
        payload = {
            "text": (
                f"SLA BREACHED: {breach.dataset_name}"
                f" — {breach.elapsed_seconds:.0f}s elapsed,"
                f" SLA={breach.freshness_sla_seconds:.0f}s"
            )
        }
        self._dispatch(alert_key, payload, "sla_alert")

    def send_contract_alert(self, violation: ContractViolation) -> None:
        alert_key = (
            f"contract:{violation.dataset_name}:{violation.column_name}:{violation.violation_type}"
        )
        payload = {
            "text": (
                f"Contract violation in {violation.dataset_name}.{violation.column_name}:"
                f" {violation.violation_type}"
            )
        }
        self._dispatch(alert_key, payload, "contract_alert")

    # ------------------------------------------------------------------
    # Internal dispatch helper
    # ------------------------------------------------------------------

    def _dispatch(self, alert_key: str, payload: dict, alert_type: str) -> None:
        """Route to the correct channel(s) and call _send_with_retry."""
        subject = payload.get("text", "Data Observability Alert")
        body = subject

        if self.channel == "slack":
            self._send_with_retry(alert_key, self._deliver_slack, payload, alert_type)
        elif self.channel == "email":
            self._send_with_retry(
                alert_key,
                lambda p: self._deliver_email(subject, body),
                payload,
                alert_type,
            )
        elif self.channel == "both":
            self._send_with_retry(alert_key, self._deliver_slack, payload, alert_type)
            self._send_with_retry(
                alert_key,
                lambda p: self._deliver_email(subject, body),
                payload,
                alert_type,
            )
        else:
            logger.warning("Unknown alert channel '%s'; skipping delivery.", self.channel)

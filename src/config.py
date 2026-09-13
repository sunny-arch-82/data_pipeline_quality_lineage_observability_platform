"""Startup configuration and environment variable validation."""

from __future__ import annotations

import os

REQUIRED_VARS: list[str] = [
    "OPENLINEAGE_URL",
    "OPENLINEAGE_NAMESPACE",
    "OPENLINEAGE_TRANSPORT_RETRY_ATTEMPTS",
    "OPENLINEAGE_TRANSPORT_RETRY_BACKOFF",
    "WAREHOUSE_URL",
    "ALERT_CHANNEL",
    "SLACK_WEBHOOK_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "ALERT_EMAIL_TO",
    "ALERT_SUPPRESSION_WINDOW_HOURS",
    "SLA_POLL_INTERVAL_MINUTES",
    "DBT_PROFILES_DIR",
    "MARQUEZ_URL",
]


def validate_env(required_vars: list[str], env_dict: dict) -> list[str]:
    """Return a list of variable names that are missing from *env_dict*.

    Raises EnvironmentError if any variables are missing, with a descriptive
    message that lists each missing variable by name.
    """
    missing = [var for var in required_vars if not env_dict.get(var)]
    if missing:
        missing_list = "\n  - ".join(missing)
        raise EnvironmentError(
            f"Missing required environment variables:\n  - {missing_list}\n"
            "Copy .env.example to .env and fill in the required values."
        )
    return missing


def load_config() -> dict:
    """Load configuration from os.environ, validating all required variables.

    Returns a dict of all required variable values.
    Raises EnvironmentError if any required variable is absent.
    """
    validate_env(REQUIRED_VARS, dict(os.environ))
    return {var: os.environ[var] for var in REQUIRED_VARS}

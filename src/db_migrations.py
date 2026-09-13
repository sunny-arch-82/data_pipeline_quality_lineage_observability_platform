"""Warehouse schema migrations for the Data Pipeline Observability Platform."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def create_observability_schema(engine: Engine) -> None:
    """Create the observability schema and platform tables if missing.

    Safe to call multiple times: every DDL statement uses ``IF NOT EXISTS``.
    """
    statements = [
        "CREATE SCHEMA IF NOT EXISTS observability",
        """
        CREATE TABLE IF NOT EXISTS observability.sla_status (
            dataset_name     TEXT NOT NULL,
            namespace        TEXT NOT NULL,
            state            TEXT NOT NULL,
            last_run_time    TIMESTAMPTZ,
            elapsed_seconds  FLOAT,
            sla_seconds      FLOAT NOT NULL,
            evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (dataset_name, namespace)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS observability.contract_violations (
            id               SERIAL PRIMARY KEY,
            dataset_name     TEXT NOT NULL,
            violation_type   TEXT NOT NULL,
            column_name      TEXT,
            expected         TEXT,
            observed         TEXT,
            null_count       INTEGER,
            run_timestamp    TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS observability.alert_delivery_failures (
            id               SERIAL PRIMARY KEY,
            alert_type       TEXT NOT NULL,
            payload          JSONB NOT NULL,
            attempts         INTEGER NOT NULL,
            last_attempt_at  TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS observability.incidents (
            incident_id                UUID PRIMARY KEY,
            dataset                    TEXT NOT NULL,
            failure_type               TEXT NOT NULL,
            severity                   TEXT NOT NULL CHECK (severity IN ('LOW', 'HIGH', 'CRITICAL')),
            affected_rows              INTEGER,
            detected_at                TIMESTAMPTZ NOT NULL,
            resolved_at                TIMESTAMPTZ,
            downstream_impact_count    INTEGER NOT NULL DEFAULT 0,
            details                    JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_incidents_open
            ON observability.incidents (dataset, failure_type, detected_at DESC)
            WHERE resolved_at IS NULL
        """,
        """
        CREATE TABLE IF NOT EXISTS observability.incident_impacts (
            incident_id       UUID NOT NULL REFERENCES observability.incidents(incident_id)
                              ON DELETE CASCADE,
            asset_namespace   TEXT NOT NULL,
            asset_name        TEXT NOT NULL,
            asset_type        TEXT NOT NULL CHECK (asset_type IN ('MODEL', 'DATASET', 'DASHBOARD')),
            depth             INTEGER NOT NULL,
            PRIMARY KEY (incident_id, asset_namespace, asset_name, asset_type)
        )
        """,
    ]

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))

    logger.info("Observability schema and tables created/verified successfully")

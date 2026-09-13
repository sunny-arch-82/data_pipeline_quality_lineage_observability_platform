"""Contract_Enforcer — validates warehouse datasets against YAML-declared data contracts."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from src.models import (
    ColumnContract,
    ContractComplianceReport,
    ContractViolation,
    DataContract,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure validation helper (module-level, testable without DB)
# ---------------------------------------------------------------------------


def _validate_contract(
    contract: DataContract,
    warehouse_schema: list[dict],
    null_counts: dict[str, int],
    run_timestamp: datetime,
) -> list[ContractViolation]:
    """Pure function: compare *contract* against *warehouse_schema* and *null_counts*.

    Args:
        contract: The declared DataContract.
        warehouse_schema: Rows from information_schema.columns —
            each dict has keys ``column_name``, ``data_type``, ``is_nullable``.
        null_counts: Mapping of column_name → null row count for non-nullable columns.
        run_timestamp: Timestamp to stamp on each violation.

    Returns:
        A list of ContractViolation objects — exactly one per offending column.
    """
    # Build a case-insensitive lookup from the warehouse schema
    schema_lookup: dict[str, dict] = {
        col["column_name"].lower(): col for col in warehouse_schema
    }

    violations: list[ContractViolation] = []

    for col in contract.columns:
        col_key = col.name.lower()

        # 1. MISSING_COLUMN — column absent from warehouse schema
        if col_key not in schema_lookup:
            violations.append(
                ContractViolation(
                    dataset_name=contract.dataset,
                    violation_type="MISSING_COLUMN",
                    column_name=col.name,
                    expected=col.type,
                    observed=None,
                    null_count=None,
                    run_timestamp=run_timestamp,
                )
            )
            # Skip further checks for this column
            continue

        warehouse_col = schema_lookup[col_key]

        # 2. TYPE_MISMATCH — data_type differs (case-insensitive)
        if warehouse_col["data_type"].lower() != col.type.lower():
            violations.append(
                ContractViolation(
                    dataset_name=contract.dataset,
                    violation_type="TYPE_MISMATCH",
                    column_name=col.name,
                    expected=col.type,
                    observed=warehouse_col["data_type"],
                    null_count=None,
                    run_timestamp=run_timestamp,
                )
            )
            continue

        # 3. NULLABILITY_VIOLATION — non-nullable column has null values
        if not col.nullable and null_counts.get(col.name, 0) > 0:
            violations.append(
                ContractViolation(
                    dataset_name=contract.dataset,
                    violation_type="NULLABILITY_VIOLATION",
                    column_name=col.name,
                    expected=None,
                    observed=None,
                    null_count=null_counts[col.name],
                    run_timestamp=run_timestamp,
                )
            )

    return violations


# ---------------------------------------------------------------------------
# ContractEnforcer class
# ---------------------------------------------------------------------------


class ContractEnforcer:
    """Loads YAML data contracts and validates them against a SQL warehouse."""

    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(db_url)
        self._contracts: list[DataContract] = []

        from src.db_migrations import create_observability_schema
        create_observability_schema(self._engine)

    # ------------------------------------------------------------------
    # Contract loading
    # ------------------------------------------------------------------

    def load_contracts(self, contracts_dir: Path) -> list[DataContract]:
        """Read all ``*.yml`` files from *contracts_dir* and parse as DataContracts.

        Malformed YAML or Pydantic validation errors are logged and skipped.
        Valid contracts are stored in ``self._contracts`` and returned.
        """
        loaded: list[DataContract] = []

        for yml_file in sorted(contracts_dir.glob("*.yml")):
            try:
                content = yml_file.read_text(encoding="utf-8")
                raw = yaml.safe_load(content)
                columns = [ColumnContract(**c) for c in raw.get("columns", [])]
                contract = DataContract(
                    dataset=raw["dataset"],
                    columns=columns,
                    freshness_sla=raw.get("freshness_sla"),
                )
                loaded.append(contract)
            except yaml.YAMLError as exc:
                logger.error("YAML parse error in %s: %s", yml_file, exc)
            except ValidationError as exc:
                for error in exc.errors():
                    field = " -> ".join(str(loc) for loc in error["loc"])
                    logger.error(
                        "Contract validation error in %s — field '%s': %s",
                        yml_file,
                        field,
                        error["msg"],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected error loading %s: %s", yml_file, exc)

        self._contracts = loaded
        return loaded

    # ------------------------------------------------------------------
    # Warehouse introspection helpers
    # ------------------------------------------------------------------

    def _get_warehouse_schema(self, dataset_name: str) -> list[dict]:
        """Query information_schema.columns for *dataset_name*.

        *dataset_name* may be ``schema.table`` or just ``table`` (defaults to
        ``public`` schema).

        Returns a list of dicts with keys ``column_name``, ``data_type``,
        ``is_nullable`` (``"YES"`` or ``"NO"``).
        """
        if "." in dataset_name:
            schema_name, table_name = dataset_name.split(".", 1)
        else:
            schema_name, table_name = "public", dataset_name

        query = text(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name   = :table
            ORDER BY ordinal_position
            """
        )

        try:
            with self._engine.connect() as conn:
                result = conn.execute(query, {"schema": schema_name, "table": table_name})
                return [
                    {
                        "column_name": row.column_name,
                        "data_type": row.data_type,
                        "is_nullable": row.is_nullable,
                    }
                    for row in result
                ]
        except OperationalError as exc:
            logger.error(
                "OperationalError querying schema for '%s': %s", dataset_name, exc
            )
            return []

    def _check_nullability(self, dataset_name: str, column_name: str) -> int:
        """Return the count of NULL values in *column_name* of *dataset_name*."""
        query = text(
            f"SELECT COUNT(*) FROM {dataset_name} WHERE {column_name} IS NULL"  # noqa: S608
        )
        try:
            with self._engine.connect() as conn:
                result = conn.execute(query)
                row = result.fetchone()
                return int(row[0]) if row else 0
        except OperationalError as exc:
            logger.error(
                "OperationalError checking nullability for '%s.%s': %s",
                dataset_name,
                column_name,
                exc,
            )
            return 0

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, contract: DataContract) -> list[ContractViolation]:
        """Validate *contract* against the live warehouse schema.

        Fetches the warehouse schema and null counts, then delegates to the
        pure ``_validate_contract`` helper.
        """
        warehouse_schema = self._get_warehouse_schema(contract.dataset)

        # Only check nullability for non-nullable columns that exist in the schema
        schema_col_names = {col["column_name"].lower() for col in warehouse_schema}
        null_counts: dict[str, int] = {}
        for col in contract.columns:
            if not col.nullable and col.name.lower() in schema_col_names:
                null_counts[col.name] = self._check_nullability(
                    contract.dataset, col.name
                )

        return _validate_contract(
            contract,
            warehouse_schema,
            null_counts,
            datetime.utcnow(),
        )

    def validate_all(self) -> ContractComplianceReport:
        """Validate all loaded contracts and return a ContractComplianceReport.

        Each violation is also persisted to ``observability.contract_violations``.
        """
        run_timestamp = datetime.utcnow()
        all_violations: list[ContractViolation] = []

        for contract in self._contracts:
            violations = self.validate(contract)
            all_violations.extend(violations)

            for violation in violations:
                self._write_violation(violation)

        return ContractComplianceReport(
            violations=all_violations,
            run_timestamp=run_timestamp,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _write_violation(self, violation: ContractViolation) -> None:
        """Persist a single violation to ``observability.contract_violations``."""
        insert = text(
            """
            INSERT INTO observability.contract_violations
                (dataset_name, violation_type, column_name, expected, observed,
                 null_count, run_timestamp)
            VALUES
                (:dataset_name, :violation_type, :column_name, :expected, :observed,
                 :null_count, :run_timestamp)
            """
        )
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert,
                    {
                        "dataset_name": violation.dataset_name,
                        "violation_type": violation.violation_type,
                        "column_name": violation.column_name,
                        "expected": violation.expected,
                        "observed": violation.observed,
                        "null_count": violation.null_count,
                        "run_timestamp": violation.run_timestamp,
                    },
                )
        except OperationalError as exc:
            logger.error("Failed to write violation to DB: %s", exc)

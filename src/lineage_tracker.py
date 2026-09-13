"""OpenLineage event construction and delivery for the Data Pipeline Quality, Lineage & Observability Platform."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_SCHEMA = (
    "https://openlineage.io/spec/facets/1-0-0/ErrorMessageRunFacet.json"
)
_RUN_EVENT_SCHEMA = (
    "https://openlineage.io/spec/1-0-5/OpenLineage.json#/$defs/RunEvent"
)
_PRODUCER = "urn:portfolio:data-pipeline-observability-platform"
_COLUMN_LINEAGE_SCHEMA = (
    "https://openlineage.io/spec/facets/1-0-1/ColumnLineageDatasetFacet.json"
)


def build_run_event(
    job_name: str,
    run_id: str,
    inputs: list[dict],
    outputs: list[dict],
    state: Literal["START", "COMPLETE", "FAIL", "ABORT"],
    namespace: str,
    error_msg: str | None = None,
) -> dict:
    """Construct a valid OpenLineage RunEvent JSON-serialisable dict.

    Args:
        job_name: Name of the dbt job / pipeline step.
        run_id: UUID string identifying this run.
        inputs: List of input dataset dicts with at least "namespace" and "name".
        outputs: List of output dataset dicts with at least "namespace" and "name".
        state: Run state — one of START, COMPLETE, FAIL, ABORT.
        namespace: OpenLineage namespace for the job.
        error_msg: Optional error message included in the errorMessage run facet.

    Returns:
        A dict that is directly JSON-serialisable as an OpenLineage RunEvent.
    """
    run_facets: dict = {}
    if error_msg is not None:
        run_facets["errorMessage"] = {
            "_producer": _PRODUCER,
            "_schemaURL": _ERROR_MESSAGE_SCHEMA,
            "message": error_msg,
            "programmingLanguage": "Python",
        }

    return {
        "eventType": state,
        "eventTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run": {
            "runId": run_id,
            "facets": run_facets,
        },
        "job": {
            "namespace": namespace,
            "name": job_name,
        },
        "inputs": inputs,
        "outputs": outputs,
        "producer": _PRODUCER,
        "schemaURL": _RUN_EVENT_SCHEMA,
    }


def build_column_lineage_facet(
    column_mappings: dict[str, list[dict]],
) -> dict:
    """Construct a ColumnLineageDatasetFacet dict.

    Args:
        column_mappings: Mapping of output column name to a list of input field
            descriptors, each with keys "namespace", "dataset", and "field".
            The "dataset" key is mapped to "name" in the output inputFields.

    Returns:
        A ColumnLineageDatasetFacet dict ready to embed in a dataset's facets.
    """
    fields: dict = {}
    for output_col, input_fields in column_mappings.items():
        fields[output_col] = {
            "inputFields": [
                {
                    "namespace": f["namespace"],
                    "name": f["dataset"],
                    "field": f["field"],
                }
                for f in input_fields
            ]
        }

    return {
        "_producer": _PRODUCER,
        "_schemaURL": _COLUMN_LINEAGE_SCHEMA,
        "fields": fields,
    }


def emit_event(
    event: dict,
    marquez_url: str,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
) -> None:
    """POST an OpenLineage event to the Marquez API with retry logic.

    Args:
        event: A JSON-serialisable OpenLineage RunEvent dict.
        marquez_url: Base URL of the Marquez instance (e.g. "http://marquez:5000").
        max_retries: Maximum number of retry attempts on transient failures.
        backoff_factor: Base for exponential backoff; sleep = backoff_factor ** attempt.

    Raises:
        httpx.ConnectError: If the Marquez API is unreachable after all retries.
        httpx.HTTPStatusError: If the Marquez API returns a non-2xx status after all retries.
    """
    url = f"{marquez_url}/api/v1/lineage"
    headers = {"Content-Type": "application/json"}
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = httpx.post(url, json=event, headers=headers)
            response.raise_for_status()
            return
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            sleep_seconds = backoff_factor ** attempt
            logger.warning(
                "emit_event attempt %d/%d failed: %s — retrying in %.1fs",
                attempt + 1,
                max_retries,
                exc,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

    logger.error(
        "emit_event failed after %d attempts for job '%s': %s",
        max_retries,
        event.get("job", {}).get("name", "<unknown>"),
        last_exc,
    )
    raise last_exc  # type: ignore[misc]

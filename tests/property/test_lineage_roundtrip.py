"""Property 1: Lineage event round-trip

Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.lineage_tracker import build_run_event

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_job_name_st = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="._-",
    ),
)

_run_id_st = st.uuids().map(str)

_namespace_st = st.text(
    min_size=1,
    max_size=30,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="_-",
    ),
)

_state_st = st.sampled_from(["START", "COMPLETE", "FAIL", "ABORT"])

_dataset_st = st.fixed_dictionaries(
    {
        "namespace": st.text(min_size=1, max_size=20),
        "name": st.text(min_size=1, max_size=20),
    }
)

_datasets_st = st.lists(_dataset_st, min_size=0, max_size=5)

_error_msg_st = st.one_of(st.none(), st.text(min_size=1, max_size=100))


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    job_name=_job_name_st,
    run_id=_run_id_st,
    inputs=_datasets_st,
    outputs=_datasets_st,
    state=_state_st,
    namespace=_namespace_st,
    error_msg=_error_msg_st,
)
@settings(max_examples=100)
def test_lineage_event_roundtrip(
    job_name: str,
    run_id: str,
    inputs: list[dict],
    outputs: list[dict],
    state: str,
    namespace: str,
    error_msg: str | None,
) -> None:
    """Property 1: Lineage event round-trip.

    For any valid combination of job name, run ID, input/output datasets, state,
    namespace, and optional error message, serialising the RunEvent to JSON and
    deserialising it back SHALL produce a structurally equivalent object.

    Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3
    """
    event = build_run_event(
        job_name=job_name,
        run_id=run_id,
        inputs=inputs,
        outputs=outputs,
        state=state,  # type: ignore[arg-type]
        namespace=namespace,
        error_msg=error_msg,
    )

    # Round-trip through JSON serialisation
    json_str = json.dumps(event)
    restored = json.loads(json_str)

    # Core structural assertions
    assert restored["eventType"] == state
    assert restored["job"]["name"] == job_name
    assert restored["job"]["namespace"] == namespace
    assert restored["run"]["runId"] == run_id
    assert restored["inputs"] == inputs
    assert restored["outputs"] == outputs

    # Error message facet assertions
    if error_msg is not None:
        assert restored["run"]["facets"]["errorMessage"]["message"] == error_msg
    else:
        assert "errorMessage" not in restored["run"]["facets"]

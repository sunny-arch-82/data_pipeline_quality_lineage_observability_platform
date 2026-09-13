# Property 4: Contract YAML round-trip
# Validates: Requirements 6.1

from __future__ import annotations

import string

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from src.models import ColumnContract, DataContract

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_NAME_ALPHABET = string.ascii_letters + string.digits + "_"
_COLUMN_TYPES = ["INTEGER", "TEXT", "FLOAT", "BOOLEAN", "TIMESTAMPTZ"]

name_strategy = st.text(
    alphabet=_NAME_ALPHABET,
    min_size=1,
    max_size=30,
)

column_strategy = st.builds(
    ColumnContract,
    name=name_strategy,
    type=st.sampled_from(_COLUMN_TYPES),
    nullable=st.booleans(),
    unique=st.booleans(),
)

contract_strategy = st.builds(
    DataContract,
    dataset=name_strategy,
    columns=st.lists(column_strategy, min_size=1, max_size=10),
    freshness_sla=st.one_of(st.none(), st.integers(min_value=60, max_value=86400)),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(contract_strategy)
@settings(max_examples=100)
def test_datacontract_yaml_roundtrip(contract: DataContract) -> None:
    """Serialising a DataContract to YAML and back produces an equivalent object."""
    yaml_str = yaml.dump(contract.model_dump())
    raw = yaml.safe_load(yaml_str)

    # Reconstruct nested ColumnContract objects from the raw dict
    raw["columns"] = [ColumnContract(**col) for col in raw["columns"]]
    restored = DataContract(**raw)

    assert restored.dataset == contract.dataset
    assert restored.freshness_sla == contract.freshness_sla
    assert len(restored.columns) == len(contract.columns)
    for orig_col, rest_col in zip(contract.columns, restored.columns):
        assert rest_col.name == orig_col.name
        assert rest_col.type == orig_col.type
        assert rest_col.nullable == orig_col.nullable
        assert rest_col.unique == orig_col.unique

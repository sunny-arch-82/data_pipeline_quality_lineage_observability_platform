# Property 3: Contract violation completeness
# Validates: Requirements 6.2, 6.3, 6.4, 6.5

from __future__ import annotations

import string
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from src.contract_enforcer import _validate_contract
from src.models import ColumnContract, DataContract

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_NAME_ALPHABET = string.ascii_lowercase + string.digits + "_"
_COLUMN_TYPES = ["INTEGER", "TEXT", "FLOAT", "BOOLEAN"]
_ALT_TYPES = {
    "INTEGER": ["TEXT", "FLOAT", "BOOLEAN"],
    "TEXT": ["INTEGER", "FLOAT", "BOOLEAN"],
    "FLOAT": ["INTEGER", "TEXT", "BOOLEAN"],
    "BOOLEAN": ["INTEGER", "TEXT", "FLOAT"],
}

name_strategy = st.text(
    alphabet=_NAME_ALPHABET,
    min_size=1,
    max_size=20,
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
    columns=st.lists(column_strategy, min_size=1, max_size=6).filter(
        # Ensure column names are unique within a contract
        lambda cols: len({c.name for c in cols}) == len(cols)
    ),
    freshness_sla=st.none(),
)


def _index_set_strategy(max_index: int) -> st.SearchStrategy:
    """Strategy that produces a set of indices in [0, max_index]."""
    if max_index < 0:
        return st.just(set())
    return st.sets(st.integers(min_value=0, max_value=max_index))


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(contract_strategy, st.data())
@settings(max_examples=100)
def test_contract_violation_completeness(contract: DataContract, data: st.DataObject) -> None:
    """Every injected mismatch produces exactly one violation — no more, no fewer.

    **Property 3: Contract violation completeness**
    **Validates: Requirements 6.2, 6.3, 6.4, 6.5**
    """
    cols = contract.columns
    n = len(cols)

    # Draw disjoint index sets for each violation type
    all_indices = list(range(n))

    # 1. Columns to remove from warehouse schema → MISSING_COLUMN
    missing_indices: set[int] = data.draw(
        st.sets(st.integers(min_value=0, max_value=n - 1), max_size=n)
    )

    remaining_indices = [i for i in all_indices if i not in missing_indices]

    # 2. From remaining columns, pick some for TYPE_MISMATCH
    type_mismatch_indices: set[int] = set()
    if remaining_indices:
        type_mismatch_indices = data.draw(
            st.sets(
                st.sampled_from(remaining_indices),
                max_size=len(remaining_indices),
            )
        )

    # 3. From remaining non-nullable columns (not missing, not type-mismatched),
    #    pick some for NULLABILITY_VIOLATION
    nullability_candidates = [
        i
        for i in remaining_indices
        if i not in type_mismatch_indices and not cols[i].nullable
    ]
    nullability_indices: set[int] = set()
    if nullability_candidates:
        nullability_indices = data.draw(
            st.sets(
                st.sampled_from(nullability_candidates),
                max_size=len(nullability_candidates),
            )
        )

    # Build the "clean" warehouse schema matching the contract exactly
    clean_schema = [
        {
            "column_name": col.name,
            "data_type": col.type,
            "is_nullable": "YES" if col.nullable else "NO",
        }
        for col in cols
    ]

    # Apply corruptions
    # Remove missing columns
    corrupted_schema = [
        row for i, row in enumerate(clean_schema) if i not in missing_indices
    ]

    # Change types for type_mismatch_indices
    for i in type_mismatch_indices:
        col = cols[i]
        alt_types = _ALT_TYPES[col.type]
        # Pick a deterministic alternative (first in list) to avoid extra draws
        alt_type = alt_types[0]
        # Find and update the row in corrupted_schema
        for row in corrupted_schema:
            if row["column_name"] == col.name:
                row["data_type"] = alt_type
                break

    # Build null_counts for nullability violations
    null_counts: dict[str, int] = {}
    for i in nullability_indices:
        null_counts[cols[i].name] = 1

    # Run the pure validation function
    violations = _validate_contract(contract, corrupted_schema, null_counts, datetime.now())

    # ---------------------------------------------------------------------------
    # Assertions
    # ---------------------------------------------------------------------------

    # Collect expected violation sets by type
    expected_missing = {cols[i].name for i in missing_indices}
    expected_type_mismatch = {cols[i].name for i in type_mismatch_indices}
    expected_nullability = {cols[i].name for i in nullability_indices}

    # Partition actual violations by type
    actual_missing = {v.column_name for v in violations if v.violation_type == "MISSING_COLUMN"}
    actual_type_mismatch = {
        v.column_name for v in violations if v.violation_type == "TYPE_MISMATCH"
    }
    actual_nullability = {
        v.column_name for v in violations if v.violation_type == "NULLABILITY_VIOLATION"
    }

    # Each injected mismatch produces exactly one violation of the right type
    assert actual_missing == expected_missing, (
        f"MISSING_COLUMN mismatch: expected {expected_missing}, got {actual_missing}"
    )
    assert actual_type_mismatch == expected_type_mismatch, (
        f"TYPE_MISMATCH mismatch: expected {expected_type_mismatch}, got {actual_type_mismatch}"
    )
    assert actual_nullability == expected_nullability, (
        f"NULLABILITY_VIOLATION mismatch: expected {expected_nullability}, got {actual_nullability}"
    )

    # Total count matches exactly
    expected_total = len(missing_indices) + len(type_mismatch_indices) + len(nullability_indices)
    assert len(violations) == expected_total, (
        f"Total violations: expected {expected_total}, got {len(violations)}"
    )

    # No extra violations beyond the injected ones
    all_expected_cols = expected_missing | expected_type_mismatch | expected_nullability
    all_actual_cols = {v.column_name for v in violations}
    assert all_actual_cols <= all_expected_cols, (
        f"Unexpected violations for columns: {all_actual_cols - all_expected_cols}"
    )

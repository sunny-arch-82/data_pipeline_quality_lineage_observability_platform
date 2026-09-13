"""Unit tests for src/db_migrations.py."""

from unittest.mock import MagicMock

from src.db_migrations import create_observability_schema


def _mock_engine_and_connection():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


def test_create_observability_schema_idempotent():
    """create_observability_schema is safe to call multiple times."""
    mock_engine, mock_conn = _mock_engine_and_connection()

    create_observability_schema(mock_engine)
    create_observability_schema(mock_engine)

    # 7 statements x 2 calls
    assert mock_conn.execute.call_count == 14


def test_create_observability_schema_executes_expected_statements():
    """Schema, operational tables, incident index, and impact table are created."""
    mock_engine, mock_conn = _mock_engine_and_connection()

    create_observability_schema(mock_engine)

    assert mock_conn.execute.call_count == 7
    sql_text = "\n".join(str(call.args[0]) for call in mock_conn.execute.call_args_list)
    assert "observability.incidents" in sql_text
    assert "observability.incident_impacts" in sql_text

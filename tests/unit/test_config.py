"""Unit tests for src/config.py — validate_env and load_config."""
import pytest
from src.config import validate_env, REQUIRED_VARS


def test_validate_env_all_present():
    """No error when all required vars are present."""
    env = {var: "value" for var in REQUIRED_VARS}
    missing = validate_env(REQUIRED_VARS, env)
    assert missing == []


def test_validate_env_one_missing():
    """EnvironmentError raised when one var is missing; var name in message."""
    env = {var: "value" for var in REQUIRED_VARS}
    missing_var = REQUIRED_VARS[0]
    del env[missing_var]
    with pytest.raises(EnvironmentError) as exc_info:
        validate_env(REQUIRED_VARS, env)
    assert missing_var in str(exc_info.value)


def test_validate_env_multiple_missing():
    """EnvironmentError lists all missing variable names."""
    env = {}
    with pytest.raises(EnvironmentError) as exc_info:
        validate_env(REQUIRED_VARS, env)
    error_msg = str(exc_info.value)
    for var in REQUIRED_VARS:
        assert var in error_msg


def test_validate_env_empty_string_counts_as_missing():
    """Empty string values are treated as missing."""
    env = {var: "value" for var in REQUIRED_VARS}
    env[REQUIRED_VARS[0]] = ""
    with pytest.raises(EnvironmentError):
        validate_env(REQUIRED_VARS, env)


def test_validate_env_custom_vars():
    """validate_env works with any list of variable names."""
    missing = validate_env(["FOO", "BAR"], {"FOO": "1", "BAR": "2"})
    assert missing == []

    with pytest.raises(EnvironmentError) as exc_info:
        validate_env(["FOO", "BAR"], {"FOO": "1"})
    assert "BAR" in str(exc_info.value)

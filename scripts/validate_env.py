#!/usr/bin/env python
"""Validate that all required environment variables are set.

Exits with code 1 and a descriptive error if any are missing.
Run directly: python scripts/validate_env.py
Also called by: make setup, make run
"""
import os
import sys

# Add project root to path so src imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import REQUIRED_VARS, validate_env

if __name__ == "__main__":
    try:
        validate_env(REQUIRED_VARS, dict(os.environ))
        print("✓ All required environment variables are set.")
        sys.exit(0)
    except EnvironmentError as exc:
        print(f"✗ Environment validation failed:\n{exc}", file=sys.stderr)
        sys.exit(1)

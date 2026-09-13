#!/usr/bin/env python
"""Check that the Marquez API is reachable.

Exits with code 1 if Marquez is not responding within 5 seconds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


def check_marquez() -> None:
    marquez_url = os.environ.get("MARQUEZ_URL", "http://localhost:5000")
    url = f"{marquez_url}/api/v1/namespaces"
    try:
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        print(f"✓ Marquez is reachable at {marquez_url}")
    except httpx.ConnectError:
        print(f"✗ Cannot connect to Marquez at {marquez_url}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"✗ Marquez returned {exc.response.status_code} at {url}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    check_marquez()

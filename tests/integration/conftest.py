"""Shared fixtures for tests that drive the running Compose stack.

These are cross-boundary tests (testing.md §5): they exercise the real Go API,
the real quant-mcp service and the real PostgreSQL instance over HTTP. Nothing
here is mocked, because the whole point is to verify the boundaries that unit
tests cannot reach.

Every test skips cleanly when the stack is not up, so `pytest` stays runnable
with nothing running.
"""

from __future__ import annotations

import os

import httpx
import pytest

API_BASE_URL = os.environ.get("INTEGRATION_API_BASE_URL", "http://localhost:8080")

# A backtest over several years of daily data plus a yfinance fetch is slow, and
# a rerun does the whole thing again. A tight timeout here would report a
# healthy system as broken.
REQUEST_TIMEOUT = 180.0


def _api_is_up(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/healthz", timeout=5.0)
    except httpx.HTTPError:
        return False
    # 503 means the API is serving but a dependency is unhealthy. That is still
    # "not ready for an integration test", and the skip message says which.
    return response.status_code == 200


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    """An HTTP client pointed at the running Go API."""
    if not _api_is_up(API_BASE_URL):
        pytest.skip(
            f"the API at {API_BASE_URL} is not reporting healthy; "
            "run `make up && make migrate` to exercise the integration tests"
        )
    with httpx.Client(base_url=API_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        yield client

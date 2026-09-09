"""`GET /healthz` must report the truth about each dependency, by name."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.quant.config import Settings
from services.quant.db import DatabaseUnavailable
from services.quant.webapi.app import create_app


def make_settings(**overrides: object) -> Settings:
    base = {
        "port": 8000,
        "log_level": "info",
        "database_url": "postgres://quant:pw@db:5432/quant",
        "llm_provider": "gemini",
        "gemini_api_key": "test-key",
        "gemini_model": "gemini-3.6-flash",
        "ollama_url": "http://ollama:11434",
        "ollama_model": "llama3.1",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def client_factory(monkeypatch: pytest.MonkeyPatch):
    def _factory(settings: Settings, *, database_ok: bool = True) -> TestClient:
        def fake_check(_url: str, **_kwargs: object) -> None:
            if not database_ok:
                raise DatabaseUnavailable("cannot connect to PostgreSQL: connection refused")

        monkeypatch.setattr("services.quant.webapi.app.check_database", fake_check)
        return TestClient(create_app(settings), raise_server_exceptions=False)

    return _factory


def test_healthz_ok_when_database_reachable_and_key_present(client_factory) -> None:
    response = client_factory(make_settings()).get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "quant-mcp"
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["llm_provider"]["status"] == "ok"


def test_healthz_does_not_call_the_llm_provider(client_factory) -> None:
    """maintenance-operations.md §3: health pings must not burn free-tier quota."""
    response = client_factory(make_settings()).get("/healthz")

    detail = response.json()["dependencies"]["llm_provider"]["detail"]
    assert "not called by this check" in detail


def test_healthz_degraded_names_the_unreachable_database(client_factory) -> None:
    response = client_factory(make_settings(), database_ok=False).get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert "connection refused" in body["dependencies"]["database"]["error"]
    # A healthy dependency still reports ok alongside a failing one.
    assert body["dependencies"]["llm_provider"]["status"] == "ok"


def test_healthz_flags_missing_gemini_key(client_factory) -> None:
    settings = make_settings(gemini_api_key=None)
    response = client_factory(settings).get("/healthz")

    assert response.status_code == 503
    llm = response.json()["dependencies"]["llm_provider"]
    assert llm["status"] == "unhealthy"
    assert "GEMINI_API_KEY" in llm["error"]


def test_healthz_ollama_provider_needs_no_gemini_key(client_factory) -> None:
    settings = make_settings(llm_provider="ollama", gemini_api_key=None)
    response = client_factory(settings).get("/healthz")

    assert response.status_code == 200
    assert "ollama" in response.json()["dependencies"]["llm_provider"]["detail"]


def test_request_id_is_adopted_and_echoed(client_factory) -> None:
    client = client_factory(make_settings())
    response = client.get("/healthz", headers={"X-Request-ID": "req_from_the_go_api"})

    assert response.headers["X-Request-ID"] == "req_from_the_go_api"


def test_request_id_is_generated_when_absent(client_factory) -> None:
    response = client_factory(make_settings()).get("/healthz")

    assert response.headers["X-Request-ID"].startswith("req_")


def test_unknown_route_returns_json(client_factory) -> None:
    response = client_factory(make_settings()).get("/no-such-endpoint")

    assert response.status_code == 404

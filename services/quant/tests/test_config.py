"""Configuration must fail loudly and specifically (NFR5.6)."""

from __future__ import annotations

import pytest

from services.quant.config import ConfigError, Settings, load_dotenv, load_settings

ENV_KEYS = (
    "QUANT_MCP_PORT",
    "LOG_LEVEL",
    "DATABASE_URL",
    "LLM_PROVIDER",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_valid_gemini_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://quant:pw@db:5432/quant")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    settings = load_settings(dotenv_paths=())

    assert isinstance(settings, Settings)
    assert settings.llm_provider == "gemini"
    assert settings.llm_is_gemini
    assert settings.gemini_model == "gemini-3.6-flash"
    assert settings.port == 8000


def test_missing_database_url_is_fatal() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL is required"):
        load_settings(dotenv_paths=())


def test_gemini_without_key_fails_loudly_and_names_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://quant:pw@db:5432/quant")

    with pytest.raises(ConfigError) as excinfo:
        load_settings(dotenv_paths=())

    message = str(excinfo.value)
    assert "GEMINI_API_KEY is required" in message
    # The runbook tells the user to switch providers; the error should too.
    assert "LLM_PROVIDER=ollama" in message


def test_ollama_provider_does_not_require_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://quant:pw@db:5432/quant")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    settings = load_settings(dotenv_paths=())

    assert not settings.llm_is_gemini
    assert settings.gemini_api_key is None


def test_all_problems_are_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    monkeypatch.setenv("LLM_PROVIDER", "gpt")
    monkeypatch.setenv("QUANT_MCP_PORT", "not-a-port")

    with pytest.raises(ConfigError) as excinfo:
        load_settings(dotenv_paths=())

    message = str(excinfo.value)
    for expected in ("DATABASE_URL", "LOG_LEVEL", "LLM_PROVIDER", "QUANT_MCP_PORT"):
        assert expected in message, f"{expected} missing from {message!r}"


def test_dotenv_does_not_override_real_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export LOG_LEVEL=debug\n"
        'DATABASE_URL="postgres://quant:p#ssw0rd@db:5432/quant"\n'
        "GEMINI_API_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_LEVEL", "warn")

    settings = load_settings(dotenv_paths=(env_file,))

    assert settings.log_level == "warn", "a real environment variable must win over .env"
    # '#' inside a quoted password must survive, or the DSN silently breaks.
    assert settings.database_url == "postgres://quant:p#ssw0rd@db:5432/quant"
    assert settings.gemini_api_key == "from-file"


def test_malformed_dotenv_line_is_rejected(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("THIS_LINE_HAS_NO_EQUALS\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="expected KEY=VALUE"):
        load_dotenv(env_file)


def test_missing_dotenv_file_is_tolerated(tmp_path) -> None:
    load_dotenv(tmp_path / "absent.env")  # must not raise

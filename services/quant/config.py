"""Runtime configuration for the `quant-mcp` service.

Configuration is read once at startup and validated immediately. An invalid or
missing required value is a startup failure, never a runtime surprise
(requirements.md NFR5.6, deployment.md §3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VALID_LOG_LEVELS = frozenset({"debug", "info", "warn", "error"})
VALID_LLM_PROVIDERS = frozenset({"gemini", "ollama"})


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid.

    Carries every problem at once so a user fixes their `.env` in one pass
    instead of restarting the container five times.
    """


def load_dotenv(path: str | Path) -> None:
    """Seed ``os.environ`` from a .env file without overriding real variables.

    A missing file is fine (in a container the values arrive as real
    environment variables). A malformed line raises: a silently ignored typo in
    a DSN or API key is exactly what NFR5.6 tells us to surface loudly.
    """
    p = Path(path)
    if not p.is_file():
        return
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        if "=" not in line:
            raise ConfigError(f"{p}:{lineno}: expected KEY=VALUE, got {line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError(f"{p}:{lineno}: empty key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class Settings:
    """Fully resolved, validated settings."""

    port: int
    log_level: str
    database_url: str
    llm_provider: str
    gemini_api_key: str | None
    gemini_model: str
    ollama_url: str
    ollama_model: str

    @property
    def llm_is_gemini(self) -> bool:
        return self.llm_provider == "gemini"


def _int_env(key: str, default: int, problems: list[str]) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        problems.append(f"{key} must be an integer, got {raw!r}")
        return default


def load_settings(dotenv_paths: tuple[str | Path, ...] = (".env",)) -> Settings:
    """Build :class:`Settings` from the environment, seeded from .env files."""
    for path in dotenv_paths:
        load_dotenv(path)

    problems: list[str] = []

    port = _int_env("QUANT_MCP_PORT", 8000, problems)
    if not 1 <= port <= 65535:
        problems.append(f"QUANT_MCP_PORT must be 1-65535, got {port}")

    log_level = (os.environ.get("LOG_LEVEL") or "info").strip().lower()
    if log_level not in VALID_LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {log_level!r}")

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        problems.append(
            "DATABASE_URL is required — experiments are the system of record and the "
            "service will not start without somewhere to persist them"
        )

    llm_provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    if llm_provider not in VALID_LLM_PROVIDERS:
        problems.append(
            f"LLM_PROVIDER must be one of {sorted(VALID_LLM_PROVIDERS)}, got {llm_provider!r}"
        )

    gemini_api_key = (os.environ.get("GEMINI_API_KEY") or "").strip() or None
    if llm_provider == "gemini" and not gemini_api_key:
        # deployment.md §3 / NFR5.6: fail loudly rather than silently falling
        # back to a different provider the user did not choose.
        problems.append(
            "GEMINI_API_KEY is required when LLM_PROVIDER=gemini. Set it in .env, or "
            "switch to the local fallback with LLM_PROVIDER=ollama and "
            "`docker compose --profile local-llm up`."
        )

    if problems:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))

    return Settings(
        port=port,
        log_level=log_level,
        database_url=database_url,
        llm_provider=llm_provider,
        gemini_api_key=gemini_api_key,
        # Verified working model as of 2026-09-09 (architecture.md §3.6).
        gemini_model=(os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash").strip(),
        ollama_url=(os.environ.get("OLLAMA_URL") or "http://ollama:11434").strip().rstrip("/"),
        ollama_model=(os.environ.get("OLLAMA_MODEL") or "llama3.1").strip(),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return load_settings()

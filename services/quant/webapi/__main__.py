"""Entry point: ``python -m services.quant.webapi``.

Configuration is validated *before* uvicorn binds a port, so a missing
GEMINI_API_KEY or DATABASE_URL is a readable startup failure rather than a
container that appears healthy and fails on the first real request (NFR5.6).
"""

from __future__ import annotations

import sys

import uvicorn

from services.quant.config import ConfigError, load_settings
from services.quant.logging_setup import configure_logging


def main() -> int:
    try:
        settings = load_settings(dotenv_paths=(".env", "/app/.env"))
    except ConfigError as exc:
        print(f"quant-mcp: fatal: {exc}", file=sys.stderr)
        return 1

    configure_logging(settings.log_level)

    from services.quant.webapi.app import create_app

    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - container-local service, bound by Compose
        port=settings.port,
        log_config=None,  # our JSON formatter owns stdout
        access_log=False,  # the request-context middleware logs each request
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

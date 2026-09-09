"""FastAPI application for the `quant-mcp` service."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.quant import __version__
from services.quant.config import Settings, get_settings
from services.quant.db import DatabaseUnavailable, check_database
from services.quant.logging_setup import (
    configure_logging,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from services.quant.webapi.errors import (
    ErrorCode,
    ServiceError,
    error_response,
    service_error_handler,
)
from services.quant.webapi.routes import router as quant_router

REQUEST_ID_HEADER = "X-Request-ID"

log = logging.getLogger("services.quant.webapi")


class DependencyStatus(BaseModel):
    status: str
    error: str | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str = "quant-mcp"
    version: str
    dependencies: dict[str, DependencyStatus]
    checked_at: str


def _check_llm_provider(settings: Settings) -> DependencyStatus:
    """Report LLM provider readiness *without calling the provider*.

    maintenance-operations.md §3 is explicit: a health check must not call the
    Gemini API, or health pings would burn free-tier quota. Key presence is
    what we can verify cheaply, and that is what we claim to have verified.
    """
    if settings.llm_is_gemini:
        if not settings.gemini_api_key:
            return DependencyStatus(
                status="unhealthy",
                error="GEMINI_API_KEY is not set while LLM_PROVIDER=gemini",
            )
        return DependencyStatus(
            status="ok",
            detail=f"gemini key present, model={settings.gemini_model} (not called by this check)",
        )
    return DependencyStatus(
        status="ok",
        detail=f"ollama configured at {settings.ollama_url}, model={settings.ollama_model} (not called by this check)",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app. Settings are injected so tests can drive it directly."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="AI Quant Terminal — quant-mcp",
        version=__version__,
        description=(
            "Quantitative engine HTTP surface consumed by the Go API. "
            "Not a public API: it is reached only from inside the Compose network."
        ),
        docs_url="/docs",
    )
    app.state.settings = resolved

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
    ) -> JSONResponse:
        # Adopt the caller's ID when present so one user action has a single
        # ID across web -> api -> quant-mcp (NFR2).
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = set_request_id(request_id)
        started = datetime.now(UTC)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "http request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
                "request_id": request_id,
            },
        )
        return response

    app.add_exception_handler(ServiceError, service_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        return error_response(
            422,
            ErrorCode.INVALID_REQUEST,
            "The request body or query parameters were not valid.",
            details=exc.errors(),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "The quant service hit an unexpected internal error. "
            "The request_id below identifies this failure in the service logs.",
        )

    # Quant capabilities live under /v1 so the ops endpoints stay unversioned
    # and a future contract change does not have to move /healthz.
    app.include_router(quant_router, prefix="/v1")

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    def healthz() -> JSONResponse:
        dependencies: dict[str, DependencyStatus] = {}

        try:
            check_database(resolved.database_url)
            dependencies["database"] = DependencyStatus(status="ok")
        except DatabaseUnavailable as exc:
            dependencies["database"] = DependencyStatus(status="unhealthy", error=str(exc))

        dependencies["llm_provider"] = _check_llm_provider(resolved)

        healthy = all(d.status == "ok" for d in dependencies.values())
        body = HealthResponse(
            status="ok" if healthy else "degraded",
            version=__version__,
            dependencies=dependencies,
            checked_at=datetime.now(UTC).isoformat(),
        )
        return JSONResponse(
            status_code=200 if healthy else 503, content=body.model_dump(mode="json")
        )

    return app

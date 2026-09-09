"""The single error shape returned by every `quant-mcp` endpoint.

The Go API forwards these to the UI, so the codes here and the codes in
`apps/api/internal/httpapi/errors.go` are one vocabulary. A uniform, specific
shape is what lets the UI say *what* went wrong instead of "something went
wrong" (NFR5.6).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.quant.logging_setup import get_request_id


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    DATA_UNAVAILABLE = "data_unavailable"
    DATA_VALIDATION_FAILED = "data_validation_failed"
    UPSTREAM_FAILURE = "upstream_failure"
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL_ERROR = "internal_error"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str = Field(description="User-facing: what went wrong and, where possible, what to do")
    details: Any | None = None
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ServiceError(Exception):
    """An error whose cause is known and safe to show the user verbatim."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details

    def to_response(self) -> JSONResponse:
        return error_response(self.status_code, self.code, self.message, self.details)


def error_response(
    status_code: int, code: ErrorCode, message: str, details: Any | None = None
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details, request_id=get_request_id())
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def service_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ServiceError)
    return exc.to_response()

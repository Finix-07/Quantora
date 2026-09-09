"""Structured JSON logging for `quant-mcp`.

One JSON object per line on stdout so `docker compose logs` is the whole
observability story (deployment.md §8). Request IDs propagate through a
ContextVar so a log line emitted deep inside the backtester still carries the
ID of the request that triggered it (NFR2).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

# Attributes LogRecord always defines; anything else was passed via `extra=`
# and belongs in the JSON output.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := _request_id.get():
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "info") -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(_LEVELS.get(level, logging.INFO))


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def set_request_id(request_id: str | None) -> contextvars.Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def stage(logger: logging.Logger, name: str, **fields: object) -> Iterator[None]:
    """Time one pipeline stage and log its latency on its own line.

    Per-stage measurement (data retrieval, quant calculation, C++ simulation,
    DB persistence, AI orchestration) is required separately rather than as one
    aggregate number (maintenance-operations.md §2.3).
    """
    start = time.perf_counter()
    try:
        yield
    except Exception:
        logger.exception(
            "stage failed",
            extra={
                "stage": name,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                **fields,
            },
        )
        raise
    logger.info(
        "stage completed",
        extra={
            "stage": name,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            **fields,
        },
    )

"""Minimal database access for `quant-mcp`.

PostgreSQL is the system of record for experiments, but the Go API owns the
experiment lifecycle (architecture.md §3.2). This module therefore stays
deliberately small: what the Python service needs is the ability to prove the
database is reachable for its health check, plus a connection factory for the
read paths added in later milestones.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL cannot be reached or queried."""


@contextmanager
def connect(database_url: str, *, connect_timeout: int = 3) -> Iterator[Any]:
    """Open a short-lived connection, raising :class:`DatabaseUnavailable`.

    psycopg is imported lazily so that unit tests covering pure quant logic do
    not require the driver (or a database) to be installed.
    """
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise DatabaseUnavailable(f"psycopg is not installed: {exc}") from exc

    try:
        conn = psycopg.connect(database_url, connect_timeout=connect_timeout)
    except Exception as exc:
        raise DatabaseUnavailable(f"cannot connect to PostgreSQL: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()


def check_database(database_url: str, *, connect_timeout: int = 3) -> None:
    """Prove the database is reachable. Raises :class:`DatabaseUnavailable`."""
    with connect(database_url, connect_timeout=connect_timeout) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        if cur.fetchone() != (1,):
            raise DatabaseUnavailable("PostgreSQL responded to SELECT 1 with an unexpected result")

"""Canonical shapes for market data inside the quant engine.

Everything downstream — indicators, strategies, the backtester, the portfolio
engine — consumes :class:`PriceSeries`. Normalising once here means no component
has to know what shape a particular provider happened to return.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

# Column order is fixed so a serialized frame hashes identically across runs.
OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "adj_close", "volume")
TIMESTAMP_INDEX_NAME = "timestamp"

Interval = Literal["1d", "1wk", "1mo", "1h", "30m", "15m", "5m"]
SUPPORTED_INTERVALS: tuple[str, ...] = ("1d", "1wk", "1mo", "1h", "30m", "15m", "5m")

AdjustmentPolicy = Literal["raw", "split_and_dividend_adjusted"]


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a dataset came from and how it was treated.

    architecture.md §4.4 is explicit that a result without this metadata is not
    complete (NFR4). It travels with the data and is persisted alongside any
    experiment that used it, so a rerun can detect that the source has changed.
    """

    provider: str
    symbol: str
    provider_ticker: str
    interval: str
    timezone: str
    retrieval_timestamp: datetime
    adjustment_policy: AdjustmentPolicy
    requested_start: str
    requested_end: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    currency: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "provider_ticker": self.provider_ticker,
            "interval": self.interval,
            "timezone": self.timezone,
            "retrieval_timestamp": self.retrieval_timestamp.astimezone(UTC).isoformat(),
            "adjustment_policy": self.adjustment_policy,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "row_count": self.row_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """Validated OHLCV bars for one instrument, with their provenance.

    The frame is indexed by a timezone-aware, strictly increasing
    ``DatetimeIndex`` named ``timestamp`` and carries exactly
    :data:`OHLCV_COLUMNS`.
    """

    symbol: str
    interval: str
    frame: pd.DataFrame
    provenance: Provenance

    def __post_init__(self) -> None:
        missing = [c for c in OHLCV_COLUMNS if c not in self.frame.columns]
        if missing:
            raise ValueError(f"PriceSeries for {self.symbol} is missing columns: {missing}")
        if self.frame.index.name != TIMESTAMP_INDEX_NAME:
            raise ValueError(
                f"PriceSeries index must be named {TIMESTAMP_INDEX_NAME!r}, got {self.frame.index.name!r}"
            )

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def is_empty(self) -> bool:
        return self.frame.empty

    @property
    def close(self) -> pd.Series:
        return self.frame["close"]

    @property
    def start(self) -> pd.Timestamp | None:
        return None if self.is_empty else self.frame.index[0]

    @property
    def end(self) -> pd.Timestamp | None:
        return None if self.is_empty else self.frame.index[-1]

    def data_version(self) -> str:
        """A content hash identifying exactly this data.

        Reproducibility (NFR6) is defined as "same result given the same
        `data_version`". That only means something if the version is derived
        from the bar values themselves rather than from the request, because
        yfinance silently revises history: the same request can return different
        numbers a week later, and the rerun must be able to notice.

        Values are rounded to 6 decimal places before hashing so that float
        round-tripping through JSON or PostgreSQL does not change the version.
        """
        payload = {
            "symbol": self.symbol,
            "provider": self.provenance.provider,
            "provider_ticker": self.provenance.provider_ticker,
            "interval": self.interval,
            "adjustment_policy": self.provenance.adjustment_policy,
            "rows": [
                [ts.isoformat(), *(round(float(v), 6) for v in row)]
                for ts, row in zip(
                    self.frame.index,
                    self.frame[list(OHLCV_COLUMNS)].to_numpy(),
                    strict=True,
                )
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"sha256:{digest[:32]}"

    def to_records(self) -> list[dict[str, Any]]:
        """JSON-serializable bars, for the HTTP surface and charts."""
        return [
            {
                "timestamp": ts.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "adj_close": float(row.adj_close),
                "volume": float(row.volume),
            }
            for ts, row in zip(self.frame.index, self.frame.itertuples(index=False), strict=True)
        ]

    def slice(
        self, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None
    ) -> PriceSeries:
        """Return the same series restricted to a time window."""
        frame = self.frame
        if start is not None:
            frame = frame[frame.index >= start]
        if end is not None:
            frame = frame[frame.index <= end]
        return PriceSeries(
            symbol=self.symbol,
            interval=self.interval,
            frame=frame,
            provenance=self.provenance,
        )

"""Market-data providers.

The engine depends on the :class:`PriceProvider` protocol, not on yfinance.
That boundary is what lets tests use deterministic fixtures while the real
provider integration stays intact and exercised separately (a fixture that
*replaces* the provider in every test would leave the integration untested).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from services.quant.data.errors import ProviderError
from services.quant.data.types import OHLCV_COLUMNS, TIMESTAMP_INDEX_NAME

log = logging.getLogger(__name__)

PROVIDER_YFINANCE = "yfinance"

_YF_COLUMN_MAP = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "adj_close",
    "adjclose": "adj_close",
    "volume": "volume",
}


@runtime_checkable
class PriceProvider(Protocol):
    """Fetches raw OHLCV bars for one provider ticker."""

    name: str

    def fetch(
        self, provider_ticker: str, start: str, end: str, interval: str
    ) -> pd.DataFrame:  # pragma: no cover - protocol
        """Return a frame with a DatetimeIndex and the canonical OHLCV columns.

        Raises:
            ProviderError: when the provider fails, rate-limits, or returns no
                rows for a request that should have produced some.
        """
        ...


def normalize_frame(raw: pd.DataFrame, *, timezone: str) -> pd.DataFrame:
    """Coerce a provider's frame into the engine's canonical shape.

    Handles the two things yfinance actually does that break naive code: it
    returns MultiIndex columns even for a single ticker, and it returns a
    timezone-naive index for daily bars but a timezone-aware one for intraday.
    """
    frame = raw.copy()

    if isinstance(frame.columns, pd.MultiIndex):
        # ('Close', 'RELIANCE.NS') -> 'Close'. yfinance uses this shape even for
        # a single-ticker download.
        frame.columns = [str(level[0]) for level in frame.columns]

    frame.columns = [
        _YF_COLUMN_MAP.get(str(c).strip().lower(), str(c).strip().lower()) for c in frame.columns
    ]

    if "adj_close" not in frame.columns and "close" in frame.columns:
        # Some responses omit the adjusted series entirely. Carrying the raw
        # close under both names would silently claim an adjustment that never
        # happened, so the adjustment policy recorded in provenance stays "raw"
        # and the columns are simply equal.
        frame["adj_close"] = frame["close"]

    missing = [c for c in OHLCV_COLUMNS if c not in frame.columns]
    if missing:
        raise ProviderError(
            f"Provider response is missing required column(s): {', '.join(missing)}. "
            f"Received: {', '.join(map(str, frame.columns))}."
        )

    frame = frame[list(OHLCV_COLUMNS)].astype("float64")

    index = pd.DatetimeIndex(frame.index)
    # Daily NSE bars arrive as naive dates; intraday bars arrive tz-aware.
    # Localising the naive case to the exchange's own timezone keeps "the 3rd of
    # March" meaning the 3rd of March in Mumbai, which is what those timestamps
    # actually denote.
    index = index.tz_localize(timezone) if index.tz is None else index.tz_convert(timezone)
    index.name = TIMESTAMP_INDEX_NAME
    frame.index = index

    return frame.sort_index()


class YFinanceProvider:
    """yfinance-backed provider.

    yfinance wraps an unofficial Yahoo endpoint with no SLA, informal rate
    limits, and behaviour that can change without notice (architecture.md §4.1),
    so every failure mode here is converted into an explicit
    :class:`ProviderError` rather than surfacing as a pandas exception.
    """

    name = PROVIDER_YFINANCE

    def __init__(self, *, timezone: str = "Asia/Kolkata") -> None:
        self.timezone = timezone

    def fetch(self, provider_ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        try:
            import yfinance
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise ProviderError(f"yfinance is not installed: {exc}") from exc

        started = datetime.now(UTC)
        try:
            raw = yfinance.download(
                provider_ticker,
                start=start,
                end=end,
                interval=interval,
                # Raw OHLC is downloaded and the adjusted close kept alongside,
                # so the adjustment is applied by this codebase where it can be
                # recorded in provenance rather than by the provider's default.
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            raise ProviderError(
                f"yfinance failed to download {provider_ticker}: {exc}. "
                "yfinance has no SLA and rate-limits informally; retry, and check the symbol "
                "if the failure persists.",
                symbol=provider_ticker,
            ) from exc

        log.info(
            "data fetched",
            extra={
                "provider": self.name,
                "provider_ticker": provider_ticker,
                "interval": interval,
                "rows": 0 if raw is None else len(raw),
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
            },
        )

        if raw is None or raw.empty:
            raise ProviderError(
                f"yfinance returned no rows for {provider_ticker} between {start} and {end} "
                f"at interval {interval}. Common causes: the range predates the instrument's "
                "listing, the range contains no trading sessions, or the interval is not "
                "offered for this symbol.",
                symbol=provider_ticker,
            )

        return normalize_frame(raw, timezone=self.timezone)

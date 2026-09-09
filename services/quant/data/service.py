"""The data layer's public interface.

architecture.md §4.2 fixes the contract::

    get_prices(symbol, start, end, interval) -> OHLCV[+provenance]
    get_universe(name) -> [symbols]

`get_prices` resolves the symbol, fetches, normalises, validates and stamps
provenance. Nothing downstream is allowed to skip this path: an unvalidated
frame must never reach a backtest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from services.quant.data.errors import InvalidRequestError
from services.quant.data.provider import PriceProvider, YFinanceProvider
from services.quant.data.types import (
    SUPPORTED_INTERVALS,
    AdjustmentPolicy,
    PriceSeries,
    Provenance,
)
from services.quant.data.universe import get_instrument, get_universe
from services.quant.data.validation import DataQualityReport, validate_prices

log = logging.getLogger(__name__)

__all__ = ["MarketDataResult", "get_prices", "get_universe"]


@dataclass(frozen=True, slots=True)
class MarketDataResult:
    """Validated bars plus everything needed to judge whether to trust them."""

    series: PriceSeries
    quality: DataQualityReport

    @property
    def data_version(self) -> str:
        return self.series.data_version()

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.series.symbol,
            "interval": self.series.interval,
            "data_version": self.data_version,
            "provenance": self.series.provenance.as_dict(),
            "quality": self.quality.as_dict(),
            "bars": self.series.to_records(),
        }


def _as_iso_date(value: str | date | datetime, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise InvalidRequestError(
            f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}."
        ) from None


def _apply_adjustment(frame: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust OHLC by the adjusted-close ratio.

    Splits and dividends make a raw price series discontinuous, and a strategy
    reading that series sees a 1:10 split as a 90% crash. Scaling open/high/low
    by ``adj_close / close`` preserves each bar's internal geometry (the
    low <= open <= high relationship survives), which matters because those
    invariants are validated and because intrabar fills are simulated against
    them. Volume is left untouched: it is a share count, not a price.
    """
    adjusted = frame.copy()
    ratio = adjusted["adj_close"] / adjusted["close"]
    for column in ("open", "high", "low"):
        adjusted[column] = adjusted[column] * ratio
    adjusted["close"] = adjusted["adj_close"]
    return adjusted


def get_prices(
    symbol: str,
    start: str | date | datetime,
    end: str | date | datetime,
    interval: str = "1d",
    *,
    adjustment: AdjustmentPolicy = "split_and_dividend_adjusted",
    provider: PriceProvider | None = None,
) -> MarketDataResult:
    """Fetch validated OHLCV bars for one instrument.

    Args:
        symbol: A user-facing symbol from the configured universe.
        start: Inclusive ISO start date.
        end: Inclusive ISO end date. Note that yfinance treats its own `end` as
            exclusive; this function adds a day so the caller's range means what
            it says.
        interval: One of :data:`SUPPORTED_INTERVALS`.
        adjustment: ``split_and_dividend_adjusted`` (default) back-adjusts OHLC
            so a split does not read as a crash; ``raw`` returns unadjusted
            prices. Whichever is used is recorded in provenance.
        provider: Injected for tests and for future providers. Defaults to
            yfinance.

    Raises:
        UnknownSymbolError, InvalidRequestError, ProviderError,
        DataValidationError: all specific, all fatal — never a partial result.
    """
    instrument = get_instrument(symbol)

    if interval not in SUPPORTED_INTERVALS:
        raise InvalidRequestError(
            f"Unsupported interval {interval!r}. Supported: {', '.join(SUPPORTED_INTERVALS)}."
        )

    start_iso = _as_iso_date(start, "start")
    end_iso = _as_iso_date(end, "end")
    if start_iso > end_iso:
        raise InvalidRequestError(f"start ({start_iso}) must not be after end ({end_iso}).")

    active_provider = provider or YFinanceProvider(timezone=instrument.timezone)

    # yfinance's `end` is exclusive, which silently drops the last requested
    # session. The caller's range is inclusive at both ends.
    fetch_end = (date.fromisoformat(end_iso) + timedelta(days=1)).isoformat()

    frame = active_provider.fetch(instrument.provider_ticker, start_iso, fetch_end, interval)

    # Trim to the requested window: yfinance sometimes returns bars slightly
    # outside the requested range, and a saved experiment's date range must
    # describe the data it actually used.
    tz = frame.index.tz
    window_start = pd.Timestamp(start_iso, tz=tz)
    window_end = pd.Timestamp(end_iso, tz=tz) + pd.Timedelta(days=1)
    frame = frame[(frame.index >= window_start) & (frame.index < window_end)]

    policy: AdjustmentPolicy = adjustment
    if adjustment == "split_and_dividend_adjusted":
        frame = _apply_adjustment(frame)
    elif adjustment != "raw":
        raise InvalidRequestError(
            f"Unsupported adjustment policy {adjustment!r}. Supported: raw, "
            "split_and_dividend_adjusted."
        )

    quality = validate_prices(frame, symbol=symbol, interval=interval)

    provenance = Provenance(
        provider=active_provider.name,
        symbol=instrument.symbol,
        provider_ticker=instrument.provider_ticker,
        interval=interval,
        timezone=instrument.timezone,
        retrieval_timestamp=datetime.now(UTC),
        adjustment_policy=policy,
        requested_start=start_iso,
        requested_end=end_iso,
        row_count=len(frame),
        first_timestamp=None if frame.empty else frame.index[0].isoformat(),
        last_timestamp=None if frame.empty else frame.index[-1].isoformat(),
        currency=instrument.currency,
    )

    series = PriceSeries(
        symbol=instrument.symbol, interval=interval, frame=frame, provenance=provenance
    )

    log.info(
        "data loaded",
        extra={
            "symbol": symbol,
            "interval": interval,
            "rows": len(frame),
            "data_version": series.data_version(),
            "quality": quality.severity,
        },
    )

    return MarketDataResult(series=series, quality=quality)

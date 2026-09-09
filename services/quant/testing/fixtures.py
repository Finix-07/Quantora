"""Builders for deterministic market-data fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from services.quant.data.errors import ProviderError
from services.quant.data.types import (
    OHLCV_COLUMNS,
    TIMESTAMP_INDEX_NAME,
    AdjustmentPolicy,
    PriceSeries,
    Provenance,
)

DEFAULT_TZ = "Asia/Kolkata"


def make_ohlcv_frame(
    closes: list[float] | np.ndarray,
    *,
    start: str = "2024-01-01",
    freq: str = "B",
    tz: str = DEFAULT_TZ,
    volume: float | list[float] = 1_000_000.0,
    spread: float = 0.01,
    timestamps: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Build a canonical OHLCV frame from a close-price path.

    Open/high/low are derived from the closes so that ``low <= open <= high``
    and ``low <= close <= high`` hold by construction. Tests that need to break
    a rule do so explicitly by mutating the returned frame, which keeps the
    intent of each malformed fixture visible at its call site.
    """
    closes = np.asarray(closes, dtype="float64")
    n = len(closes)

    if timestamps is None:
        # `B` gives business days, so a fixture never accidentally contains a
        # weekend bar that the gap detector would then report.
        index = pd.date_range(start=start, periods=n, freq=freq, tz=tz)
    else:
        index = timestamps
        if len(index) != n:
            raise ValueError(f"timestamps has {len(index)} entries but {n} closes were given")
    index = pd.DatetimeIndex(index)
    index.name = TIMESTAMP_INDEX_NAME

    opens = np.empty(n, dtype="float64")
    if n:
        # Open at the previous close, giving a continuous path. Guarded because
        # an empty series is a legitimate fixture (a date range with no trading
        # sessions), and indexing closes[0] would crash before the frame could
        # be built.
        opens[0] = closes[0]
        opens[1:] = closes[:-1]

    highs = np.maximum(opens, closes) * (1.0 + spread)
    lows = np.minimum(opens, closes) * (1.0 - spread)

    volumes = (
        np.full(n, float(volume), dtype="float64")
        if isinstance(volume, int | float)
        else np.asarray(volume, dtype="float64")
    )

    frame = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        },
        index=index,
    )
    return frame[list(OHLCV_COLUMNS)]


def make_provenance(
    symbol: str = "RELIANCE.NS",
    *,
    frame: pd.DataFrame | None = None,
    interval: str = "1d",
    provider: str = "fixture",
    adjustment_policy: AdjustmentPolicy = "split_and_dividend_adjusted",
    tz: str = DEFAULT_TZ,
) -> Provenance:
    """Provenance for a fixture, shaped exactly like the real thing."""
    first = None if frame is None or frame.empty else frame.index[0].isoformat()
    last = None if frame is None or frame.empty else frame.index[-1].isoformat()
    return Provenance(
        provider=provider,
        symbol=symbol,
        provider_ticker=symbol,
        interval=interval,
        timezone=tz,
        # Fixed rather than "now": provenance must not make an otherwise
        # deterministic fixture change between runs.
        retrieval_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        adjustment_policy=adjustment_policy,
        requested_start=(first or "")[:10],
        requested_end=(last or "")[:10],
        row_count=0 if frame is None else len(frame),
        first_timestamp=first,
        last_timestamp=last,
        currency="INR",
    )


def make_price_series(
    closes: list[float] | np.ndarray,
    *,
    symbol: str = "RELIANCE.NS",
    interval: str = "1d",
    start: str = "2024-01-01",
    tz: str = DEFAULT_TZ,
    **frame_kwargs: object,
) -> PriceSeries:
    """A validated-shape :class:`PriceSeries` built from a close path."""
    frame = make_ohlcv_frame(closes, start=start, tz=tz, **frame_kwargs)  # type: ignore[arg-type]
    return PriceSeries(
        symbol=symbol,
        interval=interval,
        frame=frame,
        provenance=make_provenance(symbol, frame=frame, interval=interval, tz=tz),
    )


class FakeProvider:
    """A :class:`~services.quant.data.provider.PriceProvider` backed by fixtures.

    Used to test the data *pipeline* — normalisation, trimming, adjustment,
    validation, provenance — without a network call. The real yfinance
    integration is covered separately by `network`-marked tests, so replacing
    the provider here never leaves the integration untested.
    """

    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | pd.DataFrame,
        *,
        name: str = "fixture",
        fail_with: Exception | None = None,
    ) -> None:
        self.frames = frames if isinstance(frames, dict) else {"*": frames}
        self.name = name
        self.fail_with = fail_with
        self.calls: list[tuple[str, str, str, str]] = []

    def fetch(self, provider_ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        self.calls.append((provider_ticker, start, end, interval))
        if self.fail_with is not None:
            raise self.fail_with
        frame = self.frames.get(provider_ticker, self.frames.get("*"))
        if frame is None:
            raise ProviderError(
                f"fixture has no data for {provider_ticker}", symbol=provider_ticker
            )
        return frame.copy()

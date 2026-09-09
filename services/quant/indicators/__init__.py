"""Indicator library.

Indicators are independent of strategies (requirements.md FR2): each takes a
price series and returns a series, with no knowledge of signals, positions or
backtests. That separation is what lets the Market view chart an indicator
without running a strategy, and lets two strategies share one implementation.

Every indicator here is causal — the value at bar *t* uses only bars ``<= t`` —
and returns NaN during its warm-up rather than a partial result.
"""

from services.quant.indicators.bollinger import BollingerResult, bollinger_bands
from services.quant.indicators.correlation import (
    SeriesAlignmentError,
    hedge_ratio,
    rolling_correlation,
)
from services.quant.indicators.macd import (
    DEFAULT_FAST,
    DEFAULT_SIGNAL,
    DEFAULT_SLOW,
    MACDResult,
    macd,
)
from services.quant.indicators.moving_average import ema, rolling_std, sma
from services.quant.indicators.zscore import rolling_zscore

__all__ = [
    "BollingerResult",
    "DEFAULT_FAST",
    "DEFAULT_SIGNAL",
    "DEFAULT_SLOW",
    "MACDResult",
    "SeriesAlignmentError",
    "bollinger_bands",
    "ema",
    "hedge_ratio",
    "macd",
    "rolling_correlation",
    "rolling_std",
    "rolling_zscore",
    "sma",
]

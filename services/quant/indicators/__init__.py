"""Indicator library.

Indicators are independent of strategies (requirements.md FR2): each takes a
price series and returns a series, with no knowledge of signals, positions or
backtests. That separation is what lets the Market view chart an indicator
without running a strategy, and lets two strategies share one implementation.

Every indicator here is causal — the value at bar *t* uses only bars ``<= t`` —
and returns NaN during its warm-up rather than a partial result.
"""

from services.quant.indicators.macd import (
    DEFAULT_FAST,
    DEFAULT_SIGNAL,
    DEFAULT_SLOW,
    MACDResult,
    macd,
)
from services.quant.indicators.moving_average import ema, rolling_std, sma

__all__ = [
    "DEFAULT_FAST",
    "DEFAULT_SIGNAL",
    "DEFAULT_SLOW",
    "MACDResult",
    "ema",
    "macd",
    "rolling_std",
    "sma",
]

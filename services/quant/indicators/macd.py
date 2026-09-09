"""MACD — Moving Average Convergence Divergence."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from services.quant.indicators.moving_average import ema

DEFAULT_FAST = 12
DEFAULT_SLOW = 26
DEFAULT_SIGNAL = 9


@dataclass(frozen=True, slots=True)
class MACDResult:
    """The three MACD series, kept together so they cannot be mismatched."""

    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series
    fast: int
    slow: int
    signal_span: int

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"macd": self.macd, "macd_signal": self.signal, "macd_histogram": self.histogram}
        )

    @property
    def parameters(self) -> dict[str, int]:
        """The exact parameters used, for display and for saved experiments."""
        return {"fast": self.fast, "slow": self.slow, "signal": self.signal_span}


def macd(
    series: pd.Series,
    fast: int = DEFAULT_FAST,
    slow: int = DEFAULT_SLOW,
    signal: int = DEFAULT_SIGNAL,
) -> MACDResult:
    """Compute MACD, its signal line, and the histogram.

    ``macd = ema(fast) - ema(slow)``; ``signal = ema(macd, signal)``;
    ``histogram = macd - signal``.

    The signal line is an EMA *of the MACD line*, and the MACD line is NaN until
    the slow EMA is seeded. Those leading NaNs are dropped before seeding the
    signal EMA and the result is reindexed back, so the signal is seeded from the
    first ``signal`` real MACD values rather than from NaNs — otherwise the whole
    signal line comes out NaN.

    Raises:
        ValueError: if ``fast >= slow``. A MACD whose "fast" average is slower
            than its "slow" one inverts the sign of every crossover, which would
            silently turn a momentum strategy into its own opposite.
    """
    if fast >= slow:
        raise ValueError(
            f"fast span ({fast}) must be strictly less than slow span ({slow}); "
            "otherwise every crossover signal is inverted."
        )

    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = (fast_ema - slow_ema).rename("macd")

    seeded = macd_line.dropna()
    signal_line = ema(seeded, signal).reindex(macd_line.index).rename("macd_signal")

    histogram = (macd_line - signal_line).rename("macd_histogram")

    return MACDResult(
        macd=macd_line,
        signal=signal_line,
        histogram=histogram,
        fast=fast,
        slow=slow,
        signal_span=signal,
    )

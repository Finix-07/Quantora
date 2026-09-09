"""Rolling z-score — how many standard deviations a value sits from its own
recent mean.

This is the statistic a mean-reversion strategy trades on: it turns a raw level
(a spread, a price) into a scale-free "how unusual is this?" number, so the same
threshold means the same thing for a ₹200 stock and a ₹4,000 one.

Causal, like every indicator here: the value at bar *t* uses only bars ``<= t``.
"""

from __future__ import annotations

import pandas as pd

from services.quant.indicators.moving_average import rolling_std, sma


def _validate_window(window: int, minimum: int = 2) -> None:
    if not isinstance(window, int) or isinstance(window, bool):
        raise TypeError(f"window must be an int, got {type(window).__name__}")
    if window < minimum:
        raise ValueError(f"window must be >= {minimum}, got {window}")


def rolling_zscore(series: pd.Series, window: int, *, ddof: int = 0) -> pd.Series:
    """``(value - rolling mean) / rolling standard deviation``.

    The first ``window - 1`` values are NaN — the mean and the dispersion the
    caller asked for do not exist yet.

    ``window`` must be at least 2: a one-bar window has zero dispersion by
    definition, so every z-score would be undefined and the result would be a
    series of NaN dressed up as an indicator.

    ``ddof`` is forwarded to :func:`~services.quant.indicators.moving_average.rolling_std`
    and defaults to the population estimator, matching the convention used by the
    band-style indicators in this package.

    Returns NaN — never 0.0 — wherever the rolling standard deviation is zero.
    That case is a genuine ``0 / 0``: with no dispersion there is no scale to
    measure the deviation against, so the statistic does not exist. Emitting 0.0
    would tell a strategy "the value is exactly at its mean", which is a
    confident, tradable claim that the data does not support; NaN says "no
    opinion", which is the truth, and every strategy here already treats NaN as
    "stay flat".
    """
    _validate_window(window)

    mean = sma(series, window)
    dispersion = rolling_std(series, window, ddof=ddof)

    # `where` keeps the value only where the condition holds and writes NaN
    # elsewhere, so a zero (or NaN) denominator propagates as NaN instead of
    # producing an infinity or a fabricated zero.
    denominator = dispersion.where(dispersion > 0)
    return ((series - mean) / denominator).rename(f"zscore_{window}")

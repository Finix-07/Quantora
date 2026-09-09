"""Two-series statistics: rolling correlation and rolling hedge ratio.

Everything else in this package describes one instrument. These two describe a
*relationship* between two, which is what a statistical-arbitrage strategy trades:
whether the legs of a pair still move together, and in what proportion.

Both are causal — the value at bar *t* uses only bars ``<= t`` — and both return
NaN, never a fabricated number, wherever the statistic is undefined.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class SeriesAlignmentError(ValueError):
    """Two series that must describe the same bars do not.

    A rolling statistic over two series is only meaningful if position *i* in
    both refers to the same moment in time. Silently letting pandas align them
    would pair a Monday with a Tuesday wherever one instrument did not trade, and
    the resulting correlation would be an artefact of the calendar rather than a
    property of the instruments. Callers align explicitly (an inner join on the
    common timestamps) before calling in here.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _validate_window(window: int, minimum: int = 2) -> None:
    if not isinstance(window, int) or isinstance(window, bool):
        raise TypeError(f"window must be an int, got {type(window).__name__}")
    if window < minimum:
        raise ValueError(f"window must be >= {minimum}, got {window}")


def _require_same_index(a: pd.Series, b: pd.Series) -> None:
    if not a.index.equals(b.index):
        raise SeriesAlignmentError(
            f"the two series must share an index: got {len(a)} and {len(b)} rows "
            f"({a.name!r} vs {b.name!r}). Align them on their common timestamps first — "
            "an unaligned rolling statistic compares different days to each other."
        )


def rolling_correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """Pearson correlation of ``a`` and ``b`` over a trailing ``window``.

    The first ``window - 1`` values are NaN, as is any window in which either
    series has zero variance. A flat series has no direction to co-move in, so
    its correlation with anything is undefined — 0.0 would read as "these two are
    unrelated", which is a stronger statement than the data supports.

    ``window`` must be at least 2: a single observation has no dispersion, so a
    one-bar correlation is undefined everywhere.
    """
    _validate_window(window)
    _require_same_index(a, b)

    rolling_a = a.rolling(window=window, min_periods=window)
    rolling_b = b.rolling(window=window, min_periods=window)

    # Population moments (ddof=0) throughout. Correlation is invariant to the
    # choice as long as covariance and variances use the same one; ddof=0 matches
    # `rolling_std`'s default so the numbers in this package stay comparable.
    covariance = rolling_a.cov(b, ddof=0)
    denominator = np.sqrt(rolling_a.var(ddof=0) * rolling_b.var(ddof=0))

    correlation = covariance / denominator.where(denominator > 0)
    # Correlation is bounded by ±1 mathematically; floating-point error can push
    # an exactly-collinear window a few ulps past the bound. Clipping restores
    # the invariant callers rely on when comparing against a threshold, and
    # cannot mask a real value because no real value lives outside it.
    return correlation.clip(-1.0, 1.0).rename(f"corr_{window}")


def hedge_ratio(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """Rolling OLS slope of ``a`` regressed on ``b``: ``cov(a, b) / var(b)``.

    This is the number of units of ``b`` that hedge one unit of ``a`` over the
    trailing window — the β in the spread ``a - β·b``. It is deliberately
    *rolling* rather than fitted once over the whole sample: a single
    full-sample β is fitted using data from the end of the backtest, which is
    look-ahead bias in its purest form (NFR5.3).

    NaN during warm-up and wherever ``b`` has zero variance in the window: with
    no variation in the explanatory leg the slope is unidentifiable, and any
    number returned there would be invented.
    """
    _validate_window(window)
    _require_same_index(a, b)

    rolling_a = a.rolling(window=window, min_periods=window)
    rolling_b = b.rolling(window=window, min_periods=window)

    covariance = rolling_a.cov(b, ddof=0)
    variance_b = rolling_b.var(ddof=0)

    return (covariance / variance_b.where(variance_b > 0)).rename(f"hedge_ratio_{window}")

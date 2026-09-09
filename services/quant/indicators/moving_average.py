"""Moving averages.

Every function here is *causal*: the value at bar *t* depends only on bars
``<= t``. That is the property the look-ahead-bias guarantee (NFR5.3) rests on,
and it is asserted directly in the tests rather than assumed from pandas'
behaviour.

Warm-up periods produce ``NaN`` rather than a partial average. A 20-day mean
computed from 3 observations is not a 20-day mean, and letting one through would
put a differently-defined number into a strategy's first signals.
"""

from __future__ import annotations

import pandas as pd


def _validate_window(window: int, name: str = "window") -> None:
    if not isinstance(window, int) or isinstance(window, bool):
        raise TypeError(f"{name} must be an int, got {type(window).__name__}")
    if window < 1:
        raise ValueError(f"{name} must be >= 1, got {window}")


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average over ``window`` bars.

    The first ``window - 1`` values are NaN: there is not yet enough history to
    compute the average the caller asked for.
    """
    _validate_window(window)
    return series.rolling(window=window, min_periods=window).mean().rename(f"sma_{window}")


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with smoothing factor ``2 / (span + 1)``.

    Seeded with the simple average of the first ``span`` observations, which is
    the classical technical-analysis definition (and what ta-lib does). The
    alternative — pandas' default of seeding from the first observation alone —
    makes early values depend heavily on one arbitrary bar and, more importantly,
    makes the result depend on *where the series was sliced*: the same date would
    get a different EMA in a 1-year window than in a 5-year window, which would
    quietly break reproducibility across date ranges.

    Values before the seed are NaN.
    """
    _validate_window(span, "span")

    values = series.to_numpy(dtype="float64", copy=True)
    n = len(values)
    out = pd.Series(float("nan"), index=series.index, dtype="float64", name=f"ema_{span}")
    if n < span:
        return out

    alpha = 2.0 / (span + 1.0)
    # Seed: the simple mean of the first `span` observations.
    current = float(values[:span].mean())
    out.iloc[span - 1] = current
    for i in range(span, n):
        current = alpha * float(values[i]) + (1.0 - alpha) * current
        out.iloc[i] = current
    return out


def rolling_std(series: pd.Series, window: int, *, ddof: int = 0) -> pd.Series:
    """Rolling standard deviation.

    ``ddof=0`` (population) by default because Bollinger Bands are conventionally
    defined that way; passing ``ddof=1`` gives the sample estimator. Which one is
    used changes the band width, so it is an explicit parameter rather than an
    inherited pandas default.
    """
    _validate_window(window)
    return series.rolling(window=window, min_periods=window).std(ddof=ddof).rename(f"std_{window}")

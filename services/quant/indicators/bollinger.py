"""Bollinger Bands."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from services.quant.indicators.moving_average import rolling_std, sma

DEFAULT_WINDOW = 20
DEFAULT_NUM_STD = 2.0


@dataclass(frozen=True, slots=True)
class BollingerResult:
    """The band series, kept together so they cannot be mismatched."""

    middle: pd.Series
    upper: pd.Series
    lower: pd.Series
    bandwidth: pd.Series
    percent_b: pd.Series
    window: int
    num_std: float

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "bb_middle": self.middle,
                "bb_upper": self.upper,
                "bb_lower": self.lower,
                "bb_bandwidth": self.bandwidth,
                "bb_percent_b": self.percent_b,
            }
        )

    @property
    def parameters(self) -> dict[str, float]:
        """The exact parameters used, for display and for saved experiments."""
        return {"window": self.window, "num_std": self.num_std}


def bollinger_bands(
    series: pd.Series,
    window: int = DEFAULT_WINDOW,
    num_std: float = DEFAULT_NUM_STD,
) -> BollingerResult:
    """Compute Bollinger Bands: a moving average with a volatility envelope.

    ``middle = sma(window)``; ``upper/lower = middle +/- num_std * rolling_std``,
    using the population standard deviation (``ddof=0``) — the conventional
    definition. ``bandwidth`` is the normalized band width ``(upper - lower) /
    middle``, and ``percent_b`` locates the price within the band: 0 at the
    lower band, 1 at the upper band, and outside [0, 1] when price pierces a
    band.

    Where the band has zero width (a perfectly flat window: ``upper == lower``)
    ``percent_b`` is ``0/0`` and left as NaN rather than defaulting to 0.5 or
    0.0. A fabricated midpoint would tell a strategy the price is exactly
    centred in the band when in truth the indicator is undefined for that bar.

    Raises:
        ValueError: if ``window < 2`` (a 1-bar rolling std is always zero, which
            would make every band collapse to the middle line) or ``num_std <=
            0`` (a non-positive multiplier inverts or collapses the envelope).
    """
    if window < 2:
        raise ValueError(
            f"window must be >= 2, got {window}; a 1-bar rolling standard "
            "deviation is always zero and would collapse the bands onto the "
            "middle line."
        )
    if num_std <= 0:
        raise ValueError(
            f"num_std must be > 0, got {num_std}; a non-positive multiplier "
            "would invert or collapse the band envelope."
        )

    middle = sma(series, window).rename("bb_middle")
    std = rolling_std(series, window)
    upper = (middle + num_std * std).rename("bb_upper")
    lower = (middle - num_std * std).rename("bb_lower")

    band_width = upper - lower
    bandwidth = (band_width / middle).rename("bb_bandwidth")

    # percent_b is (price - lower) / (upper - lower). Where the band has
    # collapsed to zero width that is 0/0 — undefined, not the 0.5 a naive
    # reading might assume. `replace(0, nan)` turns that division into NaN/NaN
    # -> NaN explicitly, rather than relying on pandas' own 0/0 -> NaN behaviour
    # to be obvious to a future reader.
    percent_b = ((series - lower) / band_width.replace(0, np.nan)).rename("bb_percent_b")

    return BollingerResult(
        middle=middle,
        upper=upper,
        lower=lower,
        bandwidth=bandwidth,
        percent_b=percent_b,
        window=window,
        num_std=num_std,
    )

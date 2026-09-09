"""MACD / momentum strategy (requirements.md FR3, family 1 of 4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.quant.data.types import PriceSeries
from services.quant.indicators.macd import macd as macd_indicator
from services.quant.strategies.base import (
    InvalidParametersError,
    ParameterSpec,
    SignalDirection,
    SignalSet,
    Strategy,
    empty_signal_frame,
)
from services.quant.strategies.registry import register


@register
class MACDStrategy(Strategy):
    """Trade the MACD line's crossovers of its signal line.

    The target goes long on an upward crossover and, when ``allow_short`` is on,
    short on a downward one; otherwise a downward crossover flattens. Between
    crossovers the previous target is held — a momentum strategy is supposed to
    stay in a trend, and re-deciding every bar would churn the position for no
    reason.

    ``require_zero_line`` adds the common trend filter: only take the long side
    while the MACD line itself is above zero (and the short side while below).
    It trades less and misses early reversals; it is off by default so the
    baseline strategy is the textbook one.
    """

    name = "macd"
    family = "momentum"
    description = (
        "Momentum. Goes long when the MACD line crosses above its signal line and "
        "exits (or reverses) when it crosses back below."
    )
    parameter_specs = (
        ParameterSpec("fast", "int", 12, "Fast EMA span", minimum=2, maximum=200),
        ParameterSpec("slow", "int", 26, "Slow EMA span", minimum=3, maximum=400),
        ParameterSpec("signal", "int", 9, "Signal-line EMA span", minimum=2, maximum=100),
        ParameterSpec(
            "allow_short",
            "bool",
            False,
            "Take the short side on a downward crossover instead of going flat",
        ),
        ParameterSpec(
            "require_zero_line",
            "bool",
            False,
            "Only hold long while the MACD line is above zero (and short while below)",
        ),
    )

    def _validate_parameters(self) -> None:
        fast = self._parameters["fast"]
        slow = self._parameters["slow"]
        if fast >= slow:
            raise InvalidParametersError(
                f"fast ({fast}) must be strictly less than slow ({slow}); otherwise every "
                "crossover signal is inverted and the strategy trades its own opposite."
            )

    @property
    def warmup_bars(self) -> int:
        # The slow EMA seeds after `slow` bars; the signal EMA then needs
        # `signal` more MACD values before the first crossover can be observed.
        return int(self._parameters["slow"]) + int(self._parameters["signal"])

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        self.require_warmup(series)

        params = self.parameters
        result = macd_indicator(
            series.close, fast=params["fast"], slow=params["slow"], signal=params["signal"]
        )

        frame = empty_signal_frame(series.frame.index)
        histogram = result.histogram

        # A crossover is a sign change in (macd - signal). Comparing against the
        # *previous* bar's histogram keeps the decision causal: bar t uses only
        # bars <= t.
        previous = histogram.shift(1)
        both_known = histogram.notna() & previous.notna()
        crossed_up = both_known & (previous <= 0) & (histogram > 0)
        crossed_down = both_known & (previous >= 0) & (histogram < 0)

        if params["require_zero_line"]:
            crossed_up &= result.macd > 0
            crossed_down &= result.macd < 0

        target = pd.Series(np.nan, index=frame.index, dtype="float64")
        target[crossed_up] = float(SignalDirection.LONG)
        target[crossed_down] = (
            float(SignalDirection.SHORT) if params["allow_short"] else float(SignalDirection.FLAT)
        )
        # Hold the last decision between crossovers; stay flat before the first.
        target = target.ffill().fillna(float(SignalDirection.FLAT))

        frame["direction"] = target.astype("int64")

        # Conviction scales with how far the histogram has separated, normalised
        # by its own recent dispersion so the scale is comparable across
        # instruments priced in the hundreds and in the tens of thousands.
        dispersion = histogram.abs().expanding(min_periods=2).mean()
        strength = (histogram.abs() / dispersion).clip(upper=1.0).fillna(0.0)
        frame["strength"] = np.where(frame["direction"] != 0, strength, 0.0)

        reasons = pd.Series("", index=frame.index, dtype="object")
        reasons[crossed_up] = "MACD crossed above its signal line"
        reasons[crossed_down] = (
            "MACD crossed below its signal line — reversing short"
            if params["allow_short"]
            else "MACD crossed below its signal line — exiting"
        )
        frame["reason"] = reasons

        return SignalSet(
            symbol=series.symbol,
            strategy=self.name,
            parameters=params,
            frame=frame,
            indicators=result.to_frame(),
            warmup_bars=self.warmup_bars,
        )

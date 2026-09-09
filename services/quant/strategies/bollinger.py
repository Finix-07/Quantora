"""Bollinger Bands / mean-reversion strategy (requirements.md FR3, family 2 of 4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.quant.data.types import PriceSeries
from services.quant.indicators.bollinger import bollinger_bands
from services.quant.strategies.base import (
    ParameterSpec,
    SignalDirection,
    SignalSet,
    Strategy,
    empty_signal_frame,
)
from services.quant.strategies.registry import register


@register
class BollingerStrategy(Strategy):
    """Trade a close price's excursions outside its own Bollinger Bands.

    The opposite bet from a momentum strategy: a close below the lower band is
    read as oversold and targets LONG, expecting reversion back toward the
    middle band, and a close above the upper band targets SHORT (or FLAT when
    ``allow_short`` is off) rather than "continuing the breakout". Between band
    touches the previous target is held, or — with ``exit_at_middle`` — closed
    as soon as price reverts back across the middle band, so a mean-reversion
    trade does not have to wait for the full round trip to the opposite band to
    realise its profit.
    """

    name = "bollinger"
    family = "mean_reversion"
    description = (
        "Mean reversion. Goes long when the close breaks below the lower Bollinger "
        "Band (oversold) and short (or flat) when it breaks above the upper band, "
        "exiting on a reversion to the middle band by default."
    )
    parameter_specs = (
        ParameterSpec(
            "window", "int", 20, "Bollinger Bands lookback window", minimum=2, maximum=400
        ),
        ParameterSpec(
            "num_std",
            "float",
            2.0,
            "Number of standard deviations the bands sit from the middle band",
            minimum=0.1,
            maximum=5.0,
        ),
        ParameterSpec(
            "exit_at_middle",
            "bool",
            True,
            "Close the position when price reverts to the middle band, rather than "
            "waiting for the opposite band to be touched",
        ),
        ParameterSpec(
            "allow_short",
            "bool",
            False,
            "Take the short side on an upper-band touch instead of staying flat",
        ),
    )

    @property
    def warmup_bars(self) -> int:
        return int(self._parameters["window"])

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        self.require_warmup(series)

        params = self.parameters
        result = bollinger_bands(series.close, window=params["window"], num_std=params["num_std"])
        close = series.close

        frame = empty_signal_frame(series.frame.index)

        seeded = result.middle.notna()
        below_lower = seeded & (close < result.lower)
        above_upper = seeded & (close > result.upper)

        # A "cross" of the middle band, not merely being on one side of it, so
        # the exit fires only on the bar the reversion actually happens rather
        # than on every bar the price already sits past the midpoint. Both
        # sides of the comparison are taken at consistent times — previous
        # close against previous middle, current close against current middle
        # — since the middle band itself moves bar to bar; comparing today's
        # close against yesterday's band level (or vice versa) would flag a
        # crossing that never happened relative to either single timestamp.
        previous_close = close.shift(1)
        previous_middle = result.middle.shift(1)
        both_seeded = seeded & previous_middle.notna()
        crossed_above_middle = (
            both_seeded & (previous_close <= previous_middle) & (close > result.middle)
        )
        crossed_below_middle = (
            both_seeded & (previous_close >= previous_middle) & (close < result.middle)
        )

        entry_target = pd.Series(np.nan, index=frame.index, dtype="float64")
        if params["exit_at_middle"]:
            entry_target[crossed_above_middle] = float(SignalDirection.FLAT)
            entry_target[crossed_below_middle] = float(SignalDirection.FLAT)

        # Band touches are applied *after* the middle-band exit so that, on the
        # rare bar where both conditions coincide (e.g. a flat, zero-width
        # band), the fresh entry decision wins over the exit — a same-bar
        # reversal is a real decision, not a hold-at-flat.
        entry_target[below_lower] = float(SignalDirection.LONG)
        entry_target[above_upper] = (
            float(SignalDirection.SHORT) if params["allow_short"] else float(SignalDirection.FLAT)
        )

        # Hold the last decision between band touches (and middle crossings, if
        # enabled); stay flat before the bands are seeded.
        target = entry_target.ffill().fillna(float(SignalDirection.FLAT))
        frame["direction"] = target.astype("int64")

        # A band touch is a binary event, like a MACD crossover: conviction is
        # full while positioned and zero while flat. Grading conviction by how
        # far outside the band the price closed was tried and rejected for the
        # same reason as in macd.py, just from the other direction — there the
        # triggering quantity is near-zero at entry; here it can be arbitrarily
        # large (a single wild bar reads as maximal conviction) and unbounded
        # across instruments, so a graded score would not be comparable between
        # strategies or symbols. The binary choice keeps sizing comparable
        # across strategies for M3.7's cross-strategy comparison.
        frame["strength"] = np.where(frame["direction"] != 0, 1.0, 0.0)

        # `below_lower`/`above_upper` are *level* conditions and can stay true
        # for several consecutive bars during a sustained move, unlike a MACD
        # crossover which is a sign change and so is true for exactly one bar.
        # Restricting reasons to bars where the target actually changed avoids
        # relabelling every bar of an already-held position as "entering".
        changed = frame["direction"].ne(frame["direction"].shift())
        reasons = pd.Series("", index=frame.index, dtype="object")
        if params["exit_at_middle"]:
            reasons[crossed_above_middle & changed] = (
                "close crossed above the middle band — exiting long"
            )
            reasons[crossed_below_middle & changed] = (
                "close crossed below the middle band — exiting short"
            )
        reasons[below_lower & changed] = "close below the lower band — oversold, entering long"
        reasons[above_upper & changed] = (
            "close above the upper band — overbought, entering short"
            if params["allow_short"]
            else "close above the upper band — overbought, exiting/staying flat"
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

"""Dual Thrust breakout strategy (requirements.md FR3, family 2 of 4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.quant.data.types import PriceSeries
from services.quant.strategies.base import (
    ParameterSpec,
    SignalDirection,
    SignalSet,
    Strategy,
    empty_signal_frame,
)
from services.quant.strategies.registry import register


@register
class DualThrustStrategy(Strategy):
    """Trade breakouts of a volatility range built from prior bars.

    Each bar's ``range`` is ``max(HH - LC, HC - LL)`` taken over the last
    ``lookback`` *completed* bars (HH/LL the highest high and lowest low, HC/LC
    the highest and lowest close). That range is projected off the current
    bar's open to get an upper and a lower trigger; price trading through
    either trigger targets a breakout position, and the previous target is
    held while price stays inside the band — a breakout strategy is supposed
    to ride the move it caught, not re-decide every bar.

    ``trigger_on`` controls what "trading through" means. ``"close"`` (the
    default) compares the bar's close to the triggers, which is the only
    fully honest reading of a daily bar: it uses only the one price a daily
    bar actually commits to. ``"intrabar"`` compares the bar's high/low to the
    triggers instead, which is what an intraday stop order resting at the
    trigger would have done — but a daily bar does not record *when* within
    the session its high or low occurred, so this mode is optimistic: it can
    flag a breakout on a bar whose close ended up back inside the band. Use it
    to see the upper bound of what the strategy could have caught, not as the
    default assumption.
    """

    name = "dual_thrust"
    family = "breakout"
    description = (
        "Breakout. Targets a position when price trades through a volatility "
        "range projected off the day's open; holds it until the opposite "
        "trigger fires."
    )
    parameter_specs = (
        ParameterSpec(
            "lookback",
            "int",
            4,
            "Number of completed bars the HH/HC/LC/LL range is built from",
            minimum=2,
            maximum=200,
        ),
        ParameterSpec(
            "k_upper",
            "float",
            0.5,
            "Multiplier on the range added to the open to form the upper trigger",
            minimum=0.05,
            maximum=3.0,
        ),
        ParameterSpec(
            "k_lower",
            "float",
            0.5,
            "Multiplier on the range subtracted from the open to form the lower trigger",
            minimum=0.05,
            maximum=3.0,
        ),
        ParameterSpec(
            "allow_short",
            "bool",
            False,
            "Take the short side on a downside breakout instead of going flat",
        ),
        ParameterSpec(
            "trigger_on",
            "str",
            "close",
            "Whether a breakout is judged from the bar's close (honest on daily "
            "bars) or its high/low (optimistic — assumes intrabar timing a "
            "daily bar does not record)",
            choices=("close", "intrabar"),
        ),
    )

    @property
    def warmup_bars(self) -> int:
        # `lookback` bars to fill the rolling HH/HC/LC/LL window, plus one more
        # because that window is built from bars shifted one back (see
        # generate_signals) so a bar's own high/low can never leak into its own
        # trigger. The shift is what makes the range causal, and it costs one
        # extra bar of warm-up.
        return int(self._parameters["lookback"]) + 1

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        self.require_warmup(series)

        params = self.parameters
        lookback = int(params["lookback"])
        k_upper = float(params["k_upper"])
        k_lower = float(params["k_lower"])
        allow_short = bool(params["allow_short"])
        trigger_on = params["trigger_on"]

        bars = series.frame
        index = bars.index
        open_ = bars["open"]
        high = bars["high"]
        low = bars["low"]
        close = bars["close"]

        # Shift every input to the range by one bar before rolling. Without the
        # shift, `rolling(lookback)` at bar t would include bar t's own
        # high/low/close — meaning the range that decides whether bar t broke
        # out would itself be partly defined by bar t, a look-ahead violation
        # the repo-wide test_lookahead_bias.py suite is built to catch. Shifted,
        # the window at bar t covers exactly bars (t-lookback) .. (t-1).
        prior_high = high.shift(1)
        prior_low = low.shift(1)
        prior_close = close.shift(1)

        hh = prior_high.rolling(lookback).max()
        hc = prior_close.rolling(lookback).max()
        lc = prior_close.rolling(lookback).min()
        ll = prior_low.rolling(lookback).min()

        # np.maximum on the raw arrays rather than on the Series directly:
        # both operands already share `index`, and going through plain arrays
        # avoids relying on ufunc-on-Series alignment semantics.
        range_ = pd.Series(np.maximum((hh - lc).to_numpy(), (hc - ll).to_numpy()), index=index)

        upper_trigger = open_ + k_upper * range_
        lower_trigger = open_ - k_lower * range_

        # A zero-width range means the prior `lookback` bars had no volatility
        # to break out of; both triggers collapse onto the open, and any close
        # away from the open would then look like a "breakout" of a band that
        # never existed. Treat that as not-tradable rather than as a signal.
        has_range = range_ > 0

        if trigger_on == "close":
            broke_up = has_range & (close >= upper_trigger)
            broke_down = has_range & (close <= lower_trigger)
        else:
            # Intrabar: the high/low may have touched the trigger without the
            # close confirming it. Both directions are evaluated independently,
            # so a single wide bar can in principle flag both — daily data
            # cannot say which happened first, so ties are broken by row order
            # below (the down-trigger assignment is applied last and wins).
            broke_up = has_range & (high >= upper_trigger)
            broke_down = has_range & (low <= lower_trigger)

        frame = empty_signal_frame(index)
        target = pd.Series(np.nan, index=index, dtype="float64")
        target[broke_up] = float(SignalDirection.LONG)
        target[broke_down] = (
            float(SignalDirection.SHORT) if allow_short else float(SignalDirection.FLAT)
        )
        # Hold the last breakout decision between triggers; stay flat before
        # the range is available and before the first breakout.
        target = target.ffill().fillna(float(SignalDirection.FLAT))
        frame["direction"] = target.astype("int64")

        # A breakout is a binary event exactly like a crossover (see the
        # comment in macd.py): conviction is full while positioned and zero
        # while flat, so position sizes stay comparable across strategy
        # families (M3.7).
        frame["strength"] = np.where(frame["direction"] != 0, 1.0, 0.0)

        reasons = pd.Series("", index=index, dtype="object")
        reasons[broke_up] = "close broke above the upper trigger — long breakout"
        reasons[broke_down] = (
            "close broke below the lower trigger — reversing short"
            if allow_short
            else "close broke below the lower trigger — exiting"
        )
        if trigger_on == "intrabar":
            reasons[broke_up] = "high touched the upper trigger — long breakout (intrabar)"
            reasons[broke_down] = (
                "low touched the lower trigger — reversing short (intrabar)"
                if allow_short
                else "low touched the lower trigger — exiting (intrabar)"
            )
        frame["reason"] = reasons

        indicators = pd.DataFrame(
            {
                "dt_range": range_,
                "dt_upper_trigger": upper_trigger,
                "dt_lower_trigger": lower_trigger,
            },
            index=index,
        )

        return SignalSet(
            symbol=series.symbol,
            strategy=self.name,
            parameters=params,
            frame=frame,
            indicators=indicators,
            warmup_bars=self.warmup_bars,
        )

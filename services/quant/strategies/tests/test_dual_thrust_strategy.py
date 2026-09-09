"""Dual Thrust breakout strategy signal generation."""

from __future__ import annotations

import pytest

from services.quant.strategies import (
    InsufficientDataError,
    SignalDirection,
    create,
)
from services.quant.strategies.dual_thrust import DualThrustStrategy
from services.quant.testing import make_price_series

LOOKBACK, K_UPPER, K_LOWER = 4, 0.5, 0.5
SHORT_PARAMS = {"lookback": LOOKBACK, "k_upper": K_UPPER, "k_lower": K_LOWER}


def flat_then_up_breakout(flat_bars: int = 20, breakout_bars: int = 20) -> list[float]:
    """A tight consolidation followed by a decisive run through the upper band.

    The flat leg still carries the fixture's default 1% OHLC spread, so it has
    a small but non-zero volatility range; the breakout leg is large enough to
    clear that range by a wide margin regardless of `k_upper`.
    """
    flat = [100.0] * flat_bars
    breakout = [100.0 + 10.0 * (i + 1) for i in range(breakout_bars)]
    return flat + breakout


def flat_then_down_breakout(flat_bars: int = 20, breakout_bars: int = 20) -> list[float]:
    flat = [100.0] * flat_bars
    breakout = [100.0 - 10.0 * (i + 1) for i in range(breakout_bars)]
    return flat + breakout


def choppy_inside_band(flat_bars: int = 20, wiggle_bars: int = 40) -> list[float]:
    """Small oscillations that stay well inside a 0.5x range band.

    The flat lead-in bakes in a volatility range of roughly 1 (from the
    fixture's 1% spread on a price of 100); a +-0.2 wiggle around 100 cannot
    reach a trigger half a unit-range away.
    """
    flat = [100.0] * flat_bars
    wiggle = [100.0 + (0.2 if i % 2 == 0 else -0.2) for i in range(wiggle_bars)]
    return flat + wiggle


class TestParameters:
    def test_registered_under_its_name(self) -> None:
        assert isinstance(create("dual_thrust"), DualThrustStrategy)

    def test_defaults(self) -> None:
        assert create("dual_thrust").parameters == {
            "lookback": 4,
            "k_upper": 0.5,
            "k_lower": 0.5,
            "allow_short": False,
            "trigger_on": "close",
        }

    def test_warmup_is_lookback_plus_one(self) -> None:
        # The extra bar is the shift that keeps the range causal (see
        # generate_signals): without it bar t's own high/low would leak into
        # the range that decides bar t's own trigger.
        assert DualThrustStrategy(lookback=4).warmup_bars == 5
        assert DualThrustStrategy(lookback=10).warmup_bars == 11

    def test_refuses_a_series_shorter_than_warmup(self) -> None:
        strategy = DualThrustStrategy(**SHORT_PARAMS)
        series = make_price_series([100.0] * (strategy.warmup_bars - 1))

        with pytest.raises(InsufficientDataError):
            strategy.generate_signals(series)


class TestCausality:
    def test_mutating_the_final_bar_leaves_earlier_signals_unchanged(self) -> None:
        """The most important test in this file (NFR5.3).

        Every bar's range is built from bars strictly before it, so corrupting
        the last bar must not be able to reach back and change any earlier
        decision.
        """
        base = flat_then_up_breakout()
        original = DualThrustStrategy(**SHORT_PARAMS).generate_signals(make_price_series(base))

        mutated = base.copy()
        mutated[-1] = 1.0  # a wild, otherwise-implausible crash on the last bar
        perturbed = DualThrustStrategy(**SHORT_PARAMS).generate_signals(make_price_series(mutated))

        assert original.frame["direction"].iloc[:-1].equals(perturbed.frame["direction"].iloc[:-1])
        assert (
            original.indicators["dt_range"]
            .iloc[:-1]
            .equals(perturbed.indicators["dt_range"].iloc[:-1])
        )

    def test_range_excludes_the_current_bar(self) -> None:
        """Hand-computed proof that the rolling window is shifted by one bar.

        closes = [100, 105, 100, 105, 300], lookback=2, spread=0 (so
        open[i] = close[i-1], high[i] = max(open[i], close[i]),
        low[i] = min(open[i], close[i]) exactly, with no fixture spread to
        muddy the arithmetic).

        Bar-by-bar (open, close, high, low):
            t0: 100, 100, 100, 100
            t1: 100, 105, 105, 100
            t2: 105, 100, 105, 100
            t3: 100, 105, 105, 100
            t4: 105, 300, 300, 105

        At t4 the *causal* range uses only bars t2 and t3 (shift(1) then a
        2-bar rolling window):
            HH = max(high[2], high[3]) = max(105, 105) = 105
            HC = max(close[2], close[3]) = max(100, 105) = 105
            LC = min(close[2], close[3]) = min(100, 105) = 100
            LL = min(low[2], low[3]) = min(100, 100) = 100
            range = max(HH - LC, HC - LL) = max(105 - 100, 105 - 100) = 5

        If bar t4 were (wrongly) included in its own window, the window would
        instead be bars t3 and t4:
            HH = max(105, 300) = 300; HC = max(105, 300) = 300
            LC = min(105, 300) = 105; LL = min(100, 105) = 100
            range = max(300 - 105, 300 - 100) = 200

        5 and 200 are nowhere near each other, so this pins the shift down
        precisely rather than merely gesturing at "causal".
        """
        closes = [100.0, 105.0, 100.0, 105.0, 300.0]
        series = make_price_series(closes, spread=0.0)

        signals = DualThrustStrategy(lookback=2, k_upper=0.5, k_lower=0.5).generate_signals(series)

        assert signals.indicators["dt_range"].iloc[4] == pytest.approx(5.0)

        # Bonus: with the correct range=5, open=105, the upper trigger is
        # 105 + 0.5*5 = 107.5 and close=300 clears it, so this must be a long.
        assert signals.frame["direction"].iloc[4] == int(SignalDirection.LONG)


class TestSignals:
    def test_one_row_per_bar_with_contract_columns(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert len(signals) == len(series)
        assert list(signals.frame.columns) == ["direction", "strength", "reason"]

    def test_flat_before_the_range_is_available(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert signals.frame["direction"].iloc[0] == int(SignalDirection.FLAT)

    def test_upside_breakout_produces_long(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert (signals.frame["direction"] == int(SignalDirection.LONG)).any()

    def test_downside_breakout_flattens_when_shorting_is_disabled(self) -> None:
        series = make_price_series(flat_then_down_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert not (signals.frame["direction"] == int(SignalDirection.SHORT)).any()
        assert signals.frame["direction"].iloc[-1] == int(SignalDirection.FLAT)

    def test_downside_breakout_shorts_when_allowed(self) -> None:
        series = make_price_series(flat_then_down_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS, allow_short=True).generate_signals(series)

        assert (signals.frame["direction"] == int(SignalDirection.SHORT)).any()

    def test_flat_series_never_trades_and_strength_stays_zero(self) -> None:
        """Exercises the zero-range guard: a perfectly flat series (no fixture
        spread) has range == 0 on every bar, and a zero-width range must not
        be treated as a breakout even though close == open == the triggers."""
        series = make_price_series([100.0] * 60, spread=0.0)

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert (signals.frame["direction"] == int(SignalDirection.FLAT)).all()
        assert signals.frame["strength"].eq(0.0).all()
        assert (signals.indicators["dt_range"].dropna() == 0.0).all()

    def test_choppy_series_holds_previous_target_rather_than_churning(self) -> None:
        series = make_price_series(choppy_inside_band())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        # The wiggle never reaches a trigger, so the only transition possible
        # is the one flat->flat non-event; there must be far fewer transitions
        # than bars.
        assert len(signals.transitions()) <= 2

    def test_intrabar_trigger_produces_at_least_as_many_transitions_as_close(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        close_based = DualThrustStrategy(**SHORT_PARAMS, trigger_on="close").generate_signals(
            series
        )
        intrabar = DualThrustStrategy(**SHORT_PARAMS, trigger_on="intrabar").generate_signals(
            series
        )

        assert len(intrabar.transitions()) >= len(close_based.transitions())

    def test_reasons_populated_on_every_transition(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)
        transitions = signals.transitions().iloc[1:]  # skip the initial flat bar

        assert not transitions.empty
        assert transitions["reason"].str.len().gt(0).all()

    def test_strength_is_bounded_and_zero_when_flat(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)
        strength = signals.frame["strength"]

        assert strength.between(0.0, 1.0).all()
        flat = signals.frame["direction"] == int(SignalDirection.FLAT)
        assert strength[flat].eq(0.0).all()

    def test_indicators_are_returned_for_inspection(self) -> None:
        # NFR4: a user must be able to see the numbers a signal came from.
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert list(signals.indicators.columns) == [
            "dt_range",
            "dt_upper_trigger",
            "dt_lower_trigger",
        ]
        assert len(signals.indicators) == len(series)

    def test_is_deterministic(self) -> None:
        series = make_price_series(flat_then_up_breakout())
        strategy = DualThrustStrategy(**SHORT_PARAMS)

        first = strategy.generate_signals(series)
        second = strategy.generate_signals(series)

        assert first.frame.equals(second.frame)
        assert first.indicators.equals(second.indicators)

    def test_wider_upper_trigger_produces_no_more_long_entries(self) -> None:
        """A wider trigger is strictly harder to reach, so it cannot fire more
        often than a narrower one on the same data."""
        series = make_price_series(flat_then_up_breakout())

        def long_entries(k_upper: float) -> int:
            signals = DualThrustStrategy(
                lookback=LOOKBACK, k_upper=k_upper, k_lower=K_LOWER
            ).generate_signals(series)
            transitions = signals.transitions()
            return int((transitions["direction"] == int(SignalDirection.LONG)).sum())

        assert long_entries(k_upper=2.5) <= long_entries(k_upper=0.1)

    def test_records_the_parameters_that_produced_it(self) -> None:
        series = make_price_series(flat_then_up_breakout())

        signals = DualThrustStrategy(**SHORT_PARAMS).generate_signals(series)

        assert signals.parameters["lookback"] == LOOKBACK
        assert signals.parameters["k_upper"] == K_UPPER


class TestSanityOnRawFixture:
    """The choppy fixture is only a useful test if the numbers actually stay
    inside the band; this pins that assumption down independently of the
    strategy under test."""

    def test_wiggle_amplitude_is_well_inside_the_flat_leg_range(self) -> None:
        flat_leg_range_estimate = 100.0 * 0.01 * 2  # ~1% spread each side
        wiggle_amplitude = 0.2
        assert wiggle_amplitude < K_UPPER * flat_leg_range_estimate

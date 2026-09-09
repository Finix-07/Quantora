"""MACD strategy signal generation."""

from __future__ import annotations

import numpy as np
import pytest

from services.quant.strategies import (
    InsufficientDataError,
    InvalidParametersError,
    SignalDirection,
    create,
)
from services.quant.strategies.macd import MACDStrategy
from services.quant.testing import make_price_series

FAST, SLOW, SIGNAL = 3, 6, 3
SHORT_PARAMS = {"fast": FAST, "slow": SLOW, "signal": SIGNAL}


def ramp_then_fall(up: int = 60, down: int = 60) -> list[float]:
    """A clean up-trend followed by a clean down-trend.

    Deterministic and monotonic in each leg, so the strategy must produce at
    least one long entry and one exit — an assertion about behaviour, not about
    a magic number.
    """
    return list(np.linspace(100, 200, up)) + list(np.linspace(200, 100, down))


class TestParameters:
    def test_registered_under_its_name(self) -> None:
        assert isinstance(create("macd"), MACDStrategy)

    def test_defaults_match_the_textbook_configuration(self) -> None:
        assert create("macd").parameters == {
            "fast": 12,
            "slow": 26,
            "signal": 9,
            "allow_short": False,
            "require_zero_line": False,
        }

    def test_rejects_fast_span_not_faster_than_slow(self) -> None:
        # A "fast" average slower than the "slow" one inverts every crossover,
        # making the strategy trade its own opposite.
        with pytest.raises(InvalidParametersError, match="strictly less than slow"):
            MACDStrategy(fast=26, slow=12)

    def test_warmup_covers_both_emas_and_the_signal_line(self) -> None:
        assert MACDStrategy(fast=12, slow=26, signal=9).warmup_bars == 35

    def test_refuses_a_series_shorter_than_warmup(self) -> None:
        strategy = MACDStrategy(**SHORT_PARAMS)
        series = make_price_series([100.0] * (strategy.warmup_bars - 1))

        with pytest.raises(InsufficientDataError):
            strategy.generate_signals(series)


class TestSignals:
    def test_one_row_per_bar_with_contract_columns(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        assert len(signals) == len(series)
        assert list(signals.frame.columns) == ["direction", "strength", "reason"]

    def test_flat_before_the_first_crossover(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        # Nothing is tradable until the indicators are seeded.
        assert signals.frame["direction"].iloc[0] == int(SignalDirection.FLAT)

    def test_uptrend_produces_a_long_entry_then_downtrend_exits(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)
        directions = signals.frame["direction"]

        assert (directions == int(SignalDirection.LONG)).any(), "an up-trend must produce a long"
        # By the end of the down leg the position must be closed.
        assert directions.iloc[-1] == int(SignalDirection.FLAT)

    def test_flat_is_the_default_exit_when_shorting_is_disabled(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        assert not (signals.frame["direction"] == int(SignalDirection.SHORT)).any()

    def test_allow_short_reverses_instead_of_flattening(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS, allow_short=True).generate_signals(series)

        assert (signals.frame["direction"] == int(SignalDirection.SHORT)).any()

    def test_target_is_held_between_crossovers(self) -> None:
        """A momentum strategy stays in a trend; re-deciding every bar would
        churn the position for no reason."""
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        # Far fewer transitions than bars means the target is being carried.
        assert len(signals.transitions()) < len(signals) / 4

    def test_flat_price_series_never_trades(self) -> None:
        series = make_price_series([100.0] * 120)

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        # No crossover ever occurs, so the honest answer is "no trades" rather
        # than an arbitrary position.
        assert (signals.frame["direction"] == int(SignalDirection.FLAT)).all()
        assert signals.frame["strength"].eq(0.0).all()

    def test_reasons_are_populated_on_every_transition(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)
        transitions = signals.transitions().iloc[1:]  # skip the initial flat bar

        assert not transitions.empty
        assert transitions["reason"].str.len().gt(0).all()
        assert transitions["reason"].str.contains("MACD").all()

    def test_strength_is_bounded_and_zero_when_flat(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)
        strength = signals.frame["strength"]

        assert strength.between(0.0, 1.0).all()
        flat = signals.frame["direction"] == int(SignalDirection.FLAT)
        assert strength[flat].eq(0.0).all()

    def test_indicators_are_returned_for_inspection(self) -> None:
        # NFR4: a user must be able to see the numbers a signal came from.
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        assert list(signals.indicators.columns) == ["macd", "macd_signal", "macd_histogram"]
        assert len(signals.indicators) == len(series)

    def test_require_zero_line_filter_trades_no_more_often(self) -> None:
        series = make_price_series(ramp_then_fall())

        unfiltered = MACDStrategy(**SHORT_PARAMS).generate_signals(series)
        filtered = MACDStrategy(**SHORT_PARAMS, require_zero_line=True).generate_signals(series)

        assert len(filtered.transitions()) <= len(unfiltered.transitions())

    def test_signals_are_causal(self) -> None:
        """Mutating the final bar must not change any earlier signal (NFR5.3)."""
        base = ramp_then_fall()
        original = MACDStrategy(**SHORT_PARAMS).generate_signals(make_price_series(base))

        mutated = base.copy()
        mutated[-1] = 10_000.0
        perturbed = MACDStrategy(**SHORT_PARAMS).generate_signals(make_price_series(mutated))

        assert original.frame["direction"].iloc[:-1].equals(perturbed.frame["direction"].iloc[:-1])

    def test_is_deterministic(self) -> None:
        series = make_price_series(ramp_then_fall())
        strategy = MACDStrategy(**SHORT_PARAMS)

        first = strategy.generate_signals(series)
        second = strategy.generate_signals(series)

        assert first.frame.equals(second.frame)

    def test_records_the_parameters_that_produced_it(self) -> None:
        series = make_price_series(ramp_then_fall())

        signals = MACDStrategy(**SHORT_PARAMS).generate_signals(series)

        assert signals.parameters["fast"] == FAST
        assert signals.parameters["slow"] == SLOW

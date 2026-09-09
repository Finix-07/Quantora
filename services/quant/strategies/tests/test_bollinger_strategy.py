"""Bollinger Bands (mean-reversion) strategy signal generation."""

from __future__ import annotations

import numpy as np
import pytest

from services.quant.strategies import (
    InsufficientDataError,
    InvalidParametersError,
    SignalDirection,
    create,
)
from services.quant.strategies.bollinger import BollingerStrategy
from services.quant.strategies.macd import MACDStrategy
from services.quant.testing import make_price_series

WINDOW, NUM_STD = 10, 1.0
SHORT_PARAMS = {"window": WINDOW, "num_std": NUM_STD}


def sine_around_level(n: int = 200, period: int = 40, amplitude: float = 15.0) -> list[float]:
    """A price path that repeatedly overshoots and reverts to a level.

    Deterministic and periodic, so a tight-banded (small window, small num_std)
    mean-reversion strategy must both enter and exit repeatedly — an assertion
    about behaviour, not about a magic number. Unlike the momentum tests'
    monotonic ramp, a mean-reversion strategy needs a series that comes back to
    where it started to exercise both band touches.
    """
    i = np.arange(n)
    return list(100.0 + amplitude * np.sin(2 * np.pi * i / period))


class TestParameters:
    def test_registered_under_its_name(self) -> None:
        assert isinstance(create("bollinger"), BollingerStrategy)

    def test_defaults(self) -> None:
        assert create("bollinger").parameters == {
            "window": 20,
            "num_std": 2.0,
            "exit_at_middle": True,
            "allow_short": False,
        }

    def test_warmup_equals_the_window(self) -> None:
        assert BollingerStrategy(window=15, num_std=2.0).warmup_bars == 15

    def test_refuses_a_series_shorter_than_warmup(self) -> None:
        strategy = BollingerStrategy(**SHORT_PARAMS)
        series = make_price_series([100.0] * (strategy.warmup_bars - 1))

        with pytest.raises(InsufficientDataError):
            strategy.generate_signals(series)

    def test_rejects_out_of_range_parameters(self) -> None:
        with pytest.raises(InvalidParametersError):
            BollingerStrategy(window=1)  # below the indicator's minimum of 2
        with pytest.raises(InvalidParametersError):
            BollingerStrategy(num_std=0.0)  # below the minimum of 0.1


class TestSignals:
    def test_one_row_per_bar_with_contract_columns(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)

        assert len(signals) == len(series)
        assert list(signals.frame.columns) == ["direction", "strength", "reason"]

    def test_flat_before_the_bands_are_seeded(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)

        assert (signals.frame["direction"].iloc[: WINDOW - 1] == int(SignalDirection.FLAT)).all()

    def test_lower_band_break_enters_long(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)
        transitions = signals.transitions().iloc[1:]  # skip the initial flat bar
        entries = transitions[transitions["reason"].str.contains("entering long")]

        assert not entries.empty
        assert (entries["direction"] == int(SignalDirection.LONG)).all()

    def test_reverting_series_produces_both_entries_and_exits(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)
        transitions = signals.transitions().iloc[1:]

        entries = transitions[transitions["direction"] != int(SignalDirection.FLAT)]
        exits = transitions[transitions["direction"] == int(SignalDirection.FLAT)]
        assert not entries.empty
        assert not exits.empty

    def test_allow_short_false_never_emits_short(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS, allow_short=False).generate_signals(series)

        assert not (signals.frame["direction"] == int(SignalDirection.SHORT)).any()

    def test_allow_short_true_shorts_on_an_upper_band_break(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS, allow_short=True).generate_signals(series)

        assert (signals.frame["direction"] == int(SignalDirection.SHORT)).any()

    def test_flat_price_series_never_trades(self) -> None:
        series = make_price_series([100.0] * 120)

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)

        # No band is ever touched (zero dispersion means the bands sit on the
        # price itself), so the honest answer is "no trades", not a fabricated
        # position.
        assert (signals.frame["direction"] == int(SignalDirection.FLAT)).all()
        assert signals.frame["strength"].eq(0.0).all()

    def test_exit_at_middle_produces_at_least_as_many_transitions(self) -> None:
        series = make_price_series(sine_around_level())

        with_middle_exit = BollingerStrategy(**SHORT_PARAMS, exit_at_middle=True).generate_signals(
            series
        )
        without_middle_exit = BollingerStrategy(
            **SHORT_PARAMS, exit_at_middle=False
        ).generate_signals(series)

        assert len(with_middle_exit.transitions()) >= len(without_middle_exit.transitions())

    def test_reasons_are_populated_on_every_transition(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)
        transitions = signals.transitions().iloc[1:]  # skip the initial flat bar

        assert not transitions.empty
        assert transitions["reason"].str.len().gt(0).all()

    def test_strength_is_bounded_and_zero_when_flat(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)
        strength = signals.frame["strength"]

        assert strength.between(0.0, 1.0).all()
        flat = signals.frame["direction"] == int(SignalDirection.FLAT)
        assert strength[flat].eq(0.0).all()

    def test_indicators_are_returned_for_inspection(self) -> None:
        # NFR4: a user must be able to see the numbers a signal came from.
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)

        assert list(signals.indicators.columns) == [
            "bb_middle",
            "bb_upper",
            "bb_lower",
            "bb_bandwidth",
            "bb_percent_b",
        ]
        assert len(signals.indicators) == len(series)

    def test_signals_are_causal(self) -> None:
        """Mutating the final bar must not change any earlier signal (NFR5.3)."""
        base = sine_around_level()
        original = BollingerStrategy(**SHORT_PARAMS).generate_signals(make_price_series(base))

        mutated = base.copy()
        mutated[-1] = 10_000.0
        perturbed = BollingerStrategy(**SHORT_PARAMS).generate_signals(make_price_series(mutated))

        assert original.frame["direction"].iloc[:-1].equals(perturbed.frame["direction"].iloc[:-1])

    def test_is_deterministic(self) -> None:
        series = make_price_series(sine_around_level())
        strategy = BollingerStrategy(**SHORT_PARAMS)

        first = strategy.generate_signals(series)
        second = strategy.generate_signals(series)

        assert first.frame.equals(second.frame)

    def test_records_the_parameters_that_produced_it(self) -> None:
        series = make_price_series(sine_around_level())

        signals = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)

        assert signals.parameters["window"] == WINDOW
        assert signals.parameters["num_std"] == NUM_STD

    def test_differs_from_macd_on_the_same_series(self) -> None:
        """A mean-reversion strategy trading the same series as a momentum one
        must not reproduce its signal sequence — if it did, one of the two
        implementations would be wrong."""
        series = make_price_series(sine_around_level())

        mean_reversion = BollingerStrategy(**SHORT_PARAMS).generate_signals(series)
        momentum = MACDStrategy(fast=3, slow=6, signal=3).generate_signals(series)

        assert not mean_reversion.frame["direction"].equals(momentum.frame["direction"])

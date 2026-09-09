"""Pair-trading strategy signal generation.

The awkward part of this family is that it needs two instruments while the
Strategy contract hands it one, so a fair share of these tests are about the
injection seam: what happens when the second leg is missing, mismatched, shorter
than the first, or not really a pair at all.
"""

from __future__ import annotations

import numpy as np
import pytest

from services.quant.backtest import BacktestConfig, BacktestRun, run_backtest
from services.quant.data.types import PriceSeries
from services.quant.strategies import (
    InsufficientDataError,
    InvalidParametersError,
    SignalDirection,
    create,
)
from services.quant.strategies.pair_trading import (
    PairDataMissingError,
    PairTradingStrategy,
)
from services.quant.testing import make_price_series

PRIMARY = "RELIANCE.NS"
PAIR = "TCS.NS"

#: Short enough to keep fixtures small, long enough that the rolling statistics
#: are not dominated by sampling noise.
LOOKBACK = 20
PARAMS = {"lookback": LOOKBACK, "entry_z": 2.0, "exit_z": 0.5}


def cointegrated_pair(n: int = 300, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Two price paths sharing a random walk, separated by a stationary spread.

    ``log(b) = log(a) - spread`` where ``spread`` is an AR(1) with coefficient
    0.9, so the spread reverts to zero and its z-score crosses ±2 repeatedly.
    The common walk's volatility (2% a bar) dominates the spread's innovations
    (0.4%), which is what makes the two legs a genuine pair: their returns are
    highly correlated even though their difference wanders.

    Built from a seeded generator, so the whole fixture is reproducible.
    """
    rng = np.random.default_rng(seed)
    log_a = np.log(100.0) + np.cumsum(rng.normal(0.0, 0.02, n))
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0.0, 0.004)
    return np.exp(log_a), np.exp(log_a - spread)


def independent_walks(n: int = 300, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Two unrelated random walks — a "pair" only in the caller's imagination."""
    rng = np.random.default_rng(seed)
    a = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    b = 500.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    return a, b


def primary_series(closes: np.ndarray) -> PriceSeries:
    return make_price_series(closes, symbol=PRIMARY)


def pair_series(closes: np.ndarray) -> PriceSeries:
    return make_price_series(closes, symbol=PAIR)


def strategy_for(closes_b: np.ndarray, **overrides: object) -> PairTradingStrategy:
    """A configured strategy with its second leg already injected."""
    return PairTradingStrategy(pair_series=pair_series(closes_b), **{**PARAMS, **overrides})


class TestParameters:
    def test_registered_under_its_name(self) -> None:
        assert isinstance(create("pair_trading"), PairTradingStrategy)

    def test_belongs_to_the_stat_arb_family(self) -> None:
        assert PairTradingStrategy.family == "stat_arb"

    def test_defaults_match_the_specified_configuration(self) -> None:
        assert create("pair_trading").parameters == {
            "pair_symbol": "TCS.NS",
            "lookback": 60,
            "entry_z": 2.0,
            "exit_z": 0.5,
            "min_correlation": 0.5,
            "use_hedge_ratio": True,
        }

    def test_rejects_an_exit_band_at_least_as_wide_as_the_entry_band(self) -> None:
        # It would close a position on the very bar it was opened, paying a round
        # trip in costs to hold nothing.
        with pytest.raises(InvalidParametersError, match="strictly less than entry_z"):
            PairTradingStrategy(entry_z=2.0, exit_z=2.0)

    def test_rejects_an_exit_band_wider_than_the_entry_band(self) -> None:
        with pytest.raises(InvalidParametersError, match="strictly less than entry_z"):
            PairTradingStrategy(entry_z=1.0, exit_z=2.5)

    def test_rejects_an_empty_pair_symbol(self) -> None:
        # The contract's own str validation now rejects this before the
        # strategy-local guard is reached; the message names the parameter.
        with pytest.raises(InvalidParametersError, match="pair_symbol must be a non-empty str"):
            PairTradingStrategy(pair_symbol="  ")

    def test_warmup_covers_the_spread_window_and_one_return(self) -> None:
        assert PairTradingStrategy(lookback=LOOKBACK).warmup_bars == LOOKBACK + 1

    def test_refuses_a_series_shorter_than_warmup(self) -> None:
        strategy = strategy_for(cointegrated_pair()[1])
        short = primary_series(cointegrated_pair()[0][: strategy.warmup_bars - 1])

        with pytest.raises(InsufficientDataError):
            strategy.generate_signals(short)


class TestPairInjection:
    def test_generate_signals_without_the_second_leg_says_exactly_what_to_do(self) -> None:
        """Not an all-flat result: that would read as "no opinion" when the truth
        is "half the inputs were never supplied"."""
        strategy = PairTradingStrategy(**PARAMS)

        with pytest.raises(PairDataMissingError) as excinfo:
            strategy.generate_signals(primary_series(cointegrated_pair()[0]))

        message = str(excinfo.value)
        assert "TCS.NS" in message
        assert "set_pair_series" in message
        assert "load_pair_series" in message

    def test_the_second_leg_can_be_injected_after_construction(self) -> None:
        a, b = cointegrated_pair()
        strategy = PairTradingStrategy(**PARAMS)
        assert strategy.pair_series is None

        strategy.set_pair_series(pair_series(b))

        assert strategy.pair_series is not None
        assert len(strategy.generate_signals(primary_series(a))) == len(a)

    def test_rejects_a_series_for_a_different_symbol_than_the_parameter(self) -> None:
        # The parameters are what a saved experiment records; injecting a
        # different instrument would make the record unreproducible.
        strategy = PairTradingStrategy(pair_symbol="TCS.NS", **PARAMS)

        with pytest.raises(InvalidParametersError, match="injected series is for"):
            strategy.set_pair_series(make_price_series([1.0, 2.0], symbol="INFY.NS"))

    def test_rejects_pairing_an_instrument_with_itself(self) -> None:
        # The spread of a series against itself is identically zero, so its
        # z-score is undefined and there is nothing to trade.
        a, _ = cointegrated_pair()
        strategy = PairTradingStrategy(pair_symbol=PRIMARY, **PARAMS)
        strategy.set_pair_series(make_price_series(a, symbol=PRIMARY))

        with pytest.raises(InvalidParametersError, match="same instrument"):
            strategy.generate_signals(primary_series(a))

    def test_insufficient_overlap_is_an_insufficient_data_error(self) -> None:
        # Both legs are long enough on their own; they simply do not share
        # enough bars to warm the spread's statistics up.
        a, b = cointegrated_pair()
        strategy = strategy_for(b[: LOOKBACK - 5])

        with pytest.raises(InsufficientDataError):
            strategy.generate_signals(primary_series(a))


class TestSignals:
    def test_one_row_per_bar_with_contract_columns(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))

        assert len(signals) == len(a)
        assert list(signals.frame.columns) == ["direction", "strength", "reason"]

    def test_signals_span_the_full_primary_index_even_where_the_pair_is_missing(self) -> None:
        """The engine requires one signal per price bar; a bar with no
        counterpart is FLAT because there is no spread to have a view on."""
        a, b = cointegrated_pair()
        overlap = 200
        series = primary_series(a)

        signals = strategy_for(b[:overlap]).generate_signals(series)

        assert signals.frame.index.equals(series.frame.index)
        assert (signals.frame["direction"].iloc[overlap:] == int(SignalDirection.FLAT)).all()
        assert signals.indicators["pair_zscore"].iloc[overlap:].isna().all()
        # The overlapping part must still have traded, or the assertion above is
        # satisfied by a strategy that simply never does anything.
        assert (signals.frame["direction"].iloc[:overlap] != int(SignalDirection.FLAT)).any()

    def test_entries_take_the_side_the_z_score_calls_for(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))
        directions = signals.frame["direction"]
        z = signals.indicators["pair_zscore"]

        entries = directions[(directions != 0) & (directions != directions.shift())]
        assert (entries == int(SignalDirection.LONG)).any(), "the fixture must produce longs"
        for timestamp, direction in entries.items():
            score = z.loc[timestamp]
            # Long when the spread is unusually negative (the primary leg is
            # cheap), short when it is unusually positive — never the reverse.
            assert np.sign(score) == -direction
            assert abs(score) >= PARAMS["entry_z"]

    def test_positions_are_closed_when_the_spread_reverts(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))
        directions = signals.frame["direction"]
        z = signals.indicators["pair_zscore"]

        previous = directions.shift()
        exits = directions[(directions == 0) & previous.isin([1, -1])]
        assert not exits.empty, "a mean-reverting spread must produce exits"
        reverted = [t for t in exits.index if abs(z.loc[t]) <= PARAMS["exit_z"]]
        assert reverted, "at least one exit must be the spread reverting inside the exit band"

    def test_the_target_is_held_between_bands(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))

        # Far fewer transitions than bars means the target is being carried
        # rather than re-decided every bar.
        assert len(signals.transitions()) < len(signals) / 4

    def test_a_decoupled_pair_is_never_traded(self) -> None:
        """Two independent random walks are not a pair, and the spread of two
        unrelated instruments is noise. A longer window is used here so that a
        correlation above the threshold would have to be a real relationship
        rather than a small-sample accident."""
        a, b = independent_walks()
        strategy = PairTradingStrategy(
            pair_series=pair_series(b), lookback=60, entry_z=2.0, exit_z=0.5
        )

        signals = strategy.generate_signals(primary_series(a))

        assert (signals.frame["direction"] == int(SignalDirection.FLAT)).all()
        assert signals.frame["reason"].str.contains("correlation").any()
        assert signals.indicators["pair_correlation"].max() < 0.5

    def test_the_correlation_guardrail_explains_itself(self) -> None:
        a, b = independent_walks()
        strategy = PairTradingStrategy(
            pair_series=pair_series(b), lookback=60, entry_z=2.0, exit_z=0.5
        )

        signals = strategy.generate_signals(primary_series(a))
        blocked = signals.frame["reason"].str.contains("decoupled")

        assert blocked.any()
        assert signals.frame.loc[blocked, "reason"].str.contains("min_correlation").all()

    def test_a_simple_ratio_spread_still_produces_valid_signals(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b, use_hedge_ratio=False).generate_signals(primary_series(a))

        assert (signals.frame["direction"] != 0).any()
        assert signals.frame["strength"].between(0.0, 1.0).all()
        # The spread was built one-for-one, and the indicator frame says so
        # rather than leaving the ratio blank.
        assert signals.indicators["pair_hedge_ratio"].dropna().eq(1.0).all()

    def test_reasons_are_populated_on_every_transition(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))
        transitions = signals.transitions().iloc[1:]  # skip the initial flat bar

        assert not transitions.empty
        assert transitions["reason"].str.len().gt(0).all()
        assert transitions["reason"].str.contains(PAIR).all()

    def test_strength_is_bounded_and_zero_when_flat(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))
        strength = signals.frame["strength"]

        assert strength.between(0.0, 1.0).all()
        flat = signals.frame["direction"] == int(SignalDirection.FLAT)
        assert strength[flat].eq(0.0).all()
        assert strength[~flat].eq(1.0).all()

    def test_indicators_are_returned_for_inspection(self) -> None:
        # NFR4: a user must be able to see the numbers a signal came from.
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))

        assert list(signals.indicators.columns) == [
            "pair_spread",
            "pair_zscore",
            "pair_correlation",
            "pair_hedge_ratio",
        ]
        assert len(signals.indicators) == len(a)
        assert signals.indicators.index.equals(signals.frame.index)

    def test_signals_are_causal_in_both_legs(self) -> None:
        """Mutating the final bar of either leg must not change any earlier
        signal (NFR5.3)."""
        a, b = cointegrated_pair()
        original = strategy_for(b).generate_signals(primary_series(a))

        mutated_a, mutated_b = a.copy(), b.copy()
        mutated_a[-1] = 10_000.0
        mutated_b[-1] = 0.01
        perturbed = strategy_for(mutated_b).generate_signals(primary_series(mutated_a))

        assert original.frame["direction"].iloc[:-1].equals(perturbed.frame["direction"].iloc[:-1])

    def test_is_deterministic(self) -> None:
        a, b = cointegrated_pair()
        strategy = strategy_for(b)
        series = primary_series(a)

        first = strategy.generate_signals(series)
        second = strategy.generate_signals(series)

        assert first.frame.equals(second.frame)
        assert first.indicators.equals(second.indicators)

    def test_records_the_parameters_that_produced_it(self) -> None:
        a, b = cointegrated_pair()

        signals = strategy_for(b).generate_signals(primary_series(a))

        assert signals.parameters["pair_symbol"] == PAIR
        assert signals.parameters["lookback"] == LOOKBACK


class TestBacktestIntegration:
    def test_runs_through_the_single_instrument_engine(self) -> None:
        """The point of the injection design: a two-instrument strategy has to be
        genuinely runnable by the engine, not merely by its own unit tests.

        ``allow_short=True`` because the short half of a spread trade is most of
        it; with a long-only portfolio the engine would flatten those targets and
        the run would exercise only half the strategy.
        """
        a, b = cointegrated_pair()
        series = primary_series(a)

        run = run_backtest(series, strategy_for(b), BacktestConfig(allow_short=True))

        assert isinstance(run, BacktestRun)
        assert len(run.equity_curve) == len(series)
        assert run.equity_curve.notna().all()
        # A mean-reverting spread over 300 bars should round-trip a handful of
        # times: enough to prove trades happen, few enough to prove the target is
        # not being re-decided every bar.
        assert 1 <= len(run.trades) <= len(series) // 10
        assert all(order.decision_timestamp < order.timestamp for order in run.simulation.orders)

    def test_the_engine_can_take_both_sides(self) -> None:
        a, b = cointegrated_pair()
        series = primary_series(a)

        run = run_backtest(series, strategy_for(b), BacktestConfig(allow_short=True))
        quantities = [event.quantity for event in run.simulation.positions]

        assert any(q > 0 for q in quantities), "the spread should be bought at some point"
        assert any(q < 0 for q in quantities), "and sold short at another"

    def test_a_long_only_portfolio_never_goes_short(self) -> None:
        # The strategy emits SHORT honestly and the engine's allow_short has the
        # final say, so portfolio policy stays in one place.
        a, b = cointegrated_pair()
        series = primary_series(a)

        run = run_backtest(series, strategy_for(b), BacktestConfig(allow_short=False))

        assert all(event.quantity >= 0 for event in run.simulation.positions)

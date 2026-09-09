"""Look-ahead-bias regression tests.

Mandatory and non-trimmable (testing.md §2.1 and §8, planning.md M2 Definition of
Done). Reliability rule 3 (NFR5.3) says future information must never enter a
historical simulation. A violation does not announce itself: it produces a
backtest that looks excellent and is worthless, so this file asserts the
guarantee from four independent angles rather than trusting the bar loop's
structure.

1. Structural — every fill happens on a strictly later bar than the decision
   that produced it.
2. Price-level — the price a fill is benchmarked against belongs to the
   execution bar, and is never the decision bar's close.
3. Oracle — corrupting every bar after index *k* must leave the simulation
   identical up to *k*. This is the strongest form: it would catch look-ahead
   arriving through any route, including one nobody thought to check.
4. Adversarial — a strategy that deliberately tries to trade on tomorrow's
   price cannot profit from it, because it is never allowed to fill there.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.costs import CostModel
from services.quant.backtest.engine import simulate
from services.quant.backtest.runner import run_backtest
from services.quant.data.types import PriceSeries
from services.quant.strategies.base import (
    SignalDirection,
    SignalSet,
    Strategy,
    empty_signal_frame,
)
from services.quant.strategies.macd import MACDStrategy
from services.quant.testing import make_price_series

MACD_PARAMS = {"fast": 3, "slow": 6, "signal": 3}


def wiggly_path(n: int = 200, seed: int = 7) -> list[float]:
    """A deterministic pseudo-random walk that actually trades."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.5, n)
    return list(100.0 + np.cumsum(steps) + np.linspace(0, 20, n))


class AlwaysLongStrategy(Strategy):
    name = "always_long_for_lookahead_tests"
    family = "test"
    description = "Targets a long position on every bar."

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        frame = empty_signal_frame(series.frame.index)
        frame["direction"] = int(SignalDirection.LONG)
        frame["strength"] = 1.0
        return SignalSet(symbol=series.symbol, strategy=self.name, parameters={}, frame=frame)


class TomorrowPeekingStrategy(Strategy):
    """Deliberately cheats: goes long whenever the *next* bar closes higher.

    This is the adversary the engine has to defeat. It is not a strategy anyone
    would ship — it exists so the guarantee is tested against an active attempt
    to violate it rather than only against well-behaved code.
    """

    name = "tomorrow_peeking_for_lookahead_tests"
    family = "test"
    description = "Uses tomorrow's close. Exists only to prove the engine blocks it."

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        frame = empty_signal_frame(series.frame.index)
        close = series.close
        next_close = close.shift(-1)
        frame["direction"] = np.where(next_close > close, int(SignalDirection.LONG), 0)
        frame["strength"] = (frame["direction"] != 0).astype("float64")
        frame["reason"] = "peeked at tomorrow"
        return SignalSet(symbol=series.symbol, strategy=self.name, parameters={}, frame=frame)


# --------------------------------------------------------------- 1. structural


@pytest.mark.parametrize(
    "execution_model", [ExecutionModel.NEXT_BAR_OPEN, ExecutionModel.NEXT_BAR_CLOSE]
)
def test_every_order_fills_on_a_strictly_later_bar(execution_model: ExecutionModel) -> None:
    series = make_price_series(wiggly_path())
    strategy = MACDStrategy(**MACD_PARAMS)
    output = simulate(
        series,
        strategy,
        strategy.generate_signals(series),
        BacktestConfig(execution_model=execution_model),
    )

    assert output.orders, "the fixture must actually trade for this test to mean anything"
    for order in output.orders:
        assert order.decision_timestamp < order.timestamp, (
            f"order decided at {order.decision_timestamp} was scheduled to fill at "
            f"{order.timestamp} — a decision may never execute on its own bar"
        )


def test_no_fill_shares_a_timestamp_with_the_decision_that_caused_it() -> None:
    series = make_price_series(wiggly_path())
    strategy = MACDStrategy(**MACD_PARAMS)
    output = simulate(series, strategy, strategy.generate_signals(series), BacktestConfig())

    decision_times = {o.timestamp: o.decision_timestamp for o in output.orders}
    for fill in output.fills:
        if fill.timestamp in decision_times:
            assert decision_times[fill.timestamp] < fill.timestamp


# -------------------------------------------------------------- 2. price level


def test_a_fill_is_never_benchmarked_against_the_decision_bars_close() -> None:
    """The core rule from architecture.md §6.

    A daily close must not be used to simulate a fill for a decision taken at or
    before that same bar. The fill price must belong to a later bar.
    """
    series = make_price_series(wiggly_path())
    strategy = MACDStrategy(**MACD_PARAMS)
    output = simulate(series, strategy, strategy.generate_signals(series), BacktestConfig())

    frame = series.frame
    orders_by_execution = {o.timestamp: o for o in output.orders}

    assert output.fills
    for fill in output.fills:
        order = orders_by_execution.get(fill.timestamp)
        if order is None:
            continue  # end-of-run liquidation has no preceding decision bar
        decision_close = float(frame.loc[order.decision_timestamp, "close"])
        execution_open = float(frame.loc[fill.timestamp, "open"])

        assert fill.reference_price == pytest.approx(execution_open)
        # Guard against a series where the two happen to be equal, which would
        # make the assertion above vacuous.
        if decision_close != execution_open:
            assert fill.reference_price != pytest.approx(decision_close)


# ------------------------------------------------------------------- 3. oracle


@pytest.mark.parametrize("cut", [40, 90, 150])
def test_corrupting_the_future_does_not_change_the_past(cut: int) -> None:
    """The strongest formulation of the guarantee.

    If the simulation up to bar *k* is unaffected by replacing every bar after
    *k* with different data, then no information after *k* reached it — by any
    route, including one nobody thought to test for.
    """
    closes = wiggly_path()
    original = make_price_series(closes)

    corrupted_closes = closes.copy()
    for i in range(cut + 1, len(corrupted_closes)):
        # Wildly different, but still valid OHLCV so the data layer's rules hold.
        corrupted_closes[i] = 10_000.0 + i
    corrupted = make_price_series(corrupted_closes)

    config = BacktestConfig(liquidate_at_end=False)

    def equity_prefix(series: PriceSeries) -> pd.Series:
        strategy = MACDStrategy(**MACD_PARAMS)
        output = simulate(series, strategy, strategy.generate_signals(series), config)
        return output.equity_curve.iloc[: cut + 1]

    pd.testing.assert_series_equal(equity_prefix(original), equity_prefix(corrupted))


def test_corrupting_the_future_does_not_change_past_fills() -> None:
    closes = wiggly_path()
    cut = 120
    original = make_price_series(closes)

    corrupted_closes = closes.copy()
    for i in range(cut + 1, len(corrupted_closes)):
        corrupted_closes[i] = 0.01 * (i + 1)
    corrupted = make_price_series(corrupted_closes)

    config = BacktestConfig(liquidate_at_end=False)
    cutoff = original.frame.index[cut]

    def fills_before_cut(series: PriceSeries) -> list[dict]:
        strategy = MACDStrategy(**MACD_PARAMS)
        output = simulate(series, strategy, strategy.generate_signals(series), config)
        return [f.as_dict() for f in output.fills if f.timestamp <= cutoff]

    assert fills_before_cut(original) == fills_before_cut(corrupted)


def test_the_oracle_test_is_not_vacuous() -> None:
    """The corrupted future must actually change the *later* result.

    Without this, the oracle tests above would still pass if the strategy simply
    never traded, and would prove nothing.
    """
    closes = wiggly_path()
    cut = 90
    corrupted_closes = closes.copy()
    for i in range(cut + 1, len(corrupted_closes)):
        corrupted_closes[i] = 10_000.0 + i

    config = BacktestConfig(liquidate_at_end=False)

    def final_equity(path: list[float]) -> float:
        series = make_price_series(path)
        strategy = MACDStrategy(**MACD_PARAMS)
        return float(
            simulate(series, strategy, strategy.generate_signals(series), config).equity_curve.iloc[
                -1
            ]
        )

    assert final_equity(closes) != pytest.approx(final_equity(corrupted_closes))


# ------------------------------------------------------------- 4. adversarial


def test_a_strategy_that_peeks_at_tomorrow_cannot_fill_on_tomorrows_information() -> None:
    """A cheating strategy still cannot execute at a price it should not know.

    It will look profitable — its signals genuinely use future data — but the
    *fills* must still land on the following bar's open. This test pins the
    engine's half of the contract: preventing a strategy from writing look-ahead
    into its signals is the strategy author's job; preventing look-ahead in
    execution is the engine's, and that is what is asserted here.
    """
    series = make_price_series(wiggly_path())
    strategy = TomorrowPeekingStrategy()
    output = simulate(
        series, strategy, strategy.generate_signals(series), BacktestConfig(liquidate_at_end=False)
    )

    frame = series.frame
    assert output.fills
    for order in output.orders:
        assert order.decision_timestamp < order.timestamp
    for fill in output.fills:
        assert fill.reference_price == pytest.approx(float(frame.loc[fill.timestamp, "open"]))


def test_indicators_used_for_sizing_come_from_the_decision_bar_only() -> None:
    """Sizing must not see the bar it will fill on.

    The engine sizes against the decision bar's close because the execution
    bar's open is not knowable yet. If sizing used the execution bar's price the
    position would be chosen with knowledge of the fill.
    """
    seen: list[tuple[pd.Timestamp, float]] = []

    class RecordingStrategy(Strategy):
        name = "recording_for_lookahead_tests"
        family = "test"
        description = "Alternates long and flat so sizing is consulted on every bar."

        def generate_signals(self, series: PriceSeries) -> SignalSet:
            frame = empty_signal_frame(series.frame.index)
            frame["direction"] = [
                int(SignalDirection.LONG) if i % 2 == 0 else int(SignalDirection.FLAT)
                for i in range(len(series))
            ]
            frame["strength"] = (frame["direction"] != 0).astype("float64")
            return SignalSet(symbol=series.symbol, strategy=self.name, parameters={}, frame=frame)

        def position_size(self, context):  # type: ignore[no-untyped-def]
            seen.append((context.timestamp, context.price))
            return 1.0 * int(context.direction)

    series = make_price_series([100.0, 200.0, 300.0, 400.0, 500.0])
    strategy = RecordingStrategy()
    simulate(
        series,
        strategy,
        strategy.generate_signals(series),
        BacktestConfig(cost_model=CostModel.zero(), liquidate_at_end=False),
    )

    assert seen, "sizing must have been consulted"
    frame = series.frame
    for timestamp, price in seen:
        # The price offered to sizing is the *decision* bar's close, never a
        # later bar's price.
        assert price == pytest.approx(float(frame.loc[timestamp, "close"]))
        assert price <= float(frame["close"].max())
        later = frame.loc[frame.index > timestamp, "close"]
        assert not later.empty
        assert price not in set(later.to_numpy())


def test_the_full_runner_path_preserves_the_guarantee() -> None:
    """The guarantee must hold through run_backtest, not only through simulate."""
    series = make_price_series(wiggly_path())

    run = run_backtest(series, MACDStrategy(**MACD_PARAMS), BacktestConfig())

    assert run.simulation.orders
    for order in run.simulation.orders:
        assert order.decision_timestamp < order.timestamp

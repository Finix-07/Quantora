"""Execution-simulator behaviour.

These tests drive the engine with a scripted strategy whose signals are fixed in
advance, so every fill, price and cash movement is hand-computable. Testing
against a real strategy would confound engine bugs with strategy behaviour.
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.costs import CostModel
from services.quant.backtest.engine import simulate
from services.quant.backtest.events import OrderSide
from services.quant.data.types import PriceSeries
from services.quant.strategies.base import (
    ExitContext,
    ExitDecision,
    SignalDirection,
    SignalSet,
    SizingContext,
    Strategy,
    empty_signal_frame,
)
from services.quant.testing import make_price_series


class ScriptedStrategy(Strategy):
    """Emits a caller-supplied direction per bar and a fixed position size."""

    name = "scripted_for_tests"
    family = "test"
    description = "Replays a fixed list of target directions."

    def __init__(
        self,
        directions: list[int],
        *,
        quantity: float = 10.0,
        exit_on_bar: int | None = None,
    ) -> None:
        super().__init__()
        self.directions = directions
        self.quantity = quantity
        self.exit_on_bar = exit_on_bar
        self._bar_seen = 0

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        frame = empty_signal_frame(series.frame.index)
        frame["direction"] = pd.Series(self.directions, index=series.frame.index, dtype="int64")
        frame["strength"] = (frame["direction"] != 0).astype("float64")
        frame["reason"] = ["scripted"] * len(frame)
        return SignalSet(symbol=series.symbol, strategy=self.name, parameters={}, frame=frame)

    def position_size(self, context: SizingContext) -> float:
        if context.direction is SignalDirection.FLAT:
            return 0.0
        return self.quantity * int(context.direction)

    def exit_signal(self, context: ExitContext) -> ExitDecision:
        self._bar_seen += 1
        if self.exit_on_bar is not None and self._bar_seen == self.exit_on_bar:
            return ExitDecision.exit("scripted exit")
        return ExitDecision.hold()


def series_with_closes(closes: list[float]) -> PriceSeries:
    return make_price_series(closes, symbol="RELIANCE.NS")


def run(
    closes: list[float],
    directions: list[int],
    *,
    config: BacktestConfig | None = None,
    quantity: float = 10.0,
    exit_on_bar: int | None = None,
):
    series = series_with_closes(closes)
    strategy = ScriptedStrategy(directions, quantity=quantity, exit_on_bar=exit_on_bar)
    signals = strategy.generate_signals(series)
    return series, simulate(series, strategy, signals, config)


ZERO_COST = BacktestConfig(cost_model=CostModel.zero(), liquidate_at_end=False)


class TestOrderTiming:
    def test_order_decided_at_a_bar_executes_on_the_next_one(self) -> None:
        _, output = run([100.0, 110.0, 120.0, 130.0], [1, 1, 1, 1], config=ZERO_COST)

        assert len(output.orders) == 1
        order = output.orders[0]
        fill = output.fills[0]

        # The decision bar and the execution bar must be different bars.
        assert order.decision_timestamp < order.timestamp
        assert fill.timestamp == order.timestamp

    def test_fill_uses_the_next_bars_open_under_the_default_model(self) -> None:
        series, output = run([100.0, 110.0, 120.0], [1, 1, 1], config=ZERO_COST)

        fill = output.fills[0]
        expected_open = float(series.frame.iloc[1]["open"])

        assert fill.reference_price == pytest.approx(expected_open)

    def test_next_bar_close_model_fills_at_the_close(self) -> None:
        config = BacktestConfig(
            cost_model=CostModel.zero(),
            liquidate_at_end=False,
            execution_model=ExecutionModel.NEXT_BAR_CLOSE,
        )
        series, output = run([100.0, 110.0, 120.0], [1, 1, 1], config=config)

        assert output.fills[0].reference_price == pytest.approx(
            float(series.frame.iloc[1]["close"])
        )

    def test_no_order_is_created_on_the_final_bar(self) -> None:
        # There is no bar left to execute on, so emitting one would be an order
        # that silently never fills.
        _, output = run([100.0, 100.0, 100.0], [0, 0, 1], config=ZERO_COST)

        assert output.orders == []


class TestPositionTargeting:
    def test_holds_an_already_correct_position_without_re_trading(self) -> None:
        # Re-sizing every bar because equity drifted would pay a full round trip
        # in costs for no change of view.
        _, output = run([100.0, 101.0, 102.0, 103.0, 104.0], [1, 1, 1, 1, 1], config=ZERO_COST)

        assert len(output.orders) == 1

    def test_flat_target_closes_the_position(self) -> None:
        _, output = run([100.0, 100.0, 100.0, 100.0], [1, 1, 0, 0], config=ZERO_COST)

        sides = [f.side for f in output.fills]
        assert sides == [OrderSide.BUY, OrderSide.SELL]
        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 0.0

    def test_short_signal_is_ignored_when_the_engine_forbids_shorting(self) -> None:
        # The engine has the final say: a long-only backtest must not be shorted
        # by a strategy's own configuration.
        _, output = run([100.0, 100.0, 100.0, 100.0], [-1, -1, -1, -1], config=ZERO_COST)

        assert output.fills == []
        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 0.0

    def test_short_signal_is_honoured_when_shorting_is_enabled(self) -> None:
        config = BacktestConfig(
            cost_model=CostModel.zero(), liquidate_at_end=False, allow_short=True
        )
        _, output = run([100.0, 100.0, 100.0, 100.0], [-1, -1, -1, -1], config=config)

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == -10.0

    def test_reversal_produces_a_single_order_crossing_zero(self) -> None:
        config = BacktestConfig(
            cost_model=CostModel.zero(), liquidate_at_end=False, allow_short=True
        )
        _, output = run([100.0, 100.0, 100.0, 100.0, 100.0], [1, 1, -1, -1, -1], config=config)

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == -10.0
        # 10 to close the long plus 10 to open the short.
        assert output.fills[-1].quantity == 20.0


class TestExits:
    def test_discretionary_exit_closes_the_position(self) -> None:
        _, output = run(
            [100.0, 100.0, 100.0, 100.0, 100.0],
            [1, 1, 1, 1, 1],
            config=ZERO_COST,
            exit_on_bar=1,
        )

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 0.0
        exit_fill = next(f for f in output.fills if f.side is OrderSide.SELL)
        # The reason is written into the trade record for journal analysis (FR8).
        assert exit_fill.reason == "scripted exit"

    def test_liquidation_at_end_closes_an_open_position(self) -> None:
        config = BacktestConfig(cost_model=CostModel.zero(), liquidate_at_end=True)
        _, output = run([100.0, 110.0, 120.0], [1, 1, 1], config=config)

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 0.0
        assert "backtest ended" in output.fills[-1].reason

    def test_final_equity_reflects_the_liquidation(self) -> None:
        # Without this a held winner would be booked as though it had been cashed
        # out for free.
        config = BacktestConfig(
            initial_cash=100_000.0, cost_model=CostModel(), liquidate_at_end=True
        )
        _, output = run([100.0, 110.0, 120.0, 130.0], [1, 1, 1, 1], config=config)

        assert output.portfolio is not None
        final_equity = output.equity_curve.iloc[-1]
        # Everything is in cash after liquidation, so equity is exactly cash.
        assert final_equity == pytest.approx(output.portfolio.cash)
        assert output.exposure.iloc[-1] == 0.0

    def test_not_liquidating_leaves_the_position_open(self) -> None:
        _, output = run([100.0, 110.0, 120.0], [1, 1, 1], config=ZERO_COST)

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 10.0


class TestCashConstraint:
    def test_a_buy_beyond_cash_is_reduced_and_reported(self) -> None:
        config = BacktestConfig(
            initial_cash=500.0, cost_model=CostModel.zero(), liquidate_at_end=False
        )
        _, output = run([100.0, 100.0, 100.0], [1, 1, 1], config=config, quantity=10.0)

        # 500 of cash buys 5 units at 100, not the 10 requested.
        assert output.fills[0].quantity == 5.0
        assert output.warnings, "the user must be told the order was reduced"
        assert "insufficient cash" in output.warnings[0]

    def test_an_unaffordable_buy_is_skipped_and_reported(self) -> None:
        config = BacktestConfig(
            initial_cash=10.0, cost_model=CostModel.zero(), liquidate_at_end=False
        )
        _, output = run([100.0, 100.0, 100.0], [1, 1, 1], config=config)

        assert output.fills == []
        assert "insufficient cash" in output.warnings[0]

    def test_cash_never_goes_negative(self) -> None:
        # A backtest that quietly runs on imaginary money reports returns the
        # user could never have earned.
        config = BacktestConfig(initial_cash=1_000.0, cost_model=CostModel())
        _, output = run([100.0, 90.0, 120.0, 80.0, 150.0], [1, 0, 1, 0, 1], config=config)

        assert output.portfolio is not None
        assert output.portfolio.cash >= 0


class TestCostsAreCharged:
    def test_costs_reduce_the_result_versus_a_frictionless_run(self) -> None:
        closes = [100.0, 110.0, 105.0, 120.0, 115.0, 130.0]
        directions = [1, 0, 1, 0, 1, 0]

        _, free = run(closes, directions, config=BacktestConfig(cost_model=CostModel.zero()))
        _, charged = run(closes, directions, config=BacktestConfig(cost_model=CostModel()))

        assert charged.equity_curve.iloc[-1] < free.equity_curve.iloc[-1]
        assert charged.portfolio is not None and free.portfolio is not None
        assert charged.portfolio.total_commission > 0
        assert free.portfolio.total_commission == 0

    def test_buys_fill_above_and_sells_below_the_reference_price(self) -> None:
        _, output = run(
            [100.0, 100.0, 100.0, 100.0],
            [1, 1, 0, 0],
            config=BacktestConfig(cost_model=CostModel()),
        )

        buy = next(f for f in output.fills if f.side is OrderSide.BUY)
        sell = next(f for f in output.fills if f.side is OrderSide.SELL)

        assert buy.fill_price > buy.reference_price
        assert sell.fill_price < sell.reference_price


class TestOutputShape:
    def test_equity_and_exposure_have_one_point_per_bar(self) -> None:
        series, output = run([100.0] * 6, [0, 1, 1, 0, 1, 1], config=ZERO_COST)

        assert len(output.equity_curve) == len(series)
        assert len(output.exposure) == len(series)
        assert output.equity_curve.index.equals(series.frame.index)

    def test_equity_starts_at_the_initial_cash(self) -> None:
        config = BacktestConfig(initial_cash=250_000.0, cost_model=CostModel.zero())
        _, output = run([100.0] * 4, [0, 0, 0, 0], config=config)

        assert output.equity_curve.iloc[0] == pytest.approx(250_000.0)

    def test_a_position_event_is_recorded_for_every_bar(self) -> None:
        series, output = run([100.0] * 5, [1, 1, 1, 1, 1], config=ZERO_COST)

        assert len(output.positions) == len(series)
        assert [p.timestamp for p in output.positions] == list(series.frame.index)

    def test_no_signal_means_no_trades_rather_than_an_arbitrary_position(self) -> None:
        _, output = run([100.0] * 5, [0, 0, 0, 0, 0], config=ZERO_COST)

        assert output.orders == []
        assert output.fills == []
        assert output.portfolio is not None
        assert output.portfolio.trades == []
        # Equity is unchanged, not zero.
        assert output.equity_curve.nunique() == 1


class TestValidation:
    def test_empty_series_is_rejected(self) -> None:
        series = make_price_series([])
        strategy = ScriptedStrategy([])

        with pytest.raises(ValueError, match="empty price series"):
            simulate(series, strategy, strategy.generate_signals(series))

    def test_mismatched_signal_index_is_rejected(self) -> None:
        series = series_with_closes([100.0, 101.0, 102.0])
        other = series_with_closes([100.0, 101.0])
        strategy = ScriptedStrategy([1, 1])

        with pytest.raises(ValueError, match="signal index does not match"):
            simulate(series, strategy, strategy.generate_signals(other))


def test_simulation_is_deterministic() -> None:
    closes = [100.0, 105.0, 103.0, 110.0, 108.0, 115.0]
    directions = [0, 1, 1, 0, 1, 1]

    _, first = run(closes, directions, config=BacktestConfig())
    _, second = run(closes, directions, config=BacktestConfig())

    pd.testing.assert_series_equal(first.equity_curve, second.equity_curve)
    assert [f.as_dict() for f in first.fills] == [f.as_dict() for f in second.fills]


class TestExitReEntry:
    def test_a_discretionary_exit_suppresses_re_entry_until_the_signal_changes(self) -> None:
        """A stop that re-enters on the next bar has achieved nothing.

        The scripted strategy keeps signalling LONG after the exit, so without
        suppression the engine would immediately buy back in.
        """
        _, output = run(
            [100.0] * 8,
            [1, 1, 1, 1, 1, 1, 1, 1],
            config=ZERO_COST,
            exit_on_bar=1,
        )

        assert output.portfolio is not None
        assert output.portfolio.quantity("RELIANCE.NS") == 0.0
        # One entry, one exit — no re-entry.
        assert len(output.fills) == 2

    def test_suppression_lifts_once_the_signal_changes_its_mind(self) -> None:
        _, output = run(
            [100.0] * 9,
            [1, 1, 1, 0, 0, 1, 1, 1, 1],
            config=ZERO_COST,
            exit_on_bar=1,
        )

        assert output.portfolio is not None
        # Entry, stop-out, then a fresh entry after the signal went flat.
        assert output.portfolio.quantity("RELIANCE.NS") == 10.0
        assert len(output.fills) == 3

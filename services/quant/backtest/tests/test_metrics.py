"""Performance/risk metrics.

Every expected number here is worked out by hand (with the arithmetic in a
comment) using plain Python, never by calling the function under test.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from services.quant.backtest.metrics import (
    annualized_volatility,
    cagr,
    compute,
    drawdown_curve,
    max_drawdown,
    max_drawdown_duration,
    profit_factor,
    returns_from_equity,
    sharpe,
    sortino,
    win_rate,
)
from services.quant.backtest.portfolio import Trade

TS = pd.Timestamp("2024-01-01", tz="Asia/Kolkata")


def make_trade(pnl: float, *, gross_pnl: float | None = None, costs: float = 0.0) -> Trade:
    return Trade(
        symbol="X",
        direction="long",
        quantity=1.0,
        entry_timestamp=TS,
        entry_price=100.0,
        exit_timestamp=TS,
        exit_price=100.0,
        gross_pnl=gross_pnl if gross_pnl is not None else pnl,
        costs=costs,
        pnl=pnl,
        return_pct=0.0,
        bars_held=1,
    )


class TestReturnsFromEquity:
    def test_drops_the_first_bar(self) -> None:
        equity = pd.Series([100.0, 110.0, 121.0])

        returns = returns_from_equity(equity)

        # first bar has no prior value; 110/100-1=0.1, 121/110-1=0.1
        assert len(returns) == 2
        assert list(returns) == pytest.approx([0.1, 0.1])


class TestDrawdown:
    def test_max_drawdown_on_a_known_peak_and_trough(self) -> None:
        equity = pd.Series([100.0, 120.0, 90.0, 150.0, 80.0])

        # running max: [100,120,120,150,150]
        # drawdown at each point: [0, 0, -0.25, 0, 80/150-1]
        # worst is the last point: 80/150 - 1 = -7/15 = -0.46666...
        dd = drawdown_curve(equity)
        assert dd.iloc[2] == pytest.approx(90.0 / 120.0 - 1.0)
        assert dd.iloc[4] == pytest.approx(-7.0 / 15.0)

        assert max_drawdown(equity) == pytest.approx(-7.0 / 15.0)

    def test_max_drawdown_is_zero_for_a_monotonically_rising_series(self) -> None:
        equity = pd.Series([100.0, 110.0, 120.0, 130.0])

        assert max_drawdown(equity) == 0.0

    def test_max_drawdown_duration_counts_the_longest_underwater_run(self) -> None:
        equity = pd.Series([100.0, 90.0, 95.0, 99.0, 105.0])

        # cummax: [100,100,100,100,105]
        # underwater (equity < cummax): [F, T, T, T, F] -> longest run = 3
        assert max_drawdown_duration(equity) == 3


class TestCagr:
    def test_doubling_over_one_year_of_bars_gives_one(self) -> None:
        equity = pd.Series([100.0, 200.0])
        # years = (2-1)/periods_per_year; choose periods_per_year = 1 (the
        # number of returns) so years = 1 exactly.
        # cagr = (200/100)**(1/1) - 1 = 1.0
        assert cagr(equity, periods_per_year=1) == pytest.approx(1.0)

    def test_none_for_fewer_than_two_bars(self) -> None:
        assert cagr(pd.Series([100.0]), periods_per_year=252) is None
        assert cagr(pd.Series([], dtype=float), periods_per_year=252) is None

    def test_none_when_equity_reaches_zero(self) -> None:
        assert cagr(pd.Series([100.0, 0.0]), periods_per_year=252) is None


class TestAnnualizedVolatility:
    def test_hand_computed_on_a_small_series(self) -> None:
        returns = pd.Series([0.1, -0.1])
        # mean = 0; sample variance (ddof=1) = ((0.1)^2 + (-0.1)^2) / 1 = 0.02
        # std = sqrt(0.02) = 0.1414213562373095
        # annualized = std * sqrt(252) = 2.2449944320643653
        assert annualized_volatility(returns, periods_per_year=252) == pytest.approx(
            2.2449944320643653
        )

    def test_none_for_fewer_than_two_returns(self) -> None:
        assert annualized_volatility(pd.Series([0.1]), periods_per_year=252) is None


class TestSharpe:
    def test_none_when_volatility_is_zero(self) -> None:
        # A constant return series has zero variance; reporting inf would read
        # as "infinitely good" rather than "undefined".
        returns = pd.Series([0.02, 0.02, 0.02])

        assert sharpe(returns, periods_per_year=252, risk_free_rate=0.0) is None

    def test_hand_computed_value(self) -> None:
        returns = pd.Series([0.01, -0.01, 0.02, 0.02])
        # mean = 0.01
        # sample std (ddof=1) = 0.01414213562373095
        # sharpe = mean / std * sqrt(252) = 11.224972160321826
        result = sharpe(returns, periods_per_year=252, risk_free_rate=0.0)
        assert result == pytest.approx(11.224972160321826)


class TestSortino:
    def test_none_with_no_downside(self) -> None:
        returns = pd.Series([0.01, 0.02, 0.03])

        assert sortino(returns, periods_per_year=252, risk_free_rate=0.0) is None

    def test_downside_deviation_uses_the_full_series_not_only_negative_returns(self) -> None:
        returns = pd.Series([0.05, 0.05, -0.01, 0.05])
        # Full-series (implemented) approach: clip upside to 0 -> [0,0,-0.01,0]
        # downside_deviation = sqrt(mean([0,0,0.0001,0])) = sqrt(0.000025) = 0.005
        # mean = 0.035; sortino = 0.035/0.005*sqrt(252) = 111.12155506471282
        #
        # A (wrong) subset-only approach using only the negative return -0.01
        # would give downside_deviation = 0.01 and sortino = 55.56077753235641
        # -- a materially different, smaller number. The implementation must
        # match the full-series value, not the subset-only one.
        result = sortino(returns, periods_per_year=252, risk_free_rate=0.0)
        assert result == pytest.approx(111.12155506471282)
        assert result != pytest.approx(55.56077753235641)


class TestTradeStatistics:
    def test_win_rate_none_with_no_trades(self) -> None:
        assert win_rate([]) is None

    def test_win_rate_hand_computed(self) -> None:
        trades = [make_trade(p) for p in (100.0, -50.0, 200.0, -30.0, -20.0)]
        # 2 winners out of 5 = 0.4
        assert win_rate(trades) == pytest.approx(0.4)

    def test_profit_factor_none_with_no_trades(self) -> None:
        assert profit_factor([]) is None

    def test_profit_factor_none_when_no_losing_trades(self) -> None:
        # Unbounded ratio: reporting inf would invite reading a lucky streak
        # as a flawless strategy.
        trades = [make_trade(100.0), make_trade(50.0)]
        assert profit_factor(trades) is None

    def test_profit_factor_hand_computed(self) -> None:
        trades = [make_trade(p) for p in (100.0, -50.0, 200.0, -30.0, -20.0)]
        # gross_profit = 100+200=300; gross_loss = 50+30+20=100
        assert profit_factor(trades) == pytest.approx(3.0)


class TestCompute:
    def test_unavailable_explains_every_none_metric(self) -> None:
        # A single bar, no trades: too short for cagr/vol/sharpe/sortino/
        # turnover, and no trades for win_rate/profit_factor/average_win/loss.
        equity = pd.Series([100_000.0])

        metrics = compute(
            equity,
            trades=[],
            periods_per_year=252,
            initial_cash=100_000.0,
            total_costs=0.0,
        )

        nullable_fields = [
            "cagr",
            "annualized_volatility",
            "sharpe",
            "sortino",
            "calmar",
            "win_rate",
            "profit_factor",
            "average_win",
            "average_loss",
            "turnover",
        ]
        for name in nullable_fields:
            value = getattr(metrics, name)
            assert value is None, f"expected {name} to be None for this fixture"
            assert name in metrics.unavailable, f"{name} is None but not explained in `unavailable`"
            assert metrics.unavailable[name], f"{name}'s explanation must be non-empty"

    def test_cost_drag_and_exposure(self) -> None:
        equity = pd.Series([100_000.0, 101_000.0, 99_500.0])
        exposure = pd.Series([0.5, -0.8, 0.3])

        metrics = compute(
            equity,
            trades=[],
            periods_per_year=252,
            initial_cash=100_000.0,
            total_costs=1_500.0,
            exposure=exposure,
        )

        # cost_drag = total_costs / initial_cash = 1500 / 100_000 = 0.015
        assert metrics.cost_drag == pytest.approx(0.015)
        # exposure = mean(|0.5|, |-0.8|, |0.3|) = 1.6 / 3 = 0.5333...
        assert metrics.exposure == pytest.approx(1.6 / 3.0)

    def test_average_win_and_average_loss_are_hand_computed(self) -> None:
        equity = pd.Series([100_000.0, 100_200.0])
        trades = [make_trade(p) for p in (100.0, -50.0, 200.0, -30.0, -20.0)]

        metrics = compute(
            equity,
            trades=trades,
            periods_per_year=252,
            initial_cash=100_000.0,
            total_costs=0.0,
        )

        # wins = [100, 200] -> mean = 150
        assert metrics.average_win == pytest.approx(150.0)
        # losses = [-50, -30, -20] -> mean = -100/3 = -33.3333...
        assert metrics.average_loss == pytest.approx(-100.0 / 3.0)
        assert metrics.win_rate == pytest.approx(0.4)
        assert metrics.profit_factor == pytest.approx(3.0)

    def test_exposure_defaults_to_zero_when_not_supplied(self) -> None:
        equity = pd.Series([100_000.0, 101_000.0])

        metrics = compute(
            equity,
            trades=[],
            periods_per_year=252,
            initial_cash=100_000.0,
            total_costs=0.0,
        )

        assert metrics.exposure == 0.0


def test_sortino_matches_manual_math_module_computation() -> None:
    # Cross-check the sortino helper against an independent computation that
    # does not import the module under test, to catch any accidental import
    # of the implementation into the "hand computed" expectations above.
    returns = [0.05, 0.05, -0.01, 0.05]
    mean = sum(returns) / len(returns)
    downside = [min(x, 0.0) for x in returns]
    downside_deviation = math.sqrt(sum(d * d for d in downside) / len(downside))
    expected = mean / downside_deviation * math.sqrt(252)

    assert sortino(pd.Series(returns), periods_per_year=252, risk_free_rate=0.0) == pytest.approx(
        expected
    )

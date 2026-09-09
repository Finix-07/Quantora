"""BacktestConfig / ExecutionModel: the assumptions that determine every number.

NFR4/NFR5.2 require every assumption behind a result to be inspectable and the
transaction-cost assumption in particular to never be hidden, so `assumptions()`
gets special attention here.
"""

from __future__ import annotations

import pytest

from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.costs import CostModel


class TestExecutionModel:
    def test_next_bar_open_price_field(self) -> None:
        assert ExecutionModel.NEXT_BAR_OPEN.price_field == "open"

    def test_next_bar_close_price_field(self) -> None:
        assert ExecutionModel.NEXT_BAR_CLOSE.price_field == "close"

    def test_both_descriptions_disclaim_use_of_the_fill_bar(self) -> None:
        # Same-bar-close execution would let a decision see information it
        # could not have had; both offered models must be explicit that they
        # don't do that.
        assert (
            "No information from the fill bar is used" in ExecutionModel.NEXT_BAR_OPEN.description
        )
        assert (
            "No information from the fill bar is used" in ExecutionModel.NEXT_BAR_CLOSE.description
        )


class TestConstructionValidation:
    def test_non_positive_initial_cash_raises(self) -> None:
        with pytest.raises(ValueError, match="initial_cash must be positive"):
            BacktestConfig(initial_cash=0.0)

        with pytest.raises(ValueError, match="initial_cash must be positive"):
            BacktestConfig(initial_cash=-1_000.0)

    def test_out_of_range_risk_free_rate_raises_with_unit_explanation(self) -> None:
        # 7 instead of 0.07 is the classic "forgot to divide by 100" mistake;
        # the error must say what units are expected, not just "invalid".
        with pytest.raises(ValueError) as excinfo:
            BacktestConfig(risk_free_rate=7.0)

        message = str(excinfo.value)
        assert "annual decimal fraction" in message
        assert "0.07" in message


class TestPeriodsPerYear:
    def test_daily(self) -> None:
        assert BacktestConfig().periods_per_year("1d") == 252

    def test_weekly(self) -> None:
        assert BacktestConfig().periods_per_year("1wk") == 52

    def test_monthly(self) -> None:
        assert BacktestConfig().periods_per_year("1mo") == 12

    def test_unknown_interval_falls_back_to_daily(self) -> None:
        assert BacktestConfig().periods_per_year("3d-exotic") == 252


class TestSerialization:
    def test_round_trip_including_nested_cost_model(self) -> None:
        config = BacktestConfig(
            initial_cash=250_000.0,
            cost_model=CostModel(
                commission_bps=1.0, commission_min=5.0, slippage_bps=2.0, spread_bps=1.0
            ),
            execution_model=ExecutionModel.NEXT_BAR_CLOSE,
            allow_short=True,
            liquidate_at_end=False,
            risk_free_rate=0.065,
        )

        restored = BacktestConfig.from_dict(config.as_dict())

        assert restored == config
        assert restored.cost_model == config.cost_model
        assert restored.as_dict() == config.as_dict()

    def test_from_dict_none_gives_defaults(self) -> None:
        assert BacktestConfig.from_dict(None) == BacktestConfig()

    def test_from_dict_rejects_unknown_field(self) -> None:
        with pytest.raises(ValueError, match="Unknown backtest-config field"):
            BacktestConfig.from_dict({"initial_cash": 1_000.0, "margin_multiplier": 2.0})


class TestAssumptions:
    def test_always_mentions_transaction_costs(self) -> None:
        # NFR5.2: cost assumptions must never be omitted from the summary.
        assumptions = BacktestConfig().assumptions()

        assert any("Transaction costs" in line for line in assumptions)

    def test_allow_short_changes_the_shorting_statement(self) -> None:
        long_only = BacktestConfig(allow_short=False).assumptions()
        shortable = BacktestConfig(allow_short=True).assumptions()

        long_only_line = next(line for line in long_only if "short" in line.lower())
        shortable_line = next(line for line in shortable if "short" in line.lower())

        assert long_only_line != shortable_line
        assert "never allowed to go negative" in long_only_line
        assert "Short selling is enabled" in shortable_line

    def test_liquidate_at_end_changes_the_end_of_run_statement(self) -> None:
        liquidated = BacktestConfig(liquidate_at_end=True).assumptions()
        left_open = BacktestConfig(liquidate_at_end=False).assumptions()

        liquidated_line = next(
            line for line in liquidated if "final bar" in line or "final close" in line
        )
        left_open_line = next(
            line for line in left_open if "final bar" in line or "final close" in line
        )

        assert liquidated_line != left_open_line
        assert "closed at the final bar's close" in liquidated_line
        assert "left open at the end" in left_open_line

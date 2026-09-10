"""Side-by-side strategy comparison (FR6)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from services.quant.backtest.compare import (
    COMPARISON_METRICS,
    StrategySpec,
    compare_strategies,
)
from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.costs import CostModel
from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import MarketDataResult
from services.quant.data.validation import validate_prices
from services.quant.testing import make_price_series

START, END = "2023-01-01", "2024-12-31"


def fixture_result(symbol: str, seed: int) -> MarketDataResult:
    rng = np.random.default_rng(seed)
    closes = list(100.0 + np.cumsum(rng.normal(0.05, 1.4, 420)))
    series = make_price_series(closes, symbol=symbol, start=START)
    return MarketDataResult(
        series=series, quality=validate_prices(series.frame, symbol=symbol, interval="1d")
    )


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve every fetch from fixtures and record which symbols were asked for."""
    seeds = {"RELIANCE.NS": 5, "TCS.NS": 6}
    calls: list[str] = []

    def fake_get_prices(symbol: str, *_a: object, **_kw: object) -> MarketDataResult:
        calls.append(symbol)
        return fixture_result(symbol, seeds.get(symbol, 7))

    for module in ("compare", "runner"):
        monkeypatch.setattr(f"services.quant.backtest.{module}.get_prices", fake_get_prices)
    return calls


def two_strategies() -> list[StrategySpec]:
    return [StrategySpec("macd"), StrategySpec("bollinger")]


def run_comparison(specs: list[StrategySpec] | None = None, **kwargs: object) -> dict:
    return compare_strategies("RELIANCE.NS", specs or two_strategies(), START, END, **kwargs)


class TestSameDataForEveryStrategy:
    def test_the_instrument_is_fetched_exactly_once(self, offline: list[str]) -> None:
        """The property the whole feature rests on.

        Two fetches of the same range can differ because yfinance revises
        history, and a comparison built on two downloads would attribute a data
        difference to a strategy difference.
        """
        run_comparison(
            [StrategySpec("macd"), StrategySpec("bollinger"), StrategySpec("dual_thrust")]
        )

        assert offline.count("RELIANCE.NS") == 1

    def test_one_data_version_is_reported_for_the_whole_comparison(
        self, offline: list[str]
    ) -> None:
        result = run_comparison()

        assert result["data_version"].startswith("sha256:")
        # Per-strategy versions must agree with it, or "the same bars" is a
        # claim nobody checked.
        for entry in result["strategies"]:
            assert entry["data_version"] == result["data_version"]

    def test_an_auxiliary_leg_is_fetched_once_across_strategies(self, offline: list[str]) -> None:
        run_comparison(
            [
                StrategySpec("pair_trading", {"pair_symbol": "TCS.NS", "lookback": 30}),
                StrategySpec("pair_trading", {"pair_symbol": "TCS.NS", "lookback": 40}),
            ]
        )

        assert offline.count("TCS.NS") == 1


class TestLabelling:
    def test_parameter_variants_get_distinct_labels(self, offline: list[str]) -> None:
        # Two runs of one strategy must not collapse into a single column.
        result = run_comparison(
            [
                StrategySpec("macd"),
                StrategySpec("macd", {"fast": 5, "slow": 20}),
            ]
        )

        labels = [entry["label"] for entry in result["strategies"]]
        assert len(set(labels)) == 2
        assert any("fast=5" in label for label in labels)

    def test_explicit_labels_are_honoured(self, offline: list[str]) -> None:
        result = run_comparison(
            [StrategySpec("macd", label="Baseline"), StrategySpec("bollinger", label="Mean rev")]
        )

        assert [e["label"] for e in result["strategies"]] == ["Baseline", "Mean rev"]

    def test_duplicate_labels_are_disambiguated(self, offline: list[str]) -> None:
        # A duplicate would silently overwrite a column in every keyed map, and
        # the user would see one fewer strategy than they asked for.
        result = run_comparison(
            [StrategySpec("macd", label="Same"), StrategySpec("bollinger", label="Same")]
        )

        labels = [e["label"] for e in result["strategies"]]
        assert len(set(labels)) == 2
        assert set(result["equity_curves"]) == set(labels)


class TestMetricTable:
    def test_every_required_metric_is_present(self, offline: list[str]) -> None:
        result = run_comparison()

        rows = {row["metric"] for row in result["metric_table"]}
        # FR6's named metrics.
        for required in (
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "win_rate",
            "profit_factor",
            "trade_count",
            "turnover",
            "exposure",
        ):
            assert required in rows, f"FR6 requires {required}"

    def test_each_row_has_a_value_for_every_strategy(self, offline: list[str]) -> None:
        result = run_comparison()
        labels = {e["label"] for e in result["strategies"]}

        for row in result["metric_table"]:
            assert set(row["values"]) == labels

    def test_direction_is_data_not_a_ui_convention(self, offline: list[str]) -> None:
        """A front-end that assumed "bigger is greener" would mark the worst
        drawdown as the winner and the highest cost as the best."""
        result = run_comparison()
        rows = {row["metric"]: row for row in result["metric_table"]}

        assert rows["sharpe"]["better"] == "higher"
        assert rows["total_costs"]["better"] == "lower"
        assert rows["annualized_volatility"]["better"] == "lower"
        # Max drawdown is negative, so closer to zero is better — which is
        # "higher", not "lower".
        assert rows["max_drawdown"]["better"] == "higher"
        # A pure count has no better direction and must not be highlighted.
        assert rows["trade_count"]["better"] is None
        assert rows["trade_count"]["best"] is None

    def test_the_best_column_matches_the_declared_direction(self, offline: list[str]) -> None:
        result = run_comparison()

        for row in result["metric_table"]:
            if row["best"] is None:
                continue
            comparable = {k: v for k, v in row["values"].items() if v is not None}
            expected = (
                max(comparable, key=lambda k: comparable[k])
                if row["better"] == "higher"
                else min(comparable, key=lambda k: comparable[k])
            )
            assert row["best"] == expected, row["metric"]

    def test_a_lone_comparable_value_is_not_declared_a_winner(self, offline: list[str]) -> None:
        # Nothing to win against; highlighting it would be a meaningless mark.
        result = run_comparison()

        for row in result["metric_table"]:
            comparable = [v for v in row["values"].values() if v is not None]
            if len(comparable) < 2:
                assert row["best"] is None, row["metric"]

    def test_unavailable_metrics_carry_the_engines_reason(self, offline: list[str]) -> None:
        """The table's explanations must match the engine's, not be invented.

        A None in the table with no reason beside it leaves the UI with nothing
        to show but a blank or a fabricated zero.
        """
        # num_std=5 puts the bands far outside anything this fixture reaches, so
        # that strategy makes no trades and its win rate and profit factor are
        # legitimately undefined — which is what this test needs to inspect.
        result = run_comparison(
            [
                StrategySpec("macd"),
                StrategySpec("bollinger", {"num_std": 5.0}, label="Never trades"),
            ]
        )
        reasons_by_label = {
            entry["label"]: entry["metrics"].get("unavailable", {})
            for entry in result["strategies"]
        }

        checked = 0
        for row in result["metric_table"]:
            for label, value in row["values"].items():
                engine_reason = reasons_by_label[label].get(row["metric"])
                if engine_reason is None:
                    # The engine gave no reason for this metric, so the table
                    # must not manufacture one.
                    assert label not in row["unavailable"], (
                        f"{row['metric']} for {label} has a reason the engine never gave"
                    )
                    continue
                assert value is None, f"{row['metric']} has both a value and an unavailable reason"
                assert row["unavailable"].get(label) == engine_reason
                checked += 1

        assert checked > 0, (
            "no metric was unavailable in this fixture, so this test proved nothing; "
            "pick strategies/params that leave at least one metric undefined"
        )


class TestBenchmark:
    def test_buy_and_hold_is_reported_for_the_same_window(self, offline: list[str]) -> None:
        # Without it a user cannot tell whether a strategy earned its
        # complexity: 30% looks good until you learn holding returned 79%.
        result = run_comparison()
        benchmark = result["benchmark"]

        assert benchmark["available"] is True
        assert benchmark["total_return"] is not None
        assert benchmark["quantity"] > 0

    def test_the_benchmark_pays_the_same_costs_as_the_strategies(self, offline: list[str]) -> None:
        """A frictionless reference would flatter every strategy against it."""
        charged = run_comparison(config=BacktestConfig(cost_model=CostModel()))["benchmark"]
        free = run_comparison(config=BacktestConfig(cost_model=CostModel.zero()))["benchmark"]

        assert charged["total_costs"] > 0
        assert free["total_costs"] == 0
        assert charged["entry_price"] > free["entry_price"]

    def test_an_unaffordable_benchmark_says_so_instead_of_reporting_zero(
        self, offline: list[str]
    ) -> None:
        result = run_comparison(config=BacktestConfig(initial_cash=1.0))

        benchmark = result["benchmark"]
        assert benchmark["available"] is False
        assert "whole unit" in benchmark["reason"]


class TestFailureHandling:
    def test_one_failing_strategy_does_not_lose_the_others(self, offline: list[str]) -> None:
        """A shorter list could be read as "these are all of them"."""
        result = run_comparison(
            [
                StrategySpec("macd"),
                # No pair series can be resolved for a self-pair, so this fails.
                StrategySpec("pair_trading", {"pair_symbol": "RELIANCE.NS"}),
                StrategySpec("bollinger"),
            ]
        )

        assert len(result["strategies"]) == 2
        assert len(result["failures"]) == 1
        failure = result["failures"][0]
        assert failure["strategy"] == "pair_trading"
        assert failure["error"], "the failure must say what went wrong"

    def test_all_strategies_failing_is_an_error_not_an_empty_table(
        self, offline: list[str]
    ) -> None:
        with pytest.raises(InvalidRequestError, match="Every strategy in the comparison failed"):
            run_comparison(
                [
                    # Every entry self-pairs, which is refused for each of them.
                    # Warm-up limits cap below the fixture length, so an
                    # insufficient-data failure could not be made to apply to
                    # all entries and would only re-prove the partial case.
                    StrategySpec("pair_trading", {"pair_symbol": "RELIANCE.NS"}, label="A"),
                    StrategySpec("pair_trading", {"pair_symbol": "RELIANCE.NS"}, label="B"),
                ]
            )

    def test_fewer_than_two_strategies_is_refused(self, offline: list[str]) -> None:
        # A "comparison" of one is a backtest; returning it silently would hide
        # a caller's mistake.
        with pytest.raises(InvalidRequestError, match="at least two strategies"):
            run_comparison([StrategySpec("macd")])


class TestResponseShape:
    def test_assumptions_are_stated_once_for_the_whole_comparison(self, offline: list[str]) -> None:
        """Repeating them per row would invite a reader to think they differ."""
        result = run_comparison()

        assert result["assumptions"]
        assert "commission" in " ".join(result["assumptions"])
        assert result["cost_model"]["commission_bps"] is not None
        for entry in result["strategies"]:
            assert entry["cost_model"] == result["cost_model"]

    def test_curves_are_returned_per_strategy_over_the_same_index(self, offline: list[str]) -> None:
        result = run_comparison()

        lengths = {len(curve) for curve in result["equity_curves"].values()}
        assert len(lengths) == 1, "curves must span the same bars to be overlaid"
        assert set(result["equity_curves"]) == set(result["drawdown_curves"])

    def test_provenance_and_quality_travel_with_the_comparison(self, offline: list[str]) -> None:
        result = run_comparison()

        assert result["provenance"]["symbol"] == "RELIANCE.NS"
        assert result["data_quality"]["severity"] in {"clean", "gaps_detected", "severe_gaps"}

    def test_the_response_is_json_serializable(self, offline: list[str]) -> None:
        # It is sent over HTTP and stored; a stray NumPy scalar would fail at
        # the boundary rather than here.
        encoded = json.dumps(run_comparison())

        assert json.loads(encoded)["symbol"] == "RELIANCE.NS"

    def test_comparison_is_deterministic(self, offline: list[str]) -> None:
        first = run_comparison()
        second = run_comparison()

        assert first["metric_table"] == second["metric_table"]


def test_every_comparison_metric_has_a_display_label() -> None:
    for key, label, direction in COMPARISON_METRICS:
        assert key and label
        assert direction in {"higher", "lower", None}

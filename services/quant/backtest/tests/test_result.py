"""The serialized backtest result contract (architecture.md §8)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.costs import CostModel
from services.quant.backtest.result import (
    RESULT_CONTRACT_VERSION,
    build_result,
    summarize_result,
)
from services.quant.backtest.runner import run_backtest
from services.quant.strategies.macd import MACDStrategy
from services.quant.testing import make_price_series

MACD_PARAMS = {"fast": 3, "slow": 6, "signal": 3}


def sample_run(**config_kwargs):
    rng = np.random.default_rng(3)
    closes = list(100.0 + np.cumsum(rng.normal(0.2, 1.5, 180)))
    series = make_price_series(closes, symbol="RELIANCE.NS")
    return run_backtest(series, MACDStrategy(**MACD_PARAMS), BacktestConfig(**config_kwargs))


def test_contract_has_every_field_architecture_requires() -> None:
    payload = build_result(sample_run(), experiment_id="exp_123").as_dict()

    for key in (
        "experiment_id",
        "strategy",
        "symbol",
        "start",
        "end",
        "parameters",
        "cost_model",
        "metrics",
        "trades",
        "equity_curve",
        "drawdown_curve",
        "data_version",
    ):
        assert key in payload, f"architecture.md §8 requires {key!r}"

    for metric in ("cagr", "sharpe", "sortino", "max_drawdown", "win_rate", "profit_factor"):
        assert metric in payload["metrics"]


def test_result_is_json_serializable() -> None:
    # It is persisted to PostgreSQL and sent over HTTP; a NumPy scalar or a
    # Timestamp anywhere in it would fail at the boundary rather than here.
    payload = build_result(sample_run()).as_dict()

    encoded = json.dumps(payload)

    assert json.loads(encoded)["strategy"] == "macd"


def test_curves_have_one_point_per_bar_with_iso_timestamps() -> None:
    run = sample_run()

    payload = build_result(run).as_dict()

    assert len(payload["equity_curve"]) == len(run.series)
    assert len(payload["drawdown_curve"]) == len(run.series)
    first = payload["equity_curve"][0]
    assert set(first) == {"timestamp", "value"}
    assert first["timestamp"].startswith("20")


def test_assumptions_are_present_and_mention_costs() -> None:
    # NFR5.2: transaction-cost assumptions are never hidden.
    payload = build_result(sample_run()).as_dict()

    assumptions = " ".join(payload["assumptions"])
    assert "commission" in assumptions
    assert "slippage" in assumptions
    assert "next bar" in assumptions.lower()


def test_zero_cost_run_says_so_explicitly() -> None:
    payload = build_result(sample_run(cost_model=CostModel.zero())).as_dict()

    assumptions = " ".join(payload["assumptions"]).lower()
    assert "frictionless" in assumptions or "optimistic" in assumptions


def test_unavailable_metrics_carry_their_reason() -> None:
    """A missing metric must explain itself rather than showing as 0."""
    payload = build_result(sample_run()).as_dict()
    metrics = payload["metrics"]

    for name, value in metrics.items():
        if name == "unavailable":
            continue
        if value is None:
            assert metrics["unavailable"].get(name), f"{name} is None with no explanation"


def test_provenance_and_data_version_travel_with_the_result() -> None:
    payload = build_result(sample_run()).as_dict()

    assert payload["data_version"].startswith("sha256:")
    provenance = payload["provenance"]
    assert provenance["symbol"] == "RELIANCE.NS"
    assert provenance["adjustment_policy"]
    assert provenance["retrieval_timestamp"]


def test_signal_summary_partitions_every_bar() -> None:
    run = sample_run()

    summary = build_result(run).as_dict()["signal_summary"]

    assert summary["bars_long"] + summary["bars_short"] + summary["bars_flat"] == len(run.series)
    assert summary["warmup_bars"] == MACDStrategy(**MACD_PARAMS).warmup_bars


def test_trades_carry_gross_costs_and_net_pnl() -> None:
    run = sample_run()
    payload = build_result(run).as_dict()

    if not payload["trades"]:
        pytest.skip("fixture produced no completed trades")
    trade = payload["trades"][0]
    for key in ("gross_pnl", "costs", "pnl", "entry_reason", "exit_reason", "bars_held"):
        assert key in trade
    assert trade["gross_pnl"] - trade["costs"] == pytest.approx(trade["pnl"])


def test_contract_version_and_code_version_are_recorded() -> None:
    payload = build_result(sample_run()).as_dict()

    assert payload["contract_version"] == RESULT_CONTRACT_VERSION
    assert payload["code_version"]


def test_warnings_field_is_always_present() -> None:
    """Any divergence between what the strategy asked for and what was
    simulated has to reach the user.

    The populated case (an order reduced or skipped for cash) is exercised in
    test_engine.py; here the contract-level guarantee is that the field always
    exists, so the UI can render it unconditionally instead of treating a
    missing key as "nothing went wrong".
    """
    payload = build_result(sample_run()).as_dict()

    assert isinstance(payload["warnings"], list)


def test_summarize_drops_the_bulky_fields_but_keeps_the_evidence() -> None:
    payload = build_result(sample_run(), experiment_id="exp_9").as_dict()

    summary = summarize_result(payload)

    assert "equity_curve" not in summary
    assert "trades" not in summary
    # Everything needed to trace a comparison row back to its run stays.
    assert summary["experiment_id"] == "exp_9"
    assert summary["data_version"] == payload["data_version"]
    assert summary["cost_model"] == payload["cost_model"]
    assert summary["metrics"] == payload["metrics"]


def test_result_is_reproducible_for_identical_inputs() -> None:
    """NFR6, at the contract level: the same inputs serialize identically.

    `code_version` and provenance's retrieval timestamp are excluded because
    they describe *when and by what* the run happened, not what was computed.
    """
    first = build_result(sample_run()).as_dict()
    second = build_result(sample_run()).as_dict()

    for payload in (first, second):
        payload.pop("code_version")
        payload["provenance"].pop("retrieval_timestamp")

    assert first == second

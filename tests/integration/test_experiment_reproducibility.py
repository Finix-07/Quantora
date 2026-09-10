"""Reproducibility of saved experiments (testing.md §4, mandatory).

NFR6 states that a saved experiment must be rerunnable and produce the same
result given the same `data_version`. This is the test that holds the whole
product's central promise, and it deliberately runs across every real boundary:

    HTTP -> Go API -> quant-mcp -> yfinance -> PostgreSQL -> Go API -> HTTP

A mocked version would prove only that the comparison function works. It would
not catch a JSONB round trip that loses precision, a float that changes on its
way through the database, or a stored configuration that fails to replay — which
are exactly the ways reproducibility breaks in practice.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration

# A window long enough that the strategy actually trades. A range that produced
# zero trades would let this test pass while proving nothing.
SYMBOL = "RELIANCE.NS"
START = "2022-01-01"
END = "2024-12-31"


@pytest.fixture(scope="module")
def saved_experiment(api: httpx.Client) -> dict:
    """Save one experiment and reuse it across the assertions below."""
    response = api.post(
        "/api/experiments",
        json={
            "name": "reproducibility integration test",
            "notes": "created by tests/integration/test_experiment_reproducibility.py",
            "symbol": SYMBOL,
            "strategy": "macd",
            "start": START,
            "end": END,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def rerun_outcome(api: httpx.Client, saved_experiment: dict) -> dict:
    response = api.post(f"/api/experiments/{saved_experiment['id']}/rerun")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_saved_experiment_is_substantial_enough_to_prove_anything(
    saved_experiment: dict,
) -> None:
    """Guard against a vacuous pass.

    Reproducing a backtest that made no trades and computed no metrics is not
    evidence of anything, so the fixture is checked for substance before the
    reproducibility assertions are allowed to mean something.
    """
    result = saved_experiment["result"]
    metrics = result["metrics"]

    assert metrics["trade_count"] > 0, "the fixture window produced no trades"
    assert result["equity_curve"], "no equity curve was produced"
    assert len(result["trades"]) == metrics["trade_count"]
    # At least the headline return must be a real number, not an unavailable one.
    assert metrics["total_return"] is not None


def test_the_experiment_stores_everything_needed_to_rerun_it(
    saved_experiment: dict,
) -> None:
    """FR5: a rerun must not require the user to remember any configuration."""
    for field in ("strategy", "symbol", "start", "end", "interval", "data_version"):
        assert saved_experiment[field], f"{field} was not persisted"

    # The assumptions behind the numbers have to survive the round trip, or a
    # rerun would silently apply today's defaults instead (NFR5.2).
    assert saved_experiment["cost_model"]["commission_bps"] is not None
    assert saved_experiment["backtest_config"]["execution_model"]
    assert saved_experiment["parameters"], "strategy parameters were not persisted"


def test_rerunning_reproduces_the_experiment_exactly(rerun_outcome: dict) -> None:
    """The core NFR6 assertion."""
    assert rerun_outcome["reproducible"] is True, rerun_outcome["explanation"]
    assert rerun_outcome["data_version_matches"] is True
    assert rerun_outcome["metrics_match"] is True
    assert rerun_outcome["differences"] == []


def test_the_rerun_used_the_same_data(saved_experiment: dict, rerun_outcome: dict) -> None:
    """`data_version` is what makes "the same result" a checkable claim.

    yfinance revises history, so identical metrics alone would not establish
    that the two runs saw the same bars.
    """
    assert rerun_outcome["original_data_version"] == saved_experiment["data_version"]
    assert rerun_outcome["rerun_data_version"] == saved_experiment["data_version"]
    assert rerun_outcome["original_data_version"].startswith("sha256:")


def test_the_reruns_metrics_are_identical_field_by_field(
    saved_experiment: dict, rerun_outcome: dict
) -> None:
    """Asserted directly, not only via the service's own verdict.

    Trusting `reproducible: true` alone would make this test a check of the
    comparison function rather than of the numbers.
    """
    original = saved_experiment["result"]["metrics"]
    rerun = rerun_outcome["rerun_result"]["metrics"]

    compared = 0
    for name, value in original.items():
        if name == "unavailable" or value is None:
            continue
        assert rerun[name] == pytest.approx(value, rel=1e-12, abs=1e-12), (
            f"{name} changed between the original run and the rerun: {value} -> {rerun[name]}"
        )
        compared += 1

    assert compared >= 5, "too few comparable metrics for this assertion to mean much"


def test_the_reruns_trades_are_identical(saved_experiment: dict, rerun_outcome: dict) -> None:
    """Metrics can coincide while the underlying trades differ."""
    original = saved_experiment["result"]["trades"]
    rerun = rerun_outcome["rerun_result"]["trades"]

    assert len(rerun) == len(original)
    for index, (first, second) in enumerate(zip(original, rerun, strict=True)):
        assert first["entry_timestamp"] == second["entry_timestamp"], f"trade {index} moved"
        assert first["exit_timestamp"] == second["exit_timestamp"], f"trade {index} moved"
        assert second["pnl"] == pytest.approx(first["pnl"], rel=1e-12, abs=1e-9)


def test_the_experiment_appears_in_the_journal(api: httpx.Client, saved_experiment: dict) -> None:
    response = api.get("/api/experiments", params={"symbol": SYMBOL, "limit": 50})
    assert response.status_code == 200, response.text

    rows = {row["id"]: row for row in response.json()["experiments"]}
    assert saved_experiment["id"] in rows

    row = rows[saved_experiment["id"]]
    assert row["metrics"]["trade_count"] == saved_experiment["result"]["metrics"]["trade_count"]
    # The list view projects metrics in SQL; an unavailable metric must stay
    # null there rather than being rendered as a fabricated zero.
    unavailable = saved_experiment["result"]["metrics"]["unavailable"]
    for name in unavailable:
        if name in row["metrics"]:
            assert row["metrics"][name] is None, (
                f"{name} was unavailable in the result but shows as "
                f"{row['metrics'][name]} in the journal"
            )


def test_rerunning_an_unknown_experiment_is_a_clean_404(api: httpx.Client) -> None:
    response = api.post("/api/experiments/exp_does_not_exist/rerun")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_experiment_can_be_deleted(api: httpx.Client, saved_experiment: dict) -> None:
    """Runs last so the fixtures above still have their row.

    Cleaning up keeps repeated local runs from accumulating rows, and exercises
    the delete path at the same time.
    """
    response = api.delete(f"/api/experiments/{saved_experiment['id']}")
    assert response.status_code == 204

    assert api.get(f"/api/experiments/{saved_experiment['id']}").status_code == 404

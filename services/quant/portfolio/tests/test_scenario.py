"""Scenario analysis: reweight, recalculate, compare (architecture.md §10)."""

from __future__ import annotations

import json

import pytest

from services.quant.data.errors import InvalidRequestError, UnknownSymbolError
from services.quant.portfolio.holdings import Holding, Portfolio
from services.quant.portfolio.risk import RISK_METRICS
from services.quant.portfolio.scenario import resolve_weights, run_scenario
from services.quant.portfolio.tests.conftest import alternating, compound
from services.quant.portfolio.tests.test_risk import BARS, BENCHMARK_PATH, RETURNS

PATH_A = compound(500.0, alternating(RETURNS, 0.03, -0.02))
PATH_B = compound(80.0, alternating(RETURNS, -0.01, 0.04))
PATH_C = compound(200.0, alternating(RETURNS, 0.01, 0.005))

PATHS = {
    "NIFTY": BENCHMARK_PATH,
    "RELIANCE.NS": PATH_A,
    "INFY.NS": PATH_B,
    "TCS.NS": PATH_C,
}


def fifty_fifty() -> Portfolio:
    """A portfolio worth the same in each leg at the as-of bar.

    One unit of A and (last A / last B) units of B have identical market value
    on the final bar, so the base weights are exactly 0.5 / 0.5 and every
    before/after number below can be checked by hand.
    """
    return Portfolio(
        holdings=(
            Holding("RELIANCE.NS", 1.0),
            Holding("INFY.NS", PATH_A[-1] / PATH_B[-1]),
        ),
        benchmark="NIFTY",
    )


def scenario(weights, price_loader, portfolio=None, paths=None, **kwargs):
    return run_scenario(
        portfolio or fifty_fifty(),
        weights,
        "2024-01-01",
        "2024-12-31",
        loader=price_loader(paths or PATHS),
        **kwargs,
    )


# --- before / after -----------------------------------------------------------


def test_before_and_after_are_both_reported_in_full(price_loader):
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)

    # Both states carry a complete report, not just the metrics that changed:
    # a delta alone cannot distinguish 1.90 -> 1.88 from 0.03 -> 0.01.
    for state in ("before", "after"):
        assert set(result[state]) >= {"portfolio", "metrics", "correlation_matrix", "assumptions"}
    assert result["before"]["portfolio"]["weights"]["RELIANCE.NS"] == pytest.approx(0.5)
    assert result["after"]["portfolio"]["weights"]["RELIANCE.NS"] == pytest.approx(0.75)


def test_concentration_deltas_match_the_hand_computed_values(price_loader):
    # Before: 0.5 / 0.5   -> largest 0.5,  HHI = 0.25 + 0.25 = 0.50, 1/HHI = 2.0
    # After:  0.75 / 0.25 -> largest 0.75, HHI = 0.5625 + 0.0625 = 0.625, 1/HHI = 1.6
    # Deltas: largest +0.25, HHI +0.125, effective holdings -0.4
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    deltas = {row["metric"]: row for row in result["deltas"]}

    assert deltas["largest_weight"]["before"] == pytest.approx(0.5)
    assert deltas["largest_weight"]["after"] == pytest.approx(0.75)
    assert deltas["largest_weight"]["delta"] == pytest.approx(0.25)
    assert deltas["hhi"]["before"] == pytest.approx(0.50)
    assert deltas["hhi"]["after"] == pytest.approx(0.625)
    assert deltas["hhi"]["delta"] == pytest.approx(0.125)
    assert deltas["effective_holdings"]["delta"] == pytest.approx(-0.4)
    # Concentrating the book is a worse outcome on both concentration measures.
    assert deltas["hhi"]["improved"] is False
    assert deltas["effective_holdings"]["improved"] is False


def test_every_risk_metric_appears_in_the_delta_table(price_loader):
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    assert [row["metric"] for row in result["deltas"]] == [m[0] for m in RISK_METRICS]


def test_beta_delta_has_no_better_direction(price_loader):
    # A higher beta is a different exposure, not a worse one. Marking it
    # good or bad would be the front-end's "bigger is greener" mistake.
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    beta_row = next(row for row in result["deltas"] if row["metric"] == "beta")
    assert beta_row["better"] is None
    assert beta_row["improved"] is None


# --- additions, removals, normalisation ---------------------------------------


def test_a_symbol_not_in_the_portfolio_is_called_out_as_an_addition(price_loader):
    result = scenario({"TCS.NS": 0.5}, price_loader)

    change = next(c for c in result["changes"] if c["symbol"] == "TCS.NS")
    assert change["change"] == "added"
    assert change["weight_before"] == pytest.approx(0.0)
    assert "not in the current portfolio" in change["note"]
    assert "TCS.NS" in result["after"]["portfolio"]["weights"]
    assert "TCS.NS" not in result["before"]["portfolio"]["weights"]


def test_a_zeroed_weight_is_called_out_as_a_removal(price_loader):
    result = scenario({"INFY.NS": 0.0}, price_loader)

    change = next(c for c in result["changes"] if c["symbol"] == "INFY.NS")
    assert change["change"] == "removed"
    assert change["weight_after"] == pytest.approx(0.0)
    assert "zeroed out" in change["note"]
    # The position is gone from the scenario portfolio, not held at zero units.
    assert "INFY.NS" not in result["after"]["portfolio"]["weights"]
    assert result["after"]["portfolio"]["weights"]["RELIANCE.NS"] == pytest.approx(1.0)


def test_unchanged_weights_do_not_appear_in_the_change_list(price_loader):
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    assert {c["symbol"] for c in result["changes"]} == {"RELIANCE.NS", "INFY.NS"}

    added_only = scenario({"TCS.NS": 0.0}, price_loader)
    # Setting an unheld symbol to zero changes nothing at all.
    assert added_only["changes"] == []


def test_weights_that_do_not_sum_to_one_are_normalised_and_said_so(price_loader):
    # Overriding only RELIANCE to 0.8 leaves INFY at its current 0.5, so the
    # requested vector sums to 1.3 and is divided through by it:
    #   RELIANCE = 0.8 / 1.3 = 0.6153846153846154
    #   INFY     = 0.5 / 1.3 = 0.38461538461538464
    result = scenario({"RELIANCE.NS": 0.8}, price_loader)

    assert result["scenario_weights"]["RELIANCE.NS"] == pytest.approx(0.8 / 1.3)
    assert result["scenario_weights"]["INFY.NS"] == pytest.approx(0.5 / 1.3)
    assert any("summed to 1.3000" in note for note in result["notes"])


def test_resolve_weights_leaves_an_already_normalised_vector_untouched():
    weights, notes = resolve_weights(
        {"RELIANCE.NS": 0.5, "INFY.NS": 0.5}, {"RELIANCE.NS": 0.75, "INFY.NS": 0.25}
    )
    assert weights == pytest.approx({"RELIANCE.NS": 0.75, "INFY.NS": 0.25})
    assert notes == []


# --- rejections ---------------------------------------------------------------


def test_a_negative_weight_is_rejected_with_a_specific_message(price_loader):
    with pytest.raises(InvalidRequestError) as excinfo:
        scenario({"RELIANCE.NS": -0.2}, price_loader)
    message = str(excinfo.value)
    assert "zero or positive" in message
    assert "RELIANCE.NS=-0.2" in message
    assert "short position" in message


def test_an_all_zero_weight_vector_is_rejected(price_loader):
    with pytest.raises(InvalidRequestError) as excinfo:
        scenario({"RELIANCE.NS": 0.0, "INFY.NS": 0.0}, price_loader)
    assert "sum to 0" in str(excinfo.value)
    assert "cannot be normalised" in str(excinfo.value)


def test_an_empty_scenario_is_rejected(price_loader):
    with pytest.raises(InvalidRequestError) as excinfo:
        scenario({}, price_loader)
    assert "at least one weight" in str(excinfo.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0.5", None])
def test_non_numeric_or_non_finite_weights_are_rejected(price_loader, value):
    with pytest.raises(InvalidRequestError) as excinfo:
        scenario({"RELIANCE.NS": value}, price_loader)
    assert "finite numbers" in str(excinfo.value)


def test_an_unknown_symbol_in_a_scenario_is_rejected(price_loader):
    with pytest.raises(UnknownSymbolError):
        scenario({"NOTREAL.NS": 0.5}, price_loader)


# --- unavailable metrics ------------------------------------------------------


def test_a_delta_is_none_when_either_side_is_unavailable(price_loader):
    # A flat benchmark makes beta undefined in *both* states. Reporting a delta
    # of 0.0 would say "the scenario did not change your market exposure",
    # which is a claim neither state supports.
    paths = {**PATHS, "NIFTY": [100.0] * BARS}
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader, paths=paths)

    beta_row = next(row for row in result["deltas"] if row["metric"] == "beta")
    assert beta_row["before"] is None
    assert beta_row["after"] is None
    assert beta_row["delta"] is None
    assert "before:" in beta_row["unavailable"]
    assert "after:" in beta_row["unavailable"]
    assert "zero variance" in beta_row["unavailable"]


# --- contract -----------------------------------------------------------------


def test_total_market_value_is_held_constant_across_the_scenario(price_loader):
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    assert result["after"]["portfolio"]["total_value"] == pytest.approx(
        result["before"]["portfolio"]["total_value"]
    )
    assert any("total market value constant" in line for line in result["assumptions"])


def test_both_states_share_one_as_of_date_and_one_set_of_bars(price_loader):
    result = scenario({"TCS.NS": 0.5}, price_loader)
    assert result["before"]["as_of"] == result["after"]["as_of"] == result["as_of"]
    assert result["before"]["observations"] == result["after"]["observations"]
    assert any("nothing else" in line for line in result["assumptions"])


def test_the_static_weight_assumption_survives_into_the_scenario(price_loader):
    result = scenario({"RELIANCE.NS": 0.75, "INFY.NS": 0.25}, price_loader)
    assert any("held constant" in line for line in result["assumptions"])


def test_the_scenario_is_deterministic(price_loader):
    weights = {"RELIANCE.NS": 0.6, "TCS.NS": 0.2}
    first = scenario(weights, price_loader)
    second = scenario(weights, price_loader)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

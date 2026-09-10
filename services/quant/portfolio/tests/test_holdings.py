"""Allocation, concentration and sector exposure (testing.md §2.1).

Every expected number here is computed by hand in a comment above the
assertion. Nothing in this file calls pandas to check pandas.
"""

from __future__ import annotations

import math

import pytest

from services.quant.data.errors import InvalidRequestError, UnknownSymbolError
from services.quant.portfolio.holdings import (
    UNCLASSIFIED,
    Holding,
    Portfolio,
    concentration,
    sector_exposure,
)


def test_allocation_weights_are_market_value_over_total():
    # 100 x 2,000 = 200,000 | 50 x 4,000 = 200,000 | 200 x 500 = 100,000
    # total = 500,000 -> weights 0.4, 0.4, 0.2
    portfolio = Portfolio(
        holdings=(
            Holding("RELIANCE.NS", 100),
            Holding("TCS.NS", 50),
            Holding("INFY.NS", 200),
        )
    )
    allocation = portfolio.value({"RELIANCE.NS": 2_000.0, "TCS.NS": 4_000.0, "INFY.NS": 500.0})

    assert allocation.total_value == pytest.approx(500_000.0)
    assert allocation.weights == pytest.approx({"RELIANCE.NS": 0.4, "TCS.NS": 0.4, "INFY.NS": 0.2})
    values = {p.symbol: p.market_value for p in allocation.positions}
    assert values == pytest.approx(
        {"RELIANCE.NS": 200_000.0, "TCS.NS": 200_000.0, "INFY.NS": 100_000.0}
    )
    # Weights are a partition of the portfolio, so they must sum to exactly 1.
    assert sum(allocation.weights.values()) == pytest.approx(1.0)


def test_concentration_on_a_hand_built_portfolio():
    # Market values 400k / 300k / 200k / 50k / 50k on a 1,000,000 book
    # -> weights 0.40, 0.30, 0.20, 0.05, 0.05
    # largest      = 0.40
    # top 3        = 0.40 + 0.30 + 0.20 = 0.90
    # HHI          = 0.16 + 0.09 + 0.04 + 0.0025 + 0.0025 = 0.295
    # effective n  = 1 / 0.295 = 3.389830508474576
    portfolio = Portfolio(
        holdings=(
            Holding("RELIANCE.NS", 200),
            Holding("TCS.NS", 75),
            Holding("HDFCBANK.NS", 100),
            Holding("INFY.NS", 100),
            Holding("ICICIBANK.NS", 50),
        )
    )
    allocation = portfolio.value(
        {
            "RELIANCE.NS": 2_000.0,
            "TCS.NS": 4_000.0,
            "HDFCBANK.NS": 2_000.0,
            "INFY.NS": 500.0,
            "ICICIBANK.NS": 1_000.0,
        }
    )

    result = allocation.concentration
    assert result.largest_symbol == "RELIANCE.NS"
    assert result.largest_weight == pytest.approx(0.40)
    assert result.top_n == 3
    assert result.top_n_weight == pytest.approx(0.90)
    assert result.top_n_symbols == ("RELIANCE.NS", "TCS.NS", "HDFCBANK.NS")
    assert result.hhi == pytest.approx(0.295)
    assert result.effective_holdings == pytest.approx(3.389830508474576)


def test_hhi_separates_books_that_share_a_largest_weight():
    # Both books have a 25% largest position, so `largest_weight` cannot tell
    # them apart. This is the whole reason HHI is reported alongside it.
    even = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    # HHI = 4 x 0.0625 = 0.25 -> 1/0.25 = 4 effective holdings
    spread = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.10, "e": 0.10, "f": 0.05}
    # HHI = 3 x 0.0625 + 2 x 0.01 + 0.0025 = 0.1875 + 0.02 + 0.0025 = 0.21
    #     -> 1/0.21 = 4.761904761904762 effective holdings

    even_result = concentration(even)
    spread_result = concentration(spread)

    assert even_result.largest_weight == spread_result.largest_weight == pytest.approx(0.25)
    assert even_result.hhi == pytest.approx(0.25)
    assert even_result.effective_holdings == pytest.approx(4.0)
    assert spread_result.hhi == pytest.approx(0.21)
    assert spread_result.effective_holdings == pytest.approx(4.761904761904762)
    assert spread_result.hhi < even_result.hhi


def test_top_n_covers_the_whole_book_when_there_are_fewer_than_n_holdings():
    result = concentration({"a": 0.6, "b": 0.4})
    assert result.top_n == 2
    assert result.top_n_weight == pytest.approx(1.0)


def test_sector_exposure_sums_holdings_within_a_sector():
    # TCS and Infosys are both Information Technology: 0.4 + 0.2 = 0.6
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 100), Holding("TCS.NS", 50), Holding("INFY.NS", 200))
    )
    exposure = portfolio.value(
        {"RELIANCE.NS": 2_000.0, "TCS.NS": 4_000.0, "INFY.NS": 500.0}
    ).sector_exposure

    assert exposure.sectors == pytest.approx({"Energy": 0.4, "Information Technology": 0.6})
    assert exposure.classified_weight == pytest.approx(1.0)
    assert exposure.unclassified_weight == pytest.approx(0.0)
    assert exposure.unclassified_symbols == ()


def test_index_holding_is_reported_as_unclassified_not_dropped():
    # 30 x 2,000 = 60,000 equity | 2 x 20,000 = 40,000 index -> 0.6 / 0.4
    # NIFTY has no sector in the universe, so 40% of the book is unclassified.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 30), Holding("NIFTY", 2)))
    exposure = portfolio.value({"RELIANCE.NS": 2_000.0, "NIFTY": 20_000.0}).sector_exposure

    assert exposure.sectors[UNCLASSIFIED] == pytest.approx(0.4)
    assert exposure.sectors["Energy"] == pytest.approx(0.6)
    assert exposure.classified_weight == pytest.approx(0.6)
    assert exposure.unclassified_weight == pytest.approx(0.4)
    assert exposure.unclassified_symbols == ("NIFTY",)
    # The remainder is named, so a reader cannot mistake the 60% for the whole.
    assert "NIFTY" in exposure.note
    # And the buckets still account for every rupee.
    assert sum(exposure.sectors.values()) == pytest.approx(1.0)


def test_sector_exposure_of_an_all_index_portfolio_is_entirely_unclassified():
    exposure = sector_exposure({"NIFTY": 0.7, "BANKNIFTY": 0.3})
    assert exposure.sectors == {UNCLASSIFIED: pytest.approx(1.0)}
    assert exposure.classified_weight == pytest.approx(0.0)
    assert exposure.unclassified_symbols == ("BANKNIFTY", "NIFTY")


def test_unrealized_pnl_uses_the_recorded_cost_basis():
    # 100 units bought at 1,500 and now worth 2,000:
    #   cost   = 150,000
    #   value  = 200,000
    #   P&L    = 50,000, i.e. 50,000 / 150,000 = 0.3333...
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 100, cost_basis=1_500.0),))
    position = portfolio.value({"RELIANCE.NS": 2_000.0}).positions[0]

    assert position.unrealized_pnl == pytest.approx(50_000.0)
    assert position.unrealized_return == pytest.approx(1.0 / 3.0)


def test_missing_cost_basis_reports_none_rather_than_zero_pnl():
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 100, cost_basis=1_500.0), Holding("TCS.NS", 10))
    )
    allocation = portfolio.value({"RELIANCE.NS": 2_000.0, "TCS.NS": 4_000.0})

    by_symbol = {p.symbol: p for p in allocation.positions}
    assert by_symbol["TCS.NS"].unrealized_pnl is None
    assert by_symbol["TCS.NS"].unrealized_return is None
    # A partial cost basis gives no portfolio-level cost: summing only the
    # holdings that have one would understate the cost and overstate the gain.
    assert allocation.total_cost is None


def test_total_cost_is_reported_when_every_holding_has_a_cost_basis():
    # 100 x 1,500 = 150,000 plus 10 x 3,000 = 30,000 -> 180,000
    portfolio = Portfolio(
        holdings=(
            Holding("RELIANCE.NS", 100, cost_basis=1_500.0),
            Holding("TCS.NS", 10, cost_basis=3_000.0),
        )
    )
    allocation = portfolio.value({"RELIANCE.NS": 2_000.0, "TCS.NS": 4_000.0})
    assert allocation.total_cost == pytest.approx(180_000.0)


def test_from_weights_rebuilds_quantities_that_reproduce_the_weights():
    # 60% of 1,000,000 at 2,000 -> 300 units; 40% at 500 -> 800 units.
    portfolio = Portfolio.from_weights(
        {"RELIANCE.NS": 0.6, "INFY.NS": 0.4},
        {"RELIANCE.NS": 2_000.0, "INFY.NS": 500.0},
        1_000_000.0,
    )
    quantities = {h.symbol: h.quantity for h in portfolio.holdings}
    assert quantities == pytest.approx({"RELIANCE.NS": 300.0, "INFY.NS": 800.0})

    allocation = portfolio.value({"RELIANCE.NS": 2_000.0, "INFY.NS": 500.0})
    assert allocation.weights == pytest.approx({"RELIANCE.NS": 0.6, "INFY.NS": 0.4})


def test_from_weights_drops_zeroed_positions():
    portfolio = Portfolio.from_weights(
        {"RELIANCE.NS": 1.0, "INFY.NS": 0.0},
        {"RELIANCE.NS": 2_000.0, "INFY.NS": 500.0},
        1_000_000.0,
    )
    assert portfolio.symbols == ("RELIANCE.NS",)


@pytest.mark.parametrize(
    ("quantity", "fragment"),
    [
        (0, "must be positive"),
        (-5, "Short positions are not modelled"),
        (float("nan"), "must be a finite number"),
    ],
)
def test_invalid_quantities_are_rejected_with_a_specific_message(quantity, fragment):
    with pytest.raises(InvalidRequestError) as excinfo:
        Holding("RELIANCE.NS", quantity)
    assert fragment in str(excinfo.value)


def test_unknown_symbol_is_rejected_when_the_holding_is_created():
    # Reported while the user is still looking at the portfolio, rather than
    # several seconds later as a data-fetch failure.
    with pytest.raises(UnknownSymbolError):
        Holding("NOTREAL.NS", 10)


def test_duplicate_symbols_are_rejected_rather_than_summed():
    with pytest.raises(InvalidRequestError) as excinfo:
        Portfolio(holdings=(Holding("TCS.NS", 10), Holding("TCS.NS", 5)))
    assert "TCS.NS" in str(excinfo.value)
    assert "Combine them into one row" in str(excinfo.value)


def test_empty_portfolio_is_rejected():
    with pytest.raises(InvalidRequestError):
        Portfolio(holdings=())


def test_a_missing_price_fails_rather_than_valuing_a_subset():
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10), Holding("TCS.NS", 5)))
    with pytest.raises(InvalidRequestError) as excinfo:
        portfolio.value({"RELIANCE.NS": 2_000.0})
    assert "TCS.NS" in str(excinfo.value)


@pytest.mark.parametrize("price", [0.0, -1.0, math.inf])
def test_non_positive_prices_are_rejected(price):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),))
    with pytest.raises(InvalidRequestError):
        portfolio.value({"RELIANCE.NS": price})


def test_from_dicts_reads_the_wire_shape():
    portfolio = Portfolio.from_dicts(
        [
            {"symbol": "RELIANCE.NS", "quantity": 10, "cost_basis": 2400},
            {"symbol": "TCS.NS", "quantity": 5.5},
        ],
        name="Core",
    )
    assert portfolio.symbols == ("RELIANCE.NS", "TCS.NS")
    assert portfolio.holdings[0].cost_basis == pytest.approx(2400.0)
    assert portfolio.holdings[1].cost_basis is None
    assert portfolio.name == "Core"


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ([{"quantity": 10}], "missing a symbol"),
        ([{"symbol": "TCS.NS"}], "missing a quantity"),
    ],
)
def test_from_dicts_rejects_incomplete_entries(payload, fragment):
    with pytest.raises(InvalidRequestError) as excinfo:
        Portfolio.from_dicts(payload)
    assert fragment in str(excinfo.value)

"""One entry point must run every registered strategy family.

M3's Definition of Done is that all four families run through the *same*
backtester. Pair trading needs a second instrument, and before
`auxiliary_symbols()` existed "run any registered strategy by name" worked for
three families and raised for the fourth — a gap no per-strategy test could
catch, because each family's own tests construct it the way it happens to need.
"""

from __future__ import annotations

import numpy as np
import pytest

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.runner import resolve_auxiliary_series, run_backtest_for_symbol
from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import MarketDataResult
from services.quant.data.validation import validate_prices
from services.quant.strategies import available, create
from services.quant.testing import make_price_series

START, END = "2024-01-01", "2025-06-30"


def fixture_result(symbol: str, seed: int) -> MarketDataResult:
    """A deterministic MarketDataResult, shaped exactly like a real fetch."""
    rng = np.random.default_rng(seed)
    closes = list(100.0 + np.cumsum(rng.normal(0.05, 1.2, 380)))
    series = make_price_series(closes, symbol=symbol, start=START)
    quality = validate_prices(series.frame, symbol=symbol, interval="1d")
    return MarketDataResult(series=series, quality=quality)


@pytest.fixture
def offline_prices(monkeypatch: pytest.MonkeyPatch):
    """Serve every get_prices call from fixtures, including auxiliary legs.

    The runner fetches auxiliary instruments itself, so a `market_data` argument
    alone would not keep the second leg offline.
    """
    seeds = {"RELIANCE.NS": 1, "TCS.NS": 2, "INFY.NS": 3}
    calls: list[str] = []

    def fake_get_prices(symbol: str, *_args: object, **_kwargs: object) -> MarketDataResult:
        calls.append(symbol)
        return fixture_result(symbol, seeds.get(symbol, 9))

    monkeypatch.setattr("services.quant.backtest.runner.get_prices", fake_get_prices)
    return calls


# The parameters each family needs beyond its defaults. Only pair trading has
# any, which is the point: everything else runs on defaults.
FAMILY_PARAMETERS: dict[str, dict[str, object] | None] = {
    "macd": None,
    "bollinger": None,
    "dual_thrust": None,
    "pair_trading": {"pair_symbol": "TCS.NS", "lookback": 30},
}


def test_every_registered_strategy_is_covered_by_this_test() -> None:
    """A new strategy family must not silently escape the DoD check."""
    assert set(available()) == set(FAMILY_PARAMETERS), (
        "a strategy family was registered without being added to this test; "
        "M3's Definition of Done covers every family, not the ones we remembered"
    )


@pytest.mark.parametrize("strategy_name", sorted(FAMILY_PARAMETERS))
def test_each_family_runs_through_the_shared_backtester(
    strategy_name: str, offline_prices: list[str]
) -> None:
    run = run_backtest_for_symbol(
        "RELIANCE.NS",
        strategy_name,
        START,
        END,
        parameters=FAMILY_PARAMETERS[strategy_name],
        config=BacktestConfig(allow_short=True),
    )

    assert run.strategy.name == strategy_name
    assert len(run.equity_curve) == len(run.series)
    # Every family must produce the full metric set, even where individual
    # metrics are legitimately unavailable.
    assert run.metrics.final_equity > 0
    assert run.simulation.portfolio is not None


def test_pair_trading_second_leg_is_fetched_and_injected(offline_prices: list[str]) -> None:
    run = run_backtest_for_symbol(
        "RELIANCE.NS",
        "pair_trading",
        START,
        END,
        parameters={"pair_symbol": "INFY.NS", "lookback": 30},
    )

    assert "INFY.NS" in offline_prices, "the declared second leg was never fetched"
    assert run.signals.parameters["pair_symbol"] == "INFY.NS"


def test_auxiliary_legs_are_cached_across_runs(offline_prices: list[str]) -> None:
    """Comparing strategies must not re-download a shared leg.

    Beyond wasted requests, two fetches of the same range can differ — yfinance
    revises history — which would make a comparison between them meaningless.
    """
    cache: dict[str, MarketDataResult] = {}
    for _ in range(3):
        run_backtest_for_symbol(
            "RELIANCE.NS",
            "pair_trading",
            START,
            END,
            parameters={"pair_symbol": "TCS.NS", "lookback": 30},
            auxiliary_cache=cache,
        )

    assert offline_prices.count("TCS.NS") == 1
    assert set(cache) == {"TCS.NS"}


def test_pairing_an_instrument_with_itself_is_refused(offline_prices: list[str]) -> None:
    # A spread against itself is identically zero, so there is nothing to trade.
    with pytest.raises(InvalidRequestError, match="both the primary instrument and its second leg"):
        run_backtest_for_symbol(
            "TCS.NS",
            "pair_trading",
            START,
            END,
            parameters={"pair_symbol": "TCS.NS"},
        )


def test_strategies_without_auxiliary_symbols_fetch_nothing_extra(
    offline_prices: list[str],
) -> None:
    run_backtest_for_symbol("RELIANCE.NS", "macd", START, END)

    assert offline_prices == ["RELIANCE.NS"]


def test_attaching_a_series_to_a_strategy_that_takes_none_raises() -> None:
    # Silently discarding it would let a caller believe data was used that
    # never was.
    strategy = create("macd")

    with pytest.raises(NotImplementedError, match="does not take auxiliary series"):
        strategy.attach_auxiliary_series(make_price_series([1.0, 2.0], symbol="TCS.NS"))


def test_resolve_is_a_no_op_for_single_instrument_strategies(
    offline_prices: list[str],
) -> None:
    resolve_auxiliary_series(create("bollinger"), START, END, "1d", primary_symbol="RELIANCE.NS")

    assert offline_prices == []


def test_describe_advertises_auxiliary_requirements() -> None:
    """The UI and the AI layer need to know before offering a strategy."""
    assert create("macd").describe()["auxiliary_symbols"] == []
    assert create("pair_trading", {"pair_symbol": "TCS.NS"}).describe()["auxiliary_symbols"] == [
        "TCS.NS"
    ]


@pytest.mark.network
def test_all_four_families_run_against_the_real_provider() -> None:
    """The offline tests prove the wiring; this proves the real path works."""
    cache: dict[str, MarketDataResult] = {}
    for strategy_name, parameters in FAMILY_PARAMETERS.items():
        run = run_backtest_for_symbol(
            "RELIANCE.NS",
            strategy_name,
            "2022-01-01",
            "2024-12-31",
            parameters=parameters,
            auxiliary_cache=cache,
            config=BacktestConfig(allow_short=True),
        )
        assert run.metrics.final_equity > 0, f"{strategy_name} produced no equity curve"

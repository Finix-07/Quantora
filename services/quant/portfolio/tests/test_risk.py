"""Portfolio risk mathematics (testing.md §2.1, portfolio-mathematics).

Each expected value is derived in a comment above the assertion from a price
path chosen so the arithmetic can be done by hand. Where a number is irrational
the derivation is written as the expression it comes from, not copied from a
previous run of the code under test.
"""

from __future__ import annotations

import json
import math

import pytest

from services.quant.data.errors import InvalidRequestError
from services.quant.portfolio.holdings import Holding, Portfolio
from services.quant.portfolio.risk import (
    MIN_RETURN_OBSERVATIONS,
    analyze_portfolio_risk,
    load_window,
)
from services.quant.portfolio.tests.conftest import alternating, compound

# 20 returns is the minimum this engine will report a statistic from, so a
# 21-bar path is the shortest honest fixture.
BARS = MIN_RETURN_OBSERVATIONS + 1
RETURNS = MIN_RETURN_OBSERVATIONS

#: A benchmark that actually moves, used wherever beta must be defined.
BENCHMARK_RETURNS = alternating(RETURNS, 0.02, -0.01)
BENCHMARK_PATH = compound(100.0, BENCHMARK_RETURNS)


def analyze(portfolio, paths, price_loader, **kwargs):
    return analyze_portfolio_risk(
        portfolio, "2024-01-01", "2024-12-31", loader=price_loader(paths), **kwargs
    )


# --- beta ---------------------------------------------------------------------


def test_beta_of_the_benchmark_against_itself_is_exactly_one(price_loader):
    # beta = cov(r, r) / var(r) = var(r) / var(r) = 1, for any series with
    # non-zero variance. A portfolio that *is* the benchmark has no other
    # defensible beta.
    portfolio = Portfolio(holdings=(Holding("NIFTY", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH}, price_loader)

    assert report["metrics"]["beta"] == pytest.approx(1.0)
    assert "beta" not in report["metrics"]["unavailable"]


def test_beta_of_a_series_built_as_twice_the_benchmark_is_exactly_two(price_loader):
    # The holding's returns are constructed as exactly 2 x the benchmark's, so
    #   beta = cov(2r, r) / var(r) = 2 var(r) / var(r) = 2.
    doubled = [2.0 * r for r in BENCHMARK_RETURNS]
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio,
        {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": compound(500.0, doubled)},
        price_loader,
    )

    assert report["metrics"]["beta"] == pytest.approx(2.0)


def test_beta_is_unavailable_when_the_benchmark_never_moves(price_loader):
    # var(benchmark) = 0, so cov/var has no denominator. 0.0 would read as
    # "this portfolio is uncorrelated with the market", which is a claim the
    # data cannot support.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio,
        {
            "NIFTY": [100.0] * BARS,
            "RELIANCE.NS": compound(500.0, BENCHMARK_RETURNS),
        },
        price_loader,
    )

    assert report["metrics"]["beta"] is None
    reason = report["metrics"]["unavailable"]["beta"]
    assert "zero variance" in reason
    assert "denominator" in reason


def test_beta_is_unavailable_when_the_benchmark_barely_overlaps(price_loader):
    # The benchmark has 10 bars, so 9 return observations overlap the
    # portfolio's 20. The reason must say *that*, not "zero variance": the two
    # failures have different fixes.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio,
        {
            "NIFTY": BENCHMARK_PATH[:10],
            "RELIANCE.NS": compound(500.0, BENCHMARK_RETURNS),
        },
        price_loader,
    )

    assert report["metrics"]["beta"] is None
    reason = report["metrics"]["unavailable"]["beta"]
    assert "common return observations" in reason
    assert "zero variance" not in reason


# --- correlation --------------------------------------------------------------


def test_correlation_is_plus_one_for_holdings_that_move_identically(price_loader):
    # Two instruments with the same return series at different price levels are
    # perfectly collinear: corr = cov(r, r) / (sd(r) sd(r)) = +1.
    path_a = compound(500.0, BENCHMARK_RETURNS)
    path_b = compound(80.0, BENCHMARK_RETURNS)
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": path_a, "INFY.NS": path_b},
        price_loader,
    )

    matrix = report["correlation_matrix"]["matrix"]
    assert matrix["RELIANCE.NS"]["INFY.NS"] == pytest.approx(1.0)
    assert matrix["RELIANCE.NS"]["RELIANCE.NS"] == pytest.approx(1.0)


def test_correlation_is_minus_one_for_mirrored_holdings(price_loader):
    # The second holding's returns are the exact negation of the first's:
    #   cov(r, -r) = -var(r), sd(-r) = sd(r)  ->  corr = -1.
    mirrored = [-r for r in BENCHMARK_RETURNS]
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {
            "NIFTY": BENCHMARK_PATH,
            "RELIANCE.NS": compound(500.0, BENCHMARK_RETURNS),
            "INFY.NS": compound(80.0, mirrored),
        },
        price_loader,
    )

    assert report["correlation_matrix"]["matrix"]["RELIANCE.NS"]["INFY.NS"] == pytest.approx(-1.0)


def test_correlation_with_a_flat_holding_is_undefined_not_zero(price_loader):
    # A holding whose price never changes has no direction to co-move in.
    # Reporting 0.0 would assert the two are unrelated, which is a stronger
    # statement than "we cannot tell".
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {
            "NIFTY": BENCHMARK_PATH,
            "RELIANCE.NS": compound(500.0, BENCHMARK_RETURNS),
            "INFY.NS": [80.0] * BARS,
        },
        price_loader,
    )

    correlation = report["correlation_matrix"]
    assert correlation["matrix"]["RELIANCE.NS"]["INFY.NS"] is None
    assert "INFY.NS" in correlation["unavailable"]["RELIANCE.NS|INFY.NS"]
    # Even the diagonal is undefined for a flat series: it has no variance to
    # correlate with itself.
    assert correlation["matrix"]["INFY.NS"]["INFY.NS"] is None


def test_correlation_matrix_is_symmetric(price_loader):
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {
            "NIFTY": BENCHMARK_PATH,
            "RELIANCE.NS": compound(500.0, alternating(RETURNS, 0.03, -0.02)),
            "INFY.NS": compound(80.0, alternating(RETURNS, -0.01, 0.04)),
        },
        price_loader,
    )
    matrix = report["correlation_matrix"]["matrix"]
    assert matrix["RELIANCE.NS"]["INFY.NS"] == pytest.approx(matrix["INFY.NS"]["RELIANCE.NS"])


# --- volatility, ratios, drawdown ---------------------------------------------

#: 20 returns alternating +10% and -5%, starting with +10%.
VOL_RETURNS = alternating(RETURNS, 0.10, -0.05)
VOL_PATH = compound(100.0, VOL_RETURNS)


def test_volatility_and_sharpe_match_the_hand_computed_values(price_loader):
    # The single holding carries 100% of the portfolio, so the portfolio return
    # series *is* the 20-return series +10%, -5%, +10%, -5%, ...
    #
    #   mean   = (10 x 0.10 + 10 x -0.05) / 20 = 0.5 / 20 = 0.025
    #   each deviation from the mean is +/- 0.075
    #   sum of squared deviations = 20 x 0.075^2 = 0.1125
    #   sample variance (ddof=1)  = 0.1125 / 19 = 0.005921052631578947
    #   sample sd                 = 0.07694837640638655
    #   annualised volatility     = sd x sqrt(252) = 1.221517606568933
    #   Sharpe (rf = 0)           = mean / sd x sqrt(252) = 5.157518783291052
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}, price_loader)
    metrics = report["metrics"]

    expected_sd = math.sqrt(20 * 0.075**2 / 19)
    assert metrics["annualized_volatility"] == pytest.approx(expected_sd * math.sqrt(252))
    assert metrics["annualized_volatility"] == pytest.approx(1.221517606568933)
    assert metrics["sharpe"] == pytest.approx(0.025 / expected_sd * math.sqrt(252))
    assert metrics["sharpe"] == pytest.approx(5.157518783291052)


def test_sortino_uses_downside_deviation_over_the_whole_series(price_loader):
    # Downside deviation squares min(r, 0) over *all* 20 observations, not only
    # the 10 negative ones:
    #   sqrt(10 x 0.05^2 / 20) = sqrt(0.025 / 20) = 0.035355339059327376
    #   Sortino = 0.025 / 0.035355339059327376 x sqrt(252) = 11.224972160321824
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}, price_loader)

    assert report["metrics"]["sortino"] == pytest.approx(11.224972160321824)


def test_total_and_annualized_return_on_a_known_path(price_loader):
    # (1.10 x 0.95)^10 = 1.045^10 = 1.5529694217328978 -> total return 55.30%.
    # The window is 20 daily bars, i.e. 20/252 of a year, so
    #   CAGR = 1.5529694217328978^(252/20) - 1 = 255.24334580201747.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}, price_loader)
    metrics = report["metrics"]

    assert metrics["total_return"] == pytest.approx(1.045**10 - 1.0)
    assert metrics["total_return"] == pytest.approx(0.5529694217328978)
    assert metrics["annualized_return"] == pytest.approx(255.24334580201747)


def test_max_drawdown_and_duration_on_a_known_path(price_loader):
    # Closes: 100, 125, then four bars at 100, then 150 for the rest.
    #   running peak reaches 125 at bar 1
    #   trough of 100 gives 100/125 - 1 = -0.20
    #   the portfolio is under water for exactly 4 consecutive bars (2..5)
    #   before 150 sets a new peak at bar 6.
    closes = [100.0, 125.0, 100.0, 100.0, 100.0, 100.0] + [150.0] * (BARS - 6)
    assert len(closes) == BARS
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": closes}, price_loader)

    assert report["metrics"]["max_drawdown"] == pytest.approx(-0.20)
    assert report["metrics"]["max_drawdown_duration_bars"] == 4


def test_a_two_holding_portfolio_return_is_the_weighted_sum_of_its_holdings(price_loader):
    # 100 units at 200 and 400 units at 50 are both worth 20,000 at the as-of
    # bar only if the paths end there; instead the weights are read off the last
    # bar and applied to every bar (the static-weight assumption). With holding
    # A returning +10%/-5% and holding B returning 0% throughout, and weights
    # w_A and w_B, the portfolio return at each bar is exactly w_A x r_A.
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 100), Holding("INFY.NS", 400)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH, "INFY.NS": [50.0] * BARS},
        price_loader,
    )

    weights = report["portfolio"]["weights"]
    first_return = report["return_series"][0]["return"]
    assert first_return == pytest.approx(weights["RELIANCE.NS"] * 0.10)
    # Volatility scales linearly with the weight for the same reason.
    expected_sd = math.sqrt(20 * 0.075**2 / 19)
    assert report["metrics"]["annualized_volatility"] == pytest.approx(
        weights["RELIANCE.NS"] * expected_sd * math.sqrt(252)
    )


# --- unavailability discipline ------------------------------------------------


def test_a_flat_portfolio_reports_undefined_ratios_with_reasons(price_loader):
    # Zero variance: Sharpe and Sortino are 0/0, not 0.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": [500.0] * BARS}, price_loader
    )
    metrics = report["metrics"]

    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
    assert "no variance" in metrics["unavailable"]["sharpe"]
    assert "downside deviation" in metrics["unavailable"]["sortino"]


def test_every_unavailable_metric_carries_a_reason(price_loader):
    # The contract the whole engine rests on: a None is always explained.
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio, {"NIFTY": [100.0] * BARS, "RELIANCE.NS": [500.0] * BARS}, price_loader
    )
    metrics = report["metrics"]

    unexplained = [
        name
        for name, value in metrics.items()
        if name != "unavailable" and value is None and name not in metrics["unavailable"]
    ]
    assert unexplained == []
    # And nothing was quietly filled in with a zero or an infinity instead.
    numeric = [v for k, v in metrics.items() if k != "unavailable" and isinstance(v, float)]
    assert all(math.isfinite(v) for v in numeric)


def test_reasons_are_sentences_a_user_can_act_on(price_loader):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(
        portfolio, {"NIFTY": [100.0] * BARS, "RELIANCE.NS": [500.0] * BARS}, price_loader
    )
    for reason in report["metrics"]["unavailable"].values():
        assert len(reason) > 25
        assert reason[0].isupper()


# --- alignment ----------------------------------------------------------------


def test_a_symbol_with_too_little_history_is_reported_not_silently_dropped(price_loader):
    # INFY has 10 bars against the others' 21, i.e. 48% of the pooled calendar.
    # Keeping it would shorten the window for every other holding, so it is
    # excluded — and the exclusion is named in the response and the assumptions.
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    report = analyze(
        portfolio,
        {
            "NIFTY": BENCHMARK_PATH,
            "RELIANCE.NS": compound(500.0, BENCHMARK_RETURNS),
            "INFY.NS": compound(80.0, BENCHMARK_RETURNS)[:10],
        },
        price_loader,
    )

    dropped = {d["symbol"] for d in report["dropped_symbols"]}
    assert dropped == {"INFY.NS"}
    assert "INFY.NS" not in report["portfolio"]["weights"]
    # The surviving holding carries the whole portfolio, so the weights the
    # report shows are the weights it actually used.
    assert report["portfolio"]["weights"]["RELIANCE.NS"] == pytest.approx(1.0)
    assert any("INFY.NS" in line for line in report["assumptions"])


def test_a_window_with_too_few_shared_sessions_fails_loudly(price_loader):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    with pytest.raises(InvalidRequestError) as excinfo:
        analyze(
            portfolio,
            {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH[:5]},
            price_loader,
        )
    assert "return observations" in str(excinfo.value)


def test_holdings_are_aligned_on_an_inner_join_not_forward_filled(price_loader):
    # RELIANCE trades on 25 sessions, INFY on 22 of the same calendar. The
    # analysis must run on the 22 they share; a forward fill would contribute
    # invented zero returns for the three missing days and understate risk.
    long_path = compound(500.0, alternating(24, 0.02, -0.01))
    short_path = compound(80.0, alternating(21, 0.02, -0.01))
    window = load_window(
        ["RELIANCE.NS", "INFY.NS"],
        "2024-01-01",
        "2024-12-31",
        benchmark="NIFTY",
        loader=price_loader(
            {
                "NIFTY": BENCHMARK_PATH,
                "RELIANCE.NS": long_path,
                "INFY.NS": short_path,
            }
        ),
    )
    assert len(window.closes) == 22
    assert len(window.returns) == 21
    assert window.dropped == ()


# --- reporting contract -------------------------------------------------------


def test_the_static_weight_assumption_is_stated(price_loader):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}, price_loader)

    joined = " ".join(report["assumptions"])
    assert "held constant" in joined
    assert "No rebalancing" in joined
    # And that returns, not price levels, are what everything is computed on.
    assert "never on price levels" in joined


def test_data_versions_are_reported_for_every_fetched_symbol(price_loader):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    report = analyze(portfolio, {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}, price_loader)

    assert set(report["data"]["data_versions"]) == {"RELIANCE.NS", "NIFTY"}
    assert all(v.startswith("sha256:") for v in report["data"]["data_versions"].values())


def test_the_report_is_deterministic(price_loader):
    portfolio = Portfolio(
        holdings=(Holding("RELIANCE.NS", 10), Holding("INFY.NS", 50)), benchmark="NIFTY"
    )
    paths = {
        "NIFTY": BENCHMARK_PATH,
        "RELIANCE.NS": VOL_PATH,
        "INFY.NS": compound(80.0, alternating(RETURNS, -0.01, 0.04)),
    }
    first = analyze(portfolio, paths, price_loader)
    second = analyze(portfolio, paths, price_loader)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_risk_free_rate_moves_sharpe_in_the_expected_direction(price_loader):
    portfolio = Portfolio(holdings=(Holding("RELIANCE.NS", 10),), benchmark="NIFTY")
    paths = {"NIFTY": BENCHMARK_PATH, "RELIANCE.NS": VOL_PATH}
    zero = analyze(portfolio, paths, price_loader)
    seven = analyze(portfolio, paths, price_loader, risk_free_rate=0.07)

    assert seven["metrics"]["sharpe"] < zero["metrics"]["sharpe"]
    assert any("7.00%" in line for line in seven["assumptions"])

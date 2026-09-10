"""Portfolio risk metrics over a date range (requirements.md FR7).

The shape of this module follows one rule that the rest of the engine already
follows: **every metric may be unavailable, and when it is, the reason is
reported in plain language instead of a fabricated number** (see
`services/quant/backtest/metrics.py`). A beta of 0.0 printed where beta is
undefined is worse than no beta at all, because the user cannot tell the two
apart.

Two further decisions are worth stating up front, because they change what the
numbers mean:

* Everything statistical is computed on **returns**, never on price levels. Two
  instruments that both drifted upwards correlate near +1 whatever they are —
  that is the spurious-regression trap `indicators/correlation.py` already warns
  about — so a "correlation matrix" built from closes would say every portfolio
  in the universe is perfectly diversified or perfectly concentrated depending
  only on the decade.

* Weights are **static**, fixed at their as-of-date values for the whole return
  series. The portfolio return series is `Σ wᵢ·rᵢ,ₜ` with the same `w` at every
  `t`. This is stated in every response's `assumptions`, because the alternative
  — letting the weights drift with prices, or rebalancing them periodically — is
  a materially different portfolio and modelling one silently would attribute
  its risk to the holdings the user actually entered.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from services.quant.backtest import metrics as metrics_module
from services.quant.backtest.config import PERIODS_PER_YEAR, TRADING_DAYS_PER_YEAR
from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import MarketDataResult, get_prices
from services.quant.indicators.correlation import hedge_ratio, rolling_correlation
from services.quant.logging_setup import stage
from services.quant.portfolio.holdings import Allocation, Portfolio

log = logging.getLogger(__name__)

#: Minimum number of *return* observations (one fewer than bars) a symbol needs
#: before any statistic computed from it is reported. Below this a correlation
#: or a beta is dominated by whichever handful of days happened to be in the
#: window; reporting one would dress noise up as a measurement.
MIN_RETURN_OBSERVATIONS = 20

#: Minimum share of the window's pooled trading calendar a symbol must cover to
#: stay in the portfolio. A symbol that traded on a fifth of the sessions would
#: otherwise shrink the common calendar for *every* holding, so the whole
#: analysis would quietly be computed over a much shorter period than requested.
MIN_CALENDAR_COVERAGE = 0.8

#: The default benchmark for beta. NIFTY 50 is the broad-market index of the
#: MVP universe's exchange, so "beta" means what an Indian-equity investor
#: expects it to mean.
DEFAULT_BENCHMARK = "NIFTY"

EPSILON = 1e-12

PriceLoader = Callable[..., MarketDataResult]

#: Metrics compared before/after in a scenario, in display order, with the
#: direction that counts as an improvement. `None` means "neither direction is
#: better" — a higher beta is not good or bad, it is a different exposure.
RISK_METRICS: tuple[tuple[str, str, str | None], ...] = (
    ("total_return", "Total return", "higher"),
    ("annualized_return", "Annualized return", "higher"),
    ("annualized_volatility", "Annualized volatility", "lower"),
    ("beta", "Beta vs benchmark", None),
    ("sharpe", "Sharpe", "higher"),
    ("sortino", "Sortino", "higher"),
    ("max_drawdown", "Max drawdown", "higher"),  # negative: closer to zero wins
    ("largest_weight", "Largest position weight", "lower"),
    ("top_n_weight", "Top-3 weight", "lower"),
    ("hhi", "Concentration (HHI)", "lower"),
    ("effective_holdings", "Effective holdings", "higher"),
)


@dataclass(frozen=True, slots=True)
class MarketWindow:
    """Aligned closes and returns for a set of symbols, plus their provenance.

    Built once and reused: a scenario compares two weightings of the *same*
    bars, and refetching between the two would let a provider revision show up
    as a risk difference the user attributed to their reweighting.
    """

    closes: pd.DataFrame
    returns: pd.DataFrame
    interval: str
    start: str
    end: str
    benchmark_symbol: str
    benchmark_returns: pd.Series | None = None
    benchmark_unavailable: str | None = None
    dropped: tuple[dict[str, str], ...] = ()
    data_versions: dict[str, str] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.closes.columns)

    @property
    def as_of(self) -> pd.Timestamp:
        return self.closes.index[-1]

    def prices_as_of(self) -> dict[str, float]:
        return {symbol: float(self.closes.iloc[-1][symbol]) for symbol in self.closes.columns}

    def periods_per_year(self) -> float:
        return PERIODS_PER_YEAR.get(self.interval, float(TRADING_DAYS_PER_YEAR))


def load_window(
    symbols: Sequence[str],
    start: str,
    end: str,
    *,
    interval: str = "1d",
    benchmark: str = DEFAULT_BENCHMARK,
    loader: PriceLoader = get_prices,
) -> MarketWindow:
    """Fetch validated bars for every symbol and align them on common sessions.

    `get_prices` is the only sanctioned way to obtain bars (architecture.md
    §4.2), so a portfolio is never analysed against data that skipped validation.

    Alignment is an explicit inner join on timestamps rather than a pandas
    outer-join-with-forward-fill. Forward filling a missing session invents a
    zero return for that day, which lowers measured volatility and pulls every
    correlation towards the symbols that did trade.
    """
    wanted = list(dict.fromkeys([*symbols, benchmark]))
    frames: dict[str, pd.Series] = {}
    data_versions: dict[str, str] = {}
    data_quality: dict[str, Any] = {}

    with stage(log, "data_retrieval", symbols=",".join(wanted), interval=interval):
        for symbol in wanted:
            result = loader(symbol, start, end, interval)
            frames[symbol] = result.series.close.rename(symbol)
            data_versions[symbol] = result.data_version
            data_quality[symbol] = result.quality.as_dict()

    holding_symbols = [s for s in symbols if s in frames]
    dropped: list[dict[str, str]] = []

    # The pooled calendar is every session on which *any* holding traded. A
    # symbol is measured against it rather than against the intersection, so the
    # report can name the one instrument responsible for a short window instead
    # of reporting that "the window is short".
    pooled = frames[holding_symbols[0]].index
    for symbol in holding_symbols[1:]:
        pooled = pooled.union(frames[symbol].index)

    kept: list[str] = []
    for symbol in holding_symbols:
        bars = len(frames[symbol])
        coverage = bars / len(pooled) if len(pooled) else 0.0
        if bars - 1 < MIN_RETURN_OBSERVATIONS:
            dropped.append(
                {
                    "symbol": symbol,
                    "reason": (
                        f"Only {bars} bars in {start}..{end}, which gives {max(bars - 1, 0)} return "
                        f"observations; at least {MIN_RETURN_OBSERVATIONS} are needed before any "
                        "statistic computed from them is worth reporting."
                    ),
                }
            )
            continue
        if coverage < MIN_CALENDAR_COVERAGE:
            dropped.append(
                {
                    "symbol": symbol,
                    "reason": (
                        f"Traded on {bars} of the {len(pooled)} sessions in this window "
                        f"({coverage:.0%}); at least {MIN_CALENDAR_COVERAGE:.0%} overlap is "
                        "required. Keeping it would shorten the common window for every other "
                        "holding."
                    ),
                }
            )
            continue
        kept.append(symbol)

    if not kept:
        raise InvalidRequestError(
            "No holding has enough usable history in "
            f"{start}..{end}: {'; '.join(d['reason'] for d in dropped)}"
        )

    closes = pd.concat([frames[s] for s in kept], axis=1, join="inner").dropna()
    if len(closes) - 1 < MIN_RETURN_OBSERVATIONS:
        raise InvalidRequestError(
            f"The holdings share only {len(closes)} common trading sessions in {start}..{end}, "
            f"which gives fewer than the {MIN_RETURN_OBSERVATIONS} return observations needed. "
            "Widen the date range."
        )

    returns = closes.pct_change().dropna()

    benchmark_returns: pd.Series | None = None
    benchmark_unavailable: str | None = None
    benchmark_closes = frames.get(benchmark)
    if benchmark_closes is None:
        benchmark_unavailable = f"No data was fetched for the benchmark {benchmark}."
    else:
        benchmark_returns = benchmark_closes.pct_change().dropna()
        overlap = returns.index.intersection(benchmark_returns.index)
        if len(overlap) < MIN_RETURN_OBSERVATIONS:
            benchmark_unavailable = (
                f"The portfolio and {benchmark} share only {len(overlap)} common return "
                f"observations in {start}..{end}; at least {MIN_RETURN_OBSERVATIONS} are needed. "
                "Beta from that few days measures the sample, not the exposure."
            )
            benchmark_returns = None

    return MarketWindow(
        closes=closes,
        returns=returns,
        interval=interval,
        start=start,
        end=end,
        benchmark_symbol=benchmark,
        benchmark_returns=benchmark_returns,
        benchmark_unavailable=benchmark_unavailable,
        dropped=tuple(dropped),
        data_versions=data_versions,
        data_quality=data_quality,
    )


def portfolio_return_series(returns: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """`Σ wᵢ·rᵢ,ₜ` — the return of a portfolio whose weights never change.

    The weights are the as-of-date weights held constant across the window (see
    the module docstring). Any symbol in ``returns`` without a weight is treated
    as absent rather than as zero-weighted by accident: a missing key here would
    otherwise silently drop a position from the risk calculation while leaving
    it in the allocation table.
    """
    missing = sorted(set(returns.columns) - set(weights))
    if missing:
        raise InvalidRequestError(
            f"No weight supplied for {', '.join(missing)}, so the portfolio return series "
            "would silently exclude them."
        )
    ordered = list(returns.columns)
    weight_vector = pd.Series([float(weights[s]) for s in ordered], index=ordered)
    return (returns[ordered] * weight_vector).sum(axis=1).rename("portfolio_return")


def _equity_index(returns: pd.Series) -> pd.Series:
    """A wealth index normalised to 1.0 the bar before the first return.

    Drawdown and CAGR are computed from a level series, and the level series is
    normalised rather than scaled to the portfolio's rupee value because the
    as-of value describes the *end* of the window, not its start. Both metrics
    are scale-invariant, so nothing is lost and nothing is implied about what
    the portfolio was worth on day one.
    """
    index = pd.Index([returns.index[0] - _one_step(returns.index)]).append(returns.index)
    values = np.concatenate([[1.0], (1.0 + returns.to_numpy()).cumprod()])
    return pd.Series(values, index=index, name="equity_index")


def _one_step(index: pd.Index) -> pd.Timedelta:
    """The spacing used to place the synthetic starting point of the index."""
    if len(index) < 2:
        return pd.Timedelta(days=1)
    return index[1] - index[0]


def beta(
    portfolio_returns: pd.Series, benchmark_returns: pd.Series
) -> tuple[float | None, str | None]:
    """Beta of the portfolio against the benchmark: `cov(rₚ, r_b) / var(r_b)`.

    Computed with :func:`~services.quant.indicators.correlation.hedge_ratio`,
    which is the same rolling OLS slope the pair-trading strategy uses — one
    definition of "how many units of b move with one unit of a", not two.

    Returns ``(value, reason_if_unavailable)``. Beta is undefined in two
    distinct situations and the caller is told which:

    * the benchmark's returns have **zero variance** over the window, so the
      slope has no denominator (an index that did not move gives no information
      about what moves with it);
    * the two series **do not overlap** on enough sessions to estimate it.

    Both would produce a number if forced; neither number would mean anything.
    """
    aligned = pd.concat(
        [portfolio_returns.rename("portfolio"), benchmark_returns.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) < MIN_RETURN_OBSERVATIONS:
        return None, (
            f"The portfolio and the benchmark overlap on only {len(aligned)} sessions; "
            f"at least {MIN_RETURN_OBSERVATIONS} return observations are needed."
        )
    if float(aligned["benchmark"].var(ddof=0)) <= EPSILON:
        return None, (
            "The benchmark's returns have zero variance over this window, so beta "
            "(covariance divided by the benchmark's variance) has no denominator."
        )

    window = len(aligned)
    slope = hedge_ratio(aligned["portfolio"], aligned["benchmark"], window=window).iloc[-1]
    if slope is None or not np.isfinite(slope):
        return None, "The regression of the portfolio on the benchmark did not resolve."
    return float(slope), None


def correlation_matrix(returns: pd.DataFrame) -> dict[str, Any]:
    """Pairwise correlation of holding *returns*, with undefined pairs named.

    Uses :func:`~services.quant.indicators.correlation.rolling_correlation` over
    a window covering the whole sample, so the portfolio view and the pair-
    trading view agree on what "correlation" means, down to the ddof.

    A flat series has no direction to co-move in, so its correlation with
    anything — including itself — is undefined and is reported as ``None`` with
    a reason rather than as 0.0 ("unrelated") or 1.0 ("identical").
    """
    symbols = list(returns.columns)
    window = len(returns)
    matrix: dict[str, dict[str, float | None]] = {a: {} for a in symbols}
    unavailable: dict[str, str] = {}

    flat = {symbol: float(returns[symbol].var(ddof=0)) <= EPSILON for symbol in symbols}

    for i, a in enumerate(symbols):
        for b in symbols[i:]:
            if flat[a] or flat[b]:
                value: float | None = None
                culprits = ", ".join(sorted({s for s in (a, b) if flat[s]}))
                unavailable[f"{a}|{b}"] = (
                    f"{culprits} had no return variation over this window, so the correlation "
                    "is undefined — 0.0 would claim the two are unrelated, which the data "
                    "does not support."
                )
            elif a == b:
                value = 1.0
            elif window < MIN_RETURN_OBSERVATIONS:
                value = None
                unavailable[f"{a}|{b}"] = (
                    f"Only {window} return observations; at least "
                    f"{MIN_RETURN_OBSERVATIONS} are needed."
                )
            else:
                computed = rolling_correlation(returns[a], returns[b], window=window).iloc[-1]
                value = None if not np.isfinite(computed) else float(computed)
                if value is None:
                    unavailable[f"{a}|{b}"] = "The correlation did not resolve to a finite value."
            matrix[a][b] = value
            matrix[b][a] = value

    return {"symbols": symbols, "matrix": matrix, "unavailable": unavailable}


def analyze(
    portfolio: Portfolio,
    window: MarketWindow,
    *,
    risk_free_rate: float = 0.0,
    include_return_series: bool = True,
) -> dict[str, Any]:
    """Full risk report for one portfolio over one already-loaded window."""
    allocation = portfolio.value(window.prices_as_of())
    returns = window.returns[[s for s in window.returns.columns if s in allocation.weights]]

    with stage(log, "quant_calculation", holdings=len(portfolio.holdings)):
        report = _metrics(allocation, returns, window, risk_free_rate=risk_free_rate)

    payload: dict[str, Any] = {
        "as_of": window.as_of.isoformat(),
        "start": window.start,
        "end": window.end,
        "interval": window.interval,
        "benchmark": window.benchmark_symbol,
        "base_currency": portfolio.base_currency,
        "name": portfolio.name,
        "observations": int(len(returns)),
        "portfolio": {
            "holdings": [h.as_dict() for h in portfolio.holdings],
            **allocation.as_dict(),
        },
        "metrics": report["metrics"],
        "correlation_matrix": report["correlation_matrix"],
        "data": {
            "data_versions": window.data_versions,
            "quality": window.data_quality,
        },
        "dropped_symbols": [dict(d) for d in window.dropped],
        "assumptions": assumptions(window, risk_free_rate=risk_free_rate),
    }
    if include_return_series:
        payload["return_series"] = report["return_series"]
        payload["equity_index"] = report["equity_index"]
    return payload


def _metrics(
    allocation: Allocation,
    returns: pd.DataFrame,
    window: MarketWindow,
    *,
    risk_free_rate: float,
) -> dict[str, Any]:
    unavailable: dict[str, str] = {}

    def record(name: str, value: float | None, reason: str) -> float | None:
        if value is None:
            unavailable[name] = reason
        return value

    periods_per_year = window.periods_per_year()
    portfolio_returns = portfolio_return_series(returns, allocation.weights)
    equity = _equity_index(portfolio_returns)

    too_few = (
        f"Only {len(portfolio_returns)} return observations in this window — too few to measure."
    )
    flat = "The portfolio's return series has no variance, so the ratio is undefined (not zero)."

    annualized_return = record(
        "annualized_return",
        metrics_module.cagr(equity, periods_per_year),
        "The window is too short, or the portfolio's value reached zero, so an annualised "
        "rate would be an extrapolation rather than a measurement.",
    )
    volatility = record(
        "annualized_volatility",
        metrics_module.annualized_volatility(portfolio_returns, periods_per_year),
        too_few,
    )
    sharpe = record(
        "sharpe",
        metrics_module.sharpe(portfolio_returns, periods_per_year, risk_free_rate),
        flat if len(portfolio_returns) >= 2 else too_few,
    )
    sortino = record(
        "sortino",
        metrics_module.sortino(portfolio_returns, periods_per_year, risk_free_rate),
        "The portfolio never closed a bar below the risk-free rate in this window, so there "
        "is no downside deviation to divide by."
        if len(portfolio_returns) >= 2
        else too_few,
    )

    if window.benchmark_returns is None:
        portfolio_beta: float | None = None
        unavailable["beta"] = window.benchmark_unavailable or (
            f"No usable benchmark series for {window.benchmark_symbol}."
        )
    else:
        portfolio_beta, reason = beta(portfolio_returns, window.benchmark_returns)
        if reason is not None:
            unavailable["beta"] = reason

    concentration = allocation.concentration
    metrics: dict[str, Any] = {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "beta": portfolio_beta,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": metrics_module.max_drawdown(equity),
        "max_drawdown_duration_bars": metrics_module.max_drawdown_duration(equity),
        "total_value": allocation.total_value,
        "largest_weight": concentration.largest_weight,
        "top_n_weight": concentration.top_n_weight,
        "hhi": concentration.hhi,
        "effective_holdings": concentration.effective_holdings,
        "unavailable": unavailable,
    }

    return {
        "metrics": metrics,
        "correlation_matrix": correlation_matrix(returns),
        "return_series": [
            {"timestamp": ts.isoformat(), "return": float(value)}
            for ts, value in portfolio_returns.items()
        ],
        "equity_index": [
            {"timestamp": ts.isoformat(), "value": float(value)} for ts, value in equity.items()
        ],
    }


def assumptions(window: MarketWindow, *, risk_free_rate: float) -> list[str]:
    """The assumptions behind every number in the report (NFR4).

    The static-weight statement is first because it is the one a reader is most
    likely to assume otherwise.
    """
    stated = [
        "Weights are held constant at their as-of-date values for the whole return series "
        "(a static-weight portfolio: Σ wᵢ·rᵢ,ₜ with the same w at every t). No rebalancing, "
        "no drift, no contributions and no withdrawals are modelled.",
        "Correlation, beta, volatility and the ratios are computed on returns, never on price "
        "levels: two trending price series correlate near ±1 by construction.",
        f"Holdings are aligned on the {len(window.returns)} trading sessions they all share; "
        "missing sessions are dropped rather than forward-filled, because a filled bar would "
        "contribute an invented zero return.",
        f"Beta is measured against {window.benchmark_symbol}.",
        f"Sharpe and Sortino use a {risk_free_rate:.2%} annualised risk-free rate.",
        f"Returns are annualised using {window.periods_per_year():g} periods per year for "
        f"{window.interval} bars.",
        "Positions are valued at the close of the as-of date; no transaction costs, taxes or "
        "bid/ask spread are applied to the valuation.",
    ]
    if window.dropped:
        stated.append(
            "Excluded for insufficient history: "
            + ", ".join(f"{d['symbol']} ({d['reason']})" for d in window.dropped)
        )
    return stated


def analyze_portfolio_risk(
    portfolio: Portfolio,
    start: str,
    end: str,
    *,
    interval: str = "1d",
    benchmark: str | None = None,
    risk_free_rate: float = 0.0,
    loader: PriceLoader = get_prices,
) -> dict[str, Any]:
    """Load the window and produce the risk report. The public entry point."""
    resolved_benchmark = benchmark or portfolio.benchmark or DEFAULT_BENCHMARK
    window = load_window(
        portfolio.symbols,
        start,
        end,
        interval=interval,
        benchmark=resolved_benchmark,
        loader=loader,
    )
    kept = set(window.symbols)
    analysed = portfolio
    if kept != set(portfolio.symbols):
        # Re-derive the portfolio from the holdings that survived alignment.
        # The dropped ones stay visible in `dropped_symbols`, so the weights the
        # report shows are the weights the report actually used.
        analysed = Portfolio(
            holdings=tuple(h for h in portfolio.holdings if h.symbol in kept),
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            benchmark=resolved_benchmark,
        )
    return analyze(analysed, window, risk_free_rate=risk_free_rate)

"""Portfolio and risk engine (requirements.md FR7).

Three layers, deliberately separable:

* :mod:`~services.quant.portfolio.holdings` — what the portfolio *is*: holdings,
  market values, allocation weights, position concentration and sector exposure.
  Needs one price per holding and no history at all.
* :mod:`~services.quant.portfolio.risk` — what the portfolio *did*: return
  series, annualised return and volatility, beta, correlation, drawdown, Sharpe
  and Sortino over a date range of validated bars.
* :mod:`~services.quant.portfolio.scenario` — what the portfolio *would be*:
  reweight, recalculate, and compare before against after.

Every metric may be unavailable, and when it is, the reason travels with it.
"""

from services.quant.portfolio.holdings import (
    DEFAULT_TOP_N,
    UNCLASSIFIED,
    Allocation,
    Concentration,
    Holding,
    Portfolio,
    PositionValue,
    SectorExposure,
    concentration,
    sector_exposure,
)
from services.quant.portfolio.risk import (
    DEFAULT_BENCHMARK,
    MIN_CALENDAR_COVERAGE,
    MIN_RETURN_OBSERVATIONS,
    RISK_METRICS,
    MarketWindow,
    analyze,
    analyze_portfolio_risk,
    beta,
    correlation_matrix,
    load_window,
    portfolio_return_series,
)
from services.quant.portfolio.scenario import (
    describe_changes,
    metric_deltas,
    resolve_weights,
    run_scenario,
    validate_overrides,
)

__all__ = [
    "DEFAULT_BENCHMARK",
    "DEFAULT_TOP_N",
    "MIN_CALENDAR_COVERAGE",
    "MIN_RETURN_OBSERVATIONS",
    "RISK_METRICS",
    "UNCLASSIFIED",
    "Allocation",
    "Concentration",
    "Holding",
    "MarketWindow",
    "Portfolio",
    "PositionValue",
    "SectorExposure",
    "analyze",
    "analyze_portfolio_risk",
    "beta",
    "concentration",
    "correlation_matrix",
    "describe_changes",
    "load_window",
    "metric_deltas",
    "portfolio_return_series",
    "resolve_weights",
    "run_scenario",
    "sector_exposure",
    "validate_overrides",
]

"""Orchestrates one backtest: data -> signals -> simulation -> metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from services.quant.backtest import metrics as metrics_module
from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.engine import SimulationOutput, simulate
from services.quant.backtest.metrics import Metrics
from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import MarketDataResult, get_prices
from services.quant.data.types import PriceSeries
from services.quant.logging_setup import stage
from services.quant.strategies.base import SignalSet, Strategy
from services.quant.strategies.registry import create

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BacktestRun:
    """One completed backtest, in memory.

    Serialization to the persisted result contract lives in `result.py`, so a
    change to the wire format cannot silently change what the engine computed.
    """

    symbol: str
    strategy: Strategy
    series: PriceSeries
    signals: SignalSet
    simulation: SimulationOutput
    metrics: Metrics
    config: BacktestConfig
    data_quality: dict[str, Any] | None = None

    @property
    def equity_curve(self) -> pd.Series:
        return self.simulation.equity_curve

    @property
    def drawdown_curve(self) -> pd.Series:
        return metrics_module.drawdown_curve(self.simulation.equity_curve)

    @property
    def trades(self) -> list:
        assert self.simulation.portfolio is not None
        return self.simulation.portfolio.trades


def run_backtest(
    series: PriceSeries,
    strategy: Strategy,
    config: BacktestConfig | None = None,
    *,
    data_quality: dict[str, Any] | None = None,
) -> BacktestRun:
    """Run one strategy over one validated price series."""
    cfg = config or BacktestConfig()

    log.info(
        "backtest started",
        extra={
            "symbol": series.symbol,
            "strategy": strategy.name,
            "bars": len(series),
            "data_version": series.data_version(),
        },
    )

    with stage(log, "quant_calculation", symbol=series.symbol, strategy=strategy.name):
        signals = strategy.generate_signals(series)

    with stage(log, "execution_simulation", symbol=series.symbol, strategy=strategy.name):
        simulation = simulate(series, strategy, signals, cfg)

    with stage(log, "metrics", symbol=series.symbol, strategy=strategy.name):
        assert simulation.portfolio is not None
        computed = metrics_module.compute(
            simulation.equity_curve,
            simulation.portfolio.trades,
            periods_per_year=cfg.periods_per_year(series.interval),
            risk_free_rate=cfg.risk_free_rate,
            initial_cash=cfg.initial_cash,
            total_costs=simulation.portfolio.total_commission + simulation.portfolio.total_slippage,
            traded_notional=simulation.portfolio.total_traded_notional,
            exposure=simulation.exposure,
        )

    return BacktestRun(
        symbol=series.symbol,
        strategy=strategy,
        series=series,
        signals=signals,
        simulation=simulation,
        metrics=computed,
        config=cfg,
        data_quality=data_quality,
    )


def resolve_auxiliary_series(
    strategy: Strategy,
    start: str,
    end: str,
    interval: str,
    *,
    primary_symbol: str,
    cache: dict[str, MarketDataResult] | None = None,
) -> None:
    """Fetch and inject any extra instruments a strategy declares.

    Pair trading needs a second leg. Rather than special-casing it at every call
    site, the strategy declares what it needs via ``auxiliary_symbols()`` and
    this resolves it — so "run any registered strategy by name" works for all
    four families instead of three.

    The extra legs are fetched over the same window and interval as the primary
    series, and go through the same validated `get_prices` path, so the second
    leg is validated and provenance-stamped exactly like the first.
    """
    for symbol in strategy.auxiliary_symbols():
        if symbol == primary_symbol:
            raise InvalidRequestError(
                f"{strategy.name} was configured with {symbol!r} as both the primary "
                "instrument and its second leg. A spread against itself is identically "
                "zero, so there is nothing to trade."
            )
        if cache is not None and symbol in cache:
            data = cache[symbol]
        else:
            data = get_prices(symbol, start, end, interval)
            if cache is not None:
                cache[symbol] = data
        strategy.attach_auxiliary_series(data.series)


def run_backtest_for_symbol(
    symbol: str,
    strategy_name: str,
    start: str,
    end: str,
    *,
    parameters: dict[str, Any] | None = None,
    interval: str = "1d",
    config: BacktestConfig | None = None,
    market_data: MarketDataResult | None = None,
    auxiliary_cache: dict[str, MarketDataResult] | None = None,
) -> BacktestRun:
    """Fetch data and run a backtest end to end.

    This is the single entry point every caller uses — the HTTP route, the MCP
    layer and `compare_strategies`. Having one path is what keeps the AI-facing
    and API-facing surfaces from drifting (testing.md §5).

    ``market_data`` can be supplied to reuse an already-fetched dataset, and
    ``auxiliary_cache`` does the same for second legs. `compare_strategies`
    (M3.7) passes both, so every compared strategy runs on byte-identical data
    rather than on separate downloads that might differ.
    """
    data = market_data or get_prices(symbol, start, end, interval)
    strategy = create(strategy_name, parameters)
    resolve_auxiliary_series(
        strategy, start, end, interval, primary_symbol=symbol, cache=auxiliary_cache
    )
    return run_backtest(data.series, strategy, config, data_quality=data.quality.as_dict())

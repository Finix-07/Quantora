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
) -> BacktestRun:
    """Fetch data and run a backtest end to end.

    ``market_data`` can be supplied to reuse an already-fetched dataset — which
    is what `compare_strategies` (M3.7) does, so every compared strategy runs on
    byte-identical data rather than on two separate downloads that might differ.
    """
    data = market_data or get_prices(symbol, start, end, interval)
    strategy = create(strategy_name, parameters)
    return run_backtest(data.series, strategy, config, data_quality=data.quality.as_dict())

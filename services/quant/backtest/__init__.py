"""Backtesting: execution simulation, portfolio accounting and metrics.

The pipeline is architecture.md §6's::

    validated data -> signal -> position sizing -> order intent
                   -> execution simulation -> portfolio state -> PnL/metrics

Its central guarantee is that a decision made at bar *t* can only be executed
using bar *t+1*'s prices (NFR5.3), enforced by a single-slot pending-order queue
in the bar loop rather than by convention.
"""

from services.quant.backtest.config import (
    PERIODS_PER_YEAR,
    TRADING_DAYS_PER_YEAR,
    BacktestConfig,
    ExecutionModel,
)
from services.quant.backtest.costs import CostModel
from services.quant.backtest.engine import SimulationOutput, simulate
from services.quant.backtest.events import (
    FillEvent,
    MarketEvent,
    OrderEvent,
    OrderSide,
    OrderType,
    PositionEvent,
)
from services.quant.backtest.metrics import Metrics, drawdown_curve, max_drawdown
from services.quant.backtest.portfolio import (
    InsufficientCashError,
    Portfolio,
    PositionState,
    Trade,
)
from services.quant.backtest.runner import BacktestRun, run_backtest, run_backtest_for_symbol

__all__ = [
    "PERIODS_PER_YEAR",
    "TRADING_DAYS_PER_YEAR",
    "BacktestConfig",
    "BacktestRun",
    "CostModel",
    "ExecutionModel",
    "FillEvent",
    "InsufficientCashError",
    "MarketEvent",
    "Metrics",
    "OrderEvent",
    "OrderSide",
    "OrderType",
    "Portfolio",
    "PositionEvent",
    "PositionState",
    "SimulationOutput",
    "Trade",
    "drawdown_curve",
    "max_drawdown",
    "run_backtest",
    "run_backtest_for_symbol",
    "simulate",
]

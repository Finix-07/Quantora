"""Strategy implementations behind one shared contract.

Importing this package registers every built-in strategy, so callers can rely on
:func:`available` returning the full set without importing each module.
"""

from services.quant.strategies.base import (
    SIGNAL_COLUMNS,
    ExitContext,
    ExitDecision,
    InsufficientDataError,
    InvalidParametersError,
    ParameterSpec,
    Position,
    SignalDirection,
    SignalSet,
    SizingContext,
    Strategy,
    StrategyError,
    empty_signal_frame,
)
from services.quant.strategies.bollinger import BollingerStrategy
from services.quant.strategies.dual_thrust import DualThrustStrategy
from services.quant.strategies.macd import MACDStrategy
from services.quant.strategies.pair_trading import PairTradingStrategy
from services.quant.strategies.registry import (
    UnknownStrategyError,
    available,
    create,
    describe_all,
    get_strategy_class,
    register,
)

__all__ = [
    "SIGNAL_COLUMNS",
    "BollingerStrategy",
    "DualThrustStrategy",
    "ExitContext",
    "ExitDecision",
    "InsufficientDataError",
    "InvalidParametersError",
    "MACDStrategy",
    "PairTradingStrategy",
    "ParameterSpec",
    "Position",
    "SignalDirection",
    "SignalSet",
    "SizingContext",
    "Strategy",
    "StrategyError",
    "UnknownStrategyError",
    "available",
    "create",
    "describe_all",
    "empty_signal_frame",
    "get_strategy_class",
    "register",
]

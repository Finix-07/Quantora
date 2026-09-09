"""The event vocabulary shared by the Python simulator and (at M4) the C++ one.

architecture.md §7 names four events — MarketEvent, OrderEvent, FillEvent,
PositionEvent. Defining them here rather than inside the Python engine means the
C++ engine's binding layer has one agreed shape to translate to, which is what
makes the M4 parity test a comparison of engines rather than of data formats.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One bar of market data becoming available."""

    timestamp: pd.Timestamp
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_bar(self) -> dict[str, float]:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """An intent to trade, created at the close of ``decision_timestamp``.

    Two timestamps, deliberately. ``decision_timestamp`` is the bar whose data
    justified the order; ``timestamp`` is the bar it is eligible to execute on.
    Keeping them separate is what makes a look-ahead violation visible as a
    field comparison rather than something to reason about.
    """

    decision_timestamp: pd.Timestamp
    timestamp: pd.Timestamp
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    reason: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"order quantity must be positive, got {self.quantity}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_timestamp": self.decision_timestamp.isoformat(),
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "side": str(self.side),
            "quantity": self.quantity,
            "order_type": str(self.order_type),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FillEvent:
    """An order that executed, with the costs it actually incurred."""

    timestamp: pd.Timestamp
    symbol: str
    side: OrderSide
    quantity: float
    reference_price: float
    """The bar price the fill was benchmarked against, before costs."""

    fill_price: float
    """What was actually paid or received, after slippage and half-spread."""

    commission: float
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.quantity * self.fill_price

    @property
    def slippage_cost(self) -> float:
        """Cost of the gap between the reference price and the fill price."""
        return abs(self.fill_price - self.reference_price) * self.quantity

    @property
    def total_cost(self) -> float:
        return self.commission + self.slippage_cost

    @property
    def cash_delta(self) -> float:
        """Change in cash: buying spends, selling receives; commission always costs."""
        return -self.side.sign * self.notional - self.commission

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "side": str(self.side),
            "quantity": self.quantity,
            "reference_price": self.reference_price,
            "fill_price": self.fill_price,
            "commission": self.commission,
            "slippage_cost": self.slippage_cost,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PositionEvent:
    """Portfolio state after a bar has been processed."""

    timestamp: pd.Timestamp
    symbol: str
    quantity: float
    average_price: float
    cash: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "cash": self.cash,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "equity": self.equity,
        }

"""Portfolio state: cash, positions, realized/unrealized PnL and closed trades.

Multi-symbol from the start even though M2 runs one instrument. Pair trading
(M3.3) holds two legs simultaneously, and retro-fitting a second symbol into a
single-position engine is exactly the kind of rewrite that introduces accounting
bugs into code that was already tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from services.quant.backtest.events import FillEvent, OrderSide


class InsufficientCashError(Exception):
    """An order could not be afforded.

    Raised by the engine's affordability check rather than silently letting cash
    go negative. A backtest that quietly runs on imaginary money reports returns
    the user could never have earned (NFR5.6).
    """


@dataclass(frozen=True, slots=True)
class Trade:
    """One completed round trip, recorded when a position is reduced or closed.

    `pnl` is net of the costs attributable to both legs, because that is the
    number a user actually keeps. `gross_pnl` is kept alongside so the cost drag
    is visible rather than buried (NFR5.2).
    """

    symbol: str
    direction: str  # "long" | "short"
    quantity: float
    entry_timestamp: pd.Timestamp
    entry_price: float
    exit_timestamp: pd.Timestamp
    exit_price: float
    gross_pnl: float
    costs: float
    pnl: float
    return_pct: float
    bars_held: int
    entry_reason: str = ""
    exit_reason: str = ""

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "quantity": self.quantity,
            "entry_timestamp": self.entry_timestamp.isoformat(),
            "entry_price": self.entry_price,
            "exit_timestamp": self.exit_timestamp.isoformat(),
            "exit_price": self.exit_price,
            "gross_pnl": self.gross_pnl,
            "costs": self.costs,
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "bars_held": self.bars_held,
            "entry_reason": self.entry_reason,
            "exit_reason": self.exit_reason,
        }


@dataclass(slots=True)
class PositionState:
    """One symbol's open position."""

    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    entry_timestamp: pd.Timestamp | None = None
    entry_bar_index: int = 0
    entry_reason: str = ""
    #: Entry-side costs still attached to the open position, released
    #: proportionally as the position is closed.
    open_costs: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0

    @property
    def sign(self) -> int:
        if self.quantity > 0:
            return 1
        if self.quantity < 0:
            return -1
        return 0

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.average_price) * self.quantity


@dataclass(slots=True)
class Portfolio:
    """Cash plus positions, updated by fills and marked to market by bar."""

    initial_cash: float
    cash: float = field(init=False)
    positions: dict[str, PositionState] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    total_commission: float = 0.0
    total_slippage: float = 0.0
    #: Absolute traded notional, used for turnover.
    total_traded_notional: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive, got {self.initial_cash}")
        self.cash = float(self.initial_cash)

    def position(self, symbol: str) -> PositionState:
        return self.positions.setdefault(symbol, PositionState(symbol=symbol))

    def quantity(self, symbol: str) -> float:
        return self.position(symbol).quantity

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(
            p.market_value(prices[p.symbol]) for p in self.positions.values() if p.symbol in prices
        )

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            p.unrealized_pnl(prices[p.symbol])
            for p in self.positions.values()
            if p.symbol in prices
        )

    @property
    def realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())

    def apply_fill(self, fill: FillEvent, *, bar_index: int) -> None:
        """Update cash, position and trade history from one fill.

        Handles the three cases separately because they account differently:
        opening or adding (average price moves), reducing (PnL is realized on
        the closed quantity, average price does not move), and reversing (the
        old position is fully closed and a new one opened at the fill price —
        blending the two would produce an average price that never existed).
        """
        position = self.position(fill.symbol)
        signed_quantity = fill.side.sign * fill.quantity

        self.cash += fill.cash_delta
        self.total_commission += fill.commission
        self.total_slippage += fill.slippage_cost
        self.total_traded_notional += abs(fill.notional)
        cost = fill.total_cost

        if position.is_flat:
            self._open(position, fill, signed_quantity, cost, bar_index)
            return

        if position.sign == fill.side.sign:
            self._add(position, fill, signed_quantity, cost)
            return

        closing_quantity = min(abs(signed_quantity), abs(position.quantity))
        self._close(position, fill, closing_quantity, cost, bar_index)

        remaining = abs(signed_quantity) - closing_quantity
        if remaining > 0:
            # Reversal: the rest opens a new position on the other side. The
            # entry-side cost of that new position is the share of this fill's
            # cost proportional to the reversing quantity.
            opening_cost = cost * (remaining / abs(signed_quantity))
            reversal = FillEvent(
                timestamp=fill.timestamp,
                symbol=fill.symbol,
                side=fill.side,
                quantity=remaining,
                reference_price=fill.reference_price,
                fill_price=fill.fill_price,
                commission=0.0,
                reason=fill.reason,
            )
            self._open(
                position,
                reversal,
                fill.side.sign * remaining,
                opening_cost,
                bar_index,
            )

    def _open(
        self,
        position: PositionState,
        fill: FillEvent,
        signed_quantity: float,
        cost: float,
        bar_index: int,
    ) -> None:
        position.quantity = signed_quantity
        position.average_price = fill.fill_price
        position.entry_timestamp = fill.timestamp
        position.entry_bar_index = bar_index
        position.entry_reason = fill.reason
        position.open_costs = cost

    def _add(
        self, position: PositionState, fill: FillEvent, signed_quantity: float, cost: float
    ) -> None:
        new_quantity = position.quantity + signed_quantity
        position.average_price = (
            position.average_price * position.quantity + fill.fill_price * signed_quantity
        ) / new_quantity
        position.quantity = new_quantity
        position.open_costs += cost

    def _close(
        self,
        position: PositionState,
        fill: FillEvent,
        closing_quantity: float,
        exit_cost: float,
        bar_index: int,
    ) -> None:
        direction_sign = position.sign
        fraction = closing_quantity / abs(position.quantity)
        entry_cost_share = position.open_costs * fraction
        exit_cost_share = exit_cost * (closing_quantity / fill.quantity) if fill.quantity else 0.0

        gross = (fill.fill_price - position.average_price) * closing_quantity * direction_sign
        costs = entry_cost_share + exit_cost_share
        net = gross - costs

        position.realized_pnl += net
        position.open_costs -= entry_cost_share

        entry_notional = position.average_price * closing_quantity
        self.trades.append(
            Trade(
                symbol=position.symbol,
                direction="long" if direction_sign > 0 else "short",
                quantity=closing_quantity,
                entry_timestamp=position.entry_timestamp or fill.timestamp,
                entry_price=position.average_price,
                exit_timestamp=fill.timestamp,
                exit_price=fill.fill_price,
                gross_pnl=gross,
                costs=costs,
                pnl=net,
                return_pct=net / entry_notional if entry_notional else 0.0,
                bars_held=max(0, bar_index - position.entry_bar_index),
                entry_reason=position.entry_reason,
                exit_reason=fill.reason,
            )
        )

        position.quantity += direction_sign * -closing_quantity
        if abs(position.quantity) < 1e-12:
            position.quantity = 0.0
            position.average_price = 0.0
            position.entry_timestamp = None
            position.entry_reason = ""
            position.open_costs = 0.0

    def can_afford(
        self, side: OrderSide, quantity: float, fill_price: float, commission: float
    ) -> bool:
        """Whether a buy is affordable with cash on hand.

        Sells always pass: closing a long returns cash, and opening a short
        receives proceeds. This engine models a cash account with short proceeds
        credited — it does not model margin requirements, which is stated in the
        result's assumptions rather than left for the user to discover.
        """
        if side is OrderSide.SELL:
            return True
        return self.cash >= quantity * fill_price + commission

    def max_affordable_quantity(self, fill_price: float, commission_rate: float) -> float:
        """Largest whole quantity buyable with current cash, including commission."""
        if fill_price <= 0:
            return 0.0
        per_unit = fill_price * (1.0 + commission_rate)
        return float(int(self.cash / per_unit)) if per_unit > 0 else 0.0

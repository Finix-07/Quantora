"""Backtest configuration.

Every assumption a result depends on lives here, is serialized into the result,
and is shown in the UI beside the metrics. NFR4 requires a user to be able to
inspect the backtest config behind any number; that is only possible if there is
exactly one place the config lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from services.quant.backtest.costs import CostModel

#: NSE trades roughly 250 sessions a year. Used to annualise from daily bars.
TRADING_DAYS_PER_YEAR = 252

PERIODS_PER_YEAR: dict[str, float] = {
    "1d": float(TRADING_DAYS_PER_YEAR),
    "1wk": 52.0,
    "1mo": 12.0,
    # Roughly 6.25 trading hours in an NSE session.
    "1h": TRADING_DAYS_PER_YEAR * 6.25,
    "30m": TRADING_DAYS_PER_YEAR * 12.5,
    "15m": TRADING_DAYS_PER_YEAR * 25.0,
    "5m": TRADING_DAYS_PER_YEAR * 75.0,
}


class ExecutionModel(StrEnum):
    """When an order created at bar *t*'s close is allowed to fill.

    Both options fill on a *later* bar than the one that produced the decision.
    Same-bar-close execution is deliberately not offered: on daily bars there is
    no way to know, at the close, that you could have transacted at that same
    close — and offering it would make every backtest in this product silently
    optimistic. The one honest use for it is a deliberate look-ahead experiment,
    which is not worth the risk of it becoming a default.
    """

    NEXT_BAR_OPEN = "next_bar_open"
    NEXT_BAR_CLOSE = "next_bar_close"

    @property
    def price_field(self) -> str:
        return "open" if self is ExecutionModel.NEXT_BAR_OPEN else "close"

    @property
    def description(self) -> str:
        if self is ExecutionModel.NEXT_BAR_OPEN:
            return (
                "Orders decided at a bar's close fill at the next bar's open. "
                "No information from the fill bar is used to make the decision."
            )
        return (
            "Orders decided at a bar's close fill at the next bar's close. "
            "No information from the fill bar is used to make the decision."
        )


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Everything that affects a backtest's numbers, other than the data."""

    initial_cash: float = 1_000_000.0
    cost_model: CostModel = field(default_factory=CostModel)
    execution_model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN
    allow_short: bool = False
    """Whether the engine will let a position go negative. Independent of a
    strategy's own `allow_short`: the engine has the final say, so a portfolio
    that must stay long-only cannot be shorted by a strategy's configuration."""

    liquidate_at_end: bool = True
    """Close any open position at the final bar's close, paying costs. Without
    it, a strategy holding a winner at the end would book an unrealised gain as
    though it had been cashed out for free."""

    risk_free_rate: float = 0.0
    """Annualised, used for Sharpe/Sortino. Defaults to 0 so the reported ratio
    is an excess-over-nothing figure rather than one silently flattered by an
    assumed rate the user never chose."""

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive, got {self.initial_cash}")
        if self.risk_free_rate < -1 or self.risk_free_rate > 1:
            raise ValueError(
                f"risk_free_rate is an annual decimal fraction (0.07 for 7%), got {self.risk_free_rate}"
            )

    def periods_per_year(self, interval: str) -> float:
        return PERIODS_PER_YEAR.get(interval, float(TRADING_DAYS_PER_YEAR))

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "cost_model": self.cost_model.as_dict(),
            "execution_model": str(self.execution_model),
            "allow_short": self.allow_short,
            "liquidate_at_end": self.liquidate_at_end,
            "risk_free_rate": self.risk_free_rate,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> BacktestConfig:
        """Rebuild from a persisted experiment, rejecting unknown fields.

        A rerun that silently ignored a config field would produce different
        numbers while claiming to reproduce the original (NFR6).
        """
        if not payload:
            return cls()
        known = {
            "initial_cash",
            "cost_model",
            "execution_model",
            "allow_short",
            "liquidate_at_end",
            "risk_free_rate",
        }
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"Unknown backtest-config field(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(known))}."
            )
        return cls(
            initial_cash=float(payload.get("initial_cash", 1_000_000.0)),
            cost_model=CostModel.from_dict(payload.get("cost_model")),
            execution_model=ExecutionModel(
                payload.get("execution_model", ExecutionModel.NEXT_BAR_OPEN)
            ),
            allow_short=bool(payload.get("allow_short", False)),
            liquidate_at_end=bool(payload.get("liquidate_at_end", True)),
            risk_free_rate=float(payload.get("risk_free_rate", 0.0)),
        )

    def assumptions(self) -> list[str]:
        """Plain-language assumptions, shown with every result (NFR4/NFR5.2)."""
        return [
            self.execution_model.description,
            f"Transaction costs: {self.cost_model.describe()}.",
            f"Starting capital: {self.initial_cash:,.0f}.",
            (
                "Short selling is enabled; short proceeds are credited to cash and no margin "
                "requirement or borrow fee is modelled."
                if self.allow_short
                else (
                    "Short selling is disabled: the position is never allowed to go negative, "
                    "so a bearish signal closes the position rather than reversing it."
                )
            ),
            (
                "Any open position is closed at the final bar's close, paying costs."
                if self.liquidate_at_end
                else "Open positions are left open at the end and valued at the final close."
            ),
            f"Sharpe and Sortino use a {self.risk_free_rate:.2%} annualised risk-free rate.",
            "Orders are whole units; fractional quantities are not simulated.",
            "Fills assume the full order size transacts at one price; market impact is not modelled.",
        ]

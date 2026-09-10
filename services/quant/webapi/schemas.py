"""Request/response models for the quant-mcp HTTP surface.

This is an internal contract between the Go API and the Python engine, not a
public API. It is still validated strictly: the Go service is the only caller,
so a shape mismatch is a bug in our own code and should fail loudly at the
boundary rather than produce a confusing result three layers down.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from services.quant.data.types import SUPPORTED_INTERVALS


class CostModelPayload(BaseModel):
    commission_bps: float = Field(default=3.0, ge=0, le=1000)
    commission_min: float = Field(default=0.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0, le=1000)
    spread_bps: float = Field(default=2.0, ge=0, le=1000)


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    start: str
    end: str
    interval: str = "1d"
    parameters: dict[str, Any] = Field(default_factory=dict)
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    cost_model: CostModelPayload = Field(default_factory=CostModelPayload)
    execution_model: Literal["next_bar_open", "next_bar_close"] = "next_bar_open"
    allow_short: bool = False
    liquidate_at_end: bool = True
    risk_free_rate: float = Field(default=0.0, ge=-1, le=1)
    experiment_id: str | None = None

    @field_validator("interval")
    @classmethod
    def _known_interval(cls, value: str) -> str:
        if value not in SUPPORTED_INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(SUPPORTED_INTERVALS)}")
        return value


class CompareRequest(BaseModel):
    """Compare several strategies over one instrument and date range.

    The strategy list is deliberately part of one request rather than several:
    every strategy must run on byte-identical data, and two separate fetches of
    the same range can differ (yfinance revises history), which would make the
    comparison meaningless.
    """

    symbol: str
    # No min_length here on purpose. Pydantic would reject a one-strategy
    # request with a generic "List should have at least 2 items", whereas the
    # domain layer says "A comparison needs at least two strategies... Use the
    # backtest endpoint to run a single strategy" — which tells the user what to
    # do instead.
    strategies: list[dict[str, Any]]
    start: str
    end: str
    interval: str = "1d"
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    cost_model: CostModelPayload = Field(default_factory=CostModelPayload)
    execution_model: Literal["next_bar_open", "next_bar_close"] = "next_bar_open"
    allow_short: bool = False
    liquidate_at_end: bool = True
    risk_free_rate: float = Field(default=0.0, ge=-1, le=1)


class IndicatorRequest(BaseModel):
    symbol: str
    start: str
    end: str
    interval: str = "1d"
    indicators: list[dict[str, Any]] = Field(min_length=1)


class HoldingPayload(BaseModel):
    """One position in a portfolio request.

    `quantity` is bounded below by an exclusive zero rather than defaulting: a
    holding of nothing is not a holding, and short positions are not modelled by
    the portfolio engine. `cost_basis` stays optional — a portfolio is analysable
    whether or not the user recorded what they paid, and a default of 0 would
    report a fabricated unrealised gain.
    """

    symbol: str
    quantity: float = Field(gt=0)
    cost_basis: float | None = Field(default=None, ge=0)


class PortfolioRiskRequest(BaseModel):
    """Risk metrics for a set of holdings over a date range (FR7)."""

    holdings: list[HoldingPayload] = Field(min_length=1)
    start: str
    end: str
    interval: str = "1d"
    benchmark: str = "NIFTY"
    risk_free_rate: float = Field(default=0.0, ge=-1, le=1)
    name: str = ""
    base_currency: str = "INR"

    @field_validator("interval")
    @classmethod
    def _known_interval(cls, value: str) -> str:
        if value not in SUPPORTED_INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(SUPPORTED_INTERVALS)}")
        return value


class PortfolioScenarioRequest(PortfolioRiskRequest):
    """A what-if reweighting of the same holdings.

    `weights` is deliberately unconstrained here beyond being a number map. The
    domain layer rejects negative, non-finite and all-zero vectors with messages
    that say which rule was broken and why — pydantic would replace those with
    "Input should be greater than or equal to 0", which tells the user the shape
    of the rule but not the reason for it.
    """

    weights: dict[str, float] = Field(min_length=1)

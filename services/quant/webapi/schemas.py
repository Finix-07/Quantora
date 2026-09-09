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
    strategies: list[dict[str, Any]] = Field(min_length=2)
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

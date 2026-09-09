"""Quant capability endpoints consumed by the Go API.

Every route is a thin adapter: it validates, calls the framework-agnostic engine,
and translates a domain error into the shared error envelope. No quantitative
logic lives here, so the MCP layer (M6) calling the same engine functions
directly cannot drift from what the API returns (testing.md §5).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.costs import CostModel
from services.quant.backtest.result import build_result
from services.quant.backtest.runner import run_backtest
from services.quant.data.errors import (
    DataError,
    DataValidationError,
    InvalidRequestError,
    ProviderError,
    UnknownSymbolError,
)
from services.quant.data.service import get_prices
from services.quant.data.universe import all_instruments, get_universe
from services.quant.strategies.base import (
    InsufficientDataError,
    InvalidParametersError,
    StrategyError,
)
from services.quant.strategies.registry import UnknownStrategyError, create, describe_all
from services.quant.webapi.errors import ErrorCode, ServiceError
from services.quant.webapi.schemas import BacktestRequest, CostModelPayload

log = logging.getLogger(__name__)

router = APIRouter()


def _service_error(exc: Exception) -> ServiceError:
    """Map a domain error onto the shared error vocabulary.

    Each class gets a distinct code because the user's next action differs: a
    bad symbol is fixed in the form, a provider outage by retrying, and a
    validation failure by narrowing the date range or distrusting the source.
    """
    if isinstance(exc, UnknownSymbolError | UnknownStrategyError | InvalidParametersError):
        return ServiceError(
            ErrorCode.INVALID_REQUEST,
            str(exc),
            status_code=400,
            details=exc.as_details() if isinstance(exc, DataError) else None,
        )
    if isinstance(exc, InvalidRequestError | InsufficientDataError):
        return ServiceError(ErrorCode.INVALID_REQUEST, str(exc), status_code=400)
    if isinstance(exc, DataValidationError):
        return ServiceError(
            ErrorCode.DATA_VALIDATION_FAILED, str(exc), status_code=422, details=exc.as_details()
        )
    if isinstance(exc, ProviderError):
        return ServiceError(
            ErrorCode.DATA_UNAVAILABLE, str(exc), status_code=503, details=exc.as_details()
        )
    if isinstance(exc, DataError | StrategyError):
        return ServiceError(ErrorCode.INVALID_REQUEST, str(exc), status_code=400)
    raise exc


def _config(payload: BacktestRequest) -> BacktestConfig:
    costs: CostModelPayload = payload.cost_model
    return BacktestConfig(
        initial_cash=payload.initial_cash,
        cost_model=CostModel(
            commission_bps=costs.commission_bps,
            commission_min=costs.commission_min,
            slippage_bps=costs.slippage_bps,
            spread_bps=costs.spread_bps,
        ),
        execution_model=ExecutionModel(payload.execution_model),
        allow_short=payload.allow_short,
        liquidate_at_end=payload.liquidate_at_end,
        risk_free_rate=payload.risk_free_rate,
    )


@router.get("/universe", tags=["market"])
def universe() -> JSONResponse:
    """The tradable universe, with each instrument's provider ticker and sector."""
    return JSONResponse(
        {
            "universe": list(get_universe()),
            "instruments": [i.as_dict() for i in all_instruments()],
        }
    )


@router.get("/strategies", tags=["strategies"])
def strategies() -> JSONResponse:
    """Every registered strategy with its parameter specs.

    The Strategy Lab renders its form from this rather than hard-coding a copy,
    so a new strategy appears in the UI without a frontend change.
    """
    return JSONResponse({"strategies": describe_all()})


@router.get("/market/{symbol}", tags=["market"])
def market_data(symbol: str, start: str, end: str, interval: str = "1d") -> JSONResponse:
    """Validated OHLCV bars with provenance and a data-quality report."""
    try:
        result = get_prices(symbol, start, end, interval)
    except Exception as exc:
        raise _service_error(exc) from exc
    return JSONResponse(result.as_dict())


@router.post("/backtests", tags=["backtests"])
def run_backtest_endpoint(payload: BacktestRequest) -> JSONResponse:
    """Run one backtest and return the full result contract."""
    try:
        data = get_prices(payload.symbol, payload.start, payload.end, payload.interval)
        strategy = create(payload.strategy, payload.parameters)
        run = run_backtest(
            data.series, strategy, _config(payload), data_quality=data.quality.as_dict()
        )
    except Exception as exc:
        raise _service_error(exc) from exc

    result = build_result(run, experiment_id=payload.experiment_id)
    return JSONResponse(result.as_dict())

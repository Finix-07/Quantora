"""Python wrapper around the C++ execution engine (services/execution-cpp).

architecture.md §7 allows the C++ path to be exposed to Python only once the C++
API is stable and a benchmark shows a measurable advantage. This module is that
exposure and nothing more: it converts a frame of bars and a target array into
the C++ executor's inputs, and converts its outputs back into the same
:class:`SimulationOutput` the Python engine produces.

**There is deliberately no fallback.** If the extension is not built,
:func:`is_available` returns ``False`` with a reason and every entry point
raises :class:`ExtensionNotBuilt` naming the build command. Silently running the
Python engine under a C++ name would make both the parity test and the benchmark
measure the same code twice while claiming otherwise — the one failure mode that
would invalidate every number M4 produces. Importing this module never fails, so
a machine without the extension can still import the package.

The C++ side speaks in *bar indices*, not timestamps: every event in a
simulation happens at a bar, an index is exact and cheap, and the calendar never
has to be reimplemented in C++. Mapping indices back onto the pandas index
happens here.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pandas as pd

from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.engine import (
    SimulationOutput,
    reduced_buy_warning,
    skipped_buy_warning,
)
from services.quant.backtest.events import (
    FillEvent,
    OrderEvent,
    OrderSide,
    PositionEvent,
)
from services.quant.backtest.portfolio import Portfolio, PositionState, Trade

#: Name of the compiled extension module.
EXTENSION_NAME = "quant_execution"

#: What to tell a user who does not have it.
BUILD_COMMAND = "make build-cpp"

#: Where `make build-cpp` puts the extension. Kept out of the Python package
#: tree so a build artefact never sits next to source files.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_DIR = _REPO_ROOT / "services" / "execution-cpp" / "build"


class ExtensionNotBuilt(RuntimeError):
    """The C++ extension is not importable.

    Carries the reason and the command that fixes it, because "module not found"
    on its own sends the reader hunting for a package that does not exist on
    PyPI.
    """


def _load_extension() -> tuple[ModuleType | None, str]:
    """Import the extension, returning ``(module, reason_if_missing)``.

    Two lookup paths, in order: a normally importable module (how it arrives in
    the container, where the build stage installs it into site-packages), then
    the local CMake build directory (how it arrives on a developer machine).
    """
    try:
        return importlib.import_module(EXTENSION_NAME), ""
    except ImportError:
        pass

    if not _BUILD_DIR.is_dir():
        return None, (f"the C++ extension has not been built: no build directory at {_BUILD_DIR}")

    candidates = sorted(_BUILD_DIR.glob(f"{EXTENSION_NAME}*.so")) + sorted(
        _BUILD_DIR.glob(f"{EXTENSION_NAME}*.pyd")
    )
    if not candidates:
        return None, (
            f"the C++ extension has not been built: no {EXTENSION_NAME} module in {_BUILD_DIR}"
        )

    spec = importlib.util.spec_from_file_location(EXTENSION_NAME, candidates[0])
    if spec is None or spec.loader is None:
        return None, f"{candidates[0]} is not a loadable extension module"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        # Most often an ABI mismatch: the extension was built against a
        # different Python than the one importing it. Say so rather than
        # reporting a bare "not built", which would send the reader to rebuild
        # something that is already there.
        return None, (
            f"{candidates[0].name} exists but could not be loaded ({exc}); "
            f"it was probably built for a different Python than {sys.version.split()[0]}"
        )
    sys.modules[EXTENSION_NAME] = module
    return module, ""


_extension, _unavailable_reason = _load_extension()


def is_available() -> bool:
    """Whether the C++ execution path can actually be used."""
    return _extension is not None


def unavailable_reason() -> str:
    """Why it cannot, or ``""`` when it can.

    Test suites use this for their skip message, so a skipped parity test says
    what is missing instead of just vanishing from the report.
    """
    return _unavailable_reason


def require_extension() -> ModuleType:
    """Return the extension or raise, naming the command that builds it."""
    if _extension is None:
        raise ExtensionNotBuilt(f"{_unavailable_reason}. Build it with `{BUILD_COMMAND}`.")
    return _extension


def _price_field(execution_model: ExecutionModel):  # noqa: ANN202 - extension enum
    extension = require_extension()
    return (
        extension.PriceField.OPEN
        if execution_model is ExecutionModel.NEXT_BAR_OPEN
        else extension.PriceField.CLOSE
    )


def _side(extension_side) -> OrderSide:  # noqa: ANN001 - extension enum
    extension = require_extension()
    return OrderSide.BUY if extension_side == extension.OrderSide.BUY else OrderSide.SELL


def simulate_targets(
    frame: pd.DataFrame,
    targets: Sequence[float],
    *,
    symbol: str = "SYNTHETIC",
    reasons: Sequence[str] | None = None,
    config: BacktestConfig | None = None,
    strategy: str = "targets",
) -> SimulationOutput:
    """The C++ counterpart of :func:`services.quant.backtest.engine.simulate_targets`.

    Same signature, same return type, same numbers — that identity is what the
    M4.5 parity test asserts, and it is why the two functions are kept
    call-compatible rather than merely equivalent.

    Raises:
        ExtensionNotBuilt: if the C++ extension is not available.
    """
    extension = require_extension()
    cfg = config or BacktestConfig()

    n = len(frame)
    if n == 0:
        raise ValueError("cannot simulate a backtest over an empty price series")
    if len(targets) != n:
        raise ValueError(
            f"targets has {len(targets)} entries but there are {n} bars; "
            "the executor needs exactly one target per bar"
        )
    if reasons is not None and len(reasons) != n:
        raise ValueError(f"reasons has {len(reasons)} entries but there are {n} bars")

    index = frame.index
    cost_model = extension.CostModel(
        commission_bps=cfg.cost_model.commission_bps,
        commission_min=cfg.cost_model.commission_min,
        slippage_bps=cfg.cost_model.slippage_bps,
        spread_bps=cfg.cost_model.spread_bps,
    )
    ext_config = extension.SimulationConfig(
        initial_cash=cfg.initial_cash,
        costs=cost_model,
        price_field=_price_field(cfg.execution_model),
        liquidate_at_end=cfg.liquidate_at_end,
    )

    result = extension.simulate_targets(
        open=frame["open"].to_numpy(dtype="float64", copy=False),
        high=frame["high"].to_numpy(dtype="float64", copy=False),
        low=frame["low"].to_numpy(dtype="float64", copy=False),
        close=frame["close"].to_numpy(dtype="float64", copy=False),
        volume=frame["volume"].to_numpy(dtype="float64", copy=False),
        targets=pd.Series(targets, dtype="float64").to_numpy(dtype="float64", copy=False),
        reasons=[] if reasons is None else [str(r) for r in reasons],
        config=ext_config,
    )

    return _to_simulation_output(result, index=index, symbol=symbol, strategy=strategy, config=cfg)


def _to_simulation_output(
    result,  # noqa: ANN001 - extension type
    *,
    index: pd.Index,
    symbol: str,
    strategy: str,
    config: BacktestConfig,
) -> SimulationOutput:
    extension = require_extension()

    portfolio = Portfolio(initial_cash=config.initial_cash)
    portfolio.cash = result.final_cash
    portfolio.total_commission = result.total_commission
    portfolio.total_slippage = result.total_slippage
    portfolio.total_traded_notional = result.total_traded_notional

    final = result.final_position
    state = PositionState(
        symbol=symbol,
        quantity=final.quantity,
        average_price=final.average_price,
        realized_pnl=final.realized_pnl,
        # A flat position has no entry, and reporting the bar of the position it
        # used to hold would be a small lie the metrics layer might believe.
        entry_timestamp=None if final.quantity == 0.0 else index[final.entry_bar_index],
        entry_bar_index=final.entry_bar_index,
        entry_reason=final.entry_reason,
        open_costs=final.open_costs,
    )
    portfolio.positions[symbol] = state
    portfolio.trades = [
        Trade(
            symbol=symbol,
            direction="long" if trade.direction > 0 else "short",
            quantity=trade.quantity,
            entry_timestamp=index[trade.entry_bar],
            entry_price=trade.entry_price,
            exit_timestamp=index[trade.exit_bar],
            exit_price=trade.exit_price,
            gross_pnl=trade.gross_pnl,
            costs=trade.costs,
            pnl=trade.pnl,
            return_pct=trade.return_pct,
            bars_held=int(trade.bars_held),
            entry_reason=trade.entry_reason,
            exit_reason=trade.exit_reason,
        )
        for trade in result.trades
    ]

    warnings: list[str] = []
    for warning in result.warnings:
        timestamp = index[warning.bar_index]
        if warning.kind == extension.WarningKind.SKIPPED_BUY:
            warnings.append(
                skipped_buy_warning(
                    timestamp, symbol, warning.requested_quantity, warning.fill_price
                )
            )
        else:
            warnings.append(
                reduced_buy_warning(
                    timestamp, symbol, warning.requested_quantity, warning.filled_quantity
                )
            )

    return SimulationOutput(
        symbol=symbol,
        strategy=strategy,
        equity_curve=pd.Series(result.equity_curve, index=index, dtype="float64", name="equity"),
        positions=[
            PositionEvent(
                timestamp=index[event.bar_index],
                symbol=symbol,
                quantity=event.quantity,
                average_price=event.average_price,
                cash=event.cash,
                market_value=event.market_value,
                unrealized_pnl=event.unrealized_pnl,
                realized_pnl=event.realized_pnl,
            )
            for event in result.positions
        ],
        orders=[
            OrderEvent(
                decision_timestamp=index[order.decision_bar],
                timestamp=index[order.bar_index],
                symbol=symbol,
                side=_side(order.side),
                quantity=order.quantity,
                reason=order.reason,
            )
            for order in result.orders
        ],
        fills=[
            FillEvent(
                timestamp=index[fill.bar_index],
                symbol=symbol,
                side=_side(fill.side),
                quantity=fill.quantity,
                reference_price=fill.reference_price,
                fill_price=fill.fill_price,
                commission=fill.commission,
                reason=fill.reason,
            )
            for fill in result.fills
        ],
        portfolio=portfolio,
        warnings=warnings,
        exposure=pd.Series(result.exposure, index=index, dtype="float64", name="exposure"),
    )

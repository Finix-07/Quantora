"""The Python execution simulator.

Implements architecture.md §6's pipeline::

    validated data -> signal -> position sizing -> order intent
                   -> execution simulation -> portfolio state -> PnL/metrics

The bar loop is deliberately explicit rather than vectorised. Vectorising it
would hide the one thing that has to be provable — that a decision made at bar
*t* can only be executed with bar *t+1*'s prices — and a look-ahead bug hidden
inside a `shift()` is exactly the failure NFR5.3 exists to prevent.

The loop is split in two along the seam the C++ engine (M4) replaces:

* deciding a **target position** for each bar — this calls the strategy, needs
  indicators and sizing context, and stays in Python;
* **executing** that target against the following bar with costs, the cash
  constraint and portfolio accounting — a closed numeric problem, and the part
  the C++ executor mirrors.

:func:`simulate` supplies the first half from a strategy; :func:`simulate_targets`
supplies it from a pre-computed array. Both drive the *same* execution loop, so
the C++ parity test compares two implementations of one problem rather than two
different problems.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pandas as pd

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.events import (
    FillEvent,
    MarketEvent,
    OrderEvent,
    OrderSide,
    PositionEvent,
)
from services.quant.backtest.portfolio import Portfolio, PositionState
from services.quant.data.types import PriceSeries
from services.quant.strategies.base import (
    ExitContext,
    Position,
    SignalDirection,
    SignalSet,
    SizingContext,
    Strategy,
)

log = logging.getLogger(__name__)

#: What a decision step returns for one bar: the position to hold from the next
#: bar onwards, and the human-readable reason recorded on the resulting order.
TargetDecision = tuple[float, str]

#: ``(bar_index, market, equity, position) -> (target_quantity, reason)``.
DecisionFn = Callable[[int, MarketEvent, float, PositionState], TargetDecision]


@dataclass(slots=True)
class SimulationOutput:
    """Raw simulation output, before metrics are computed.

    Metrics live in a separate module so the same run can be measured different
    ways without re-simulating, and so a metric bug cannot corrupt the trade log.
    """

    symbol: str
    strategy: str
    equity_curve: pd.Series
    positions: list[PositionEvent] = field(default_factory=list)
    orders: list[OrderEvent] = field(default_factory=list)
    fills: list[FillEvent] = field(default_factory=list)
    portfolio: Portfolio | None = None
    signals: SignalSet | None = None
    warnings: list[str] = field(default_factory=list)
    #: Per-bar signed exposure as a fraction of equity, for the exposure metric.
    exposure: pd.Series = field(default_factory=lambda: pd.Series(dtype="float64"))


def _pending_order(
    *,
    decision_timestamp: pd.Timestamp,
    execution_timestamp: pd.Timestamp,
    symbol: str,
    delta: float,
    reason: str,
) -> OrderEvent | None:
    if abs(delta) < 1e-9:
        return None
    return OrderEvent(
        decision_timestamp=decision_timestamp,
        timestamp=execution_timestamp,
        symbol=symbol,
        side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
        quantity=abs(delta),
        reason=reason,
    )


# ---------------------------------------------------------------- warnings ---
# The C++ executor reports a constrained fill as *facts* (which bar, how much was
# asked for, how much was affordable, at what price) and leaves the wording to
# these two functions, so the two engines cannot drift into describing the same
# event differently. That is also why the text lives at module level rather than
# inline in `_execute`.


def skipped_buy_warning(
    timestamp: pd.Timestamp, symbol: str, quantity: float, fill_price: float
) -> str:
    return (
        f"{timestamp.date()}: skipped a buy of {quantity:g} "
        f"{symbol} — insufficient cash at {fill_price:.2f}."
    )


def reduced_buy_warning(
    timestamp: pd.Timestamp, symbol: str, quantity: float, affordable: float
) -> str:
    return (
        f"{timestamp.date()}: reduced a buy of {quantity:g} to {affordable:g} "
        f"{symbol} — insufficient cash for the full size."
    )


def _run_execution(
    *,
    symbol: str,
    frame: pd.DataFrame,
    config: BacktestConfig,
    portfolio: Portfolio,
    output: SimulationOutput,
    decide: DecisionFn,
) -> None:
    """The execution half of the pipeline: targets in, portfolio state out.

    Everything strategy-specific has already happened by the time ``decide``
    returns a number, so this loop is the entire behaviour the C++ executor has
    to reproduce.
    """
    index = frame.index
    n = len(frame)
    price_field = config.execution_model.price_field

    equity_values: list[float] = []
    exposure_values: list[float] = []

    # Orders decided on the previous bar, awaiting execution on this one. This
    # single-slot queue is the mechanism that enforces the causality guarantee:
    # nothing can be placed and filled within the same bar.
    pending: OrderEvent | None = None

    for i in range(n):
        timestamp = index[i]
        row = frame.iloc[i]
        market = MarketEvent(
            timestamp=timestamp,
            symbol=symbol,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

        # --- 1. Execute the order decided on the previous bar ----------------
        if pending is not None:
            fill = _execute(pending, market, price_field, portfolio, config, output.warnings)
            if fill is not None:
                portfolio.apply_fill(fill, bar_index=i)
                output.fills.append(fill)
            pending = None

        # --- 2. Mark to market at this bar's close ---------------------------
        prices = {symbol: market.close}
        equity = portfolio.equity(prices)
        position_state = portfolio.position(symbol)
        equity_values.append(equity)
        exposure_values.append(
            position_state.market_value(market.close) / equity if equity else 0.0
        )
        output.positions.append(
            PositionEvent(
                timestamp=timestamp,
                symbol=symbol,
                quantity=position_state.quantity,
                average_price=position_state.average_price,
                cash=portfolio.cash,
                market_value=position_state.market_value(market.close),
                unrealized_pnl=position_state.unrealized_pnl(market.close),
                realized_pnl=position_state.realized_pnl,
            )
        )

        if i == n - 1:
            break  # no bar left to execute on

        # --- 3. Decide the position to hold from the next bar ----------------
        target_quantity, reason = decide(i, market, equity, position_state)

        order = _pending_order(
            decision_timestamp=timestamp,
            execution_timestamp=index[i + 1],
            symbol=symbol,
            delta=target_quantity - position_state.quantity,
            reason=reason,
        )
        if order is not None:
            output.orders.append(order)
            pending = order

    # --- 4. Close out ------------------------------------------------------
    if config.liquidate_at_end:
        _liquidate(symbol, frame, portfolio, config, output, n)
        # The final equity point must reflect the liquidation, or a held winner
        # would be booked as though it had been cashed out for free.
        last_close = float(frame.iloc[-1]["close"])
        equity_values[-1] = portfolio.equity({symbol: last_close})
        exposure_values[-1] = 0.0

    output.equity_curve = pd.Series(equity_values, index=index, dtype="float64", name="equity")
    output.exposure = pd.Series(exposure_values, index=index, dtype="float64", name="exposure")


def simulate(
    series: PriceSeries,
    strategy: Strategy,
    signals: SignalSet,
    config: BacktestConfig | None = None,
) -> SimulationOutput:
    """Run one instrument through the execution simulator.

    Args:
        series: validated OHLCV bars.
        strategy: used for sizing and discretionary exits; signals are passed in
            separately so a caller can inspect or cache them.
        signals: the strategy's output over the same index as ``series``.
        config: execution assumptions. Defaults are non-zero cost.
    """
    cfg = config or BacktestConfig()
    if len(series) == 0:
        raise ValueError("cannot simulate a backtest over an empty price series")
    if not series.frame.index.equals(signals.frame.index):
        raise ValueError(
            "signal index does not match the price index; the strategy must emit one row per bar"
        )

    portfolio = Portfolio(initial_cash=cfg.initial_cash)
    output = SimulationOutput(
        symbol=series.symbol,
        strategy=strategy.name,
        equity_curve=pd.Series(dtype="float64"),
        portfolio=portfolio,
        signals=signals,
    )

    indicator_frame = signals.indicators

    # After a discretionary exit (a stop, a time-based exit), the strategy's
    # target direction is usually still pointing the same way, so the engine
    # would re-enter on the very next bar and the stop would have achieved
    # nothing. Re-entry is therefore suppressed until the signal itself changes
    # its mind.
    suppressed_direction: SignalDirection | None = None

    def decide(
        i: int, market: MarketEvent, equity: float, position_state: PositionState
    ) -> TargetDecision:
        nonlocal suppressed_direction
        timestamp = market.timestamp
        indicators = _indicator_row(indicator_frame, timestamp)

        # --- Discretionary exit, evaluated on this bar's close ---------------
        target_quantity: float | None = None
        reason = ""
        if not position_state.is_flat:
            exit_decision = strategy.exit_signal(
                ExitContext(
                    timestamp=timestamp,
                    symbol=series.symbol,
                    position=_as_contract_position(portfolio, series.symbol, i),
                    price=market.close,
                    bar=market.as_bar(),
                    unrealized_pnl=position_state.unrealized_pnl(market.close),
                    unrealized_return=(
                        position_state.unrealized_pnl(market.close)
                        / abs(position_state.average_price * position_state.quantity)
                        if position_state.quantity
                        else 0.0
                    ),
                    indicators=indicators,
                )
            )
            if exit_decision.should_exit:
                target_quantity = 0.0
                reason = exit_decision.reason or "strategy exit signal"
                suppressed_direction = _direction_of(position_state.quantity)

        # --- Target position from this bar's signal --------------------------
        if target_quantity is None:
            direction = SignalDirection(int(signals.frame.iloc[i]["direction"]))
            if direction is SignalDirection.SHORT and not cfg.allow_short:
                # The engine has the final say: a long-only backtest must not be
                # shorted by a strategy's own configuration.
                direction = SignalDirection.FLAT

            if suppressed_direction is not None:
                if direction is suppressed_direction:
                    direction = SignalDirection.FLAT
                else:
                    suppressed_direction = None

            current_direction = _direction_of(position_state.quantity)
            if direction is current_direction and not strategy.rebalances_continuously:
                # Already positioned as intended. Re-sizing every bar because
                # equity or conviction drifted would churn the position and pay
                # a full round trip in costs for no change of view; that churn
                # is a simulation artefact, not a trading decision.
                target_quantity = position_state.quantity
            else:
                reason = str(signals.frame.iloc[i]["reason"]) or f"target {direction.label}"
                # Size against the price the order is expected to fill near,
                # which is this bar's close — the next bar's open is not yet
                # knowable.
                target_quantity = strategy.position_size(
                    SizingContext(
                        timestamp=timestamp,
                        symbol=series.symbol,
                        direction=direction,
                        strength=float(signals.frame.iloc[i]["strength"]),
                        price=market.close,
                        equity=equity,
                        cash=portfolio.cash,
                        position=_as_contract_position(portfolio, series.symbol, i),
                        indicators=indicators,
                    )
                )

        return target_quantity, reason

    _run_execution(
        symbol=series.symbol,
        frame=series.frame,
        config=cfg,
        portfolio=portfolio,
        output=output,
        decide=decide,
    )

    log.info(
        "execution simulation completed",
        extra={
            "symbol": series.symbol,
            "strategy": strategy.name,
            "bars": len(series.frame),
            "orders": len(output.orders),
            "fills": len(output.fills),
            "trades": len(portfolio.trades),
        },
    )
    return output


def simulate_targets(
    frame: pd.DataFrame,
    targets: Sequence[float],
    *,
    symbol: str = "SYNTHETIC",
    reasons: Sequence[str] | None = None,
    config: BacktestConfig | None = None,
    strategy: str = "targets",
) -> SimulationOutput:
    """Execute a pre-computed per-bar target position.

    This is the parity surface shared with the C++ executor (architecture.md §7):
    bars in, a target quantity per bar in, portfolio state out — no strategy, no
    indicators, no pandas beyond the frame itself. ``targets[i]`` is the position
    to hold *from bar i+1 onwards*, decided at bar ``i``'s close; the last entry
    is therefore never acted on, exactly as in :func:`simulate`.

    Args:
        frame: OHLCV bars, one row per bar.
        targets: one target quantity per bar.
        symbol: recorded on the events; the executor is single-instrument.
        reasons: one order reason per bar. Defaults to empty strings.
        config: execution assumptions. ``allow_short`` is not consulted — a
            direction constraint belongs to the decision half, and by the time a
            target has been chosen the sign has already been settled.
        strategy: name recorded on the output.
    """
    cfg = config or BacktestConfig()
    if len(frame) == 0:
        raise ValueError("cannot simulate a backtest over an empty price series")
    if len(targets) != len(frame):
        raise ValueError(
            f"targets has {len(targets)} entries but there are {len(frame)} bars; "
            "the executor needs exactly one target per bar"
        )
    if reasons is not None and len(reasons) != len(frame):
        raise ValueError(f"reasons has {len(reasons)} entries but there are {len(frame)} bars")

    portfolio = Portfolio(initial_cash=cfg.initial_cash)
    output = SimulationOutput(
        symbol=symbol,
        strategy=strategy,
        equity_curve=pd.Series(dtype="float64"),
        portfolio=portfolio,
    )

    def decide(
        i: int, _market: MarketEvent, _equity: float, _position: PositionState
    ) -> TargetDecision:
        return float(targets[i]), ("" if reasons is None else str(reasons[i]))

    _run_execution(
        symbol=symbol,
        frame=frame,
        config=cfg,
        portfolio=portfolio,
        output=output,
        decide=decide,
    )
    return output


def _direction_of(quantity: float) -> SignalDirection:
    if quantity > 0:
        return SignalDirection.LONG
    if quantity < 0:
        return SignalDirection.SHORT
    return SignalDirection.FLAT


def _indicator_row(indicators: pd.DataFrame, timestamp: pd.Timestamp) -> dict[str, float]:
    if indicators.empty or timestamp not in indicators.index:
        return {}
    row = indicators.loc[timestamp]
    return {str(k): float(v) for k, v in row.items() if pd.notna(v)}


def _as_contract_position(portfolio: Portfolio, symbol: str, bar_index: int) -> Position:
    state = portfolio.position(symbol)
    return Position(
        symbol=symbol,
        quantity=state.quantity,
        entry_price=state.average_price,
        entry_timestamp=state.entry_timestamp or pd.Timestamp(0, tz="UTC"),
        bars_held=max(0, bar_index - state.entry_bar_index) if not state.is_flat else 0,
    )


def _execute(
    order: OrderEvent,
    market: MarketEvent,
    price_field: str,
    portfolio: Portfolio,
    config: BacktestConfig,
    warnings: list[str],
) -> FillEvent | None:
    """Turn an order into a fill, honouring costs and the cash constraint."""
    if order.timestamp != market.timestamp:
        # Defensive: the queue is single-slot, so this can only happen if the
        # loop is changed incorrectly. Failing loudly beats filling on the wrong
        # bar, which is precisely a look-ahead bug.
        raise AssertionError(
            f"order scheduled for {order.timestamp} reached bar {market.timestamp}"
        )

    reference_price = float(getattr(market, price_field))
    side_sign = order.side.sign
    fill_price = config.cost_model.fill_price(reference_price, side_sign)
    quantity = order.quantity

    if order.side is OrderSide.BUY:
        commission = config.cost_model.commission(quantity * fill_price)
        if not portfolio.can_afford(order.side, quantity, fill_price, commission):
            affordable = portfolio.max_affordable_quantity(
                fill_price, config.cost_model.commission_bps / 10_000.0
            )
            if affordable <= 0:
                warnings.append(
                    skipped_buy_warning(market.timestamp, order.symbol, quantity, fill_price)
                )
                return None
            warnings.append(
                reduced_buy_warning(market.timestamp, order.symbol, quantity, affordable)
            )
            quantity = affordable
            commission = config.cost_model.commission(quantity * fill_price)
    else:
        commission = config.cost_model.commission(quantity * fill_price)

    return FillEvent(
        timestamp=market.timestamp,
        symbol=order.symbol,
        side=order.side,
        quantity=quantity,
        reference_price=reference_price,
        fill_price=fill_price,
        commission=commission,
        reason=order.reason,
    )


def _liquidate(
    symbol: str,
    frame: pd.DataFrame,
    portfolio: Portfolio,
    config: BacktestConfig,
    output: SimulationOutput,
    bar_count: int,
) -> None:
    position = portfolio.position(symbol)
    if position.is_flat:
        return

    last = frame.iloc[-1]
    timestamp = frame.index[-1]
    side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
    quantity = abs(position.quantity)
    reference_price = float(last["close"])
    fill_price = config.cost_model.fill_price(reference_price, side.sign)

    fill = FillEvent(
        timestamp=timestamp,
        symbol=symbol,
        side=side,
        quantity=quantity,
        reference_price=reference_price,
        fill_price=fill_price,
        commission=config.cost_model.commission(quantity * fill_price),
        reason="backtest ended — position closed at the final close",
    )
    portfolio.apply_fill(fill, bar_index=bar_count - 1)
    output.fills.append(fill)

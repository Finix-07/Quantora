"""Side-by-side strategy comparison (requirements.md FR6).

The single most important property here is that every strategy sees **the same
bars**. yfinance revises history, so two separate fetches of the same range can
differ, and a comparison built on two downloads would attribute a data
difference to a strategy difference. One dataset is fetched and reused for every
strategy, and its `data_version` is reported once for the whole comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.result import build_result, summarize_result
from services.quant.backtest.runner import BacktestRun, run_backtest_for_symbol
from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import MarketDataResult, get_prices
from services.quant.logging_setup import stage

log = logging.getLogger(__name__)

Direction = Literal["higher", "lower"]

#: The metrics shown side by side, in display order, each with the direction
#: that counts as better. The direction is data, not a UI convention: a
#: front-end that hard-coded "bigger is greener" would mark the worst drawdown
#: as the winner.
COMPARISON_METRICS: tuple[tuple[str, str, Direction | None], ...] = (
    ("total_return", "Total return", "higher"),
    ("cagr", "CAGR", "higher"),
    ("sharpe", "Sharpe", "higher"),
    ("sortino", "Sortino", "higher"),
    ("max_drawdown", "Max drawdown", "higher"),  # negative: closer to zero wins
    ("calmar", "Calmar", "higher"),
    ("win_rate", "Win rate", "higher"),
    ("profit_factor", "Profit factor", "higher"),
    ("trade_count", "Trades", None),
    ("turnover", "Turnover (annual)", None),
    ("exposure", "Avg exposure", None),
    ("total_costs", "Total costs", "lower"),
    ("cost_drag", "Cost drag", "lower"),
    ("annualized_volatility", "Volatility", "lower"),
    ("final_equity", "Final equity", "higher"),
)


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """One entry in a comparison."""

    strategy: str
    parameters: dict[str, Any] = field(default_factory=dict)
    label: str | None = None

    def display_label(self) -> str:
        if self.label:
            return self.label
        if not self.parameters:
            return self.strategy
        # Two runs of the same strategy with different parameters must not
        # collapse into one indistinguishable column.
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.parameters.items()))
        return f"{self.strategy} ({detail})"


def _unique_labels(specs: list[StrategySpec]) -> list[str]:
    """Labels that are guaranteed distinct.

    Labels key every map in the response. A duplicate would silently overwrite a
    column and the user would see one fewer strategy than they asked for.
    """
    labels: list[str] = []
    seen: dict[str, int] = {}
    for spec in specs:
        base = spec.display_label()
        count = seen.get(base, 0)
        seen[base] = count + 1
        labels.append(base if count == 0 else f"{base} #{count + 1}")
    return labels


def _buy_and_hold(data: MarketDataResult, config: BacktestConfig) -> dict[str, Any]:
    """A buy-and-hold reference for the same instrument and window.

    Without it a user cannot tell whether a strategy earned its complexity. A
    strategy that returns 30% looks good until you learn holding returned 79%.

    Costs are charged for the single entry and the single exit, so the reference
    is measured on the same footing as the strategies rather than being a
    frictionless number that flatters it.
    """
    frame = data.series.frame
    if len(frame) < 2:
        return {"available": False, "reason": "Fewer than two bars in the window."}

    costs = config.cost_model
    entry_price = costs.fill_price(float(frame.iloc[0]["open"]), 1)
    exit_price = costs.fill_price(float(frame.iloc[-1]["close"]), -1)
    if entry_price <= 0:
        return {"available": False, "reason": "The opening price is not positive."}

    quantity = float(int(config.initial_cash / entry_price))
    if quantity <= 0:
        return {
            "available": False,
            "reason": (
                f"Starting capital of {config.initial_cash:,.0f} cannot buy a whole unit at "
                f"{entry_price:,.2f}."
            ),
        }

    entry_commission = costs.commission(quantity * entry_price)
    exit_commission = costs.commission(quantity * exit_price)
    cash_left = config.initial_cash - quantity * entry_price - entry_commission
    final_equity = cash_left + quantity * exit_price - exit_commission

    return {
        "available": True,
        "label": "Buy and hold",
        "total_return": final_equity / config.initial_cash - 1.0,
        "final_equity": final_equity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "total_costs": entry_commission + exit_commission,
        "note": (
            "Bought at the first bar's open and sold at the last bar's close, paying the same "
            "commission, slippage and spread as the strategies. Fully invested throughout."
        ),
    }


def compare_strategies(
    symbol: str,
    specs: list[StrategySpec],
    start: str,
    end: str,
    *,
    interval: str = "1d",
    config: BacktestConfig | None = None,
) -> dict[str, Any]:
    """Run several strategies over one instrument and window, side by side.

    Raises:
        InvalidRequestError: if fewer than two strategies are supplied. A
            "comparison" of one is a backtest, and returning one silently would
            hide a caller's mistake.
    """
    if len(specs) < 2:
        raise InvalidRequestError(
            f"A comparison needs at least two strategies, got {len(specs)}. "
            "Use the backtest endpoint to run a single strategy."
        )

    cfg = config or BacktestConfig()
    labels = _unique_labels(specs)

    log.info(
        "strategy comparison started",
        extra={"symbol": symbol, "strategies": [s.strategy for s in specs], "bars_from": start},
    )

    # Fetched once. Every strategy — and every auxiliary leg — is served from
    # here, so the comparison cannot be contaminated by a revised download.
    with stage(log, "data_retrieval", symbol=symbol):
        data = get_prices(symbol, start, end, interval)
    auxiliary_cache: dict[str, MarketDataResult] = {}

    runs: list[tuple[str, StrategySpec, BacktestRun]] = []
    failures: list[dict[str, Any]] = []
    for label, spec in zip(labels, specs, strict=True):
        try:
            run = run_backtest_for_symbol(
                symbol,
                spec.strategy,
                start,
                end,
                parameters=spec.parameters,
                interval=interval,
                config=cfg,
                market_data=data,
                auxiliary_cache=auxiliary_cache,
            )
        except Exception as exc:
            # One strategy failing must not lose the others' results. The
            # failure is reported in the response rather than swallowed, so the
            # user sees a comparison with a named gap instead of a shorter list
            # they might read as "these are all of them".
            log.warning(
                "strategy failed during comparison",
                extra={"label": label, "strategy": spec.strategy, "error": str(exc)},
            )
            failures.append(
                {
                    "label": label,
                    "strategy": spec.strategy,
                    "parameters": spec.parameters,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
            continue
        runs.append((label, spec, run))

    if not runs:
        raise InvalidRequestError(
            "Every strategy in the comparison failed. First error: " + failures[0]["error"]
        )

    entries = []
    equity_curves: dict[str, list[dict[str, Any]]] = {}
    drawdown_curves: dict[str, list[dict[str, Any]]] = {}
    for label, _spec, run in runs:
        payload = build_result(run).as_dict()
        entries.append(
            {
                "label": label,
                **summarize_result(payload),
                "signal_summary": payload["signal_summary"],
            }
        )
        equity_curves[label] = payload["equity_curve"]
        drawdown_curves[label] = payload["drawdown_curve"]

    metric_table = _build_metric_table(entries)
    reference = _reference_config(runs[0][2])

    return {
        "symbol": symbol,
        "interval": interval,
        "start": start,
        "end": end,
        # One data_version for the whole comparison: it is what makes "the same
        # bars" a checkable claim rather than an assurance.
        "data_version": data.data_version,
        "provenance": data.series.provenance.as_dict(),
        "data_quality": data.quality.as_dict(),
        "cost_model": reference["cost_model"],
        "backtest_config": reference["backtest_config"],
        "assumptions": reference["assumptions"],
        "strategies": entries,
        "metric_table": metric_table,
        "equity_curves": equity_curves,
        "drawdown_curves": drawdown_curves,
        "benchmark": _buy_and_hold(data, cfg),
        "failures": failures,
    }


def _reference_config(run: BacktestRun) -> dict[str, Any]:
    """Assumptions are stated once for the comparison, not per column.

    Every strategy runs under the identical config, so repeating it per row
    would invite a reader to think they might differ.
    """
    return {
        "cost_model": run.config.cost_model.as_dict(),
        "backtest_config": run.config.as_dict(),
        "assumptions": run.config.assumptions(),
    }


def _build_metric_table(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Metrics pivoted into rows, with the winner marked where one exists."""
    table: list[dict[str, Any]] = []
    for key, label, direction in COMPARISON_METRICS:
        values: dict[str, float | None] = {}
        unavailable: dict[str, str] = {}
        for entry in entries:
            metrics = entry["metrics"]
            values[entry["label"]] = metrics.get(key)
            if metrics.get(key) is None:
                reason = metrics.get("unavailable", {}).get(key)
                if reason:
                    unavailable[entry["label"]] = reason

        best: str | None = None
        if direction is not None:
            comparable = {k: v for k, v in values.items() if v is not None}
            # A single comparable value has nothing to win against; declaring it
            # the best would be a meaningless highlight.
            if len(comparable) > 1:
                best = (
                    max(comparable, key=lambda k: comparable[k])
                    if direction == "higher"
                    else min(comparable, key=lambda k: comparable[k])
                )

        table.append(
            {
                "metric": key,
                "label": label,
                "values": values,
                "better": direction,
                "best": best,
                "unavailable": unavailable,
            }
        )
    return table

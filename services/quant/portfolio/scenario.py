"""What-if scenario analysis (architecture.md §10, requirements.md FR7).

    current portfolio -> modify one or more weights -> recalculate risk
                       -> compare before/after

The response carries **both states in full** plus a per-metric delta. Returning
only the delta would leave a user unable to tell a 0.02 drop in Sharpe from 1.90
to 1.88 apart from the same drop from 0.03 to 0.01, and returning only the new
state would make them re-run the original to have anything to compare against.

Both states are measured over one fetch of one aligned window, so a difference
in the table is a difference in the weights and can be nothing else.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from services.quant.data.errors import InvalidRequestError
from services.quant.data.service import get_prices
from services.quant.data.universe import get_instrument
from services.quant.portfolio.holdings import Portfolio
from services.quant.portfolio.risk import (
    DEFAULT_BENCHMARK,
    RISK_METRICS,
    PriceLoader,
    analyze,
    load_window,
)

log = logging.getLogger(__name__)

#: Weights closer together than this are treated as unchanged. Floating-point
#: noise from normalising a weight vector is several orders of magnitude below
#: it, and no user ever means to reweight a position by 1e-9.
WEIGHT_EPSILON = 1e-9


def validate_overrides(overrides: Mapping[str, float]) -> None:
    """Reject a weight map that is malformed regardless of the portfolio.

    Separate from :func:`resolve_weights` so a caller can fail on a bad request
    *before* spending a network fetch on it. It deliberately does not check that
    the weights normalise: "set TCS.NS to 0" is a legitimate scenario whose
    override map sums to zero, and only the combined vector can say whether the
    resulting portfolio is empty.

    Raises:
        InvalidRequestError: with a message specific to the failure, because
            each one has a different fix — a negative weight is a short position
            (not modelled), a non-finite weight is a malformed request, and an
            empty map is a scenario that changes nothing.
    """
    if not overrides:
        raise InvalidRequestError(
            "A scenario must change at least one weight. Supply `weights` as a map of "
            'symbol to target weight, for example {"TCS.NS": 0.25}.'
        )

    invalid = {
        symbol: value
        for symbol, value in overrides.items()
        if not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    }
    if invalid:
        detail = ", ".join(f"{s}={v!r}" for s, v in sorted(invalid.items()))
        raise InvalidRequestError(f"Scenario weights must be finite numbers; got {detail}.")

    negative = {s: float(v) for s, v in overrides.items() if float(v) < 0}
    if negative:
        detail = ", ".join(f"{s}={v:g}" for s, v in sorted(negative.items()))
        raise InvalidRequestError(
            f"Scenario weights must be zero or positive; got {detail}. A negative weight is a "
            "short position, which the portfolio engine does not model, and a weight vector "
            "containing one has no single sensible normalisation."
        )

    for symbol in overrides:
        # Resolve now so an unknown symbol is reported as a bad request rather
        # than surfacing later as a failed price fetch.
        get_instrument(symbol)


def resolve_weights(
    base_weights: Mapping[str, float], overrides: Mapping[str, float]
) -> tuple[dict[str, float], list[str]]:
    """Apply weight overrides to the current allocation and normalise.

    Returns the normalised weight vector and the plain-language notes that must
    travel with the result.

    Raises:
        InvalidRequestError: when the requested weights cannot be normalised
            into a portfolio — either because the override map is malformed
            (see :func:`validate_overrides`) or because the combined vector is
            all zero, which is not a portfolio.
    """
    validate_overrides(overrides)

    notes: list[str] = []
    combined: dict[str, float] = {s: float(w) for s, w in base_weights.items()}
    for symbol, value in overrides.items():
        combined[symbol] = float(value)

    total = sum(combined.values())
    if total <= WEIGHT_EPSILON:
        raise InvalidRequestError(
            "The scenario's weights sum to 0, so they cannot be normalised into a portfolio. "
            "At least one holding must carry a positive weight."
        )

    if abs(total - 1.0) > WEIGHT_EPSILON:
        notes.append(
            f"The requested weights summed to {total:.4f}; every weight was divided by that sum "
            "so the scenario portfolio is fully invested and its weights total 1."
        )

    normalised = {symbol: weight / total for symbol, weight in combined.items()}
    return normalised, notes


def describe_changes(
    base_weights: Mapping[str, float], scenario_weights: Mapping[str, float]
) -> list[dict[str, Any]]:
    """One entry per symbol whose weight moved, saying what kind of move it was.

    Additions and removals are called out by name rather than being left for the
    reader to spot by diffing two weight tables: "TCS.NS is not currently held"
    is exactly the kind of thing a user needs told, because a scenario that
    silently introduced a position would answer a question they did not ask.
    """
    changes: list[dict[str, Any]] = []
    for symbol in sorted(set(base_weights) | set(scenario_weights)):
        before = float(base_weights.get(symbol, 0.0))
        after = float(scenario_weights.get(symbol, 0.0))
        if abs(after - before) <= WEIGHT_EPSILON:
            continue
        if before <= WEIGHT_EPSILON:
            kind = "added"
            note = (
                f"{symbol} is not in the current portfolio, so the scenario treats it as a new "
                f"position at {after:.2%}."
            )
        elif after <= WEIGHT_EPSILON:
            kind = "removed"
            note = f"{symbol} is zeroed out: the scenario sells the whole {before:.2%} position."
        else:
            kind = "reweighted"
            note = f"{symbol} moves from {before:.2%} to {after:.2%}."
        changes.append(
            {
                "symbol": symbol,
                "change": kind,
                "weight_before": before,
                "weight_after": after,
                "delta": after - before,
                "note": note,
            }
        )
    return changes


def metric_deltas(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per-metric before/after with the change, and why a change is missing.

    A delta is ``None`` whenever either side is unavailable. Treating an
    unavailable metric as zero would report a scenario as "improving Sharpe by
    1.4" when the truth is that one of the two Sharpes does not exist.
    """
    before_unavailable = dict(before.get("unavailable") or {})
    after_unavailable = dict(after.get("unavailable") or {})

    rows: list[dict[str, Any]] = []
    for key, label, direction in RISK_METRICS:
        left = before.get(key)
        right = after.get(key)
        delta: float | None = None
        improved: bool | None = None
        reason: str | None = None

        if isinstance(left, int | float) and isinstance(right, int | float):
            delta = float(right) - float(left)
            if direction is not None and abs(delta) > WEIGHT_EPSILON:
                improved = delta > 0 if direction == "higher" else delta < 0
        else:
            missing = []
            if not isinstance(left, int | float):
                missing.append(f"before: {before_unavailable.get(key, 'not available')}")
            if not isinstance(right, int | float):
                missing.append(f"after: {after_unavailable.get(key, 'not available')}")
            reason = "; ".join(missing)

        rows.append(
            {
                "metric": key,
                "label": label,
                "before": left if isinstance(left, int | float) else None,
                "after": right if isinstance(right, int | float) else None,
                "delta": delta,
                "better": direction,
                "improved": improved,
                "unavailable": reason,
            }
        )
    return rows


def run_scenario(
    portfolio: Portfolio,
    weights: Mapping[str, float],
    start: str,
    end: str,
    *,
    interval: str = "1d",
    benchmark: str | None = None,
    risk_free_rate: float = 0.0,
    loader: PriceLoader = get_prices,
) -> dict[str, Any]:
    """Reweight a portfolio, recalculate its risk, and compare before/after."""
    resolved_benchmark = benchmark or portfolio.benchmark or DEFAULT_BENCHMARK

    # Reject a malformed weight map before spending a network fetch on it. The
    # full resolution needs the base allocation, which needs prices.
    validate_overrides(weights)

    scenario_symbols = [s for s in weights if s not in set(portfolio.symbols)]
    window = load_window(
        [*portfolio.symbols, *scenario_symbols],
        start,
        end,
        interval=interval,
        benchmark=resolved_benchmark,
        loader=loader,
    )

    kept = set(window.symbols)
    base_holdings = tuple(h for h in portfolio.holdings if h.symbol in kept)
    if not base_holdings:
        raise InvalidRequestError(
            "Every holding in the current portfolio was excluded for insufficient history, so "
            "there is no 'before' state to compare a scenario against."
        )
    base_portfolio = Portfolio(
        holdings=base_holdings,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        benchmark=resolved_benchmark,
    )

    before = analyze(base_portfolio, window, risk_free_rate=risk_free_rate)

    usable_overrides = {s: w for s, w in weights.items() if s in kept}
    ignored = sorted(set(weights) - kept)
    scenario_weights, notes = resolve_weights(before["portfolio"]["weights"], usable_overrides)
    if ignored:
        notes.append(
            "Ignored in the scenario because they have too little history in this window: "
            + ", ".join(ignored)
        )

    prices = window.prices_as_of()
    scenario_portfolio = Portfolio.from_weights(
        scenario_weights,
        prices,
        before["portfolio"]["total_value"],
        name=f"{portfolio.name} (scenario)".strip(),
        base_currency=portfolio.base_currency,
        benchmark=resolved_benchmark,
    )
    after = analyze(scenario_portfolio, window, risk_free_rate=risk_free_rate)

    log.info(
        "scenario evaluated",
        extra={
            "holdings_before": len(base_portfolio.holdings),
            "holdings_after": len(scenario_portfolio.holdings),
            "overrides": len(usable_overrides),
        },
    )

    return {
        "as_of": before["as_of"],
        "start": start,
        "end": end,
        "interval": interval,
        "benchmark": resolved_benchmark,
        "base_currency": portfolio.base_currency,
        "requested_weights": {s: float(w) for s, w in weights.items()},
        "scenario_weights": scenario_weights,
        "changes": describe_changes(before["portfolio"]["weights"], scenario_weights),
        "before": before,
        "after": after,
        "deltas": metric_deltas(before["metrics"], after["metrics"]),
        "notes": notes,
        "assumptions": [
            *before["assumptions"],
            "The scenario holds the portfolio's total market value constant: it reallocates "
            "the same money rather than adding or withdrawing any.",
            "Both states are measured over one fetch of the same aligned bars, so every "
            "difference in the table is caused by the reweighting and by nothing else.",
            "Scenario quantities are fractional. The scenario answers 'what if the allocation "
            "looked like this', so it is not rounded to whole tradable units and no transaction "
            "cost is charged for getting there.",
        ],
    }

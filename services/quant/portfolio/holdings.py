"""Holdings, allocation and position concentration (requirements.md FR7).

This module knows nothing about market history. It answers the questions that
only need *today's* prices — what is each position worth, what fraction of the
portfolio is it, how concentrated is the whole thing, and how is it spread
across sectors. The time-series questions (volatility, beta, drawdown) live in
`risk.py`, which builds on top of this.

Keeping the split means a user can inspect an allocation without waiting for a
multi-year download, and it means the scenario engine can rebuild a portfolio
from a weight vector without re-deriving any market statistics.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.quant.data.errors import InvalidRequestError
from services.quant.data.universe import get_instrument

#: How many of the largest positions the "top N" concentration figure covers.
#: Three is small enough that a concentrated book cannot hide behind it and
#: large enough to be meaningful for the seven-instrument MVP universe.
DEFAULT_TOP_N = 3

#: Below this the total market value is treated as zero rather than as a very
#: small positive number that would produce enormous weights.
EPSILON = 1e-9

#: Label used for the weight whose sector is not known. It is a visible bucket
#: rather than a silent omission: a sector breakdown that quietly sums to 62%
#: invites the reader to assume the missing 38% is spread like the rest.
UNCLASSIFIED = "Unclassified"


@dataclass(frozen=True, slots=True)
class Holding:
    """One position: how much of what, and optionally what it cost.

    ``quantity`` must be positive. Short positions are deliberately not modelled
    here: the scenario engine's contract is a vector of non-negative weights
    that normalise to 1 (architecture.md §10), and a book containing a negative
    weight has no single sensible normalisation — the same "50% short" reads as
    a different fraction of the portfolio depending on whether you divide by net
    or gross exposure. Rejecting it is honest; picking one convention silently
    would not be.
    """

    symbol: str
    quantity: float
    cost_basis: float | None = None
    """Average price paid per unit, if the user recorded it. Optional because a
    portfolio is useful for risk analysis whether or not its cost is known, and
    inventing a cost basis would fabricate a P&L."""

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidRequestError("A holding needs a symbol.")
        # Resolving now rather than at pricing time means an unknown symbol is
        # reported while the user is still looking at the portfolio form,
        # instead of surfacing several seconds later as a data-fetch failure.
        get_instrument(self.symbol)
        if not math.isfinite(self.quantity):
            raise InvalidRequestError(
                f"Quantity for {self.symbol} must be a finite number, got {self.quantity!r}."
            )
        if self.quantity <= 0:
            raise InvalidRequestError(
                f"Quantity for {self.symbol} must be positive, got {self.quantity}. "
                "Short positions are not modelled by the portfolio engine."
            )
        if self.cost_basis is not None and (
            not math.isfinite(self.cost_basis) or self.cost_basis < 0
        ):
            raise InvalidRequestError(
                f"Cost basis for {self.symbol} must be a non-negative number, "
                f"got {self.cost_basis!r}."
            )

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "quantity": self.quantity, "cost_basis": self.cost_basis}


@dataclass(frozen=True, slots=True)
class PositionValue:
    """One holding priced at an as-of date."""

    symbol: str
    quantity: float
    price: float
    market_value: float
    weight: float
    cost_basis: float | None
    unrealized_pnl: float | None
    """None — not 0.0 — when no cost basis was recorded. There is no P&L to
    report, which is a different statement from "it broke even"."""

    unrealized_return: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": self.price,
            "market_value": self.market_value,
            "weight": self.weight,
            "cost_basis": self.cost_basis,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_return": self.unrealized_return,
        }


@dataclass(frozen=True, slots=True)
class Concentration:
    """How much of the portfolio rides on how few positions."""

    largest_symbol: str
    largest_weight: float
    top_n: int
    top_n_weight: float
    top_n_symbols: tuple[str, ...]
    hhi: float
    """Herfindahl-Hirschman index: the sum of squared weights.

    It exists because `largest_weight` answers exactly one question — how big is
    the single biggest bet — and is blind to everything behind it. A book of one
    25% position plus fifteen 5% positions and a book of four 25% positions have
    the same largest weight and are not remotely as diversified as each other.
    HHI reads the whole distribution: it is 1.0 when everything sits in one
    name and 1/n when n positions are equally weighted, so it moves whenever the
    *shape* of the allocation changes rather than only its peak."""

    effective_holdings: float
    """1 / HHI — the number of equally-weighted positions that would be as
    concentrated as this portfolio. It is the same information as HHI expressed
    in a unit a person can hold in their head ("this behaves like 2.6 stocks")."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "largest_symbol": self.largest_symbol,
            "largest_weight": self.largest_weight,
            "top_n": self.top_n,
            "top_n_weight": self.top_n_weight,
            "top_n_symbols": list(self.top_n_symbols),
            "hhi": self.hhi,
            "effective_holdings": self.effective_holdings,
        }


@dataclass(frozen=True, slots=True)
class SectorExposure:
    """Weight by sector, with the unclassified remainder stated out loud."""

    sectors: dict[str, float]
    classified_weight: float
    unclassified_weight: float
    unclassified_symbols: tuple[str, ...]
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sectors": self.sectors,
            "classified_weight": self.classified_weight,
            "unclassified_weight": self.unclassified_weight,
            "unclassified_symbols": list(self.unclassified_symbols),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Allocation:
    """A portfolio priced at one moment: values, weights and concentration."""

    positions: tuple[PositionValue, ...]
    total_value: float
    total_cost: float | None
    weights: dict[str, float]
    concentration: Concentration
    sector_exposure: SectorExposure

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_value": self.total_value,
            "total_cost": self.total_cost,
            "positions": [p.as_dict() for p in self.positions],
            "weights": self.weights,
            "concentration": self.concentration.as_dict(),
            "sector_exposure": self.sector_exposure.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class Portfolio:
    """A set of holdings, at most one row per symbol.

    Duplicate symbols are rejected rather than summed: two rows for the same
    instrument almost always mean the user pasted the same line twice, and
    silently merging them would hide that while changing every weight.
    """

    holdings: tuple[Holding, ...]
    name: str = ""
    base_currency: str = "INR"
    benchmark: str = "NIFTY"

    def __post_init__(self) -> None:
        if not self.holdings:
            raise InvalidRequestError("A portfolio needs at least one holding.")
        counts = Counter(h.symbol for h in self.holdings)
        duplicates = sorted(symbol for symbol, count in counts.items() if count > 1)
        if duplicates:
            raise InvalidRequestError(
                f"Duplicate holding(s): {', '.join(duplicates)}. "
                "Combine them into one row with the total quantity."
            )
        get_instrument(self.benchmark)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(h.symbol for h in self.holdings)

    def by_symbol(self) -> dict[str, Holding]:
        return {h.symbol: h for h in self.holdings}

    def value(self, prices: Mapping[str, float], *, top_n: int = DEFAULT_TOP_N) -> Allocation:
        """Price every holding and derive weights, concentration and sectors.

        Args:
            prices: One price per holding symbol, as of the same moment. A
                missing or non-positive price is fatal rather than skipped: a
                portfolio valued from a subset of its holdings would report
                weights that are wrong for every position, not just the missing
                one (NFR5.1).
        """
        missing = sorted(s for s in self.symbols if s not in prices)
        if missing:
            raise InvalidRequestError(
                f"No price available for {', '.join(missing)}, so the portfolio cannot be valued. "
                "Every holding must be priced at the same moment for weights to mean anything."
            )
        bad = sorted(
            s for s in self.symbols if not math.isfinite(float(prices[s])) or float(prices[s]) <= 0
        )
        if bad:
            raise InvalidRequestError(
                f"Non-positive or non-finite price for {', '.join(bad)}; the portfolio cannot be valued."
            )

        market_values = {h.symbol: h.quantity * float(prices[h.symbol]) for h in self.holdings}
        total_value = sum(market_values.values())
        if total_value <= EPSILON:
            raise InvalidRequestError(
                "The portfolio's total market value is zero, so allocation weights are undefined."
            )

        costs = [h.quantity * h.cost_basis for h in self.holdings if h.cost_basis is not None]
        # Only a portfolio where *every* holding has a cost basis has a
        # meaningful total cost; a partial sum would be compared against the
        # full market value and would understate the gain.
        total_cost = float(sum(costs)) if len(costs) == len(self.holdings) else None

        positions = tuple(
            _position_value(h, float(prices[h.symbol]), market_values[h.symbol], total_value)
            for h in self.holdings
        )
        weights = {p.symbol: p.weight for p in positions}

        return Allocation(
            positions=positions,
            total_value=float(total_value),
            total_cost=total_cost,
            weights=weights,
            concentration=concentration(weights, top_n=top_n),
            sector_exposure=sector_exposure(weights),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_currency": self.base_currency,
            "benchmark": self.benchmark,
            "holdings": [h.as_dict() for h in self.holdings],
        }

    @classmethod
    def from_dicts(
        cls,
        holdings: Iterable[Mapping[str, Any]],
        *,
        name: str = "",
        base_currency: str = "INR",
        benchmark: str = "NIFTY",
    ) -> Portfolio:
        """Build from the wire shape used by the Go API and the MCP layer."""
        parsed: list[Holding] = []
        for index, entry in enumerate(holdings):
            symbol = str(entry.get("symbol", "")).strip()
            if not symbol:
                raise InvalidRequestError(f"holdings[{index}] is missing a symbol.")
            raw_quantity = entry.get("quantity")
            if raw_quantity is None:
                raise InvalidRequestError(f"holdings[{index}] ({symbol}) is missing a quantity.")
            cost_basis = entry.get("cost_basis")
            parsed.append(
                Holding(
                    symbol=symbol,
                    quantity=float(raw_quantity),
                    cost_basis=None if cost_basis is None else float(cost_basis),
                )
            )
        return cls(
            holdings=tuple(parsed), name=name, base_currency=base_currency, benchmark=benchmark
        )

    @classmethod
    def from_weights(
        cls,
        weights: Mapping[str, float],
        prices: Mapping[str, float],
        total_value: float,
        *,
        name: str = "",
        base_currency: str = "INR",
        benchmark: str = "NIFTY",
    ) -> Portfolio:
        """Rebuild a portfolio that holds ``weights`` of ``total_value``.

        This is how a scenario turns a reweighting back into holdings. Zero
        weights are dropped rather than kept as zero-quantity rows, because a
        position the scenario removed is not a position.

        Quantities are fractional here, unlike the backtester's whole-unit
        orders: the scenario is a "what if the allocation looked like this"
        question, not a simulated trade, and rounding to whole units would move
        every weight away from the one the user asked about.
        """
        holdings = [
            Holding(symbol=symbol, quantity=weight * total_value / float(prices[symbol]))
            for symbol, weight in weights.items()
            if weight > 0
        ]
        if not holdings:
            raise InvalidRequestError("The scenario leaves the portfolio with no positions.")
        return cls(
            holdings=tuple(holdings), name=name, base_currency=base_currency, benchmark=benchmark
        )


def _position_value(
    holding: Holding, price: float, market_value: float, total_value: float
) -> PositionValue:
    pnl: float | None = None
    pnl_return: float | None = None
    if holding.cost_basis is not None:
        cost = holding.quantity * holding.cost_basis
        pnl = market_value - cost
        # A zero cost basis (a gift, a bonus issue recorded at nil) has no
        # denominator, so the percentage return does not exist even though the
        # rupee P&L does.
        pnl_return = pnl / cost if cost > EPSILON else None
    return PositionValue(
        symbol=holding.symbol,
        quantity=holding.quantity,
        price=price,
        market_value=market_value,
        weight=market_value / total_value,
        cost_basis=holding.cost_basis,
        unrealized_pnl=pnl,
        unrealized_return=pnl_return,
    )


def concentration(weights: Mapping[str, float], *, top_n: int = DEFAULT_TOP_N) -> Concentration:
    """Largest weight, top-N weight and HHI for a weight vector."""
    if not weights:
        raise InvalidRequestError("Concentration needs at least one weight.")
    ordered: Sequence[tuple[str, float]] = sorted(
        weights.items(), key=lambda item: (-item[1], item[0])
    )
    effective_n = min(top_n, len(ordered))
    top = ordered[:effective_n]
    hhi = float(sum(w * w for _, w in weights.items()))
    return Concentration(
        largest_symbol=ordered[0][0],
        largest_weight=float(ordered[0][1]),
        top_n=effective_n,
        top_n_weight=float(sum(w for _, w in top)),
        top_n_symbols=tuple(symbol for symbol, _ in top),
        hhi=hhi,
        # HHI is > 0 whenever any weight is non-zero, and `value` already
        # refuses to build an all-zero allocation, so this cannot divide by zero.
        effective_holdings=float(1.0 / hhi) if hhi > EPSILON else float("nan"),
    )


def sector_exposure(weights: Mapping[str, float]) -> SectorExposure:
    """Weight by sector, reporting the unclassified remainder explicitly.

    `Instrument.sector` is populated for equities and None for indices
    (requirements.md FR7, "where data permits"). An index holding is therefore
    neither dropped nor forced into a sector it does not belong to — it is
    counted under `unclassified_weight` and named, so the reader can see exactly
    how much of the breakdown is missing and why.
    """
    sectors: dict[str, float] = {}
    unclassified: list[str] = []
    unclassified_weight = 0.0

    for symbol, weight in weights.items():
        sector = get_instrument(symbol).sector
        if sector is None:
            unclassified.append(symbol)
            unclassified_weight += float(weight)
            continue
        sectors[sector] = sectors.get(sector, 0.0) + float(weight)

    if unclassified_weight > EPSILON:
        sectors[UNCLASSIFIED] = unclassified_weight

    classified = float(sum(w for name, w in sectors.items() if name != UNCLASSIFIED))
    note = (
        f"{unclassified_weight:.1%} of the portfolio has no sector in the instrument "
        f"universe ({', '.join(sorted(unclassified))}) and is reported as "
        f"'{UNCLASSIFIED}' rather than being dropped."
        if unclassified
        else "Every holding has a sector in the instrument universe."
    )
    return SectorExposure(
        sectors=dict(sorted(sectors.items())),
        classified_weight=classified,
        unclassified_weight=float(unclassified_weight),
        unclassified_symbols=tuple(sorted(unclassified)),
        note=note,
    )

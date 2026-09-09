"""Errors raised by the data layer.

The layer never silently repairs or drops bad market data (requirements.md
NFR5.1). Every failure below is specific enough that a user reading it knows
which symbol, which rule and which rows were involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DataError(Exception):
    """Base class for every data-layer failure."""

    def as_details(self) -> dict[str, Any]:
        """Structured payload surfaced through the API's `details` field."""
        return {}


class UnknownSymbolError(DataError):
    """The requested symbol is not part of the configured universe."""

    def __init__(self, symbol: str, known: tuple[str, ...]) -> None:
        super().__init__(
            f"Unknown symbol {symbol!r}. Known symbols: {', '.join(known)}. "
            "Symbols outside the configured universe are rejected rather than "
            "passed through to the provider untested."
        )
        self.symbol = symbol
        self.known = known

    def as_details(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "known_symbols": list(self.known)}


class UnknownUniverseError(DataError):
    """The requested universe name is not configured."""

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        super().__init__(f"Unknown universe {name!r}. Known universes: {', '.join(known)}.")
        self.name = name
        self.known = known

    def as_details(self) -> dict[str, Any]:
        return {"universe": self.name, "known_universes": list(self.known)}


class InvalidRequestError(DataError):
    """The request itself is malformed (bad date range, unsupported interval)."""


class ProviderError(DataError):
    """The upstream provider failed, rate-limited us, or returned nothing.

    Kept distinct from validation failures: this one means *we could not get
    data*, which the user resolves by retrying or checking the symbol — not by
    doubting the data they already have.
    """

    def __init__(
        self, message: str, *, symbol: str | None = None, provider: str = "yfinance"
    ) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.provider = provider

    def as_details(self) -> dict[str, Any]:
        return {"provider": self.provider, "symbol": self.symbol}


@dataclass(frozen=True, slots=True)
class Violation:
    """One failed validation rule, located precisely enough to inspect."""

    rule: str
    message: str
    row_count: int
    sample: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "row_count": self.row_count,
            "sample": self.sample,
        }


class DataValidationError(DataError):
    """Retrieved data violated one or more validation rules.

    Raised instead of dropping or patching the offending rows: a backtest run on
    silently repaired data produces a plausible number that is wrong, which is
    the exact failure mode NFR5.6 ranks as worse than no answer at all.
    """

    def __init__(self, symbol: str, violations: list[Violation]) -> None:
        summary = "; ".join(f"{v.rule} ({v.row_count} row(s))" for v in violations)
        super().__init__(
            f"Market data for {symbol} failed validation: {summary}. "
            "The data was not repaired or partially used — fix the source or narrow the date range."
        )
        self.symbol = symbol
        self.violations = violations

    def as_details(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "violations": [v.as_dict() for v in self.violations]}

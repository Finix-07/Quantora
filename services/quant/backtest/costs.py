"""Transaction-cost model.

Reliability rule 2 (requirements.md NFR5.2) forbids hiding transaction-cost
assumptions. Every backtest therefore carries an explicit :class:`CostModel`,
it is serialized into the result and into the saved experiment, and the UI shows
it next to the metrics. A "zero cost" run is possible but must be chosen
deliberately — it is never the default, because a costless backtest is the
single easiest way to make a losing strategy look profitable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class CostModel:
    """What it costs to trade, in basis points of notional unless stated.

    Defaults approximate Indian retail equity/delivery trading through a
    discount broker: roughly 3 bps of brokerage-equivalent friction, a 2 bps
    round-trip spread on liquid large caps, and 5 bps of slippage. They are a
    documented starting point, not a claim about any particular broker — the
    user can and should override them.
    """

    commission_bps: float = 3.0
    """Broker commission per side, in basis points of traded notional."""

    commission_min: float = 0.0
    """Minimum commission per fill, in account currency."""

    slippage_bps: float = 5.0
    """Adverse price movement between the decision and the fill, per side."""

    spread_bps: float = 2.0
    """Full bid-ask spread. Half is paid on each side, which is what crossing
    the spread actually costs."""

    def __post_init__(self) -> None:
        for name in ("commission_bps", "commission_min", "slippage_bps", "spread_bps"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")

    @classmethod
    def zero(cls) -> CostModel:
        """A frictionless model, for isolating strategy logic in tests.

        Never a default. A costless backtest is the easiest way to make a losing
        strategy look profitable.
        """
        return cls(commission_bps=0.0, commission_min=0.0, slippage_bps=0.0, spread_bps=0.0)

    def fill_price(self, reference_price: float, side: int) -> float:
        """The price actually paid or received.

        Args:
            reference_price: the bar price the order is benchmarked against.
            side: +1 to buy, -1 to sell.

        Buying pays slippage and half the spread *up*; selling receives them
        *down*. Both sides are penalised — modelling cost on one side only
        halves the true round-trip drag.
        """
        if side not in (1, -1):
            raise ValueError(f"side must be +1 (buy) or -1 (sell), got {side}")
        penalty = (self.slippage_bps + self.spread_bps / 2.0) / BPS
        return reference_price * (1.0 + side * penalty)

    def commission(self, notional: float) -> float:
        """Commission for one fill of the given absolute notional."""
        return max(self.commission_min, abs(notional) * self.commission_bps / BPS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "commission_bps": self.commission_bps,
            "commission_min": self.commission_min,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> CostModel:
        """Rebuild from a persisted experiment.

        Unknown keys are rejected rather than ignored: a rerun that silently
        drops a cost parameter would produce different numbers while claiming to
        reproduce the original (NFR6).
        """
        if not payload:
            return cls()
        known = {"commission_bps", "commission_min", "slippage_bps", "spread_bps"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"Unknown cost-model field(s): {', '.join(unknown)}. Supported: {', '.join(sorted(known))}."
            )
        return cls(**{k: float(v) for k, v in payload.items()})

    def describe(self) -> str:
        """One-line human summary, shown next to every result."""
        if self == CostModel.zero():
            return "No transaction costs (frictionless — results are optimistic)"
        parts = [
            f"{self.commission_bps:g} bps commission per side",
            f"{self.slippage_bps:g} bps slippage per side",
            f"{self.spread_bps:g} bps spread (half paid per side)",
        ]
        if self.commission_min > 0:
            parts.insert(1, f"min {self.commission_min:g} per fill")
        return ", ".join(parts)

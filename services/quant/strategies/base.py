"""The strategy contract.

architecture.md §5 fixes the shape::

    class Strategy:
        def generate_signals(self, data): ...
        def position_size(self, context): ...
        def exit_signal(self, context): ...

with the invariant that a strategy must be runnable from Python directly, from a
unit test, from a backtest, and through the API **without knowing who called
it**. Nothing in this module imports HTTP, the database, MCP or an LLM, and
nothing ever will.

Time alignment is part of the contract, not an implementation detail. A signal
stamped at bar *t* may only use information available at the close of bar *t*.
The backtester is what decides when that signal is allowed to fill (the next
bar's open, by default) — a strategy never fills its own order, which is how the
look-ahead-bias guarantee (NFR5.3) stays enforceable in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, ClassVar

import pandas as pd

from services.quant.data.types import PriceSeries

SIGNAL_COLUMNS: tuple[str, ...] = ("direction", "strength", "reason")


class SignalDirection(IntEnum):
    """The position a strategy wants to hold from the next bar onwards.

    Deliberately a *target state* rather than an action ("buy"/"sell"): the
    backtester diffs the target against the current position to derive orders,
    so a strategy cannot accidentally double up by emitting BUY twice.
    """

    SHORT = -1
    FLAT = 0
    LONG = 1

    @property
    def label(self) -> str:
        return self.name.lower()


class StrategyError(Exception):
    """Base class for strategy-level failures."""


class InsufficientDataError(StrategyError):
    """The series is shorter than the strategy's warm-up requirement.

    Raised instead of returning signals computed from a partial indicator
    window: a "20-day" average built from 5 bars is a different indicator, and
    trading on it would produce a result that is wrong in a way no metric would
    reveal.
    """

    def __init__(self, strategy: str, required: int, available: int) -> None:
        super().__init__(
            f"Strategy {strategy!r} needs at least {required} bars to warm up its indicators "
            f"but only {available} were supplied. Widen the date range, or lower the "
            "strategy's lookback parameters."
        )
        self.strategy = strategy
        self.required = required
        self.available = available


class InvalidParametersError(StrategyError):
    """Strategy parameters are outside the range the strategy can honour."""


@dataclass(frozen=True, slots=True)
class SignalSet:
    """A strategy's output for one instrument.

    Attributes:
        frame: indexed by timestamp, columns ``direction`` (int, a
            :class:`SignalDirection`), ``strength`` (0..1 conviction) and
            ``reason`` (a short human-readable explanation of *why* this bar
            produced this target).
        indicators: the indicator series the decision was made from. Carried
            alongside so the UI can show a user the exact numbers behind a
            signal (NFR4) instead of asking them to trust it.
    """

    symbol: str
    strategy: str
    parameters: dict[str, Any]
    frame: pd.DataFrame
    indicators: pd.DataFrame = field(default_factory=pd.DataFrame)
    warmup_bars: int = 0

    def __post_init__(self) -> None:
        missing = [c for c in SIGNAL_COLUMNS if c not in self.frame.columns]
        if missing:
            raise ValueError(f"SignalSet for {self.strategy} is missing columns: {missing}")
        if not self.frame.index.is_monotonic_increasing:
            raise ValueError(
                f"SignalSet for {self.strategy} has non-monotonic timestamps; out-of-order "
                "signals would let a later bar drive an earlier decision."
            )

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def directions(self) -> pd.Series:
        return self.frame["direction"]

    def direction_at(self, timestamp: pd.Timestamp) -> SignalDirection:
        return SignalDirection(int(self.frame.loc[timestamp, "direction"]))

    def transitions(self) -> pd.DataFrame:
        """Only the bars where the target position actually changes.

        Useful for display and for asserting in tests that a strategy did not
        emit a continuous stream of identical targets.
        """
        changed = self.frame["direction"].ne(self.frame["direction"].shift())
        return self.frame.loc[changed]


@dataclass(frozen=True, slots=True)
class Position:
    """An open position, as the strategy sees it."""

    symbol: str
    quantity: float
    """Signed: positive is long, negative is short, zero is flat."""

    entry_price: float
    entry_timestamp: pd.Timestamp
    bars_held: int

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


@dataclass(frozen=True, slots=True)
class SizingContext:
    """Everything :meth:`Strategy.position_size` may look at.

    Deliberately a closed set: passing the whole backtester in would let a
    strategy read future bars. A strategy can only see the present bar, its own
    signal, and the portfolio state as of now.
    """

    timestamp: pd.Timestamp
    symbol: str
    direction: SignalDirection
    strength: float
    price: float
    """The price the order is expected to fill near (the next bar's open)."""

    equity: float
    cash: float
    position: Position
    indicators: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExitContext:
    """Everything :meth:`Strategy.exit_signal` may look at."""

    timestamp: pd.Timestamp
    symbol: str
    position: Position
    price: float
    bar: dict[str, float]
    """The current bar's open/high/low/close/volume."""

    unrealized_pnl: float
    unrealized_return: float
    indicators: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """Whether to close a position now, and why.

    The reason is not decoration: it is written into the trade record so the
    journal can analyse performance by exit reason (requirements.md FR8).
    """

    should_exit: bool
    reason: str = ""

    @classmethod
    def hold(cls) -> ExitDecision:
        return cls(should_exit=False)

    @classmethod
    def exit(cls, reason: str) -> ExitDecision:
        return cls(should_exit=True, reason=reason)


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One tunable parameter, described well enough to build a UI control.

    The Strategy Lab (M8.4) needs bounds and a description to render a usable
    form; the AI layer needs them to avoid proposing a nonsensical
    configuration. Both read this rather than hard-coding their own copy.
    """

    name: str
    type: str  # "int" | "float" | "bool" | "str"
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices) if self.choices else None,
        }

    def validate(self, value: Any) -> Any:
        """Coerce and bounds-check one value, raising a specific error."""
        if self.choices is not None and value not in self.choices:
            raise InvalidParametersError(
                f"{self.name} must be one of {list(self.choices)}, got {value!r}"
            )
        try:
            if self.type == "int":
                if isinstance(value, bool) or (
                    isinstance(value, float) and not float(value).is_integer()
                ):
                    raise ValueError
                value = int(value)
            elif self.type == "float":
                value = float(value)
            elif self.type == "bool":
                value = bool(value)
            elif self.type == "str":
                # Not str(value): coercing would silently accept a number or a
                # dict and turn it into a plausible-looking string. A parameter
                # naming an instrument has to be rejected when it is not one,
                # or the saved experiment records something that was never used.
                if not isinstance(value, str):
                    raise ValueError
                value = value.strip()
                if not value:
                    raise ValueError
        except (TypeError, ValueError):
            raise InvalidParametersError(
                f"{self.name} must be a non-empty {self.type}, got {value!r}"
                if self.type == "str"
                else f"{self.name} must be a {self.type}, got {value!r}"
            ) from None

        # Numeric bounds do not apply to strings; comparing a str against a
        # number raises TypeError rather than reporting a useful message.
        if isinstance(value, str):
            return value

        if self.minimum is not None and value < self.minimum:
            raise InvalidParametersError(f"{self.name} must be >= {self.minimum}, got {value}")
        if self.maximum is not None and value > self.maximum:
            raise InvalidParametersError(f"{self.name} must be <= {self.maximum}, got {value}")
        return value


class Strategy(ABC):
    """Base class every strategy family implements.

    Subclasses declare :attr:`name`, :attr:`family`, :attr:`description` and
    :attr:`parameter_specs`, then implement :meth:`generate_signals`.
    :meth:`position_size` and :meth:`exit_signal` have workable defaults so a
    simple strategy does not have to restate them.
    """

    name: ClassVar[str] = ""
    family: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameter_specs: ClassVar[tuple[ParameterSpec, ...]] = ()

    #: Whether the engine should re-size an already-correct position on every
    #: bar. False for signal-driven strategies: once positioned as intended,
    #: re-sizing because equity or conviction drifted pays a full round trip in
    #: costs for no change of view. A strategy that genuinely targets a
    #: continuously varying exposure (a volatility-targeted or hedged-ratio
    #: strategy) sets this to True.
    rebalances_continuously: ClassVar[bool] = False

    #: Fraction of equity committed to a full-conviction position by the default
    #: sizing rule. 0.95 rather than 1.0 leaves headroom for costs, so an order
    #: sized at the previous close does not get rejected for insufficient cash
    #: when the next open gaps up.
    default_allocation: ClassVar[float] = 0.95

    def __init__(self, **parameters: Any) -> None:
        specs = {spec.name: spec for spec in self.parameter_specs}
        unknown = sorted(set(parameters) - set(specs))
        if unknown:
            raise InvalidParametersError(
                f"Unknown parameter(s) for {self.name}: {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(specs)) or 'none'}."
            )

        resolved: dict[str, Any] = {}
        for spec in self.parameter_specs:
            raw = parameters.get(spec.name, spec.default)
            resolved[spec.name] = spec.validate(raw)
        self._parameters = resolved
        self._validate_parameters()

    def _validate_parameters(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Hook for cross-parameter rules (e.g. fast span < slow span).

        Intentionally concrete and empty: most strategies have no cross-parameter
        rule, and forcing every subclass to write `pass` would add noise.
        """

    @property
    def parameters(self) -> dict[str, Any]:
        """The resolved parameters, including defaults.

        Returned as a copy and persisted with every experiment, so a saved
        result records what actually ran rather than what the caller typed.
        """
        return dict(self._parameters)

    @property
    def warmup_bars(self) -> int:
        """Bars consumed before the first tradable signal. 0 if none."""
        return 0

    def require_warmup(self, series: PriceSeries) -> None:
        """Raise :class:`InsufficientDataError` if the series is too short."""
        if len(series) < self.warmup_bars:
            raise InsufficientDataError(self.name, self.warmup_bars, len(series))

    @abstractmethod
    def generate_signals(self, series: PriceSeries) -> SignalSet:
        """Return the target position for every bar.

        The value at bar *t* must be computable from bars ``<= t`` only.
        """

    def position_size(self, context: SizingContext) -> float:
        """Signed target quantity for the next bar.

        Default: allocate :attr:`default_allocation` of current equity, scaled
        by the signal's conviction. Whole units only — the instruments in this
        universe are not fractionally tradable, and pretending otherwise would
        flatter every backtest by removing the rounding drag a real trader pays.
        """
        if context.direction is SignalDirection.FLAT or context.price <= 0:
            return 0.0
        strength = min(max(context.strength, 0.0), 1.0)
        notional = context.equity * self.default_allocation * strength
        quantity = float(int(notional / context.price))
        return quantity * int(context.direction)

    def exit_signal(self, context: ExitContext) -> ExitDecision:  # noqa: ARG002 - part of the contract
        """Discretionary exit, evaluated before the next bar's signal.

        Default: no discretionary exit. Most strategies close a position by
        emitting a new target direction, and a strategy that also needs a stop
        or a time-based exit overrides this.
        """
        return ExitDecision.hold()

    def auxiliary_symbols(self) -> tuple[str, ...]:
        """Instruments this strategy needs *beyond* the primary series.

        Almost every strategy reads one instrument and returns (). Pair trading
        reads two. Declaring the extra symbols here — rather than having the
        strategy fetch them — keeps `generate_signals` a pure function of the
        data it was handed, and gives the runner a single generic place to
        resolve them. Without it, "run any registered strategy by name" would
        work for three of the four families and fail for the fourth.
        """
        return ()

    def attach_auxiliary_series(self, series: PriceSeries) -> None:
        """Supply one of the instruments named by :meth:`auxiliary_symbols`.

        Raises by default: a strategy that declares no auxiliary symbols has
        nowhere to put one, and silently discarding it would let a caller
        believe data was used that never was.
        """
        raise NotImplementedError(
            f"{self.name} does not take auxiliary series (auxiliary_symbols() is empty), "
            f"but one for {series.symbol!r} was supplied."
        )

    def describe(self) -> dict[str, Any]:
        """Self-description for the API, the Strategy Lab and the MCP layer."""
        return {
            "name": self.name,
            "family": self.family,
            "description": self.description,
            "parameters": self.parameters,
            "parameter_specs": [spec.as_dict() for spec in self.parameter_specs],
            "warmup_bars": self.warmup_bars,
            # The UI and the AI layer need to know a strategy requires a second
            # instrument before they can offer it as a runnable option.
            "auxiliary_symbols": list(self.auxiliary_symbols()),
        }

    def __repr__(self) -> str:
        params = ", ".join(f"{k}={v!r}" for k, v in self._parameters.items())
        return f"{type(self).__name__}({params})"


def empty_signal_frame(index: pd.Index) -> pd.DataFrame:
    """A correctly shaped, all-flat signal frame.

    Strategies build their output on top of this so every SignalSet has the same
    columns and dtypes regardless of which strategy produced it.
    """
    return pd.DataFrame(
        {
            "direction": pd.Series(int(SignalDirection.FLAT), index=index, dtype="int64"),
            "strength": pd.Series(0.0, index=index, dtype="float64"),
            "reason": pd.Series("", index=index, dtype="object"),
        },
        index=index,
    )

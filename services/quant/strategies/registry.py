"""Strategy discovery.

The API, the Strategy Lab and the MCP layer all need to answer "which strategies
exist, and what parameters do they take?" without importing each strategy module
by hand. One registry answers it for all three, which is what stops the AI-facing
and API-facing surfaces from drifting apart (testing.md §5).
"""

from __future__ import annotations

from typing import Any

from services.quant.strategies.base import Strategy, StrategyError

_REGISTRY: dict[str, type[Strategy]] = {}


class UnknownStrategyError(StrategyError):
    """The requested strategy name is not registered."""

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        super().__init__(
            f"Unknown strategy {name!r}. Available: {', '.join(known) if known else 'none'}."
        )
        self.name = name
        self.known = known


def register(cls: type[Strategy]) -> type[Strategy]:
    """Class decorator registering a strategy under its ``name``."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must declare a non-empty `name` to be registered")
    existing = _REGISTRY.get(cls.name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Strategy name {cls.name!r} is already registered to {existing.__name__}; "
            "names identify saved experiments and must stay unique."
        )
    _REGISTRY[cls.name] = cls
    return cls


def available() -> tuple[str, ...]:
    """Registered strategy names, sorted for a stable UI ordering."""
    return tuple(sorted(_REGISTRY))


def get_strategy_class(name: str) -> type[Strategy]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownStrategyError(name, available()) from None


def create(name: str, parameters: dict[str, Any] | None = None) -> Strategy:
    """Instantiate a registered strategy with the given parameters.

    Unknown or out-of-range parameters raise rather than being ignored: silently
    dropping a parameter would run a different strategy than the one the user
    configured, and the saved experiment would record the wrong thing.
    """
    return get_strategy_class(name)(**(parameters or {}))


def describe_all() -> list[dict[str, Any]]:
    """Every strategy's self-description, with default parameters applied."""
    return [get_strategy_class(name)().describe() for name in available()]

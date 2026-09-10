"""Fixtures for the portfolio tests.

The loader here replaces :func:`services.quant.data.service.get_prices` so the
mathematics can be tested against price paths chosen by hand. It still returns a
real :class:`MarketDataResult` built by the real validator, so a fixture that
violated an OHLC invariant would fail exactly as live data would.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pytest

from services.quant.data.errors import ProviderError
from services.quant.data.service import MarketDataResult
from services.quant.data.validation import validate_prices
from services.quant.testing.fixtures import make_price_series

PriceLoaderFactory = Callable[[Mapping[str, Sequence[float]]], Callable[..., MarketDataResult]]


@pytest.fixture
def price_loader() -> PriceLoaderFactory:
    """Build a `get_prices`-shaped loader from ``{symbol: [closes]}``."""

    def factory(paths: Mapping[str, Sequence[float]]):
        def loader(symbol: str, _start: str, _end: str, interval: str = "1d", **_: object):
            closes = paths.get(symbol)
            if closes is None:
                raise ProviderError(f"no fixture path for {symbol}", symbol=symbol)
            series = make_price_series(list(closes), symbol=symbol, interval=interval)
            return MarketDataResult(
                series=series,
                quality=validate_prices(series.frame, symbol=symbol, interval=interval),
            )

        return loader

    return factory


def compound(start: float, returns: Sequence[float]) -> list[float]:
    """A close path whose per-bar returns are exactly ``returns``.

    Building the path from the returns rather than the other way round is what
    makes "this holding moves at twice the benchmark" an exact statement instead
    of an approximate one.
    """
    path = [float(start)]
    for r in returns:
        path.append(path[-1] * (1.0 + r))
    return path


def alternating(count: int, first: float, second: float) -> list[float]:
    """``count`` returns alternating between two values, starting with ``first``."""
    return [first if i % 2 == 0 else second for i in range(count)]

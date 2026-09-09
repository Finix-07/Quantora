"""Deterministic test doubles and fixture builders.

This ships inside the package rather than under `tests/` on purpose: the same
builders are used by unit tests, by integration tests, and by the golden-fixture
generator, and duplicating them would let those three drift apart.

Nothing here fabricates *results*. It fabricates deterministic *inputs* so a
calculation can be checked against a hand-computable answer — which is the
opposite of hard-coding a market outcome.
"""

from services.quant.testing.fixtures import (
    FakeProvider,
    make_ohlcv_frame,
    make_price_series,
    make_provenance,
)

__all__ = ["FakeProvider", "make_ohlcv_frame", "make_price_series", "make_provenance"]

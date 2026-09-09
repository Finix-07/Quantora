"""Tests for the tradable universe and its symbol -> provider-ticker mapping.

architecture.md §4 decision 3 fixes the MVP universe at seven instruments; the
symbol -> provider_ticker mapping is the whole point of this module (NIFTY and
BANKNIFTY are not valid Yahoo tickers on their own).
"""

from __future__ import annotations

import pytest

from services.quant.data.errors import UnknownSymbolError, UnknownUniverseError
from services.quant.data.universe import get_instrument, get_universe

MVP_SYMBOLS = (
    "NIFTY",
    "BANKNIFTY",
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
)


def test_default_universe_is_the_seven_confirmed_mvp_symbols_in_order() -> None:
    assert get_universe() == MVP_SYMBOLS


def test_indices_and_equities_partition_the_mvp_set() -> None:
    indices = get_universe("indices")
    equities = get_universe("equities")

    assert indices == ("NIFTY", "BANKNIFTY")
    assert equities == ("RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS")
    # Partition: no overlap, and together they cover the full MVP set.
    assert set(indices) & set(equities) == set()
    assert set(indices) | set(equities) == set(MVP_SYMBOLS)


def test_unknown_universe_raises_and_lists_known_names() -> None:
    with pytest.raises(UnknownUniverseError, match="Unknown universe 'bogus'") as excinfo:
        get_universe("bogus")

    message = str(excinfo.value)
    for known in ("mvp", "indices", "equities"):
        assert known in message
    assert excinfo.value.as_details() == {
        "universe": "bogus",
        "known_universes": ["equities", "indices", "mvp"],
    }


@pytest.mark.parametrize(
    ("symbol", "provider_ticker"),
    [
        # A plain "NIFTY" is not a valid Yahoo ticker: this mapping is the
        # reason the module exists.
        ("NIFTY", "^NSEI"),
        ("BANKNIFTY", "^NSEBANK"),
        ("RELIANCE.NS", "RELIANCE.NS"),
    ],
)
def test_get_instrument_resolves_provider_ticker(symbol: str, provider_ticker: str) -> None:
    assert get_instrument(symbol).provider_ticker == provider_ticker


def test_unknown_symbol_raises_and_lists_known_symbols() -> None:
    with pytest.raises(UnknownSymbolError, match="Unknown symbol 'FAKE.NS'") as excinfo:
        get_instrument("FAKE.NS")

    message = str(excinfo.value)
    for symbol in MVP_SYMBOLS:
        assert symbol in message
    assert excinfo.value.as_details()["known_symbols"] == list(MVP_SYMBOLS)


@pytest.mark.parametrize("symbol", ["NIFTY", "BANKNIFTY"])
def test_indices_have_no_sector(symbol: str) -> None:
    assert get_instrument(symbol).sector is None


@pytest.mark.parametrize(
    "symbol", ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]
)
def test_equities_have_a_sector(symbol: str) -> None:
    assert get_instrument(symbol).sector is not None

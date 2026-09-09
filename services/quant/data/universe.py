"""The tradable universe and its mapping to provider tickers.

planning.md §4 decision 3 fixes the MVP universe at seven instruments. Two of
them — NIFTY and BANKNIFTY — are index *names*, not Yahoo Finance tickers;
Yahoo knows them as ^NSEI and ^NSEBANK. The user-facing symbol and the provider
ticker are therefore kept as separate concepts and both are recorded in
provenance, so a result can always be traced back to the exact series that was
downloaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.quant.data.errors import UnknownSymbolError, UnknownUniverseError

AssetClass = Literal["index", "equity"]


@dataclass(frozen=True, slots=True)
class Instrument:
    """One tradable instrument in the configured universe."""

    symbol: str
    """User-facing symbol, as it appears in the UI, API and saved experiments."""

    provider_ticker: str
    """The ticker actually sent to yfinance."""

    name: str
    asset_class: AssetClass
    exchange: str
    currency: str
    timezone: str
    sector: str | None = None
    """Sector for equities where it is known; None for indices. Portfolio sector
    exposure is reported only where this is populated (requirements.md FR7,
    'where data permits')."""

    def as_dict(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "provider_ticker": self.provider_ticker,
            "name": self.name,
            "asset_class": self.asset_class,
            "exchange": self.exchange,
            "currency": self.currency,
            "timezone": self.timezone,
            "sector": self.sector,
        }


_NSE = {"exchange": "NSE", "currency": "INR", "timezone": "Asia/Kolkata"}

INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument("NIFTY", "^NSEI", "NIFTY 50 Index", "index", **_NSE),
    Instrument("BANKNIFTY", "^NSEBANK", "NIFTY Bank Index", "index", **_NSE),
    Instrument(
        "RELIANCE.NS", "RELIANCE.NS", "Reliance Industries", "equity", **_NSE, sector="Energy"
    ),
    Instrument(
        "TCS.NS",
        "TCS.NS",
        "Tata Consultancy Services",
        "equity",
        **_NSE,
        sector="Information Technology",
    ),
    Instrument("HDFCBANK.NS", "HDFCBANK.NS", "HDFC Bank", "equity", **_NSE, sector="Financials"),
    Instrument("INFY.NS", "INFY.NS", "Infosys", "equity", **_NSE, sector="Information Technology"),
    Instrument("ICICIBANK.NS", "ICICIBANK.NS", "ICICI Bank", "equity", **_NSE, sector="Financials"),
)

_BY_SYMBOL: dict[str, Instrument] = {i.symbol: i for i in INSTRUMENTS}

UNIVERSES: dict[str, tuple[str, ...]] = {
    # The confirmed MVP universe (planning.md §4 decision 3).
    "mvp": tuple(i.symbol for i in INSTRUMENTS),
    "indices": tuple(i.symbol for i in INSTRUMENTS if i.asset_class == "index"),
    "equities": tuple(i.symbol for i in INSTRUMENTS if i.asset_class == "equity"),
}

DEFAULT_UNIVERSE = "mvp"


def get_universe(name: str = DEFAULT_UNIVERSE) -> tuple[str, ...]:
    """Return the symbols in a named universe.

    Raises :class:`UnknownUniverseError` rather than returning an empty tuple:
    a typo that silently yields "no instruments" would look like a data outage.
    """
    try:
        return UNIVERSES[name]
    except KeyError:
        raise UnknownUniverseError(name, tuple(sorted(UNIVERSES))) from None


def get_instrument(symbol: str) -> Instrument:
    """Resolve a user-facing symbol to its instrument definition."""
    try:
        return _BY_SYMBOL[symbol]
    except KeyError:
        raise UnknownSymbolError(symbol, tuple(i.symbol for i in INSTRUMENTS)) from None


def all_instruments() -> tuple[Instrument, ...]:
    return INSTRUMENTS

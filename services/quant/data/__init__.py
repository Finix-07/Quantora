"""Market data: retrieval, normalisation, validation and provenance.

Every dataset that reaches an indicator, a strategy or a backtest comes through
:func:`get_prices`, which validates it and stamps provenance. Bypassing this
module means running on unvalidated data, which the reliability rules forbid.
"""

from services.quant.data.errors import (
    DataError,
    DataValidationError,
    InvalidRequestError,
    ProviderError,
    UnknownSymbolError,
    UnknownUniverseError,
    Violation,
)
from services.quant.data.provider import PriceProvider, YFinanceProvider, normalize_frame
from services.quant.data.service import MarketDataResult, get_prices
from services.quant.data.types import (
    OHLCV_COLUMNS,
    SUPPORTED_INTERVALS,
    PriceSeries,
    Provenance,
)
from services.quant.data.universe import (
    INSTRUMENTS,
    Instrument,
    all_instruments,
    get_instrument,
    get_universe,
)
from services.quant.data.validation import (
    DataQualityReport,
    Gap,
    detect_gaps,
    validate_prices,
)

__all__ = [
    "INSTRUMENTS",
    "OHLCV_COLUMNS",
    "SUPPORTED_INTERVALS",
    "DataError",
    "DataQualityReport",
    "DataValidationError",
    "Gap",
    "Instrument",
    "InvalidRequestError",
    "MarketDataResult",
    "PriceProvider",
    "PriceSeries",
    "Provenance",
    "ProviderError",
    "UnknownSymbolError",
    "UnknownUniverseError",
    "Violation",
    "YFinanceProvider",
    "all_instruments",
    "detect_gaps",
    "get_instrument",
    "get_prices",
    "get_universe",
    "normalize_frame",
    "validate_prices",
]

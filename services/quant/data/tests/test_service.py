"""Tests for services.quant.data.service.get_prices: the public data-layer contract.

Every test here uses FakeProvider (never the network) except the one test
marked @pytest.mark.network, which is the only place the real yfinance
integration is exercised.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from services.quant.data.errors import DataValidationError, InvalidRequestError, ProviderError
from services.quant.data.service import MarketDataResult, get_prices
from services.quant.testing.fixtures import FakeProvider, make_ohlcv_frame


def test_happy_path_returns_result_with_row_count_provenance_and_data_version() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0], start="2024-01-01", freq="B")
    provider = FakeProvider(frame)

    result = get_prices("RELIANCE.NS", "2024-01-01", "2024-01-03", "1d", provider=provider)

    assert isinstance(result, MarketDataResult)
    assert result.series.symbol == "RELIANCE.NS"
    assert len(result.series) == 3

    prov = result.series.provenance
    assert prov.provider == "fixture"
    assert prov.symbol == "RELIANCE.NS"
    assert prov.provider_ticker == "RELIANCE.NS"
    assert prov.interval == "1d"
    assert prov.timezone == "Asia/Kolkata"
    assert prov.adjustment_policy == "split_and_dividend_adjusted"
    assert prov.requested_start == "2024-01-01"
    assert prov.requested_end == "2024-01-03"
    assert prov.row_count == 3
    assert prov.first_timestamp is not None
    assert prov.last_timestamp is not None
    assert prov.currency == "INR"

    assert result.data_version.startswith("sha256:")


def test_unsupported_interval_raises_invalid_request_error() -> None:
    provider = FakeProvider(make_ohlcv_frame([100.0]))
    with pytest.raises(InvalidRequestError, match="Unsupported interval"):
        get_prices("RELIANCE.NS", "2024-01-01", "2024-01-01", "3d", provider=provider)


def test_start_after_end_raises_invalid_request_error() -> None:
    provider = FakeProvider(make_ohlcv_frame([100.0]))
    with pytest.raises(InvalidRequestError, match="must not be after"):
        get_prices("RELIANCE.NS", "2024-02-01", "2024-01-01", "1d", provider=provider)


def test_malformed_date_string_raises_invalid_request_error() -> None:
    provider = FakeProvider(make_ohlcv_frame([100.0]))
    with pytest.raises(InvalidRequestError, match="ISO date"):
        get_prices("RELIANCE.NS", "not-a-date", "2024-01-01", "1d", provider=provider)


def test_fetch_window_is_end_plus_one_day_and_result_is_trimmed_to_inclusive_range() -> None:
    # 8 business days from Mon 2024-01-01: 1,2,3,4,5(Fri),8,9,10.
    frame = make_ohlcv_frame(
        [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0], start="2024-01-01", freq="B"
    )
    provider = FakeProvider(frame)

    result = get_prices("RELIANCE.NS", "2024-01-03", "2024-01-05", "1d", provider=provider)

    # yfinance's `end` is exclusive, so the provider must be asked for one day
    # past the caller's inclusive end or the last requested session is dropped.
    assert provider.calls == [("RELIANCE.NS", "2024-01-03", "2024-01-06", "1d")]

    # The fixture provider ignores start/end and returns its whole frame, so
    # this also proves the service itself trims back to the caller's range.
    assert len(result.series) == 3
    assert result.series.start.date().isoformat() == "2024-01-03"
    assert result.series.end.date().isoformat() == "2024-01-05"


def _mismatched_adjustment_frame() -> pd.DataFrame:
    """A fixture where adj_close != close, with independently varying ratios per row."""
    index = pd.date_range("2024-01-01", periods=3, freq="B", tz="Asia/Kolkata")
    index.name = "timestamp"
    return pd.DataFrame(
        {
            "open": [100.0, 110.0, 105.0],
            "high": [105.0, 115.0, 110.0],
            "low": [95.0, 105.0, 100.0],
            "close": [102.0, 112.0, 107.0],
            "adj_close": [90.0, 105.0, 107.0],  # different back-adjustment ratio per row
            "volume": [1000.0, 2000.0, 1500.0],
        },
        index=index,
    )


def test_split_and_dividend_adjusted_rescales_ohlc_and_leaves_volume() -> None:
    frame = _mismatched_adjustment_frame()
    provider = FakeProvider(frame)

    result = get_prices(
        "RELIANCE.NS",
        "2024-01-01",
        "2024-01-03",
        "1d",
        adjustment="split_and_dividend_adjusted",
        provider=provider,
    )

    out = result.series.frame
    ratio = frame["adj_close"].to_numpy() / frame["close"].to_numpy()

    np.testing.assert_allclose(out["open"].to_numpy(), frame["open"].to_numpy() * ratio)
    np.testing.assert_allclose(out["high"].to_numpy(), frame["high"].to_numpy() * ratio)
    np.testing.assert_allclose(out["low"].to_numpy(), frame["low"].to_numpy() * ratio)
    np.testing.assert_allclose(out["close"].to_numpy(), frame["adj_close"].to_numpy())
    # Volume is a share count, not a price: it must be untouched by adjustment.
    np.testing.assert_allclose(out["volume"].to_numpy(), frame["volume"].to_numpy())

    # The per-row scaling factor differs, so this also proves the bar's
    # internal geometry (low <= open <= high) survives a non-uniform adjustment.
    assert (out["low"] <= out["open"]).all()
    assert (out["open"] <= out["high"]).all()
    assert (out["low"] <= out["close"]).all()
    assert (out["close"] <= out["high"]).all()


def test_raw_adjustment_leaves_prices_untouched() -> None:
    frame = _mismatched_adjustment_frame()
    provider = FakeProvider(frame)

    result = get_prices(
        "RELIANCE.NS", "2024-01-01", "2024-01-03", "1d", adjustment="raw", provider=provider
    )

    out = result.series.frame
    np.testing.assert_allclose(out["open"].to_numpy(), frame["open"].to_numpy())
    np.testing.assert_allclose(out["high"].to_numpy(), frame["high"].to_numpy())
    np.testing.assert_allclose(out["low"].to_numpy(), frame["low"].to_numpy())
    np.testing.assert_allclose(out["close"].to_numpy(), frame["close"].to_numpy())
    assert result.series.provenance.adjustment_policy == "raw"


def test_provider_error_propagates_unchanged() -> None:
    provider = FakeProvider({}, fail_with=ProviderError("boom", symbol="RELIANCE.NS"))

    with pytest.raises(ProviderError, match="boom"):
        get_prices("RELIANCE.NS", "2024-01-01", "2024-01-03", "1d", provider=provider)


def test_invalid_provider_data_raises_data_validation_error_not_partial_result() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0], start="2024-01-01", freq="B")
    frame.loc[frame.index[1], "volume"] = -1.0  # deliberately malformed
    provider = FakeProvider(frame)

    # The service must fail closed: no MarketDataResult is ever constructed
    # from data that fails validation, not even a partial one.
    with pytest.raises(DataValidationError, match="negative_volume"):
        get_prices("RELIANCE.NS", "2024-01-01", "2024-01-03", "1d", provider=provider)


def test_as_dict_is_json_serializable_with_expected_keys() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0], start="2024-01-01", freq="B")
    provider = FakeProvider(frame)

    result = get_prices("RELIANCE.NS", "2024-01-01", "2024-01-03", "1d", provider=provider)
    payload = result.as_dict()

    json.dumps(payload)  # must not raise
    for key in ("symbol", "interval", "data_version", "provenance", "quality", "bars"):
        assert key in payload


@pytest.mark.network
def test_real_provider_fetches_reliance_for_a_fixed_past_range() -> None:
    # A fixed, well-past date range: stable trading history, no reliance on
    # "today" being a trading day. Real network call to yfinance.
    result = get_prices("RELIANCE.NS", "2023-01-02", "2023-01-10", "1d")

    assert result.series.symbol == "RELIANCE.NS"
    assert len(result.series) > 0
    assert result.quality is not None
    assert result.series.provenance.provider == "yfinance"

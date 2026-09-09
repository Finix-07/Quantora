"""Tests for services.quant.data.provider: normalize_frame and YFinanceProvider.

No test here makes a real network call: YFinanceProvider.fetch is exercised by
monkeypatching yfinance.download.
"""

from __future__ import annotations

import pandas as pd
import pytest
import yfinance

from services.quant.data.errors import ProviderError
from services.quant.data.provider import YFinanceProvider, normalize_frame
from services.quant.data.types import OHLCV_COLUMNS


def _raw_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Adj Close": [101.0, 102.0],
            "Volume": [1_000, 1_500],
        },
        index=index,
    )


def test_normalize_frame_collapses_yfinance_multiindex_columns() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")
    raw = pd.DataFrame(
        {
            ("Open", "RELIANCE.NS"): [100.0, 101.0],
            ("High", "RELIANCE.NS"): [102.0, 103.0],
            ("Low", "RELIANCE.NS"): [99.0, 100.0],
            ("Close", "RELIANCE.NS"): [101.0, 102.0],
            ("Adj Close", "RELIANCE.NS"): [101.0, 102.0],
            ("Volume", "RELIANCE.NS"): [1_000, 1_500],
        },
        index=index,
    )
    assert isinstance(raw.columns, pd.MultiIndex)  # sanity: this is what yfinance actually returns

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert list(result.columns) == list(OHLCV_COLUMNS)
    assert result["close"].tolist() == [101.0, 102.0]


def test_normalize_frame_lowercases_and_maps_adj_close() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")
    raw = _raw_frame(index)

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert "adj_close" in result.columns
    assert result["adj_close"].tolist() == [101.0, 102.0]


def test_normalize_frame_synthesizes_adj_close_from_close_when_omitted() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")
    raw = _raw_frame(index).drop(columns=["Adj Close"])

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert result["adj_close"].tolist() == result["close"].tolist()


def test_normalize_frame_localizes_tz_naive_index() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")  # naive
    raw = _raw_frame(index)

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert str(result.index.tz) == "Asia/Kolkata"
    assert result.index[0].isoformat().startswith("2024-01-01")


def test_normalize_frame_converts_tz_aware_index_to_target_timezone() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D", tz="UTC")
    raw = _raw_frame(index)

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert str(result.index.tz) == "Asia/Kolkata"
    # Converting, not re-labeling: the instant in time is preserved.
    assert result.index[0].tz_convert("UTC") == index[0]


def test_normalize_frame_sorts_out_of_order_index() -> None:
    index = pd.date_range("2024-01-01", periods=3, freq="D")[::-1]  # descending
    raw = pd.DataFrame(
        {
            "Open": [102.0, 101.0, 100.0],
            "High": [103.0, 102.0, 101.0],
            "Low": [101.0, 100.0, 99.0],
            "Close": [102.5, 101.5, 100.5],
            "Adj Close": [102.5, 101.5, 100.5],
            "Volume": [3_000, 2_000, 1_000],
        },
        index=index,
    )

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert result.index.is_monotonic_increasing
    assert result["close"].tolist() == [100.5, 101.5, 102.5]


def test_normalize_frame_names_index_timestamp_and_canonical_columns_are_float64() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")
    raw = _raw_frame(index)

    result = normalize_frame(raw, timezone="Asia/Kolkata")

    assert result.index.name == "timestamp"
    assert list(result.columns) == list(OHLCV_COLUMNS)
    for column in OHLCV_COLUMNS:
        assert result[column].dtype == "float64"


def test_normalize_frame_raises_provider_error_naming_missing_column() -> None:
    index = pd.date_range("2024-01-01", periods=2, freq="D")
    raw = _raw_frame(index).drop(columns=["Volume"])

    with pytest.raises(ProviderError, match="volume"):
        normalize_frame(raw, timezone="Asia/Kolkata")


def test_fetch_raises_provider_error_on_empty_download(monkeypatch: pytest.MonkeyPatch) -> None:
    def empty_download(*_args: object, **_kwargs: object) -> pd.DataFrame:
        return pd.DataFrame()

    monkeypatch.setattr(yfinance, "download", empty_download)

    provider = YFinanceProvider(timezone="Asia/Kolkata")
    with pytest.raises(ProviderError, match="RELIANCE.NS") as excinfo:
        provider.fetch("RELIANCE.NS", "2024-01-01", "2024-01-02", "1d")

    assert excinfo.value.symbol == "RELIANCE.NS"


def test_fetch_raises_provider_error_when_yfinance_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(yfinance, "download", boom)

    provider = YFinanceProvider(timezone="Asia/Kolkata")
    with pytest.raises(ProviderError, match="RELIANCE.NS") as excinfo:
        provider.fetch("RELIANCE.NS", "2024-01-01", "2024-01-02", "1d")

    assert "rate limited" in str(excinfo.value)

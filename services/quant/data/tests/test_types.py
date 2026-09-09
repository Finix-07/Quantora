"""Tests for services.quant.data.types: PriceSeries, Provenance, data_version.

data_version() is the reproducibility primitive (NFR6, "same result given the
same data_version"): it must change whenever the bars actually change (yfinance
silently revises history), and it must NOT change under float noise that has no
real meaning (round-tripping through JSON/PostgreSQL at 6dp precision).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from services.quant.data.types import OHLCV_COLUMNS, TIMESTAMP_INDEX_NAME, PriceSeries
from services.quant.testing.fixtures import make_ohlcv_frame, make_price_series, make_provenance


def test_price_series_rejects_frame_missing_a_required_column() -> None:
    frame = make_ohlcv_frame([100, 101, 102])
    frame = frame.drop(columns=["volume"])

    with pytest.raises(ValueError, match=r"missing columns.*volume"):
        PriceSeries(
            symbol="RELIANCE.NS",
            interval="1d",
            frame=frame,
            provenance=make_price_series([100, 101, 102]).provenance,
        )


def test_price_series_rejects_wrongly_named_index() -> None:
    frame = make_ohlcv_frame([100, 101, 102])
    frame.index.name = "date"  # must be "timestamp"

    with pytest.raises(ValueError, match=r"index must be named 'timestamp'.*'date'"):
        PriceSeries(
            symbol="RELIANCE.NS",
            interval="1d",
            frame=frame,
            provenance=make_price_series([100, 101, 102]).provenance,
        )


def test_data_version_is_stable_across_two_identical_constructions() -> None:
    series_a = make_price_series([100.0, 101.0, 102.5, 99.0])
    series_b = make_price_series([100.0, 101.0, 102.5, 99.0])

    assert series_a.data_version() == series_b.data_version()


def test_data_version_changes_when_a_single_bar_value_changes() -> None:
    # This is the whole point of data_version: yfinance can silently revise
    # history on rerun, and the caller must be able to notice.
    series_a = make_price_series([100.0, 101.0, 102.5, 99.0])
    series_b = make_price_series([100.0, 101.0, 102.51, 99.0])

    assert series_a.data_version() != series_b.data_version()


def test_data_version_changes_when_adjustment_policy_differs() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])

    series_raw = PriceSeries(
        symbol="RELIANCE.NS",
        interval="1d",
        frame=frame,
        provenance=make_provenance("RELIANCE.NS", frame=frame, adjustment_policy="raw"),
    )
    series_adjusted = PriceSeries(
        symbol="RELIANCE.NS",
        interval="1d",
        frame=frame,
        provenance=make_provenance(
            "RELIANCE.NS", frame=frame, adjustment_policy="split_and_dividend_adjusted"
        ),
    )

    assert series_raw.data_version() != series_adjusted.data_version()


def test_data_version_insensitive_to_float_noise_beyond_6dp() -> None:
    # Values are rounded to 6dp before hashing, so precision noise from a
    # float round-trip through json.dumps/json.loads (as would happen storing
    # a bar in Postgres/JSON) must not flip the version.
    noisy_value = json.loads(json.dumps(100.123456789))
    clean_value = 100.123456789

    series_noisy = make_price_series([noisy_value, 101.0])
    series_clean = make_price_series([clean_value, 101.0])

    assert series_noisy.data_version() == series_clean.data_version()

    # A sub-microscopic perturbation (below the 6dp rounding floor) must also
    # leave the version unchanged...
    series_tiny_delta = make_price_series([100.123456789 + 1e-9, 101.0])
    assert series_clean.data_version() == series_tiny_delta.data_version()

    # ...while a perturbation visible at 6dp must change it.
    series_visible_delta = make_price_series([100.123456789 + 1e-4, 101.0])
    assert series_clean.data_version() != series_visible_delta.data_version()


def test_to_records_emits_iso_timestamps_and_float_values() -> None:
    series = make_price_series([100.0, 101.5, 99.25])

    records = series.to_records()

    assert len(records) == 3
    for ts, record in zip(series.frame.index, records, strict=True):
        assert record["timestamp"] == ts.isoformat()
        for column in ("open", "high", "low", "close", "adj_close", "volume"):
            assert isinstance(record[column], float)


def test_slice_restricts_window_inclusively_at_both_ends() -> None:
    series = make_price_series([100.0, 101.0, 102.0, 103.0, 104.0])
    index = series.frame.index

    sliced = series.slice(start=index[1], end=index[3])

    assert list(sliced.frame.index) == [index[1], index[2], index[3]]
    # Both endpoints included.
    assert sliced.start == index[1]
    assert sliced.end == index[3]


def test_populated_series_start_end_is_empty_len() -> None:
    series = make_price_series([100.0, 101.0, 102.0])

    assert series.start == series.frame.index[0]
    assert series.end == series.frame.index[-1]
    assert series.is_empty is False
    assert len(series) == 3


def test_empty_series_start_end_is_empty_len() -> None:
    # make_ohlcv_frame requires at least one close (opens[0] = closes[0]), so
    # an empty frame in the canonical shape is built directly here.
    empty_index = pd.DatetimeIndex([], tz="Asia/Kolkata", name=TIMESTAMP_INDEX_NAME)
    empty_frame = pd.DataFrame(
        {col: pd.Series(dtype="float64") for col in OHLCV_COLUMNS}, index=empty_index
    )
    empty_series = PriceSeries(
        symbol="RELIANCE.NS",
        interval="1d",
        frame=empty_frame,
        provenance=make_price_series([100.0]).provenance,
    )

    assert empty_series.start is None
    assert empty_series.end is None
    assert empty_series.is_empty is True
    assert len(empty_series) == 0

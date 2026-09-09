"""Tests for services.quant.data.validation: hard rules and gap detection.

Each hard-rule test builds a valid fixture with make_ohlcv_frame (where
low <= open <= high and low <= close <= high hold by construction) and then
mutates exactly the one thing that breaks the rule under test, per
testing.md §2.1's requirement to use both valid and deliberately malformed
fixtures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.quant.data.errors import DataValidationError
from services.quant.data.validation import detect_gaps, validate_prices
from services.quant.testing.fixtures import make_ohlcv_frame

SYMBOL = "RELIANCE.NS"


def test_valid_fixture_passes_clean_with_no_gaps() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0, 101.5, 103.0])

    report = validate_prices(frame, symbol=SYMBOL, interval="1d")

    assert report.severity == "clean"
    assert report.gaps == ()
    assert report.row_count == 5


def test_high_below_low_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "high"] = frame.loc[frame.index[1], "low"] - 1.0

    with pytest.raises(DataValidationError, match="high_below_low") as excinfo:
        validate_prices(frame, symbol=SYMBOL, interval="1d")

    rules = {v.rule for v in excinfo.value.violations}
    assert "high_below_low" in rules


def test_open_out_of_range_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "open"] = frame.loc[frame.index[1], "high"] + 1.0

    with pytest.raises(DataValidationError, match="open_out_of_range"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_close_out_of_range_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "close"] = frame.loc[frame.index[1], "low"] - 1.0

    with pytest.raises(DataValidationError, match="close_out_of_range"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_negative_volume_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "volume"] = -1.0

    with pytest.raises(DataValidationError, match="negative_volume"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_non_positive_price_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "close"] = 0.0
    # Keep low/high consistent with the zeroed close so only non_positive_price fires.
    frame.loc[frame.index[1], "low"] = 0.0

    with pytest.raises(DataValidationError, match="non_positive_price"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_nan_in_ohlcv_field_is_rejected_and_not_forward_filled() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    bad_ts = frame.index[1]
    frame.loc[bad_ts, "close"] = np.nan

    with pytest.raises(DataValidationError, match="missing_value") as excinfo:
        validate_prices(frame, symbol=SYMBOL, interval="1d")

    # The row must not have been silently forward-filled with the previous
    # close: an invented price is worse than a refused answer.
    assert pd.isna(frame.loc[bad_ts, "close"])
    violation = next(v for v in excinfo.value.violations if v.rule == "missing_value")
    assert violation.sample[0]["close"] is None


def test_duplicate_timestamp_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    duped_index = frame.index.to_list()
    duped_index[1] = duped_index[0]  # collide two rows onto the same timestamp
    frame.index = pd.DatetimeIndex(duped_index, name=frame.index.name)

    with pytest.raises(DataValidationError, match="duplicate_timestamp"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_non_monotonic_timestamp_is_rejected() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    swapped_index = frame.index.to_list()
    swapped_index[0], swapped_index[1] = swapped_index[1], swapped_index[0]
    frame.index = pd.DatetimeIndex(swapped_index, name=frame.index.name)

    with pytest.raises(DataValidationError, match="non_monotonic_timestamp"):
        validate_prices(frame, symbol=SYMBOL, interval="1d")


def test_multiple_simultaneous_violations_are_all_reported_in_one_raise() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[0], "volume"] = -1.0
    # Independent of the volume problem: zero out close/low on a different row
    # so only non_positive_price fires there (not open/close-range rules too).
    frame.loc[frame.index[2], "close"] = 0.0
    frame.loc[frame.index[2], "low"] = 0.0

    with pytest.raises(DataValidationError) as excinfo:
        validate_prices(frame, symbol=SYMBOL, interval="1d")

    rules = {v.rule for v in excinfo.value.violations}
    # Both problems surface together, not just the first one hit.
    assert rules == {"negative_volume", "non_positive_price"}


def test_data_validation_error_as_details_exposes_rule_row_count_and_sample() -> None:
    frame = make_ohlcv_frame([100.0, 101.0, 102.0])
    frame.loc[frame.index[1], "volume"] = -1.0

    with pytest.raises(DataValidationError) as excinfo:
        validate_prices(frame, symbol=SYMBOL, interval="1d")

    details = excinfo.value.as_details()
    assert details["symbol"] == SYMBOL
    violation = details["violations"][0]
    assert violation["rule"] == "negative_volume"
    assert violation["row_count"] == 1
    assert len(violation["sample"]) > 0


def test_weekend_only_gap_is_not_reported() -> None:
    # Business-day frequency already skips weekends; a plain Friday->Monday
    # spacing must not be flagged as a gap.
    frame = make_ohlcv_frame([100.0, 101.0, 102.0, 103.0, 104.0], start="2024-01-01", freq="B")

    report = validate_prices(frame, symbol=SYMBOL, interval="1d")

    assert report.severity == "clean"
    assert report.gaps == ()


def test_removed_midweek_session_is_reported_as_gap_detected() -> None:
    # 7 business days from Mon 2024-01-01: Mon,Tue,Wed,Thu,Fri,Mon(8th),Tue(9th).
    frame = make_ohlcv_frame(
        [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0], start="2024-01-01", freq="B"
    )
    # Drop Monday the 8th: Friday(5th) -> Tuesday(9th) is a 4-calendar-day span
    # with one missing business day (the 8th), unlike a plain weekend (3 days,
    # nothing missing) which must NOT be flagged.
    frame = frame.drop(frame.index[5])

    report = validate_prices(frame, symbol=SYMBOL, interval="1d")

    assert report.severity == "gaps_detected"
    assert len(report.gaps) == 1


def test_gap_longer_than_ten_calendar_days_is_severe() -> None:
    early = make_ohlcv_frame([100.0, 101.0], start="2024-01-01", freq="B")
    # Second block starts more than 10 calendar days after the first block ends.
    late = make_ohlcv_frame([102.0, 103.0], start="2024-01-20", freq="B")
    frame = pd.concat([early, late])

    report = validate_prices(frame, symbol=SYMBOL, interval="1d")

    assert report.severity == "severe_gaps"
    assert len(report.gaps) == 1
    assert report.gaps[0].calendar_days >= 10


def test_gap_detection_is_skipped_for_intraday_intervals() -> None:
    # A large spacing is expected every night for intraday bars (session
    # boundary), so the calendar-day gap rule must not apply to them at all.
    timestamps = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-01 09:15", tz="Asia/Kolkata"),
            pd.Timestamp("2024-01-01 09:30", tz="Asia/Kolkata"),
            pd.Timestamp("2024-01-15 09:15", tz="Asia/Kolkata"),  # 14-day jump
        ],
        name="timestamp",
    )
    frame = make_ohlcv_frame([100.0, 101.0, 102.0], timestamps=timestamps)

    gaps = detect_gaps(frame, "15m")

    assert gaps == ()

    report = validate_prices(frame, symbol=SYMBOL, interval="15m")
    assert report.severity == "clean"
    assert report.gaps == ()

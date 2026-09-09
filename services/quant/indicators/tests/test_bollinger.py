"""Bollinger Bands are checked against values computed by hand from their own
definition, not against pandas' own rolling functions."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from services.quant.indicators import bollinger_bands


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


def test_middle_band_is_the_simple_moving_average() -> None:
    result = bollinger_bands(s([1, 2, 3, 4, 5]), window=3, num_std=2.0)

    # (1+2+3)/3 = 2, (2+3+4)/3 = 3, (3+4+5)/3 = 4
    assert result.middle.iloc[2] == pytest.approx(2.0)
    assert result.middle.iloc[3] == pytest.approx(3.0)
    assert result.middle.iloc[4] == pytest.approx(4.0)


def test_upper_and_lower_are_exactly_mean_plus_or_minus_num_std_times_population_std() -> None:
    # values 2, 4, 4 -> mean = 10/3.
    # deviations: -4/3, 2/3, 2/3 -> squares 16/9, 4/9, 4/9 -> sum 24/9 = 8/3
    # population variance = (8/3)/3 = 8/9 -> std = sqrt(8/9) = 2*sqrt(2)/3
    result = bollinger_bands(s([2.0, 4.0, 4.0]), window=3, num_std=2.0)

    mean = 10.0 / 3.0
    std = math.sqrt(8.0 / 9.0)
    assert result.middle.iloc[2] == pytest.approx(mean)
    assert result.upper.iloc[2] == pytest.approx(mean + 2.0 * std)
    assert result.lower.iloc[2] == pytest.approx(mean - 2.0 * std)


def test_warmup_is_nan_for_every_band() -> None:
    result = bollinger_bands(s([1.0, 2.0, 3.0, 4.0]), window=3, num_std=2.0)

    for series in (result.middle, result.upper, result.lower, result.percent_b):
        assert math.isnan(series.iloc[0])
        assert math.isnan(series.iloc[1])
        assert not math.isnan(series.iloc[2])


def test_constant_series_gives_zero_width_bands_and_nan_percent_b() -> None:
    # A flat window has zero dispersion, so upper == lower == middle, and
    # percent_b -- (price - lower) / (upper - lower) -- is 0/0: genuinely
    # undefined, not 0.5 or 0.0.
    result = bollinger_bands(s([7.0] * 10), window=4, num_std=2.0)

    assert (result.upper.dropna() == result.lower.dropna()).all()
    assert (result.upper.dropna() == result.middle.dropna()).all()
    assert result.percent_b.iloc[3:].apply(math.isnan).all(), (
        "0/0 must be NaN, not a fabricated midpoint"
    )


def test_percent_b_is_one_at_the_upper_band() -> None:
    # window=5, num_std=2, values [0,0,0,0,1]: mean=0.2, deviations
    # (-0.2 x4, 0.8 x1), squares (0.04 x4, 0.64), sum=0.8, variance=0.16,
    # std=0.4. upper = 0.2 + 2*0.4 = 1.0 -- exactly the closing price.
    result = bollinger_bands(s([0.0, 0.0, 0.0, 0.0, 1.0]), window=5, num_std=2.0)

    assert result.upper.iloc[-1] == pytest.approx(1.0)
    assert result.percent_b.iloc[-1] == pytest.approx(1.0)


def test_percent_b_is_zero_at_the_lower_band() -> None:
    # Mirror image of the case above: mean=-0.2, std=0.4 (same magnitude by
    # symmetry), lower = -0.2 - 2*0.4 = -1.0 -- exactly the closing price.
    result = bollinger_bands(s([0.0, 0.0, 0.0, 0.0, -1.0]), window=5, num_std=2.0)

    assert result.lower.iloc[-1] == pytest.approx(-1.0)
    assert result.percent_b.iloc[-1] == pytest.approx(0.0)


def test_bandwidth_is_upper_minus_lower_over_middle() -> None:
    # Reuse the hand-computed mean/std from the population-std test above:
    # mean = 10/3, std = 2*sqrt(2)/3 -> bandwidth = (2*2*std)/mean.
    result = bollinger_bands(s([2.0, 4.0, 4.0]), window=3, num_std=2.0)

    mean = 10.0 / 3.0
    std = math.sqrt(8.0 / 9.0)
    expected_bandwidth = (4.0 * std) / mean
    assert result.bandwidth.iloc[2] == pytest.approx(expected_bandwidth)


@pytest.mark.parametrize("bad_window", [0, 1, -1])
def test_rejects_window_below_two(bad_window: int) -> None:
    with pytest.raises(ValueError, match="must be >= 2"):
        bollinger_bands(s([1.0, 2.0, 3.0]), window=bad_window, num_std=2.0)


@pytest.mark.parametrize("bad_num_std", [0.0, -1.0, -0.5])
def test_rejects_non_positive_num_std(bad_num_std: float) -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        bollinger_bands(s([1.0, 2.0, 3.0]), window=2, num_std=bad_num_std)


def test_is_causal() -> None:
    """Mutating the final bar must not change any earlier value (NFR5.3)."""
    base = list(np.linspace(100, 130, 40))
    original = bollinger_bands(s(base), window=5, num_std=2.0)

    mutated = base.copy()
    mutated[-1] = 10_000.0
    perturbed = bollinger_bands(s(mutated), window=5, num_std=2.0)

    pd.testing.assert_series_equal(original.middle.iloc[:-1], perturbed.middle.iloc[:-1])
    pd.testing.assert_series_equal(original.upper.iloc[:-1], perturbed.upper.iloc[:-1])
    pd.testing.assert_series_equal(original.lower.iloc[:-1], perturbed.lower.iloc[:-1])
    pd.testing.assert_series_equal(original.percent_b.iloc[:-1], perturbed.percent_b.iloc[:-1])


def test_preserves_a_datetime_index() -> None:
    index = pd.date_range("2024-01-01", periods=30, freq="B", tz="Asia/Kolkata")
    prices = pd.Series(np.linspace(100, 150, 30), index=index, dtype="float64")

    result = bollinger_bands(prices, window=5, num_std=2.0)

    pd.testing.assert_index_equal(result.middle.index, index)
    pd.testing.assert_index_equal(result.upper.index, index)
    pd.testing.assert_index_equal(result.lower.index, index)
    pd.testing.assert_index_equal(result.percent_b.index, index)


def test_to_frame_has_stable_column_names() -> None:
    frame = bollinger_bands(s(list(np.linspace(1, 100, 40))), window=5, num_std=2.0).to_frame()

    assert list(frame.columns) == [
        "bb_middle",
        "bb_upper",
        "bb_lower",
        "bb_bandwidth",
        "bb_percent_b",
    ]


def test_result_reports_the_parameters_it_used() -> None:
    result = bollinger_bands(s(list(np.linspace(1, 100, 40))), window=10, num_std=1.5)

    assert result.parameters == {"window": 10, "num_std": 1.5}

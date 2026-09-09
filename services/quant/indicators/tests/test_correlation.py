"""Rolling correlation and hedge ratio, checked against hand-computed arithmetic.

The interesting cases here are the undefined ones: a flat window has no
direction to co-move in and no slope to fit, and both functions must say so with
NaN rather than inventing a number.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from services.quant.indicators import SeriesAlignmentError, hedge_ratio, rolling_correlation


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


class TestRollingCorrelation:
    def test_identical_series_are_perfectly_correlated(self) -> None:
        values = s([1, 2, 3, 4, 5])

        result = rolling_correlation(values, values, 3)

        assert result.dropna().apply(lambda v: v == pytest.approx(1.0)).all()

    def test_a_series_and_its_negation_are_perfectly_anti_correlated(self) -> None:
        values = s([1, 2, 3, 4, 5])

        result = rolling_correlation(values, -values, 3)

        assert result.dropna().apply(lambda v: v == pytest.approx(-1.0)).all()

    def test_hand_computed_value(self) -> None:
        a = s([1, 2, 3, 4])
        b = s([1, 3, 2, 4])

        result = rolling_correlation(a, b, 4)

        # Both means are 2.5. Deviations of a: -1.5, -0.5, 0.5, 1.5;
        # of b: -1.5, 0.5, -0.5, 1.5. Products: 2.25, -0.25, -0.25, 2.25
        # -> covariance 4.0/4 = 1.0. Both variances are 5.0/4 = 1.25.
        # corr = 1.0 / sqrt(1.25 * 1.25) = 1.0 / 1.25 = 0.8.
        assert result.iloc[3] == pytest.approx(0.8)

    def test_constant_series_gives_nan_not_zero(self) -> None:
        """Zero variance makes the correlation undefined, not "unrelated".

        0.0 would read as a measured finding — that these two instruments move
        independently — when nothing was measurable at all.
        """
        result = rolling_correlation(s([1, 2, 3, 4]), s([7, 7, 7, 7]), 3)

        assert result.isna().all()
        assert not (result.fillna(-2.0) == 0.0).any()

    def test_warmup_is_nan(self) -> None:
        result = rolling_correlation(s([1, 2, 3, 4]), s([2, 4, 5, 9]), 3)

        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].notna().all()

    def test_stays_within_the_mathematical_bounds(self) -> None:
        a = s([100.0, 100.1, 100.2, 100.3, 100.4, 100.5])

        result = rolling_correlation(a, a * 3.0, 3)

        assert result.dropna().between(-1.0, 1.0).all()

    @pytest.mark.parametrize("bad", [1, 0])
    def test_rejects_a_window_below_two(self, bad: int) -> None:
        with pytest.raises(ValueError, match="must be >= 2"):
            rolling_correlation(s([1, 2, 3]), s([1, 2, 3]), bad)

    def test_rejects_a_non_integer_window(self) -> None:
        with pytest.raises(TypeError, match="must be an int"):
            rolling_correlation(s([1, 2, 3]), s([1, 2, 3]), 2.0)  # type: ignore[arg-type]

    def test_rejects_mismatched_indexes(self) -> None:
        """Letting pandas align them would compare different days to each other."""
        a = pd.Series([1.0, 2.0, 3.0], index=[0, 1, 2])
        b = pd.Series([1.0, 2.0, 3.0], index=[0, 1, 5])

        with pytest.raises(SeriesAlignmentError, match="share an index"):
            rolling_correlation(a, b, 2)

    def test_is_causal(self) -> None:
        base_a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        base_b = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]
        original = rolling_correlation(s(base_a), s(base_b), 3)

        mutated_a = base_a.copy()
        mutated_b = base_b.copy()
        mutated_a[-1] = -900.0
        mutated_b[-1] = 900.0
        perturbed = rolling_correlation(s(mutated_a), s(mutated_b), 3)

        pd.testing.assert_series_equal(original.iloc[:-1], perturbed.iloc[:-1])

    def test_preserves_the_input_index(self) -> None:
        index = pd.date_range("2024-01-01", periods=4, freq="B", tz="Asia/Kolkata")
        a = pd.Series([1.0, 2.0, 3.0, 4.0], index=index)
        b = pd.Series([2.0, 3.0, 5.0, 7.0], index=index)

        pd.testing.assert_index_equal(rolling_correlation(a, b, 3).index, index)


class TestHedgeRatio:
    def test_exact_multiple_gives_that_multiple_as_the_slope(self) -> None:
        b = s([1, 2, 3, 4, 5])
        a = b * 3.0

        result = hedge_ratio(a, b, 3)

        # a = 3b exactly, so cov(a, b) = 3 * var(b) and the slope is
        # cov(a, b) / var(b) = 3.0 in every window.
        assert result.dropna().apply(lambda v: v == pytest.approx(3.0)).all()
        assert len(result.dropna()) == 3

    def test_hand_computed_slope(self) -> None:
        a = s([2, 4, 7])
        b = s([1, 2, 3])

        result = hedge_ratio(a, b, 3)

        # mean(b) = 2, deviations -1, 0, 1 -> var(b) = 2/3.
        # mean(a) = 13/3, deviations -7/3, -1/3, 8/3.
        # cov = ((-1)(-7/3) + 0 + (1)(8/3)) / 3 = (15/3)/3 = 5/3.
        # slope = (5/3) / (2/3) = 2.5.
        assert result.iloc[2] == pytest.approx(2.5)

    def test_constant_explanatory_leg_gives_nan(self) -> None:
        """With no variation in b the slope is unidentifiable."""
        result = hedge_ratio(s([2, 4, 7]), s([1, 1, 1]), 3)

        assert result.isna().all()

    def test_warmup_is_nan(self) -> None:
        result = hedge_ratio(s([1, 2, 4, 8]), s([1, 2, 3, 4]), 3)

        assert result.iloc[:2].isna().all()
        assert math.isfinite(result.iloc[2])

    @pytest.mark.parametrize("bad", [1, 0])
    def test_rejects_a_window_below_two(self, bad: int) -> None:
        with pytest.raises(ValueError, match="must be >= 2"):
            hedge_ratio(s([1, 2, 3]), s([1, 2, 3]), bad)

    def test_rejects_mismatched_indexes(self) -> None:
        a = pd.Series([1.0, 2.0, 3.0], index=[0, 1, 2])
        b = pd.Series([1.0, 2.0, 3.0], index=[3, 4, 5])

        with pytest.raises(SeriesAlignmentError, match="share an index"):
            hedge_ratio(a, b, 2)

    def test_is_causal(self) -> None:
        """A rolling beta must never be a full-sample fit in disguise (NFR5.3)."""
        base_a = [1.0, 2.5, 3.0, 4.5, 5.0, 6.5]
        base_b = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        original = hedge_ratio(s(base_a), s(base_b), 3)

        mutated_a = base_a.copy()
        mutated_b = base_b.copy()
        mutated_a[-1] = 1_000.0
        mutated_b[-1] = -1_000.0
        perturbed = hedge_ratio(s(mutated_a), s(mutated_b), 3)

        pd.testing.assert_series_equal(original.iloc[:-1], perturbed.iloc[:-1])

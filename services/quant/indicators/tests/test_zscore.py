"""The rolling z-score is checked against hand-computed arithmetic, not against
pandas — testing pandas with pandas would prove nothing about this module.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from services.quant.indicators import rolling_zscore


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


class TestRollingZScore:
    def test_hand_computed_values(self) -> None:
        result = rolling_zscore(s([1, 2, 3, 4, 5]), 3)

        # Window [1,2,3]: mean 2, population variance ((-1)^2 + 0 + 1^2)/3 = 2/3,
        # so std = sqrt(2/3) and z = (3 - 2) / sqrt(2/3) = sqrt(3/2).
        expected = math.sqrt(1.5)
        assert result.iloc[2] == pytest.approx(expected)
        # The same arithmetic repeats for every 3-point arithmetic ramp.
        assert result.iloc[3] == pytest.approx(expected)
        assert result.iloc[4] == pytest.approx(expected)

    def test_hand_computed_value_on_an_uneven_window(self) -> None:
        result = rolling_zscore(s([5, 5, 5, 6]), 3)

        # Window [5,5,6]: mean 16/3, deviations -1/3, -1/3, 2/3, population
        # variance (1/9 + 1/9 + 4/9)/3 = 2/9, std = sqrt(2)/3.
        # z = (6 - 16/3) / (sqrt(2)/3) = (2/3) * 3/sqrt(2) = sqrt(2).
        assert result.iloc[3] == pytest.approx(math.sqrt(2))

    def test_warmup_is_nan_not_a_partial_statistic(self) -> None:
        result = rolling_zscore(s([1, 2, 3, 4]), 3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert not math.isnan(result.iloc[2])

    def test_zero_variance_window_is_nan_never_zero(self) -> None:
        """A flat window makes the z-score a genuine 0/0.

        Returning 0.0 would tell a strategy "this value is exactly at its mean",
        which is a confident, tradable claim; the truth is that with no
        dispersion there is no scale to measure a deviation against and the
        statistic does not exist.
        """
        result = rolling_zscore(s([5.0, 5.0, 5.0, 5.0]), 3)

        assert result.isna().all()
        # Explicitly: not merely "falsy", but NaN.
        assert not (result.fillna(-1.0) == 0.0).any()

    def test_only_the_flat_windows_are_undefined(self) -> None:
        # The window ending at index 2 is [5,5,5] — undefined. The next one,
        # [5,5,6], has dispersion again, so the indicator recovers rather than
        # being poisoned by the flat stretch behind it.
        result = rolling_zscore(s([5, 5, 5, 6]), 3)

        assert math.isnan(result.iloc[2])
        assert not math.isnan(result.iloc[3])

    def test_series_shorter_than_window_is_all_nan(self) -> None:
        result = rolling_zscore(s([1, 2]), 5)

        assert result.isna().all()
        assert len(result) == 2

    def test_sample_estimator_shrinks_the_score(self) -> None:
        # ddof=1 gives a larger standard deviation, so the same deviation is
        # fewer standard deviations away. Which estimator is used changes when a
        # strategy trades, so it is an explicit argument.
        population = rolling_zscore(s([1, 2, 3, 4, 5]), 3, ddof=0).iloc[2]
        sample = rolling_zscore(s([1, 2, 3, 4, 5]), 3, ddof=1).iloc[2]

        assert abs(sample) < abs(population)

    @pytest.mark.parametrize("bad", [1, 0, -3])
    def test_rejects_a_window_below_two(self, bad: int) -> None:
        # A one-bar window has zero dispersion by definition, so every z-score
        # would be undefined and the result would be NaN dressed up as an
        # indicator.
        with pytest.raises(ValueError, match="must be >= 2"):
            rolling_zscore(s([1, 2, 3]), bad)

    def test_rejects_a_non_integer_window(self) -> None:
        with pytest.raises(TypeError, match="must be an int"):
            rolling_zscore(s([1, 2, 3]), 3.0)  # type: ignore[arg-type]

    def test_is_causal(self) -> None:
        """Changing a future bar must not change a past value (NFR5.3)."""
        base = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        original = rolling_zscore(s(base), 3)

        mutated = base.copy()
        mutated[-1] = 10_000.0
        perturbed = rolling_zscore(s(mutated), 3)

        pd.testing.assert_series_equal(original.iloc[:-1], perturbed.iloc[:-1])

    def test_preserves_the_input_index(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="B", tz="Asia/Kolkata")
        result = rolling_zscore(pd.Series([1, 2, 3, 4, 5], index=index, dtype="float64"), 3)

        pd.testing.assert_index_equal(result.index, index)

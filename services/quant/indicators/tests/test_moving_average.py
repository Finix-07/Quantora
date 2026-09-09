"""Moving averages are checked against hand-computed values, not against
another library's output — testing pandas against pandas would prove nothing.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from services.quant.indicators import ema, rolling_std, sma


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


class TestSMA:
    def test_hand_computed_values(self) -> None:
        result = sma(s([1, 2, 3, 4, 5]), 3)

        # (1+2+3)/3 = 2, (2+3+4)/3 = 3, (3+4+5)/3 = 4
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_warmup_is_nan_not_a_partial_average(self) -> None:
        # A 3-bar mean computed from 2 observations is not a 3-bar mean. Emitting
        # one would feed a differently-defined number into a strategy's first
        # signals.
        result = sma(s([1, 2, 3, 4]), 3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert not math.isnan(result.iloc[2])

    def test_window_of_one_is_the_series_itself(self) -> None:
        values = [3.0, 1.0, 4.0]
        pd.testing.assert_series_equal(
            sma(s(values), 1), s(values).rename("sma_1"), check_names=True
        )

    def test_series_shorter_than_window_is_all_nan(self) -> None:
        result = sma(s([1, 2]), 5)

        assert result.isna().all()
        assert len(result) == 2

    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_non_positive_window(self, bad: int) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            sma(s([1, 2, 3]), bad)

    def test_rejects_non_integer_window(self) -> None:
        with pytest.raises(TypeError, match="must be an int"):
            sma(s([1, 2, 3]), 3.0)  # type: ignore[arg-type]

    def test_is_causal(self) -> None:
        """Changing a future bar must not change a past value (NFR5.3)."""
        base = [1.0, 2.0, 3.0, 4.0, 5.0]
        original = sma(s(base), 3)

        mutated = base.copy()
        mutated[-1] = 1000.0
        perturbed = sma(s(mutated), 3)

        pd.testing.assert_series_equal(original.iloc[:-1], perturbed.iloc[:-1])


class TestEMA:
    def test_seed_is_the_simple_mean_of_the_first_span_values(self) -> None:
        result = ema(s([1, 2, 3, 4, 5]), 3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)  # mean(1, 2, 3)

    def test_hand_computed_recursion(self) -> None:
        result = ema(s([1, 2, 3, 4, 5]), 3)

        alpha = 2 / (3 + 1)  # 0.5
        expected_3 = alpha * 4 + (1 - alpha) * 2.0  # 3.0
        expected_4 = alpha * 5 + (1 - alpha) * expected_3  # 4.0

        assert result.iloc[3] == pytest.approx(expected_3)
        assert result.iloc[4] == pytest.approx(expected_4)

    def test_constant_series_equals_the_constant(self) -> None:
        result = ema(s([7.0] * 10), 4)

        assert result.dropna().eq(7.0).all()

    def test_series_shorter_than_span_is_all_nan(self) -> None:
        assert ema(s([1, 2]), 5).isna().all()

    def test_seeding_choice_makes_the_value_slice_independent(self) -> None:
        """A date's EMA must not depend on how much history was requested.

        Seeding from the first observation (pandas' default) makes early values
        depend on one arbitrary bar, so the same date gets a different EMA in a
        1-year window than in a 5-year window — which would quietly break
        reproducibility across date ranges. The SMA seed converges instead.
        """
        rng = np.random.default_rng(11)
        long_path = pd.Series(100 + rng.normal(0, 1, 300).cumsum(), dtype="float64")

        full = ema(long_path, 12)
        # Recompute from a later start: by the tail the two must agree closely.
        truncated = ema(long_path.iloc[100:].reset_index(drop=True), 12)

        assert full.iloc[-1] == pytest.approx(truncated.iloc[-1], rel=1e-9)

    def test_is_causal(self) -> None:
        base = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        original = ema(s(base), 3)

        mutated = base.copy()
        mutated[-1] = -500.0
        perturbed = ema(s(mutated), 3)

        pd.testing.assert_series_equal(original.iloc[:-1], perturbed.iloc[:-1])

    def test_preserves_the_input_index(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="B", tz="Asia/Kolkata")
        result = ema(pd.Series([1, 2, 3, 4, 5], index=index, dtype="float64"), 3)

        pd.testing.assert_index_equal(result.index, index)


class TestRollingStd:
    def test_population_standard_deviation_by_default(self) -> None:
        # values 2,4,4 -> mean 10/3; population std computed by hand below.
        values = [2.0, 4.0, 4.0]
        result = rolling_std(s(values), 3)

        mean = sum(values) / 3
        expected = math.sqrt(sum((v - mean) ** 2 for v in values) / 3)
        assert result.iloc[2] == pytest.approx(expected)

    def test_sample_estimator_is_available_and_larger(self) -> None:
        values = [2.0, 4.0, 4.0]
        population = rolling_std(s(values), 3, ddof=0).iloc[2]
        sample = rolling_std(s(values), 3, ddof=1).iloc[2]

        # Which estimator is used changes Bollinger band width, so the choice is
        # explicit rather than an inherited default.
        assert sample > population

    def test_constant_series_has_zero_dispersion(self) -> None:
        assert rolling_std(s([5.0] * 6), 3).dropna().eq(0.0).all()

    def test_warmup_is_nan(self) -> None:
        result = rolling_std(s([1.0, 2.0, 3.0]), 3)

        assert result.iloc[:2].isna().all()

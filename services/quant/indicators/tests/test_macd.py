"""MACD is checked against values computed by hand from its own definition."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from services.quant.indicators import ema, macd


def s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


def test_macd_line_is_fast_ema_minus_slow_ema() -> None:
    prices = s(list(np.linspace(100, 140, 60)))

    result = macd(prices, fast=3, slow=6, signal=2)

    expected = ema(prices, 3) - ema(prices, 6)
    pd.testing.assert_series_equal(result.macd, expected.rename("macd"), check_names=True)


def test_signal_is_an_ema_of_the_macd_line_seeded_from_real_values() -> None:
    """The MACD line is NaN through the slow EMA's warm-up.

    Seeding the signal EMA on those NaNs would make the entire signal line NaN,
    so the leading NaNs are dropped before seeding and the result reindexed back.
    """
    prices = s([float(x) for x in range(1, 41)])

    result = macd(prices, fast=3, slow=6, signal=2)

    seeded = result.macd.dropna()
    expected = ema(seeded, 2).reindex(result.macd.index)

    assert not result.signal.dropna().empty, "signal line must not be entirely NaN"
    pd.testing.assert_series_equal(result.signal, expected.rename("macd_signal"), check_names=True)


def test_histogram_is_macd_minus_signal() -> None:
    prices = s(list(np.linspace(50, 80, 50)))

    result = macd(prices, fast=4, slow=9, signal=3)

    pd.testing.assert_series_equal(
        result.histogram, (result.macd - result.signal).rename("macd_histogram")
    )


def test_hand_computed_first_macd_value() -> None:
    # With fast=2 and slow=3 on a simple ramp, both EMAs are hand-computable.
    prices = s([1.0, 2.0, 3.0, 4.0])

    result = macd(prices, fast=2, slow=3, signal=2)

    # ema(2) seed at index 1 = mean(1,2) = 1.5; at index 2:
    #   a = 2/3 -> 2/3*3 + 1/3*1.5 = 2.5 ; at index 3: 2/3*4 + 1/3*2.5 = 3.5
    # ema(3) seed at index 2 = mean(1,2,3) = 2.0 ; at index 3:
    #   a = 0.5 -> 0.5*4 + 0.5*2 = 3.0
    assert math.isnan(result.macd.iloc[1]), "MACD is undefined until the slow EMA is seeded"
    assert result.macd.iloc[2] == pytest.approx(2.5 - 2.0)
    assert result.macd.iloc[3] == pytest.approx(3.5 - 3.0)


def test_rising_trend_puts_macd_above_zero() -> None:
    result = macd(s(list(np.linspace(100, 200, 120))))

    assert result.macd.dropna().iloc[-1] > 0


def test_falling_trend_puts_macd_below_zero() -> None:
    result = macd(s(list(np.linspace(200, 100, 120))))

    assert result.macd.dropna().iloc[-1] < 0


def test_flat_series_gives_zero_macd() -> None:
    result = macd(s([42.0] * 120))

    assert result.macd.dropna().abs().max() == pytest.approx(0.0, abs=1e-12)
    assert result.histogram.dropna().abs().max() == pytest.approx(0.0, abs=1e-12)


def test_rejects_fast_span_not_faster_than_slow() -> None:
    # A "fast" average slower than the "slow" one inverts the sign of every
    # crossover, silently turning a momentum strategy into its own opposite.
    with pytest.raises(ValueError, match="strictly less than slow"):
        macd(s([1.0] * 50), fast=26, slow=12)

    with pytest.raises(ValueError, match="strictly less than slow"):
        macd(s([1.0] * 50), fast=12, slow=12)


def test_is_causal() -> None:
    base = list(np.linspace(100, 130, 80))
    original = macd(s(base))

    mutated = base.copy()
    mutated[-1] = 10_000.0
    perturbed = macd(s(mutated))

    pd.testing.assert_series_equal(original.macd.iloc[:-1], perturbed.macd.iloc[:-1])
    pd.testing.assert_series_equal(original.signal.iloc[:-1], perturbed.signal.iloc[:-1])


def test_result_reports_the_parameters_it_used() -> None:
    # A saved experiment has to record the exact parameters behind a number
    # (requirements.md NFR4), so the result carries them rather than the caller
    # having to remember what it passed.
    result = macd(s(list(np.linspace(1, 100, 100))), fast=5, slow=20, signal=4)

    assert result.parameters == {"fast": 5, "slow": 20, "signal": 4}


def test_to_frame_has_stable_column_names() -> None:
    frame = macd(s(list(np.linspace(1, 100, 100)))).to_frame()

    assert list(frame.columns) == ["macd", "macd_signal", "macd_histogram"]


def test_short_series_returns_all_nan_rather_than_raising() -> None:
    # Too little history is a normal condition at the start of any window; it is
    # the strategy's job to refuse to trade on NaN, not the indicator's job to
    # invent a value.
    result = macd(s([1.0, 2.0, 3.0]))

    assert result.macd.isna().all()
    assert result.signal.isna().all()


def test_preserves_a_datetime_index() -> None:
    index = pd.date_range("2024-01-01", periods=100, freq="B", tz="Asia/Kolkata")
    prices = pd.Series(np.linspace(100, 150, 100), index=index, dtype="float64")

    result = macd(prices)

    pd.testing.assert_index_equal(result.macd.index, index)
    pd.testing.assert_index_equal(result.signal.index, index)

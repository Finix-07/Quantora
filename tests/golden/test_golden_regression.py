"""Golden regression tests (testing.md §3, mandatory).

One pinned dataset and one pinned expected result per strategy family. If a
change alters an important result, these fail — loudly and by name. Silent drift
is not acceptable (NFR5.6).

The bars are committed rather than fetched. A golden test that called yfinance
would fail whenever the provider was slow or had revised history, which trains
people to ignore it, and it could not separate a regression in our code from a
change in the data. Pinned bars make these tests answer exactly one question:
did our numbers change?

When one fails, the question is "did we mean to change this?". If yes, run
`python scripts/generate_golden_fixtures.py` and commit the new expectations
with an explanation. If no, the test has done its job.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.costs import CostModel
from services.quant.backtest.result import build_result
from services.quant.backtest.runner import run_backtest
from services.quant.strategies.registry import available, create
from services.quant.testing.golden import (
    dataset_metadata,
    load_dataset,
    load_expected,
)

PRIMARY_SYMBOL = "RELIANCE.NS"
PAIR_SYMBOL = "TCS.NS"

# One case per strategy family. The name is also the expectation file's name.
GOLDEN_CASES = ("macd", "bollinger", "dual_thrust", "pair_trading")

# Tight, but not bit-exact. The expectations are generated on one machine and
# checked on another, and float64 arithmetic is not bit-identical across CPU
# architectures: numpy accumulates a sum in a different order depending on the
# SIMD width available, so a compounded equity value can land a couple of ULPs
# away on x86_64 from where it landed on arm64. That is drift in the last digit
# or two, around 1e-15 relative at the worst observed. Comparing relatively
# absorbs it and still leaves six orders of magnitude between the noise floor
# and anything a real regression would move. Determinism on a single machine is
# checked directly, by test_rerunning_a_golden_case_twice_is_identical.
RELATIVE_TOLERANCE = 1e-9
# Relative comparison has no meaning for a value that is legitimately zero, so
# the two rules are applied together and the more generous one wins.
ABSOLUTE_TOLERANCE = 1e-9


def agrees(got: Any, want: Any) -> bool:
    """Whether a recorded value still matches, allowing cross-platform float drift."""
    if isinstance(got, bool) or isinstance(want, bool):
        return got is want
    if isinstance(got, int | float) and isinstance(want, int | float):
        return got == pytest.approx(want, rel=RELATIVE_TOLERANCE, abs=ABSOLUTE_TOLERANCE)
    return got == want


def drift_in_records(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]], label: str
) -> list[str]:
    """Name every field where a list of recorded rows disagrees.

    Comparing the lists with `==` would report only that two long lists differ
    and print both in full. Naming the row and the field says which number moved.
    """
    if len(actual) != len(expected):
        return [f"{label}: expected {len(expected)} rows, produced {len(actual)}"]

    drifted: list[str] = []
    for position, (got, want) in enumerate(zip(actual, expected, strict=True)):
        if got.keys() != want.keys():
            drifted.append(f"{label}[{position}]: fields {sorted(want)} -> {sorted(got)}")
            continue
        drifted.extend(
            f"{label}[{position}].{name}: {want[name]!r} -> {got[name]!r}"
            for name in want
            if not agrees(got[name], want[name])
        )
    return drifted


def regression_message(case: str, drifted: list[str]) -> str:
    return (
        f"{case} produced different results than the pinned expectation:\n  "
        + "\n  ".join(drifted)
        + "\n\nIf this change was intended, regenerate with "
        "`python scripts/generate_golden_fixtures.py` and explain it in the commit."
    )


def build_run(expected: dict[str, Any]):
    """Reproduce the pinned run exactly as the generator produced it."""
    primary = load_dataset(PRIMARY_SYMBOL)
    strategy = create(expected["strategy"], expected["parameters"])
    for symbol in strategy.auxiliary_symbols():
        strategy.attach_auxiliary_series(load_dataset(symbol))

    config = BacktestConfig(
        initial_cash=expected["backtest_config"]["initial_cash"],
        cost_model=CostModel.from_dict(expected["cost_model"]),
        allow_short=expected["backtest_config"]["allow_short"],
        liquidate_at_end=expected["backtest_config"]["liquidate_at_end"],
        risk_free_rate=expected["backtest_config"]["risk_free_rate"],
    )
    return primary, run_backtest(primary, strategy, config)


class TestPinnedDatasets:
    @pytest.mark.parametrize("symbol", [PRIMARY_SYMBOL, PAIR_SYMBOL])
    def test_the_committed_bars_still_hash_to_their_recorded_version(self, symbol: str) -> None:
        """The dataset itself is part of the fixture.

        If the CSV changes — an edit, a different writer precision, a re-fetch
        committed by accident — every downstream expectation becomes untrustworthy
        without any of them necessarily failing. This catches that first.
        """
        series = load_dataset(symbol)
        meta = dataset_metadata(symbol)

        assert series.data_version() == meta["data_version"], (
            f"the committed {symbol} bars no longer hash to the recorded data_version; "
            "the dataset changed, so the expectations below no longer describe it"
        )
        assert len(series) == meta["row_count"]

    @pytest.mark.parametrize("symbol", [PRIMARY_SYMBOL, PAIR_SYMBOL])
    def test_the_pinned_dataset_is_substantial(self, symbol: str) -> None:
        # A handful of bars would let every expectation below pass trivially.
        series = load_dataset(symbol)

        assert len(series) > 500, "the golden window is too short to be meaningful"
        assert series.provenance.provider == "yfinance"


def test_every_strategy_family_has_a_golden_fixture() -> None:
    """A new family must not ship without a regression fixture.

    testing.md §3 requires at least one per family, and a family with none would
    be free to drift undetected.
    """
    covered = {load_expected(case)["strategy"] for case in GOLDEN_CASES}

    assert covered == set(available()), (
        f"strategies without a golden fixture: {sorted(set(available()) - covered)}; "
        "add a case to scripts/generate_golden_fixtures.py"
    )


@pytest.mark.parametrize("case", GOLDEN_CASES)
class TestGoldenResults:
    def test_metrics_match_the_pinned_expectation(self, case: str) -> None:
        expected = load_expected(case)
        _, run = build_run(expected)
        actual = build_result(run).as_dict()["metrics"]

        drifted: list[str] = []
        for name, want in expected["metrics"].items():
            if name == "unavailable":
                continue
            got = actual.get(name)
            if want is None or got is None:
                # An available metric becoming unavailable (or the reverse) is
                # itself a regression, so the two must agree on that too.
                if want is not got:
                    drifted.append(f"{name}: {want!r} -> {got!r}")
                continue
            if not agrees(got, want):
                drifted.append(f"{name}: {want} -> {got}")

        assert not drifted, regression_message(case, drifted)

    def test_the_signal_summary_matches(self, case: str) -> None:
        """Metrics can coincide while the strategy's behaviour changed."""
        expected = load_expected(case)
        _, run = build_run(expected)
        actual = build_result(run).as_dict()["signal_summary"]

        assert actual == expected["signal_summary"]

    def test_the_trade_log_boundaries_match(self, case: str) -> None:
        """Averages can hide a changed trade; the log cannot."""
        expected = load_expected(case)
        _, run = build_run(expected)
        trades = build_result(run).as_dict()["trades"]

        drifted = drift_in_records(trades[:5], expected["first_trades"], "first_trades")
        drifted += drift_in_records(trades[-5:], expected["last_trades"], "last_trades")

        assert not drifted, regression_message(case, drifted)

    def test_the_equity_curve_endpoints_match(self, case: str) -> None:
        expected = load_expected(case)
        _, run = build_run(expected)
        curve = build_result(run).as_dict()["equity_curve"]

        drifted = drift_in_records(curve[:3], expected["equity_curve_head"], "equity_curve_head")
        drifted += drift_in_records(curve[-3:], expected["equity_curve_tail"], "equity_curve_tail")

        assert not drifted, regression_message(case, drifted)

    def test_the_expectation_describes_the_committed_dataset(self, case: str) -> None:
        """Guards against expectations pinned against different bars."""
        expected = load_expected(case)
        primary, _ = build_run(expected)

        assert expected["data_version"] == primary.data_version()

    def test_the_fixture_actually_traded(self, case: str) -> None:
        """A zero-trade fixture would pass forever while testing nothing."""
        expected = load_expected(case)

        assert expected["metrics"]["trade_count"] > 0, (
            f"the {case} golden fixture produced no trades, so it cannot detect a regression; "
            "choose a window or parameters that trade"
        )

    def test_costs_were_charged(self, case: str) -> None:
        # A frictionless fixture would never exercise the commission, slippage
        # or spread paths.
        expected = load_expected(case)

        assert expected["metrics"]["total_costs"] > 0
        assert expected["cost_model"]["commission_bps"] > 0


def test_rerunning_a_golden_case_twice_is_identical() -> None:
    """Determinism, checked directly rather than inferred from the fixtures."""
    expected = load_expected("macd")

    _, first = build_run(expected)
    _, second = build_run(expected)

    assert build_result(first).as_dict()["metrics"] == build_result(second).as_dict()["metrics"]

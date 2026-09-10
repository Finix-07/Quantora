"""Python/C++ execution parity — mandatory and non-trimmable.

testing.md §2.2 and §8: the same inputs must produce matching results from the
Python and the C++ engines before the C++ path is trusted at all. A fast engine
that disagrees with the reference one is not an optimisation, it is a second
source of truth.

The comparison is made at the *parity surface* both engines are built around
(see `engine.simulate_targets` and `services/execution-cpp/include/quant/
simulator.hpp`): bars in, one target quantity per bar in, portfolio state out.
Deriving the targets is the strategy's job and stays in Python, so it is held
fixed here — otherwise a difference in signal generation would masquerade as an
execution difference.

Every scenario is checked field by field, not by a summary statistic: two engines
can agree on final equity while disagreeing about which trades produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from services.quant.backtest import cpp_engine
from services.quant.backtest.config import BacktestConfig, ExecutionModel
from services.quant.backtest.costs import CostModel
from services.quant.backtest.engine import SimulationOutput
from services.quant.backtest.engine import simulate_targets as python_simulate_targets
from services.quant.testing import golden, make_price_series

pytestmark = pytest.mark.skipif(
    not cpp_engine.is_available(),
    reason=(
        f"the C++ execution engine is not built ({cpp_engine.unavailable_reason()}); "
        f"build it with `{cpp_engine.BUILD_COMMAND}`"
    ),
)

# Both engines evaluate the same expressions in the same order — the C++ build
# turns FMA contraction off precisely so that stays true (see CMakeLists.txt) —
# and in practice every number below matches bit for bit on this toolchain. The
# tolerance is not there to absorb a known difference; it is a guard so that a
# platform whose libm rounds one ULP differently reports a *drift*, not a false
# alarm. It is set four orders of magnitude tighter than the 1e-9 floor
# testing.md allows, and far tighter than any real regression would be.
TOLERANCE = 1e-12

SYMBOL = "RELIANCE.NS"


def close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= TOLERANCE * max(1.0, abs(actual), abs(expected))


@dataclass(frozen=True)
class Scenario:
    """One deterministic execution problem, run through both engines."""

    name: str
    frame: pd.DataFrame
    targets: list[float]
    reasons: list[str]
    config: BacktestConfig


def _frame(closes: list[float]) -> pd.DataFrame:
    return make_price_series(closes, symbol=SYMBOL).frame


def _wiggle(n: int, seed: int, *, start: float = 100.0, scale: float = 1.5) -> list[float]:
    """A deterministic pseudo-random walk that actually moves."""
    rng = np.random.default_rng(seed)
    return list(start + np.cumsum(rng.normal(0.0, scale, n)))


def _alternating(n: int, quantity: float, period: int) -> list[float]:
    """Targets that flip between a position and flat, so trades really close."""
    return [quantity if (i // period) % 2 == 0 else 0.0 for i in range(n)]


def _crossover_targets(frame: pd.DataFrame, *, fast: int, slow: int, notional: float) -> list[float]:
    """Whole-unit long/flat targets from a moving-average crossover.

    Computed here rather than by a Strategy so this file has no dependency on
    the strategy layer: the thing under test is execution, and a target array is
    the entire input it needs. The `shift(1)` keeps the rule causal, which is
    not required for parity but keeps the fixture honest about what it models.
    """
    price = frame["close"]
    signal = (price.rolling(fast).mean() > price.rolling(slow).mean()).shift(1).fillna(False)
    size = np.floor(notional / price.to_numpy())
    return [float(q) if long else 0.0 for long, q in zip(signal, size, strict=True)]


def scenarios() -> list[Scenario]:
    """The parity fixtures, one per behaviour the executor has to get right."""
    n = 240
    walk = _wiggle(n, seed=11)
    flat_market = [100.0] * 40

    reversal_targets = [
        (50.0 if (i // 25) % 2 == 0 else -50.0) if i >= 20 else 0.0 for i in range(n)
    ]

    golden_frame = golden.load_dataset(SYMBOL).frame

    return [
        Scenario(
            name="long_only",
            frame=_frame(walk),
            targets=_alternating(n, 100.0, 30),
            reasons=[f"bar {i}" for i in range(n)],
            config=BacktestConfig(),
        ),
        Scenario(
            name="short_with_reversal",
            frame=_frame(walk),
            targets=reversal_targets,
            reasons=["flip"] * n,
            config=BacktestConfig(allow_short=True),
        ),
        Scenario(
            name="cash_constrained_order_is_reduced",
            frame=_frame(_wiggle(60, seed=5, start=200.0, scale=2.0)),
            # 400 units at ~200 is ~80,000 against 10,000 of cash, so the first
            # buy cannot fill in full and has to be cut to what cash allows.
            targets=_alternating(60, 400.0, 15),
            reasons=["oversized"] * 60,
            config=BacktestConfig(initial_cash=10_000.0),
        ),
        Scenario(
            name="cash_starved_order_is_skipped",
            frame=_frame([5_000.0] * 20),
            targets=[10.0] * 20,
            reasons=["unaffordable"] * 20,
            config=BacktestConfig(initial_cash=1_000.0),
        ),
        Scenario(
            name="zero_costs",
            frame=_frame(walk),
            targets=_alternating(n, 100.0, 30),
            reasons=[""] * n,
            config=BacktestConfig(cost_model=CostModel.zero()),
        ),
        Scenario(
            name="liquidation_on",
            frame=_frame(walk),
            # Still holding at the last bar, so the close-out actually happens.
            targets=[100.0] * n,
            reasons=["hold"] * n,
            config=BacktestConfig(liquidate_at_end=True),
        ),
        Scenario(
            name="liquidation_off",
            frame=_frame(walk),
            targets=[100.0] * n,
            reasons=["hold"] * n,
            config=BacktestConfig(liquidate_at_end=False),
        ),
        Scenario(
            name="no_trade",
            frame=_frame(flat_market),
            targets=[0.0] * len(flat_market),
            reasons=[""] * len(flat_market),
            config=BacktestConfig(),
        ),
        Scenario(
            name="next_bar_close_execution",
            frame=_frame(walk),
            targets=_alternating(n, 100.0, 17),
            reasons=["close fill"] * n,
            config=BacktestConfig(execution_model=ExecutionModel.NEXT_BAR_CLOSE),
        ),
        Scenario(
            name="golden_reliance_crossover",
            frame=golden_frame,
            targets=_crossover_targets(golden_frame, fast=20, slow=50, notional=200_000.0),
            reasons=["golden"] * len(golden_frame),
            config=BacktestConfig(),
        ),
    ]


SCENARIOS = {scenario.name: scenario for scenario in scenarios()}


def run_both(scenario: Scenario) -> tuple[SimulationOutput, SimulationOutput]:
    kwargs = {
        "symbol": SYMBOL,
        "reasons": scenario.reasons,
        "config": scenario.config,
    }
    python_output = python_simulate_targets(scenario.frame, scenario.targets, **kwargs)
    cpp_output = cpp_engine.simulate_targets(scenario.frame, scenario.targets, **kwargs)
    return python_output, cpp_output


@pytest.fixture(scope="module")
def outputs() -> dict[str, tuple[SimulationOutput, SimulationOutput]]:
    """Run every scenario once; the assertions below slice into the results."""
    return {name: run_both(scenario) for name, scenario in SCENARIOS.items()}


NAMES = sorted(SCENARIOS)


@pytest.mark.parametrize("name", NAMES)
def test_equity_curve_matches(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    python_equity = python_output.equity_curve
    cpp_equity = cpp_output.equity_curve

    assert list(cpp_equity.index) == list(python_equity.index)
    assert len(cpp_equity) == len(scenario_of(name).frame)
    for timestamp, expected, actual in zip(
        python_equity.index, python_equity, cpp_equity, strict=True
    ):
        assert close(actual, expected), f"{name}: equity differs at {timestamp}"


@pytest.mark.parametrize("name", NAMES)
def test_exposure_matches(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    for timestamp, expected, actual in zip(
        python_output.exposure.index, python_output.exposure, cpp_output.exposure, strict=True
    ):
        assert close(actual, expected), f"{name}: exposure differs at {timestamp}"


@pytest.mark.parametrize("name", NAMES)
def test_every_fill_matches(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    assert len(cpp_output.fills) == len(python_output.fills), f"{name}: fill count differs"

    for i, (expected, actual) in enumerate(
        zip(python_output.fills, cpp_output.fills, strict=True)
    ):
        where = f"{name}: fill {i} at {expected.timestamp}"
        assert actual.timestamp == expected.timestamp, where
        assert actual.side is expected.side, where
        assert actual.reason == expected.reason, where
        for field in ("quantity", "reference_price", "fill_price", "commission", "slippage_cost"):
            assert close(getattr(actual, field), getattr(expected, field)), f"{where}: {field}"


@pytest.mark.parametrize("name", NAMES)
def test_every_order_matches(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    assert len(cpp_output.orders) == len(python_output.orders), f"{name}: order count differs"

    for i, (expected, actual) in enumerate(
        zip(python_output.orders, cpp_output.orders, strict=True)
    ):
        where = f"{name}: order {i}"
        # Both timestamps, so the look-ahead guarantee is compared too, not just
        # the fill it produced.
        assert actual.decision_timestamp == expected.decision_timestamp, where
        assert actual.timestamp == expected.timestamp, where
        assert actual.side is expected.side, where
        assert actual.reason == expected.reason, where
        assert close(actual.quantity, expected.quantity), where


@pytest.mark.parametrize("name", NAMES)
def test_every_trade_matches(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    python_trades = python_output.portfolio.trades
    cpp_trades = cpp_output.portfolio.trades
    assert len(cpp_trades) == len(python_trades), f"{name}: trade count differs"

    for i, (expected, actual) in enumerate(zip(python_trades, cpp_trades, strict=True)):
        where = f"{name}: trade {i}"
        assert actual.direction == expected.direction, where
        assert actual.entry_timestamp == expected.entry_timestamp, where
        assert actual.exit_timestamp == expected.exit_timestamp, where
        assert actual.bars_held == expected.bars_held, where
        assert actual.entry_reason == expected.entry_reason, where
        assert actual.exit_reason == expected.exit_reason, where
        for field in (
            "quantity",
            "entry_price",
            "exit_price",
            "gross_pnl",
            "costs",
            "pnl",
            "return_pct",
        ):
            assert close(getattr(actual, field), getattr(expected, field)), f"{where}: {field}"


@pytest.mark.parametrize("name", NAMES)
def test_per_bar_position_events_match(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    assert len(cpp_output.positions) == len(python_output.positions)

    for expected, actual in zip(python_output.positions, cpp_output.positions, strict=True):
        where = f"{name}: position at {expected.timestamp}"
        assert actual.timestamp == expected.timestamp, where
        for field in (
            "quantity",
            "average_price",
            "cash",
            "market_value",
            "unrealized_pnl",
            "realized_pnl",
        ):
            assert close(getattr(actual, field), getattr(expected, field)), f"{where}: {field}"


@pytest.mark.parametrize("name", NAMES)
def test_final_state_and_cost_totals_match(name: str, outputs) -> None:
    python_output, cpp_output = outputs[name]
    python_portfolio = python_output.portfolio
    cpp_portfolio = cpp_output.portfolio

    assert close(cpp_portfolio.cash, python_portfolio.cash), f"{name}: final cash"
    assert close(
        cpp_portfolio.quantity(SYMBOL), python_portfolio.quantity(SYMBOL)
    ), f"{name}: final position"
    assert close(
        cpp_portfolio.realized_pnl, python_portfolio.realized_pnl
    ), f"{name}: realized PnL"
    for field in ("total_commission", "total_slippage", "total_traded_notional"):
        assert close(
            getattr(cpp_portfolio, field), getattr(python_portfolio, field)
        ), f"{name}: {field}"


@pytest.mark.parametrize("name", NAMES)
def test_warnings_match_word_for_word(name: str, outputs) -> None:
    """A constrained fill must be reported identically, not merely counted.

    Both engines route the wording through `engine.skipped_buy_warning` /
    `reduced_buy_warning`, so this asserts the C++ side reported the same
    *facts* — which bar, how much was asked for, how much was affordable.
    """
    python_output, cpp_output = outputs[name]
    assert cpp_output.warnings == python_output.warnings


def scenario_of(name: str) -> Scenario:
    return SCENARIOS[name]


# ------------------------------------------------------- the test is not vacuous


def test_the_scenarios_actually_trade(outputs) -> None:
    """Without this, every assertion above would pass on two idle engines.

    A parity suite whose fixtures never open a position proves only that both
    engines can do nothing identically.
    """
    total_fills = sum(len(python.fills) for python, _ in outputs.values())
    total_trades = sum(len(python.portfolio.trades) for python, _ in outputs.values())
    assert total_fills > 50, f"the parity fixtures produced only {total_fills} fills"
    assert total_trades > 20, f"the parity fixtures produced only {total_trades} trades"


def test_each_trading_scenario_produced_fills(outputs) -> None:
    # Two scenarios are *meant* to fill nothing and have their own assertions
    # below; every other one has to trade or it is testing an idle engine.
    deliberately_idle = {"no_trade", "cash_starved_order_is_skipped"}
    for name, (python_output, _) in outputs.items():
        if name in deliberately_idle:
            continue
        assert python_output.fills, f"{name} produced no fills and so tests nothing"


def test_the_no_trade_scenario_really_does_nothing(outputs) -> None:
    python_output, cpp_output = outputs["no_trade"]
    assert not python_output.fills
    assert not cpp_output.fills


def test_the_reversal_scenario_actually_reverses(outputs) -> None:
    python_output, _ = outputs["short_with_reversal"]
    quantities = [event.quantity for event in python_output.positions]
    assert any(q > 0 for q in quantities), "no long position was ever held"
    assert any(q < 0 for q in quantities), "no short position was ever held"
    # A reversal is a single fill larger than the position it replaced, which is
    # the accounting case that closes and re-opens rather than blending.
    assert any(trade.direction == "short" for trade in python_output.portfolio.trades)


def test_the_cash_constrained_scenario_actually_reduced_an_order(outputs) -> None:
    python_output, cpp_output = outputs["cash_constrained_order_is_reduced"]
    assert any("reduced a buy" in warning for warning in python_output.warnings)
    assert any("reduced a buy" in warning for warning in cpp_output.warnings)


def test_the_cash_starved_scenario_actually_skipped_an_order(outputs) -> None:
    python_output, cpp_output = outputs["cash_starved_order_is_skipped"]
    assert any("skipped a buy" in warning for warning in python_output.warnings)
    assert any("skipped a buy" in warning for warning in cpp_output.warnings)
    assert not python_output.fills


def test_the_liquidation_scenarios_really_differ(outputs) -> None:
    """`liquidate_at_end` must change the outcome, or testing both proves nothing."""
    liquidated, _ = outputs["liquidation_on"]
    held, _ = outputs["liquidation_off"]
    assert liquidated.portfolio.quantity(SYMBOL) == 0.0
    assert held.portfolio.quantity(SYMBOL) != 0.0
    assert len(liquidated.fills) == len(held.fills) + 1


def test_the_golden_scenario_uses_the_pinned_dataset(outputs) -> None:
    """The realistic case: real NSE bars, hundreds of trades, both engines."""
    python_output, cpp_output = outputs["golden_reliance_crossover"]
    assert len(python_output.equity_curve) == len(golden.load_dataset(SYMBOL))
    assert len(python_output.portfolio.trades) > 5
    assert close(
        float(cpp_output.equity_curve.iloc[-1]), float(python_output.equity_curve.iloc[-1])
    )

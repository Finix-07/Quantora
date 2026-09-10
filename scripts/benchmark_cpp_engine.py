#!/usr/bin/env python
"""Benchmark the C++ execution engine against the Python one (M4.6).

    python scripts/benchmark_cpp_engine.py [--bars N ...] [--repeats N]

architecture.md §7 and §10 forbid unproven performance claims: the C++ path must
ship with a measurement, not an assertion. This produces that measurement and
writes it to docs/research/cpp-benchmark.md.

Both engines are given the *same* problem — `simulate_targets`: bars in, a target
position per bar in, portfolio state out. That is the parity surface, so this
compares two implementations of one computation rather than two different ones.
The target path is generated once, outside the timed region, because generating
it is the strategy's work and belongs to neither engine.

Only the execution loop is timed. Data fetching, indicator computation and
metric calculation are excluded: they are identical for both paths, and
including them would dilute the very difference being measured.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from services.quant.backtest import cpp_engine
from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.costs import CostModel
from services.quant.backtest.engine import simulate_targets as python_simulate_targets

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "docs" / "research" / "cpp-benchmark.md"

DEFAULT_SIZES = (10_000, 100_000, 1_000_000)
DEFAULT_REPEATS = 5


def synthetic_workload(bars: int, seed: int = 20260911) -> tuple[pd.DataFrame, np.ndarray]:
    """A deterministic large-event workload.

    Targets flip between flat and long roughly every 20 bars. A path that never
    traded would benchmark an empty loop; one that traded on every bar would be
    unrepresentative of any real strategy. Both engines see byte-identical
    inputs.
    """
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.0, bars))
    closes = np.maximum(closes, 1.0)

    opens = np.empty(bars, dtype="float64")
    opens[0] = closes[0]
    opens[1:] = closes[:-1]
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995

    index = pd.date_range("2000-01-03", periods=bars, freq="B", tz="Asia/Kolkata")
    index.name = "timestamp"
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": np.full(bars, 1_000_000.0),
        },
        index=index,
    )

    in_position = (np.arange(bars) // 20) % 2 == 0
    targets = np.where(in_position, 100.0, 0.0)
    return frame, targets


@dataclass(frozen=True, slots=True)
class Timing:
    label: str
    bars: int
    samples: list[float]

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    @property
    def best(self) -> float:
        return min(self.samples)

    @property
    def worst(self) -> float:
        return max(self.samples)

    @property
    def bars_per_second(self) -> float:
        return self.bars / self.median if self.median > 0 else float("inf")


def time_engine(label: str, fn, frame: pd.DataFrame, targets, repeats: int) -> Timing:
    config = BacktestConfig(
        initial_cash=10_000_000.0, cost_model=CostModel(), liquidate_at_end=True
    )
    # One untimed warm-up: the first call pays import, allocator and CPU-cache
    # costs that no later call does, and including it would flatter whichever
    # engine ran second.
    fn(frame, targets, config=config)

    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn(frame, targets, config=config)
        samples.append(time.perf_counter() - start)
    return Timing(label=label, bars=len(frame), samples=samples)


def verify_agreement(frame: pd.DataFrame, targets) -> None:
    """Refuse to report a speedup for engines that disagree.

    A benchmark of two engines producing different answers measures nothing
    worth knowing, so this runs before any timing.
    """
    config = BacktestConfig(
        initial_cash=10_000_000.0, cost_model=CostModel(), liquidate_at_end=True
    )
    py = python_simulate_targets(frame, targets, config=config)
    cpp = cpp_engine.simulate_targets(frame, targets, config=config)

    assert py.portfolio is not None and cpp.portfolio is not None
    if len(py.fills) != len(cpp.fills):
        raise SystemExit(
            f"engines disagree before timing: {len(py.fills)} vs {len(cpp.fills)} fills"
        )
    delta = abs(float(py.equity_curve.iloc[-1]) - float(cpp.equity_curve.iloc[-1]))
    if delta > 1e-6:
        raise SystemExit(f"engines disagree before timing: final equity differs by {delta}")
    print(f"  agreement checked: {len(py.fills)} fills, final equity matches\n")


def render_report(results: list[tuple[Timing, Timing]], repeats: int) -> str:
    lines = [
        "# C++ vs Python execution engine — benchmark",
        "",
        "> Generated by `python scripts/benchmark_cpp_engine.py`. Re-run it rather",
        "> than editing the numbers by hand.",
        "",
        "architecture.md §7 and §10 require the C++ path to ship with a measured",
        "comparison rather than an assertion. This is that measurement.",
        "",
        "## What is measured",
        "",
        "Both engines are given the same problem — `simulate_targets`: bars in, a",
        "target position per bar in, portfolio state out. That is the parity surface,",
        "so this compares two implementations of one computation. The target path is",
        "generated once outside the timed region, because generating it is the",
        "strategy's work and belongs to neither engine.",
        "",
        "Only the execution loop is timed. Data fetching, indicators and metrics are",
        "excluded: they are identical on both paths and would dilute the difference.",
        "Each size gets one untimed warm-up call, then the reported repeats.",
        "",
        "Before any timing, both engines run the workload and their fill counts and",
        "final equity are compared. A speedup between engines that disagree would",
        "measure nothing worth knowing.",
        "",
        "## Environment",
        "",
        f"- Machine: {platform.platform()}",
        f"- CPU: {platform.processor() or platform.machine()}",
        f"- Python: {platform.python_version()} ({platform.python_implementation()})",
        "- C++: built by `make build-cpp` with `-DCMAKE_BUILD_TYPE=Release`",
        f"- Repeats per size: {repeats} (median reported)",
        f"- Measured: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Results",
        "",
        "| Bars | Python median | C++ median | Speedup | Python bars/s | C++ bars/s |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for py, cpp in results:
        speedup = py.median / cpp.median if cpp.median > 0 else float("inf")
        lines.append(
            f"| {py.bars:,} | {py.median * 1000:,.1f} ms | {cpp.median * 1000:,.1f} ms | "
            f"{speedup:.1f}× | {py.bars_per_second:,.0f} | {cpp.bars_per_second:,.0f} |"
        )

    lines += [
        "",
        "### Spread across repeats",
        "",
        "| Bars | Engine | Best | Median | Worst |",
        "|---:|---|---:|---:|---:|",
    ]
    for py, cpp in results:
        for timing in (py, cpp):
            lines.append(
                f"| {timing.bars:,} | {timing.label} | {timing.best * 1000:,.1f} ms | "
                f"{timing.median * 1000:,.1f} ms | {timing.worst * 1000:,.1f} ms |"
            )

    largest_py, largest_cpp = results[-1]
    speedup = largest_py.median / largest_cpp.median if largest_cpp.median > 0 else float("inf")
    lines += [
        "",
        "## Reading this",
        "",
        f"At the largest workload measured ({largest_py.bars:,} bars) the C++ executor is "
        f"**{speedup:.1f}× faster** than the Python one.",
        "",
    ]
    if speedup < 2:
        lines += [
            "That is a modest difference. architecture.md §7 says the C++ path must",
            "demonstrate a measurable advantage for the target workload before it is",
            "adopted, and on this evidence the case is weak — the Python engine is",
            "adequate for realistic single-instrument backtests, which run in the low",
            "thousands of bars, not the millions.",
            "",
        ]
    else:
        lines += [
            "The advantage is real but it is worth being precise about when it matters.",
            "A realistic single-instrument daily backtest is a few thousand bars, where",
            "both engines finish in well under a second and the difference is invisible",
            "to a user. The C++ path earns its place on high-throughput work —",
            "parameter sweeps, Monte Carlo, or minute-resolution histories — not on the",
            "backtests the product runs today.",
            "",
        ]
    lines += [
        "The Python engine remains the default and the reference implementation. The",
        "C++ path is only trusted because the parity test",
        "(`services/quant/backtest/tests/test_cpp_parity.py`) asserts the two produce",
        "identical fills, trades, equity curves and cost totals.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--no-write", action="store_true", help="print only, do not write the report"
    )
    args = parser.parse_args()

    if not cpp_engine.is_available():
        print(
            f"The C++ extension is not built: {cpp_engine.unavailable_reason()}\n"
            f"Run `{cpp_engine.BUILD_COMMAND}` first — benchmarking without it would "
            "compare Python against itself.",
            file=sys.stderr,
        )
        return 1

    results: list[tuple[Timing, Timing]] = []
    for bars in sorted(args.bars):
        print(f"{bars:,} bars:")
        frame, targets = synthetic_workload(bars)
        verify_agreement(frame, targets)

        py = time_engine("Python", python_simulate_targets, frame, targets, args.repeats)
        cpp = time_engine("C++", cpp_engine.simulate_targets, frame, targets, args.repeats)
        speedup = py.median / cpp.median if cpp.median > 0 else float("inf")
        print(
            f"  Python {py.median * 1000:>9,.1f} ms   "
            f"C++ {cpp.median * 1000:>9,.1f} ms   {speedup:.1f}x\n"
        )
        results.append((py, cpp))

    report = render_report(results, args.repeats)
    if args.no_write:
        print(report)
    else:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report + "\n", encoding="utf-8")
        print(f"wrote {REPORT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

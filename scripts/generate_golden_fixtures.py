#!/usr/bin/env python
"""Regenerate the golden regression fixtures (testing.md §3).

Run deliberately, never automatically:

    python scripts/generate_golden_fixtures.py

Golden tests exist to make an unintended change in a strategy or the engine fail
loudly. A generator wired into the test run would rewrite the expectations to
match whatever the code now produces, which is the same as having no test. So
this is a separate command, and regenerating is an explicit decision a reviewer
can see in the diff.

When a golden test fails, the question is always "did we mean to change this?".
If yes, run this and commit the new expectations with an explanation. If no, the
test has done its job.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from services.quant.backtest.config import BacktestConfig
from services.quant.backtest.costs import CostModel
from services.quant.backtest.result import build_result
from services.quant.backtest.runner import run_backtest
from services.quant.data.service import get_prices
from services.quant.strategies.registry import create
from services.quant.testing.golden import load_dataset, save_dataset, save_expected

# Pinned to the confirmed MVP universe (planning.md §4 decision 3) over a fixed
# window. TCS.NS is included because pair trading needs a second leg; using two
# instruments from the same universe keeps the fixture representative.
PRIMARY_SYMBOL = "RELIANCE.NS"
PAIR_SYMBOL = "TCS.NS"
START = "2020-01-01"
END = "2023-12-31"
INTERVAL = "1d"

# A fixed, non-zero cost model. A frictionless golden fixture would not exercise
# the commission, slippage or spread paths at all.
COST_MODEL = CostModel(commission_bps=3.0, commission_min=0.0, slippage_bps=5.0, spread_bps=2.0)

# One configuration per strategy family (testing.md §3 asks for at least one per
# family). Parameters are pinned explicitly rather than relying on defaults, so
# a change to a default is caught as a change rather than silently absorbed.
CASES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("macd", "macd", {"fast": 12, "slow": 26, "signal": 9, "allow_short": False}),
    ("bollinger", "bollinger", {"window": 20, "num_std": 2.0, "exit_at_middle": True}),
    ("dual_thrust", "dual_thrust", {"lookback": 4, "k_upper": 0.5, "k_lower": 0.5}),
    (
        "pair_trading",
        "pair_trading",
        {"pair_symbol": PAIR_SYMBOL, "lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
    ),
)


def refresh_datasets() -> None:
    """Fetch and pin the bars. Only this step touches the network."""
    for symbol in (PRIMARY_SYMBOL, PAIR_SYMBOL):
        print(f"fetching {symbol} {START}..{END} ...", flush=True)
        data = get_prices(symbol, START, END, INTERVAL)
        save_dataset(data.series)
        print(f"  {len(data.series)} bars, data_version={data.data_version}")


def build_expectations() -> None:
    """Run each family against the pinned bars and record the results."""
    primary = load_dataset(PRIMARY_SYMBOL)
    pair = load_dataset(PAIR_SYMBOL)

    config = BacktestConfig(
        initial_cash=1_000_000.0,
        cost_model=COST_MODEL,
        allow_short=True,
        liquidate_at_end=True,
    )

    for name, strategy_name, parameters in CASES:
        strategy = create(strategy_name, parameters)
        for symbol in strategy.auxiliary_symbols():
            strategy.attach_auxiliary_series(
                pair if symbol == PAIR_SYMBOL else load_dataset(symbol)
            )

        run = run_backtest(primary, strategy, config)
        payload = build_result(run).as_dict()

        save_expected(
            name,
            {
                "strategy": strategy_name,
                "parameters": parameters,
                "symbol": PRIMARY_SYMBOL,
                "start": START,
                "end": END,
                "interval": INTERVAL,
                "cost_model": COST_MODEL.as_dict(),
                "backtest_config": config.as_dict(),
                "data_version": primary.data_version(),
                "metrics": payload["metrics"],
                "signal_summary": payload["signal_summary"],
                # Enough of the trade log to catch a change the metrics could
                # average away, without pinning a thousand rows.
                "first_trades": payload["trades"][:5],
                "last_trades": payload["trades"][-5:],
                "equity_curve_head": payload["equity_curve"][:3],
                "equity_curve_tail": payload["equity_curve"][-3:],
            },
        )
        metrics = payload["metrics"]
        print(
            f"{name:<14} trades={metrics['trade_count']:>4} "
            f"return={metrics['total_return']:>9.2%} "
            f"sharpe={metrics['sharpe'] if metrics['sharpe'] is None else round(metrics['sharpe'], 3)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="reuse the committed bars instead of re-fetching (recompute expectations only)",
    )
    args = parser.parse_args()

    if args.keep_data:
        print("reusing the committed datasets; only expectations will be rewritten\n")
    else:
        refresh_datasets()
        print()

    build_expectations()
    print("\nReview the diff before committing: a changed expectation is a changed result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

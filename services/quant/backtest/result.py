"""The serialized backtest result contract.

architecture.md §8 fixes the core shape. This module is the single place a
:class:`~services.quant.backtest.runner.BacktestRun` becomes JSON, so a change
to the wire format cannot silently change what the engine computed.

The contract carries more than the numbers, because a number without its
assumptions is not inspectable (NFR4): provenance and data quality come along,
the cost model and execution assumptions are stated in plain language, and any
metric that could not be computed is accompanied by the reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

from services.quant.backtest.metrics import drawdown_curve
from services.quant.backtest.runner import BacktestRun

#: Bumped when the shape below changes incompatibly, so a stored experiment can
#: be recognised as belonging to an older contract instead of being misread.
RESULT_CONTRACT_VERSION = 1


def code_version() -> str:
    """Identifier for the code that produced a result (architecture.md §9).

    Supplied by the deployment (the API stamps its build version into
    APP_VERSION); "dev" locally. Stored with every experiment so a reproduced
    result can be attributed to a build.
    """
    return os.environ.get("APP_VERSION", "dev")


def _curve(series: pd.Series) -> list[dict[str, Any]]:
    return [
        {"timestamp": ts.isoformat(), "value": None if pd.isna(v) else float(v)}
        for ts, v in series.items()
    ]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """A backtest, serializable and persistable."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]


def build_result(run: BacktestRun, *, experiment_id: str | None = None) -> BacktestResult:
    """Serialize a completed run into the result contract."""
    series = run.series
    provenance = series.provenance
    equity = run.simulation.equity_curve
    signals = run.signals

    transitions = signals.transitions()
    signal_summary = {
        "warmup_bars": signals.warmup_bars,
        "signal_changes": int(len(transitions)),
        "bars_long": int((signals.frame["direction"] > 0).sum()),
        "bars_short": int((signals.frame["direction"] < 0).sum()),
        "bars_flat": int((signals.frame["direction"] == 0).sum()),
    }

    payload: dict[str, Any] = {
        "contract_version": RESULT_CONTRACT_VERSION,
        "experiment_id": experiment_id,
        "strategy": run.strategy.name,
        "strategy_family": run.strategy.family,
        "symbol": series.symbol,
        "interval": series.interval,
        "start": provenance.requested_start,
        "end": provenance.requested_end,
        "parameters": run.strategy.parameters,
        "cost_model": run.config.cost_model.as_dict(),
        "backtest_config": run.config.as_dict(),
        # Plain-language assumptions, shown next to the metrics. Reliability
        # rule 2 forbids hiding cost assumptions, and a user cannot judge a
        # result without knowing how fills were modelled.
        "assumptions": run.config.assumptions(),
        "metrics": run.metrics.as_dict(),
        "trades": [t.as_dict() for t in run.trades],
        "equity_curve": _curve(equity),
        "drawdown_curve": _curve(drawdown_curve(equity)),
        "signal_summary": signal_summary,
        "data_version": series.data_version(),
        "provenance": provenance.as_dict(),
        "data_quality": run.data_quality,
        # Order reductions and skipped fills. Empty is the normal case; a
        # non-empty list means the result differs from what the strategy asked
        # for, and the user has to see that.
        "warnings": list(run.simulation.warnings),
        "code_version": code_version(),
    }
    return BacktestResult(payload=payload)


def summarize_result(payload: dict[str, Any]) -> dict[str, Any]:
    """A compact view for comparison tables and the AI layer.

    Drops the curves and the trade list, which are large and rarely needed when
    the question is "how did these strategies compare?".
    """
    return {
        "experiment_id": payload.get("experiment_id"),
        "strategy": payload["strategy"],
        "symbol": payload["symbol"],
        "start": payload["start"],
        "end": payload["end"],
        "parameters": payload["parameters"],
        "cost_model": payload["cost_model"],
        "metrics": payload["metrics"],
        "data_version": payload["data_version"],
        "warnings": payload.get("warnings", []),
    }

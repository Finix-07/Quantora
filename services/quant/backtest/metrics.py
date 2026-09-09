"""Performance and risk metrics.

Every metric here can return ``None``. That is deliberate: a Sharpe ratio over
a return series with zero variance, or a profit factor with no losing trades, is
undefined — and reporting `0.0` or `inf` for it would put a fabricated number in
front of a user (NFR5.4/NFR5.6). The UI renders `None` as "not applicable" with
the reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from services.quant.backtest.portfolio import Trade

EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class Metrics:
    """The metric set required by requirements.md FR6."""

    total_return: float
    cagr: float | None
    annualized_volatility: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    max_drawdown_duration_bars: int
    calmar: float | None
    win_rate: float | None
    profit_factor: float | None
    trade_count: int
    winning_trades: int
    losing_trades: int
    average_win: float | None
    average_loss: float | None
    turnover: float | None
    exposure: float
    total_costs: float
    cost_drag: float
    """Total costs as a fraction of starting capital — how much of the gross
    result the frictions consumed."""

    final_equity: float
    #: Explains any metric that came back None, so the UI never has to guess.
    unavailable: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "cagr": self.cagr,
            "annualized_volatility": self.annualized_volatility,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "calmar": self.calmar,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "trade_count": self.trade_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "turnover": self.turnover,
            "exposure": self.exposure,
            "total_costs": self.total_costs,
            "cost_drag": self.cost_drag,
            "final_equity": self.final_equity,
            "unavailable": self.unavailable,
        }


def returns_from_equity(equity: pd.Series) -> pd.Series:
    """Simple per-bar returns. The first bar has no prior value and is dropped."""
    return equity.pct_change().dropna()


def drawdown_curve(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak, at every bar."""
    running_max = equity.cummax()
    return (equity / running_max - 1.0).rename("drawdown")


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough decline, as a negative fraction."""
    if equity.empty:
        return 0.0
    return float(drawdown_curve(equity).min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """Longest run of bars spent below a previous peak.

    Reported alongside depth because a 15% drawdown lasting three weeks and one
    lasting two years are very different experiences for the person holding it.
    """
    if equity.empty:
        return 0
    underwater = equity < equity.cummax()
    longest = current = 0
    for is_under in underwater:
        current = current + 1 if is_under else 0
        longest = max(longest, current)
    return int(longest)


def cagr(equity: pd.Series, periods_per_year: float) -> float | None:
    """Compound annual growth rate.

    Returns None when the series is shorter than can be annualised meaningfully
    or when equity reached zero — extrapolating an annual rate from a handful of
    bars produces a spectacular number with no information in it.
    """
    if len(equity) < 2 or periods_per_year <= 0:
        return None
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    if start <= 0 or end <= 0:
        return None
    years = (len(equity) - 1) / periods_per_year
    if years < EPSILON:
        return None
    return float((end / start) ** (1.0 / years) - 1.0)


def annualized_volatility(returns: pd.Series, periods_per_year: float) -> float | None:
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe(
    returns: pd.Series, periods_per_year: float, risk_free_rate: float = 0.0
) -> float | None:
    """Annualised Sharpe ratio.

    None when there are too few returns or when volatility is zero: dividing by
    zero would report `inf`, which reads as "infinitely good" rather than
    "undefined".
    """
    if len(returns) < 2:
        return None
    period_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = returns - period_rf
    std = excess.std(ddof=1)
    if std is None or not np.isfinite(std) or std < EPSILON:
        return None
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino(
    returns: pd.Series, periods_per_year: float, risk_free_rate: float = 0.0
) -> float | None:
    """Annualised Sortino ratio: excess return over downside deviation.

    Downside deviation is computed over the full series with upside excursions
    set to zero, not over the subset of negative returns only. Using the subset
    understates the denominator whenever losses are rare and inflates the ratio.
    """
    if len(returns) < 2:
        return None
    period_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = returns - period_rf
    downside = excess.clip(upper=0.0)
    downside_deviation = math.sqrt(float((downside**2).mean()))
    if downside_deviation < EPSILON:
        return None
    return float(excess.mean() / downside_deviation * math.sqrt(periods_per_year))


def win_rate(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return sum(1 for t in trades if t.is_win) / len(trades)


def profit_factor(trades: list[Trade]) -> float | None:
    """Gross profit divided by gross loss.

    None when there were no losing trades: the ratio is unbounded, and printing
    `inf` invites a user to read a two-trade sample as a flawless strategy.
    """
    if not trades:
        return None
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    if gross_loss < EPSILON:
        return None
    return float(gross_profit / gross_loss)


def compute(
    equity: pd.Series,
    trades: list[Trade],
    *,
    periods_per_year: float,
    risk_free_rate: float = 0.0,
    initial_cash: float,
    total_costs: float,
    traded_notional: float = 0.0,
    exposure: pd.Series | None = None,
) -> Metrics:
    """Compute the full metric set, recording why anything is unavailable."""
    unavailable: dict[str, str] = {}

    def record(name: str, value: float | None, reason: str) -> float | None:
        if value is None:
            unavailable[name] = reason
        return value

    returns = returns_from_equity(equity)
    final_equity = float(equity.iloc[-1]) if not equity.empty else initial_cash
    total_return = (final_equity / initial_cash) - 1.0 if initial_cash else 0.0

    too_few = "Fewer than two return observations — the period is too short to measure."
    flat = "The return series has no variance, so the ratio is undefined (not zero)."

    metrics_cagr = record(
        "cagr",
        cagr(equity, periods_per_year),
        "Too few bars, or equity reached zero — an annualised rate would be meaningless.",
    )
    volatility = record(
        "annualized_volatility", annualized_volatility(returns, periods_per_year), too_few
    )
    metrics_sharpe = record(
        "sharpe",
        sharpe(returns, periods_per_year, risk_free_rate),
        flat if len(returns) >= 2 else too_few,
    )
    metrics_sortino = record(
        "sortino",
        sortino(returns, periods_per_year, risk_free_rate),
        "No downside deviation in the period, so the ratio is undefined."
        if len(returns) >= 2
        else too_few,
    )

    drawdown = max_drawdown(equity)
    calmar = None
    if metrics_cagr is not None and abs(drawdown) > EPSILON:
        calmar = float(metrics_cagr / abs(drawdown))
    else:
        unavailable["calmar"] = "Requires a CAGR and a non-zero maximum drawdown."

    no_trades = "The strategy produced no completed trades in this period."
    metrics_win_rate = record("win_rate", win_rate(trades), no_trades)
    metrics_profit_factor = record(
        "profit_factor",
        profit_factor(trades),
        no_trades if not trades else "There were no losing trades, so the ratio is unbounded.",
    )

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    average_win = record(
        "average_win", float(np.mean(wins)) if wins else None, "No winning trades."
    )
    average_loss = record(
        "average_loss", float(np.mean(losses)) if losses else None, "No losing trades."
    )

    turnover = None
    if initial_cash > 0 and len(equity) > 1:
        years = (len(equity) - 1) / periods_per_year
        if years > EPSILON:
            # Annualised: traded notional relative to average equity, per year.
            average_equity = float(equity.mean())
            if average_equity > EPSILON:
                turnover = float(traded_notional / average_equity / years)
    if turnover is None:
        unavailable["turnover"] = "Requires more than one bar and non-zero average equity."

    mean_exposure = (
        float(exposure.abs().mean()) if exposure is not None and not exposure.empty else 0.0
    )

    return Metrics(
        total_return=float(total_return),
        cagr=metrics_cagr,
        annualized_volatility=volatility,
        sharpe=metrics_sharpe,
        sortino=metrics_sortino,
        max_drawdown=float(drawdown),
        max_drawdown_duration_bars=max_drawdown_duration(equity),
        calmar=calmar,
        win_rate=metrics_win_rate,
        profit_factor=metrics_profit_factor,
        trade_count=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        average_win=average_win,
        average_loss=average_loss,
        turnover=turnover,
        exposure=mean_exposure,
        total_costs=float(total_costs),
        cost_drag=float(total_costs / initial_cash) if initial_cash else 0.0,
        final_equity=final_equity,
        unavailable=unavailable,
    )

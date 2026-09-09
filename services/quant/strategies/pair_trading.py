"""Pair trading / statistical arbitrage (requirements.md FR3, family 4 of 4).

Two instruments, one single-instrument contract
-----------------------------------------------
Every other strategy family here answers "what should I do in *this* instrument?".
A pair trade answers "what should I do in this instrument *given another one*",
and :meth:`Strategy.generate_signals` only ever receives one
:class:`~services.quant.data.types.PriceSeries`. The three ways out of that, and
why this module picks the third:

1. Widen the contract to accept many series. That is the right long-term answer
   for a portfolio engine, but it would change the signature every existing
   strategy, the backtest engine and the API already depend on — for one family.
2. Let the strategy fetch its own second leg inside ``generate_signals``. This is
   the tempting one, and it is the worst: a strategy that calls the data layer
   performs network I/O from inside what is documented as a pure, framework-
   agnostic calculation. The same inputs would stop producing the same outputs
   (yfinance revises history), unit tests would need the network or a monkeypatch,
   and the provenance of the second leg would never be recorded anywhere.
3. **Inject the second leg.** The pair is named by a parameter (``pair_symbol``),
   the bars for it are handed to the strategy by whoever owns the I/O — the API
   layer, a notebook, a test — through :meth:`PairTradingStrategy.set_pair_series`,
   and ``generate_signals`` stays a pure function of the data it was given.
   :func:`load_pair_series` is the one supported way to obtain those bars, and it
   lives at module level rather than on the class precisely so that the class
   cannot reach the data layer.

If the second leg was never supplied, :meth:`generate_signals` raises
:class:`PairDataMissingError`. It deliberately does *not* return an all-flat
signal set: "flat everywhere" is a legitimate answer meaning "the strategy had no
opinion", and using it to mean "the strategy was never given half its inputs"
would turn a setup mistake into a silently empty backtest that looks like a
result.

Alignment and the index contract
--------------------------------
The two legs are aligned on their **common timestamps** (an inner join). Signals
are then returned over the *original* series' full index, with any bar that has
no counterpart in the pair marked FLAT: the backtest engine requires the signal
index to equal the price index exactly (`backtest/engine.py`), and a bar for
which the second leg is unknown is a bar this strategy genuinely has no view on.

What the backtest actually simulates
------------------------------------
The engine trades one instrument, so a backtest of this strategy trades the
primary leg only — the hedge leg's P&L is not simulated. The signals are still
the pair's signals (the spread decides when to be long or short), but the
returns are those of a one-legged version of the trade. That is a real
limitation of the current single-instrument engine, stated here rather than left
for a reader to infer from a suspiciously volatile equity curve.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from services.quant.data.types import PriceSeries
from services.quant.indicators.correlation import hedge_ratio, rolling_correlation
from services.quant.indicators.zscore import rolling_zscore
from services.quant.strategies.base import (
    InsufficientDataError,
    InvalidParametersError,
    ParameterSpec,
    SignalDirection,
    SignalSet,
    Strategy,
    StrategyError,
    empty_signal_frame,
)
from services.quant.strategies.registry import register


class PairDataMissingError(StrategyError):
    """The second leg of the pair was never supplied.

    Its own class rather than a bare ``ValueError`` so the API layer can map it
    to "you forgot to send the pair data" instead of a 500, and so the message
    can say exactly which call fixes it.
    """

    def __init__(self, strategy: str, pair_symbol: str) -> None:
        super().__init__(
            f"Strategy {strategy!r} needs price bars for its second leg {pair_symbol!r} "
            "before it can generate signals, and none were supplied. Load them with "
            "`load_pair_series(pair_symbol, start, end, interval)` from "
            "services.quant.strategies.pair_trading, then inject them with "
            "`strategy.set_pair_series(series)` — or pass `pair_series=` to the "
            "constructor — before calling generate_signals()."
        )
        self.strategy = strategy
        self.pair_symbol = pair_symbol


def load_pair_series(symbol: str, start: str, end: str, interval: str = "1d") -> PriceSeries:
    """Fetch validated bars for the second leg of a pair.

    Deliberately a module-level function and not a method: it is the only piece
    of this module that touches the data layer, and keeping it off the strategy
    class is what makes "the strategy performs no I/O" checkable by reading the
    class rather than by trusting a comment. Callers (the API, MCP, a notebook)
    use this so every second leg arrives through the same validated path as the
    first one, with provenance attached.
    """
    # Imported here rather than at module scope so that importing the strategy
    # package — which the registry does eagerly for every strategy — does not
    # pull in the provider stack. It also keeps the dependency one-directional:
    # nothing in the strategy layer depends on the data layer at import time.
    from services.quant.data.service import get_prices

    return get_prices(symbol, start, end, interval).series


@register
class PairTradingStrategy(Strategy):
    """Trade the mean reversion of the spread between two correlated instruments.

    The spread is built from **log** prices: ``log(a) - β·log(b)``. A log spread
    is scale-invariant, so ``entry_z = 2`` means the same thing for a ₹200 stock
    paired with a ₹4,000 one as it does for two ₹500 stocks — with raw prices the
    same threshold would silently mean a different trade for every pair, and a
    parameter that changes meaning per instrument cannot be compared across
    experiments (M3.7).

    When the z-score of that spread falls to ``-entry_z`` the primary leg is
    cheap relative to its pair, so the target is LONG; at ``+entry_z`` it is rich
    and the target is SHORT. Inside ``±exit_z`` the spread has reverted and the
    target is FLAT. Between the bands the previous target is held — the trade
    thesis has not changed, and re-deciding every bar would churn the position.

    The whole idea rests on the two legs actually being a pair. ``min_correlation``
    is the guardrail: while the rolling correlation of the legs' returns is below
    it, the target is forced FLAT, because trading the spread of two instruments
    that have stopped moving together is trading noise with extra steps.
    """

    name = "pair_trading"
    family = "stat_arb"
    description = (
        "Statistical arbitrage. Trades the mean reversion of the log-price spread "
        "between two correlated instruments, going long the primary leg when the "
        "spread is unusually cheap and short when it is unusually rich."
    )
    parameter_specs = (
        ParameterSpec(
            "pair_symbol",
            "str",
            "TCS.NS",
            "The second leg of the pair. Its bars must be supplied via set_pair_series().",
        ),
        ParameterSpec(
            "lookback",
            "int",
            60,
            "Bars used for the spread's mean, dispersion, correlation and hedge ratio",
            minimum=10,
            maximum=500,
        ),
        ParameterSpec(
            "entry_z",
            "float",
            2.0,
            "Open a position when the spread is this many standard deviations from its mean",
            minimum=0.5,
            maximum=5.0,
        ),
        ParameterSpec(
            "exit_z",
            "float",
            0.5,
            "Close the position once the spread is back within this many standard deviations",
            minimum=0.0,
            maximum=3.0,
        ),
        ParameterSpec(
            "min_correlation",
            "float",
            0.5,
            "Refuse to trade while the legs' rolling return correlation is below this",
            minimum=-1.0,
            maximum=1.0,
        ),
        ParameterSpec(
            "use_hedge_ratio",
            "bool",
            True,
            "Weight the second leg by a rolling OLS beta instead of one-for-one",
        ),
    )

    def __init__(self, *, pair_series: PriceSeries | None = None, **parameters: Any) -> None:
        super().__init__(**parameters)
        # Injected setup, not per-bar state: it is written once before signals are
        # generated and read-only afterwards, so `generate_signals` stays a pure
        # function of (primary series, pair series, parameters) and remains safe
        # to call repeatedly with the same result.
        self._pair_series: PriceSeries | None = None
        if pair_series is not None:
            self.set_pair_series(pair_series)

    def _validate_parameters(self) -> None:
        pair_symbol = self._parameters["pair_symbol"]
        # ParameterSpec coerces int/float/bool but has no rule for "str", so the
        # type check for this one lives here.
        if not isinstance(pair_symbol, str) or not pair_symbol.strip():
            raise InvalidParametersError(
                f"pair_symbol must be a non-empty symbol, got {pair_symbol!r}"
            )

        entry_z = self._parameters["entry_z"]
        exit_z = self._parameters["exit_z"]
        if exit_z >= entry_z:
            raise InvalidParametersError(
                f"exit_z ({exit_z}) must be strictly less than entry_z ({entry_z}); an exit "
                "band at least as wide as the entry band closes a position on the very bar "
                "it was opened, so the strategy would pay costs to hold nothing."
            )

    @property
    def pair_series(self) -> PriceSeries | None:
        """The injected second leg, or None if it has not been supplied yet."""
        return self._pair_series

    def set_pair_series(self, series: PriceSeries) -> None:
        """Supply the bars for the second leg.

        The symbol is checked against ``pair_symbol`` because the parameters are
        what gets persisted with a saved experiment: injecting INFY while the
        recorded parameter says TCS would produce a result that can never be
        reproduced from its own record.
        """
        expected = str(self._parameters["pair_symbol"])
        if series.symbol.casefold() != expected.casefold():
            raise InvalidParametersError(
                f"pair_symbol is {expected!r} but the injected series is for "
                f"{series.symbol!r}. The saved parameters must describe the data that was "
                "actually used, so either fix the parameter or inject the matching series."
            )
        self._pair_series = series

    @property
    def warmup_bars(self) -> int:
        # `lookback` bars to form the spread's mean and dispersion, plus one for
        # the first log return the correlation guardrail needs.
        #
        # With `use_hedge_ratio=True` the first *tradable* bar is later still: the
        # rolling beta itself consumes `lookback` bars before the spread exists,
        # so the z-score only appears around bar 2·lookback. That is handled
        # honestly rather than by inflating this number — a NaN z-score forces
        # FLAT — because warmup_bars is also the minimum data requirement, and
        # doubling it would reject date ranges the simple-spread variant can
        # trade perfectly well.
        return int(self._parameters["lookback"]) + 1

    def generate_signals(self, series: PriceSeries) -> SignalSet:
        params = self.parameters
        pair_symbol = str(params["pair_symbol"])
        lookback = int(params["lookback"])
        entry_z = float(params["entry_z"])
        exit_z = float(params["exit_z"])
        min_correlation = float(params["min_correlation"])

        pair = self._pair_series
        if pair is None:
            raise PairDataMissingError(self.name, pair_symbol)
        if pair.symbol.casefold() == series.symbol.casefold():
            raise InvalidParametersError(
                f"pair_symbol {pair_symbol!r} is the same instrument as the primary leg "
                f"{series.symbol!r}. The spread of a series against itself is identically "
                "zero, so its z-score is undefined and there is nothing to trade."
            )
        self.require_warmup(series)

        # --- Align the two legs ------------------------------------------------
        # Inner join: a bar the pair did not trade (a holiday on one exchange, a
        # halt) is a bar with no spread, not a bar with a stale spread.
        common = series.frame.index.intersection(pair.frame.index)
        if len(common) < self.warmup_bars:
            raise InsufficientDataError(self.name, self.warmup_bars, len(common))

        log_primary = np.log(series.close.reindex(common))
        log_pair = np.log(pair.close.reindex(common))

        # --- Spread, z-score, guardrail ---------------------------------------
        if params["use_hedge_ratio"]:
            # Rolling, never full-sample: a beta fitted over the whole series is
            # fitted with data from the end of the backtest (NFR5.3).
            beta = hedge_ratio(log_primary, log_pair, lookback)
        else:
            # A one-for-one log spread, i.e. the log of the price *ratio*. Beta is
            # reported as 1.0 rather than left empty so the indicator frame always
            # says what ratio the spread was actually built from.
            beta = pd.Series(1.0, index=common, dtype="float64")

        spread = (log_primary - beta * log_pair).rename("pair_spread")
        z = rolling_zscore(spread, lookback).rename("pair_zscore")

        # Correlation is measured on log *returns*, not on price levels. Two
        # trending price series are correlated almost by construction — the
        # classic spurious-regression result — so a level correlation would sit
        # near 1 for two unrelated instruments and the guardrail would never
        # fire. Return correlation measures what the guardrail is actually about:
        # whether the legs still move together day to day.
        correlation = rolling_correlation(log_primary.diff(), log_pair.diff(), lookback).rename(
            "pair_correlation"
        )

        # --- Target position ---------------------------------------------------
        enter_long = z <= -entry_z
        enter_short = z >= entry_z
        revert_flat = z.abs() <= exit_z

        decoupled = correlation.notna() & (correlation < min_correlation)
        # A NaN correlation (warm-up) is not evidence that the pair is intact, so
        # it blocks trading too — but silently, since "not computed yet" is not
        # the same news as "these two have come apart".
        unusable = z.isna() | correlation.isna() | decoupled

        enter_long &= ~unusable
        enter_short &= ~unusable
        revert_flat &= ~unusable

        target = pd.Series(np.nan, index=common, dtype="float64")
        target[enter_long] = float(SignalDirection.LONG)
        # SHORT is emitted honestly even when the caller's portfolio is long-only:
        # the engine's `allow_short` has the final say and flattens it there, so
        # the strategy states its view and the portfolio policy stays in one
        # place instead of being duplicated as a strategy parameter.
        target[enter_short] = float(SignalDirection.SHORT)
        target[revert_flat] = float(SignalDirection.FLAT)
        # Applied *before* the forward fill so a forced flat is carried forward:
        # after the pair decouples the strategy stays out until a fresh entry
        # band is crossed, rather than quietly resuming the old position the
        # moment the correlation ticks back up.
        target[unusable] = float(SignalDirection.FLAT)
        target = target.ffill().fillna(float(SignalDirection.FLAT))

        # --- Explanations ------------------------------------------------------
        # Formatted over the whole series rather than per mask so the text stays
        # object-dtype even when a mask selects nothing — concatenating a string
        # onto an empty float column raises.
        z_text = z.map(_format_z)
        correlation_text = correlation.map(_format_z)
        reasons = pd.Series("", index=common, dtype="object")
        reasons[enter_long] = (
            "spread z "
            + z_text[enter_long]
            + f" <= -{entry_z:g}: {series.symbol} is cheap versus {pair_symbol} — long the spread"
        )
        reasons[enter_short] = (
            "spread z "
            + z_text[enter_short]
            + f" >= +{entry_z:g}: {series.symbol} is rich versus {pair_symbol} — short the spread"
        )
        reasons[revert_flat] = (
            "spread z "
            + z_text[revert_flat]
            + f" within ±{exit_z:g}: the {series.symbol}/{pair_symbol} spread has reverted — flat"
        )
        reasons[decoupled] = (
            f"{lookback}-bar return correlation with {pair_symbol} "
            + correlation_text[decoupled]
            + f" is below min_correlation {min_correlation:g}: the pair has decoupled — flat"
        )

        # --- Back onto the original index -------------------------------------
        # The engine requires one signal row per price bar. Bars with no
        # counterpart in the pair series get FLAT: no second leg means no spread,
        # which means no view.
        frame = empty_signal_frame(series.frame.index)
        frame["direction"] = (
            target.reindex(frame.index).fillna(float(SignalDirection.FLAT)).astype("int64")
        )
        # Binary conviction, matching the other families (see macd.py): full
        # size while positioned, nothing while flat. Grading it by |z| would make
        # position size incomparable across strategies, which is exactly what the
        # M3.7 comparison needs to be able to do.
        frame["strength"] = np.where(frame["direction"] != 0, 1.0, 0.0)
        frame["reason"] = reasons.reindex(frame.index).fillna("")

        indicators = pd.DataFrame(
            {
                "pair_spread": spread,
                "pair_zscore": z,
                "pair_correlation": correlation,
                "pair_hedge_ratio": beta,
            }
        ).reindex(frame.index)

        return SignalSet(
            symbol=series.symbol,
            strategy=self.name,
            parameters=params,
            frame=frame,
            indicators=indicators,
            warmup_bars=self.warmup_bars,
        )


def _format_z(value: float) -> str:
    """Signed, two-decimal rendering for a reason string."""
    return "n/a" if pd.isna(value) else f"{value:+.2f}"

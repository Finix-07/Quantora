"""Validation rules applied to every dataset the data layer returns.

architecture.md §4.3 fixes the hard rules::

    low <= open <= high
    low <= close <= high
    volume >= 0
    timestamps are monotonic and unique

A violation raises rather than dropping or patching rows. Repairing bad market
data produces a backtest number that looks plausible and is wrong, which
NFR5.6 ranks as strictly worse than no answer.

Gaps are handled differently, and deliberately. NSE is closed at weekends and on
public holidays, so missing calendar days are normal and cannot be treated as
corruption. They are *detected and reported* rather than ignored (NFR5.1 forbids
silently using incomplete data) and the report travels with the result so the
user can see what was missing before trusting a metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from services.quant.data.errors import DataValidationError, Violation
from services.quant.data.types import OHLCV_COLUMNS

# Bars whose spacing exceeds this many calendar days are flagged as a "long"
# gap. Three days covers an ordinary Friday->Monday weekend; anything longer is
# a holiday cluster or missing data, and the user should see it either way.
LONG_GAP_DAYS = 4

# A single gap this long almost certainly means missing history rather than
# market closure, so it is escalated in the report's severity.
SEVERE_GAP_DAYS = 10

_PRICE_COLUMNS = ("open", "high", "low", "close", "adj_close")
_SAMPLE_ROWS = 5


@dataclass(frozen=True, slots=True)
class Gap:
    """A stretch of missing bars between two consecutive rows."""

    after: str
    before: str
    calendar_days: float
    missing_business_days: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "after": self.after,
            "before": self.before,
            "calendar_days": self.calendar_days,
            "missing_business_days": self.missing_business_days,
        }


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """What the validator observed. Always attached to a result, never hidden."""

    symbol: str
    interval: str
    row_count: int
    gaps: tuple[Gap, ...] = ()
    severity: str = "clean"  # clean | gaps_detected | severe_gaps
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "row_count": self.row_count,
            "severity": self.severity,
            "gaps": [g.as_dict() for g in self.gaps],
            "notes": list(self.notes),
        }


def _sample(frame: pd.DataFrame, mask: pd.Series) -> list[dict[str, Any]]:
    """A few offending rows, so the error can be inspected rather than guessed at."""
    offending = frame.loc[mask].head(_SAMPLE_ROWS)
    return [
        {
            "timestamp": ts.isoformat(),
            **{col: (None if pd.isna(row[col]) else float(row[col])) for col in OHLCV_COLUMNS},
        }
        for ts, row in offending.iterrows()
    ]


def _check_timestamps(frame: pd.DataFrame, violations: list[Violation]) -> None:
    index = frame.index

    if not isinstance(index, pd.DatetimeIndex):
        violations.append(
            Violation(
                rule="timestamp_type",
                message=f"Index must be a DatetimeIndex, got {type(index).__name__}.",
                row_count=len(frame),
            )
        )
        return

    duplicated = index.duplicated(keep=False)
    if duplicated.any():
        dupes = sorted({ts.isoformat() for ts in index[duplicated]})
        violations.append(
            Violation(
                rule="duplicate_timestamp",
                message=(
                    f"{len(dupes)} timestamp(s) appear more than once. Duplicated bars would be "
                    "counted twice by the backtester."
                ),
                row_count=int(duplicated.sum()),
                sample=[{"timestamp": ts} for ts in dupes[:_SAMPLE_ROWS]],
            )
        )

    if not index.is_monotonic_increasing:
        # Report the first out-of-order pair: knowing where the ordering breaks
        # is what makes this actionable.
        positions = np.flatnonzero(np.asarray(index[1:] <= index[:-1]))
        sample = [
            {"previous": index[p].isoformat(), "next": index[p + 1].isoformat()}
            for p in positions[:_SAMPLE_ROWS]
        ]
        violations.append(
            Violation(
                rule="non_monotonic_timestamp",
                message=(
                    "Timestamps are not strictly increasing. Out-of-order bars would let a later "
                    "price influence an earlier decision (look-ahead bias)."
                ),
                row_count=int(len(positions)),
                sample=sample,
            )
        )


def _check_values(frame: pd.DataFrame, violations: list[Violation]) -> None:
    missing = frame[list(OHLCV_COLUMNS)].isna().any(axis=1)
    if missing.any():
        violations.append(
            Violation(
                rule="missing_value",
                message=(
                    f"{int(missing.sum())} row(s) contain NaN in an OHLCV field. The rows are not "
                    "forward-filled: an invented price is worse than a refused answer."
                ),
                row_count=int(missing.sum()),
                sample=_sample(frame, missing),
            )
        )

    # Value rules are evaluated only on complete rows; NaN comparisons are
    # always False and would otherwise mask the real problem reported above.
    complete = frame.loc[~missing]
    if complete.empty:
        return

    non_positive = (complete[list(_PRICE_COLUMNS)] <= 0).any(axis=1)
    if non_positive.any():
        violations.append(
            Violation(
                rule="non_positive_price",
                message=f"{int(non_positive.sum())} row(s) contain a price <= 0.",
                row_count=int(non_positive.sum()),
                sample=_sample(complete, non_positive),
            )
        )

    negative_volume = complete["volume"] < 0
    if negative_volume.any():
        violations.append(
            Violation(
                rule="negative_volume",
                message=f"{int(negative_volume.sum())} row(s) report negative volume.",
                row_count=int(negative_volume.sum()),
                sample=_sample(complete, negative_volume),
            )
        )

    bad_high_low = complete["high"] < complete["low"]
    if bad_high_low.any():
        violations.append(
            Violation(
                rule="high_below_low",
                message=f"{int(bad_high_low.sum())} row(s) have high < low.",
                row_count=int(bad_high_low.sum()),
                sample=_sample(complete, bad_high_low),
            )
        )

    open_out_of_range = (complete["open"] < complete["low"]) | (complete["open"] > complete["high"])
    if open_out_of_range.any():
        violations.append(
            Violation(
                rule="open_out_of_range",
                message=(
                    f"{int(open_out_of_range.sum())} row(s) violate low <= open <= high. "
                    "An open outside the bar's range makes every fill simulated at the open wrong."
                ),
                row_count=int(open_out_of_range.sum()),
                sample=_sample(complete, open_out_of_range),
            )
        )

    close_out_of_range = (complete["close"] < complete["low"]) | (
        complete["close"] > complete["high"]
    )
    if close_out_of_range.any():
        violations.append(
            Violation(
                rule="close_out_of_range",
                message=f"{int(close_out_of_range.sum())} row(s) violate low <= close <= high.",
                row_count=int(close_out_of_range.sum()),
                sample=_sample(complete, close_out_of_range),
            )
        )


def detect_gaps(frame: pd.DataFrame, interval: str) -> tuple[Gap, ...]:
    """Find stretches of missing bars.

    Only daily-and-coarser intervals are gap-checked. Intraday spacing is
    dominated by the session boundary (a 15-minute series jumps ~17.5 hours
    every night), so applying a calendar-day rule there would report a "gap"
    for every trading day and teach the user to ignore the signal.
    """
    if interval not in {"1d", "1wk", "1mo"} or len(frame) < 2:
        return ()

    index = frame.index
    deltas = (index[1:] - index[:-1]).days
    gaps: list[Gap] = []
    for position, days in enumerate(deltas):
        if days < LONG_GAP_DAYS:
            continue
        after = index[position]
        before = index[position + 1]
        # Business days strictly between the two bars: weekends are expected,
        # so counting them as missing would flag every ordinary weekend.
        missing = int(len(pd.bdate_range(after, before, inclusive="neither", tz=index.tz)))
        if missing == 0:
            continue
        gaps.append(
            Gap(
                after=after.isoformat(),
                before=before.isoformat(),
                calendar_days=float(days),
                missing_business_days=missing,
            )
        )
    return tuple(gaps)


def validate_prices(frame: pd.DataFrame, *, symbol: str, interval: str) -> DataQualityReport:
    """Apply every hard rule, then report gaps.

    Raises:
        DataValidationError: if any hard rule is violated. Every violation is
            collected first so the user sees the whole picture at once instead
            of fixing one problem per retry.
    """
    violations: list[Violation] = []
    _check_timestamps(frame, violations)
    if not frame.empty:
        _check_values(frame, violations)

    if violations:
        raise DataValidationError(symbol, violations)

    gaps = detect_gaps(frame, interval)
    notes: list[str] = []
    severity = "clean"
    if gaps:
        severity = "gaps_detected"
        worst = max(gaps, key=lambda g: g.missing_business_days)
        notes.append(
            f"{len(gaps)} gap(s) detected; the longest skips {worst.missing_business_days} "
            f"trading day(s) between {worst.after} and {worst.before}. NSE holidays produce "
            "gaps legitimately — inspect before treating this as missing data."
        )
        if worst.calendar_days >= SEVERE_GAP_DAYS:
            severity = "severe_gaps"
            notes.append(
                f"The longest gap spans {worst.calendar_days:.0f} calendar days, which is longer "
                "than any NSE holiday cluster. Treat metrics over this range as unreliable."
            )

    return DataQualityReport(
        symbol=symbol,
        interval=interval,
        row_count=len(frame),
        gaps=gaps,
        severity=severity,
        notes=tuple(notes),
    )

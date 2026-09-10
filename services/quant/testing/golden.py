"""Loading and describing pinned golden datasets.

testing.md §3 requires a small set of known datasets with known expected results
per strategy family, and requires a change that alters an important result to
fail loudly — silent drift is not acceptable.

The datasets are **committed to the repository**, not fetched. A golden test
that called yfinance would fail whenever the provider was slow, rate-limited or
had revised history, which trains people to ignore it; and it could not detect a
regression in our own code separately from a change in the data. Pinning the
bars makes the test answer exactly one question: did our numbers change?

Both the generator and the tests go through this module, so the file format
cannot drift between the thing that writes it and the thing that reads it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from services.quant.data.types import (
    OHLCV_COLUMNS,
    TIMESTAMP_INDEX_NAME,
    PriceSeries,
    Provenance,
)

GOLDEN_ROOT = Path(__file__).resolve().parents[3] / "tests" / "golden"
DATA_DIR = GOLDEN_ROOT / "data"
EXPECTED_DIR = GOLDEN_ROOT / "expected"

#: Full float repr, so a saved bar round-trips bit-for-bit. Anything shorter
#: would make the pinned data_version depend on the CSV writer's precision.
FLOAT_FORMAT = "%.17g"


def dataset_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}.csv"


def metadata_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}.meta.json"


def expected_path(name: str) -> Path:
    return EXPECTED_DIR / f"{name}.json"


def save_dataset(series: PriceSeries) -> None:
    """Write a PriceSeries as a pinned golden dataset."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    series.frame.to_csv(dataset_path(series.symbol), float_format=FLOAT_FORMAT)
    metadata_path(series.symbol).write_text(
        json.dumps(
            {
                "symbol": series.symbol,
                "interval": series.interval,
                "provenance": series.provenance.as_dict(),
                "data_version": series.data_version(),
                "row_count": len(series),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def load_dataset(symbol: str) -> PriceSeries:
    """Read a pinned golden dataset back as a PriceSeries.

    Raises:
        FileNotFoundError: with the command that regenerates it, rather than a
            bare missing-file error that leaves the reader guessing.
    """
    path = dataset_path(symbol)
    if not path.is_file():
        raise FileNotFoundError(
            f"No golden dataset for {symbol} at {path}. "
            "Regenerate with `python scripts/generate_golden_fixtures.py`."
        )

    frame = pd.read_csv(path, index_col=TIMESTAMP_INDEX_NAME, parse_dates=True)
    frame = frame[list(OHLCV_COLUMNS)].astype("float64")
    frame.index = pd.DatetimeIndex(frame.index)
    frame.index.name = TIMESTAMP_INDEX_NAME

    meta = json.loads(metadata_path(symbol).read_text(encoding="utf-8"))
    stored = meta["provenance"]
    provenance = Provenance(
        provider=stored["provider"],
        symbol=stored["symbol"],
        provider_ticker=stored["provider_ticker"],
        interval=stored["interval"],
        timezone=stored["timezone"],
        retrieval_timestamp=datetime.fromisoformat(stored["retrieval_timestamp"]).astimezone(UTC),
        adjustment_policy=stored["adjustment_policy"],
        requested_start=stored["requested_start"],
        requested_end=stored["requested_end"],
        row_count=stored["row_count"],
        first_timestamp=stored["first_timestamp"],
        last_timestamp=stored["last_timestamp"],
        currency=stored["currency"],
    )
    return PriceSeries(
        symbol=meta["symbol"], interval=meta["interval"], frame=frame, provenance=provenance
    )


def dataset_metadata(symbol: str) -> dict[str, Any]:
    return json.loads(metadata_path(symbol).read_text(encoding="utf-8"))


def save_expected(name: str, payload: dict[str, Any]) -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    expected_path(name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_expected(name: str) -> dict[str, Any]:
    path = expected_path(name)
    if not path.is_file():
        raise FileNotFoundError(
            f"No golden expectation for {name} at {path}. "
            "Regenerate with `python scripts/generate_golden_fixtures.py`."
        )
    return json.loads(path.read_text(encoding="utf-8"))

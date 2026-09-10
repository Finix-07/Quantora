# API reference

Every endpoint below has been exercised against the running stack. Shapes are
copied from real responses, not from the source.

Base URL: `http://localhost:8080` (the Go API). The Python `quant-mcp` service on
port 8000 is an internal boundary — the UI and any external client talk only to
the Go API (architecture.md §3.4).

## Conventions

**Errors.** Every failure returns the same envelope, so a client can branch on
`code` and show `message` verbatim:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Unknown symbol 'NOPE'. Known symbols: NIFTY, BANKNIFTY, RELIANCE.NS, …",
    "details": { "symbol": "NOPE", "known_symbols": ["NIFTY", "…"] },
    "request_id": "req_4327a66f3e061513"
  }
}
```

| `code` | Meaning | What the user does |
|---|---|---|
| `invalid_request` | The request or its parameters were rejected | Fix the input; the message says what |
| `not_found` | No such backtest, experiment or endpoint | Check the ID |
| `data_validation_failed` | Retrieved market data broke a validation rule | Narrow the range or distrust the source |
| `data_unavailable` | The data provider failed or returned nothing | Retry; check the symbol if it persists |
| `upstream_failure` | The quant service is unreachable | `docker compose ps` |
| `internal_error` | An unexpected failure | Quote the `request_id` |

**Unknown JSON fields are rejected.** A typo'd `commision_bps` that silently
applied the default would produce a result under different cost assumptions than
the user asked for (NFR5.2), so the request fails and names the field.

**Request IDs.** Send `X-Request-ID` to correlate your action across web → api →
quant-mcp in the logs; one is generated if you don't. It is echoed on every
response and included in every error.

**Unavailable metrics are `null`, never `0`.** A Sharpe ratio over a zero-variance
return series and a profit factor with no losing trades are undefined. Each
`null` has a reason under `metrics.unavailable`:

```json
"unavailable": { "profit_factor": "There were no losing trades, so the ratio is unbounded." }
```

---

## Reference data

### `GET /healthz`

Reports each dependency by name. `200` when all pass, `503` when any fails — the
body still names which.

```json
{ "status": "ok", "service": "api", "version": "dev",
  "dependencies": { "database": {"status":"ok"}, "process": {"status":"ok"}, "quant_mcp": {"status":"ok"} },
  "checked_at": "2026-09-11T00:00:00Z" }
```

### `GET /api/universe`

The tradable instruments. Note `provider_ticker`: `NIFTY` is not a Yahoo ticker,
`^NSEI` is, and both are recorded so a result traces to the series downloaded.

```json
{ "symbol": "NIFTY", "provider_ticker": "^NSEI", "name": "NIFTY 50 Index",
  "asset_class": "index", "exchange": "NSE", "currency": "INR",
  "timezone": "Asia/Kolkata", "sector": null }
```

`sector` is populated for equities and `null` for indices. Sector exposure is
reported only where it is known (FR7, "where data permits").

### `GET /api/strategies`

Every registered strategy with enough detail to build a form without hard-coding
a copy of it:

```json
{ "name": "bollinger", "family": "mean_reversion", "warmup_bars": 20,
  "auxiliary_symbols": [],
  "parameter_specs": [
    { "name": "window", "type": "int", "default": 20,
      "description": "Bollinger Bands lookback window",
      "minimum": 2, "maximum": 400, "choices": null }
  ] }
```

`auxiliary_symbols` is non-empty for strategies needing a second instrument
(pair trading), so a client knows before offering it.

### `GET /api/market/{symbol}?start=&end=&interval=`

Validated OHLCV bars with provenance and a data-quality report.

```json
{ "symbol": "RELIANCE.NS", "interval": "1d",
  "data_version": "sha256:…",
  "provenance": { "provider": "yfinance", "provider_ticker": "RELIANCE.NS",
                  "adjustment_policy": "split_and_dividend_adjusted",
                  "retrieval_timestamp": "…", "timezone": "Asia/Kolkata", "currency": "INR" },
  "quality": { "severity": "clean", "gaps": [], "notes": [] },
  "bars": [ { "timestamp": "2024-01-01T00:00:00+05:30", "open": 1274.90, "high": 1287.89,
              "low": 1271.24, "close": 1279.69, "adj_close": 1279.69, "volume": 4030540.0 } ] }
```

`quality.severity` is `clean`, `gaps_detected` or `severe_gaps`. NSE holidays
produce gaps legitimately, so a gap is reported rather than treated as
corruption — but it is never hidden (NFR5.1).

`data_version` is a hash of the bar values, not of the request. yfinance revises
history, so this is what makes "the same data" a checkable claim.

---

## Backtests

### `POST /api/backtests`

```bash
curl -X POST localhost:8080/api/backtests -H 'Content-Type: application/json' -d '{
  "symbol": "RELIANCE.NS", "strategy": "macd",
  "start": "2022-01-01", "end": "2024-12-31"
}'
```

Optional: `interval`, `parameters`, `initial_cash`, `cost_model`,
`execution_model` (`next_bar_open` | `next_bar_close`), `allow_short`,
`liquidate_at_end`, `risk_free_rate`.

Returns `201` with the record. `result` is the full contract: `metrics`,
`trades`, `equity_curve`, `drawdown_curve`, `signal_summary`, `provenance`,
`data_quality`, `warnings`, and `assumptions` — a plain-language list of exactly
how fills and costs were modelled:

```
Orders decided at a bar's close fill at the next bar's open. No information from
the fill bar is used to make the decision.
Transaction costs: 3 bps commission per side, 5 bps slippage per side, 2 bps
spread (half paid per side).
```

`warnings` is always present. A non-empty list means the simulation differed from
what the strategy asked for — an order reduced or skipped for insufficient cash.

### `GET /api/backtests/{id}` and `GET /api/backtests?limit=`

Backtests are held **in memory** until saved as an experiment, so a restart
clears them. The list response says so, and the 404 explains it.

---

## Strategy comparison

### `POST /api/strategies/compare`

```bash
curl -X POST localhost:8080/api/strategies/compare -H 'Content-Type: application/json' -d '{
  "symbol": "RELIANCE.NS", "start": "2021-01-01", "end": "2024-12-31", "allow_short": true,
  "strategies": [
    {"strategy": "macd"},
    {"strategy": "bollinger"},
    {"strategy": "pair_trading", "parameters": {"pair_symbol": "TCS.NS"}, "label": "Pair vs TCS"}
  ]
}'
```

Every strategy runs on **one** fetched dataset, reported as a single
`data_version` — two downloads of the same range can differ, and a comparison
built on separate fetches would attribute a data difference to a strategy.

The response carries `metric_table` (rows pivoted per metric, with `better`
direction and the winning `best` column computed server-side), `equity_curves`
and `drawdown_curves` per label, one shared `assumptions` list, a `benchmark`
(buy-and-hold over the same window, charged the same costs), and `failures` —
strategies that failed are named rather than silently dropped.

A comparison of fewer than two strategies is refused.

---

## Experiments

An experiment is a backtest saved with everything needed to rerun it (FR5, NFR6).

### `POST /api/experiments`

Either supply a run configuration inline, or promote a backtest already run this
session with `{"backtest_id": "bt_…"}`. `name` and `notes` are optional.

### `GET /api/experiments?strategy=&symbol=&limit=`

The journal view. Metrics are projected in SQL, so listing does not transfer
every equity curve. A metric the engine could not compute stays `null`.

### `GET /api/experiments/{id}` · `DELETE /api/experiments/{id}`

### `POST /api/experiments/{id}/rerun`

Reruns from the stored configuration — the caller supplies nothing but the ID.

```json
{ "reproducible": true,
  "data_version_matches": true,
  "metrics_match": true,
  "differences": [],
  "explanation": "Reproduced exactly: the source data is unchanged and every compared metric matches.",
  "original_data_version": "sha256:…", "rerun_data_version": "sha256:…" }
```

Three verdicts, not one, because they need opposite responses:

| Outcome | Meaning |
|---|---|
| `data_version_matches: false`, `metrics_match: true` | The provider revised history; the result is consistent but no longer backed by identical bars |
| `data_version_matches: false`, `metrics_match: false` | Revised history explains the difference — compare versions before calling it a regression |
| `data_version_matches: true`, `metrics_match: false` | Same bars, different numbers — treat it as a regression in the engine or strategy |

`differences` names each metric that moved, including `trade_count`: a changed
trade count with unchanged returns is exactly the silent drift worth catching.

A rerun that does not reproduce still returns `200`. The rerun succeeded; the
verdict is the answer to the user's question, not an error.

---

## Not yet available

These are planned and deliberately absent rather than stubbed:

| Surface | Milestone |
|---|---|
| Portfolio and risk endpoints | M5 |
| MCP tool surface | M6 |
| AI research endpoint | M7 |
| Web UI views | M8 |

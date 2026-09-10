# Implementation Memory

Running engineering log for this repository. Purpose: stop future sessions
(and subagents) from re-solving problems that are already solved, and record
commands/configurations that are known to work or known to fail.

Format per entry:

```text
Problem / What failed / Root cause / What fixed it / Why it works /
Tests & verification / Relevant files
```

Short factual entries only. Successful discoveries worth remembering are
recorded the same way, with "What failed" left as `n/a`.

---

## Environment baseline (2026-09-10, M1)

- macOS (darwin 25.6.0), Apple Silicon.
- Python 3.13.5, Node v22.23.2, npm 10.9.8, Docker 29.2.1,
  Apple clang 21.0.0, cmake 4.0.3, uv 0.11.5.
- **Go was not installed.** Installed via `brew install go` → go1.27.1
  darwin/arm64 at `/opt/homebrew/bin/go`. `/opt/homebrew/bin` is **not** on
  the default non-interactive shell PATH used by tooling here, so Go
  commands in scripts/CI-local runs need
  `export PATH="/opt/homebrew/bin:$PATH"`.
- **No git remote is configured and the `gh` CLI is not installed.** Commits
  are therefore local-only; pushing to GitHub is blocked until a remote
  exists. This is recorded as an external blocker, not silently ignored.

---

## Entries

### 2026-09-10 — Migration trigger mechanism (M1.5 deferred decision)

- **Problem:** `deployment.md` §9 left open whether `golang-migrate` runs
  automatically on `api` startup or via an explicit command.
- **Decision:** explicit `make migrate` target (implementation-plan M1.5).
- **Why:** a failing migration inside container startup surfaces as an
  opaque crash loop. Explicit invocation keeps the failure readable. The
  `api` still refuses to serve traffic when migrations have not been applied
  (fail loudly, NFR5.6) — it checks `schema_migrations` at boot.
- **Relevant files:** `Makefile`, `db/migrations/`, `apps/api/`.

### 2026-09-10 — Two DSNs are needed, not one (M1.5)

- **Problem:** `DATABASE_URL=postgres://...@db:5432/...` uses the Compose
  service name and only resolves inside the stack network. Go repository tests
  and any host-side `go run ./cmd/api` cannot use it.
- **Fix:** `.env` carries both `DATABASE_URL` (container-facing, host `db`) and
  `TEST_DATABASE_URL` (host-facing, `127.0.0.1`). Tests skip cleanly when
  `TEST_DATABASE_URL` is unset so `go test ./...` runs with no stack up.
- **Verified:** `TEST_DATABASE_URL=... go test ./internal/storage/...` — 7/7
  pass against the real container; the same command with the variable unset
  skips the 5 database-backed cases.
- **Files:** `.env.example`, `Makefile`, `apps/api/internal/storage/postgres/`.

### 2026-09-10 — API startup precondition verified end to end (M1.5)

- **Discovery (not a failure):** the "refuse to serve unmigrated" rule is
  observable, not just asserted in a unit test. Against a freshly created empty
  database the API prints
  `api: fatal: database migrations have not been applied: run \`make migrate\``
  and exits 1; against the migrated database it logs
  `database ready schema_version=1` and serves `/healthz` with
  `database: ok`.
- **Known-good commands:** `make up` → `make migrate` → migrate prints
  `1/u baseline`. Docker Desktop must be running first (`open -a Docker`);
  otherwise Compose fails with "Cannot connect to the Docker daemon".

### 2026-09-10 — Next.js standalone output needs the monorepo layout inside the image (M1.6)

- **Problem:** the `web` container crash-looped with
  `Error: Cannot find module '/app/apps/web/server.js'`.
- **Root cause:** `output: "standalone"` writes its file tree relative to
  `outputFileTracingRoot`, which `next.config.ts` resolves to the repo root
  (`../../` from `apps/web`). The first Dockerfile flattened `apps/web/` to the
  image root `/app`, so `../../` resolved to `/` and Next emitted
  `.next/standalone/app/server.js` — not the path the CMD used.
- **Fix:** reproduce the monorepo layout inside the image: `WORKDIR /repo`,
  `COPY apps/web/ ./apps/web/`, build from `/repo/apps/web`, then copy
  `.next/standalone` to `/repo` and run `node apps/web/server.js`.
- **Why it works:** the tracing root inside the image now matches the tracing
  root the config computes, so emitted paths and the CMD agree.
- **Also note:** `.next/static` and `public/` are *not* traced into standalone
  and must be copied separately, or the page renders unstyled.
- **Verified:** `aiqt-web` reports `Up (healthy)`, `/healthz` returns
  `{"status":"ok","service":"web"}`, `GET /` returns 200.

### 2026-09-10 — Health probes without curl (M1.6)

- Neither runtime image has curl: `api` is distroless (no shell at all) and
  `quant-mcp` is `python:3.13-slim`.
- **What works:** `api` probes itself via a `-healthcheck` flag on the same
  binary; `quant-mcp` uses `python -c` with `urllib.request`; `web` uses
  `node -e` with `fetch`. Adding a shell or curl to an image purely for health
  probing was rejected as the worse trade.

### 2026-09-10 — CI exists but cannot run: no git remote (M1.7)

- **Blocker (external, unresolved):** this repository has **no configured git
  remote**, and the `gh` CLI is not installed. `.github/workflows/ci.yml` is
  committed and its steps are verified locally, but no GitHub Actions run can
  be triggered and no commit can be pushed until a remote exists.
- **What was done instead:** every CI step was executed locally against the same
  commands the workflow runs — `ruff check`/`ruff format --check`, `pytest -m
  "not network"`, `go vet`, `go test -race` against the real Postgres container,
  `npm run lint`/`typecheck`/`build`, and the full `docker compose up --wait`
  health-check sweep.
- **To unblock:** `git remote add origin <url>` then `git push -u origin main`.

## M2 — data layer and walking skeleton

### 2026-09-10 — yfinance 1.7.0 behaviour (M2.1)

- `yfinance.download()` returns **MultiIndex columns even for a single ticker**
  (`('Close', 'RELIANCE.NS')`). Flattening to the first level is required.
- The index is **tz-naive for daily bars** and tz-aware for intraday. Daily NSE
  bars are localised to `Asia/Kolkata`.
- `end` is **exclusive**. The requested window is widened by one day and the
  result trimmed back, or the last requested session silently disappears.
- `NIFTY` / `BANKNIFTY` are **not** Yahoo tickers. The working tickers are
  `^NSEI` and `^NSEBANK`. User-facing symbol and provider ticker are separate
  fields, both recorded in provenance.
- Gap detection over 2024-Q1 finds exactly 4 one-session gaps for both NIFTY and
  RELIANCE.NS — these are the real NSE holidays, so the detector is finding
  signal, not noise.

### 2026-09-10 — MACD conviction must not be graded by the histogram (M2.7)

- **Problem:** RELIANCE.NS 2020-2024 backtested to +5.6% with 9.8% average
  exposure while reporting itself long.
- **Root cause:** signal `strength` was `|histogram| / expanding_mean(|histogram|)`.
  At a crossover the histogram is *by construction* near zero, so every entry
  was sized at almost nothing.
- **Fix:** a crossover is a binary event, so conviction is binary (1.0 while
  positioned, 0.0 while flat).
- **Result:** exposure 9.8% → 47.1%, return +5.6% → +29.2% against a +79.3%
  buy-and-hold — a believable trend-following underperformance.
- **Lesson:** never grade conviction by the same quantity whose *sign change*
  defined the signal.

### 2026-09-10 — Two engine behaviours found only by running on real data (M2.7)

- **Position churn:** target quantity is a function of equity and conviction,
  both of which drift, so the engine re-traded on every bar — 118 "trades" over
  300 bars. Fix: only re-size when the target *direction* changes;
  `Strategy.rebalances_continuously` opts back in.
- **Useless stops:** after a discretionary exit the strategy's target direction
  still pointed the same way, so the engine re-entered on the next bar. Fix:
  suppress re-entry until the signal itself changes.
- **Lesson:** both bugs pass every unit test that checks a single bar. They only
  appear when the trade count over a long run is inspected for plausibility.

### 2026-09-10 — Test-fixture defect that silently injected slippage (M2.7)

- A `make_fill` helper defaulted `reference_price=100.0` while tests set
  `fill_price=110.0` and commented "no slippage". `FillEvent.slippage_cost` is
  `|fill - reference| * qty`, so every such expectation was off by 40.
- **Fix:** default `reference_price` to `fill_price`.
- **Lesson:** a fixture default that is independent of the value under test will
  eventually contradict the test's own stated intent.

### 2026-09-10 — Verified end-to-end through the Go API (M2.10)

Known-good, against the running stack:

```
curl -s -X POST localhost:8080/api/backtests -H 'Content-Type: application/json' \
  -d '{"symbol":"RELIANCE.NS","strategy":"macd","start":"2022-01-01","end":"2024-12-31"}'
```

returns 30 trades over 739 bars with provenance, assumptions and data-quality
attached; `GET /api/backtests/{id}` round-trips it. Error paths verified:
unknown symbol → 400 listing the known symbols, too-short range → 400 naming
the warm-up requirement, unknown JSON field → 400 naming the field (a typo'd
`commision_bps` must not silently apply the default cost model).

## M3 — strategy framework and experiments

### 2026-09-10 — Strategy results are plausible and mutually distinct (M3.1/M3.2)

RELIANCE.NS 2020-2024, buy-and-hold +79.26%:

| strategy | trades | win rate | profit factor | return | exposure |
|---|---|---|---|---|---|
| macd | 48 | 33.3% | 1.16 | +29.2% | 47.1% |
| bollinger | 23 | 65.2% | 1.25 | +11.9% | 21.1% |
| dual_thrust | 65 | 36.9% | 0.94 | +1.5% | 43.3% |

These have the right *shapes*: mean reversion wins often and small, breakout
wins rarely and loses money after costs on a trending large cap, momentum sits
between. All three underperforming buy-and-hold on a strong uptrend is expected,
not a bug. Treat a strategy whose profile does not match its family as a signal
to look for an implementation error.

### 2026-09-10 — Dual Thrust range must exclude the current bar (M3.2)

The volatility range is computed over `lookback` bars **shifted by one**.
Including the current bar's own high/low in the range that sets its own trigger
is look-ahead. `warmup_bars = lookback + 1` pays for the shift. A test
hand-computes both readings (causal 5 vs same-bar-inclusive 200) so the shift is
proven load-bearing.

### 2026-09-10 — Undefined indicator values must be NaN, never a plausible default

Two independent cases so far: Bollinger `percent_b` on a zero-width band (0/0 —
NaN, not 0.5) and the rolling z-score on a zero-variance window. A fabricated
midpoint tells a strategy the price is exactly centred when the statistic does
not exist. Same rule as metrics returning None rather than 0.0 or inf.

### 2026-09-10 — Reproducibility reports three verdicts, not one (M3.5)

`POST /api/experiments/{id}/rerun` returns `data_version_matches`,
`metrics_match` and a list of the metrics that moved — because "the provider
revised history" and "our code regressed" need opposite responses. Comparison
uses a 1e-9 tolerance (the result round-trips through JSON and PostgreSQL, so
bit-for-bit equality is not guaranteed) which is far tighter than any real
regression.

Verified end to end: a saved 2022-2024 RELIANCE.NS MACD experiment reruns with
`reproducible: true` and zero differences against the live provider and the real
database.

### 2026-09-11 — Renaming the Compose project orphans the database volume

- **What happens:** `name:` in `infra/compose/docker-compose.yml` is the Compose
  project name and prefixes every volume. Changing `ai-quant-terminal` →
  `quantora` means the stack creates `quantora_db_data` and no longer sees
  `ai-quant-terminal_db_data`. Nothing is deleted; the old volume is simply
  unreferenced, so saved experiments appear to have vanished.
- **To migrate the old data (only if that volume still holds wanted rows):**

  ```bash
  docker run --rm -v ai-quant-terminal_db_data:/from -v quantora_db_data:/to \
    alpine sh -c 'cd /from && cp -a . /to'
  ```

  with the stack down, then `make up && make migrate`.
- **To discard it:** `docker volume rm ai-quant-terminal_db_data`.
- **Lesson:** treat the Compose `name:` as part of the persistence contract, not
  as a label.

### 2026-09-11 — Golden fixtures must be proven load-bearing (M3.8)

- A golden test that never fails is indistinguishable from no test. After
  writing `tests/golden/`, the fixtures were verified by **deliberately
  breaking the code**: changing the spread charged per side from half to a third
  failed 12 tests across all four families with named metric drifts; reverting
  restored green.
- A first attempt at a perturbation (nudging the MACD crossover threshold by
  1e-4) changed no signal and correctly failed nothing — which is itself useful:
  the suite does not fire on noise.
- **Do this whenever a regression fixture is added.** Confirm it fails for the
  right reason before trusting it.

### 2026-09-11 — Golden bars are committed, never fetched (M3.8)

- `tests/golden/data/*.csv` holds 992 pinned bars each for RELIANCE.NS and
  TCS.NS (2020-2023), ~296KB total, written at `%.17g` so the CSV round-trips to
  the identical `data_version` — asserted by a test, because a fixture whose own
  hash drifts makes every expectation below it untrustworthy.
- Regeneration is `python scripts/generate_golden_fixtures.py`, deliberately a
  separate command. A generator wired into the test run would rewrite
  expectations to match whatever the code now produces.

### 2026-09-11 — Docker content-lease faults after a daemon restart

- After restarting Docker Desktop, `make up` failed twice with
  `failed to resolve source metadata ... unable to lease content: lease does not
  exist: not found` on `golang:1.27-alpine`, then on the quant-mcp pip layer.
- **Fix:** `docker pull <image>` explicitly, then retry. Both were transient
  content-store faults, not Dockerfile problems — the same build succeeded
  unchanged on the next attempt.
- Also: containers from a previous Compose project name keep holding the host
  ports. Stop them explicitly with
  `docker compose -p <old-name> ... down` before bringing up the renamed stack.

## M4 — C++ execution engine

### 2026-09-11 — The parity surface had to be carved out first (M4.4)

- `simulate()` mixed two concerns: deciding a per-bar **target position** (calls
  the strategy, needs indicators, stays in Python) and **executing** that target
  (a closed numeric problem — costs, cash constraint, portfolio accounting).
- `engine.py` was split along that seam: `simulate()` supplies the decision half
  from a strategy, `simulate_targets()` from a pre-computed array, and both drive
  the *same* execution loop. Without that split, parity would have compared two
  different problems.
- The refactor's behaviour-preservation was **checked, not asserted**: the golden
  fixtures and the look-ahead suite predate it and both stayed green.

### 2026-09-11 — pybind11 was never declared (M4.6)

- **Problem:** the Docker build failed with `ModuleNotFoundError: No module
  named 'pybind11'`. It existed only in the local `.venv`, installed ad hoc.
- **Fix:** a `build` extra in `pyproject.toml` (build-time only, never imported
  at runtime), installed by the Docker builder stage via `pip install ".[build]"`.
- **Lesson:** a dependency that works because it happens to be in your venv is
  not a declared dependency. The container is the check.

### 2026-09-11 — Measured C++ speedup: 3.5-3.9x, and what it means

| Bars | Python | C++ | Speedup |
|---:|---:|---:|---:|
| 10,000 | 207 ms | 54 ms | 3.9x |
| 100,000 | 2,167 ms | 559 ms | 3.9x |
| 1,000,000 | 22,325 ms | 6,423 ms | 3.5x |

A realistic daily single-instrument backtest is a few thousand bars, where both
finish well under a second. The C++ path earns its place on sweeps, Monte Carlo
and minute data — **not** on the backtests the product runs today. Python remains
the default and the reference implementation. Full report:
`docs/research/cpp-benchmark.md`.

### 2026-09-11 — The C++ path must never silently fall back to Python

`cpp_engine.is_available()` returns False with a reason and every entry point
raises rather than degrading to Python. A silent fallback would make the parity
test and the benchmark measure the same code twice while claiming otherwise —
the one failure mode that invalidates every number M4 produces. The CI job
asserts the extension is importable *before* running parity, because the parity
test skips silently without it.

## M5 — portfolio & risk engine

### 2026-09-11 — Correlation and beta on returns, never price levels

Two trending price series correlate near ±1 by construction. A level-based beta
reports a confident number that measures the trend rather than the relationship.
Same spurious-regression trap the pair-trading guardrail avoids — this is now the
third place in the codebase it has come up.

### 2026-09-11 — Static weights are an assumption, so they are stated

Portfolio risk holds weights constant at their as-of-date values across the
return series. The alternative — silently modelling rebalancing the user never
performed — describes a portfolio they do not hold. It appears in the response's
`assumptions` list.

### 2026-09-11 — A scenario report projects its BEFORE state

The report list's metric columns come from the "before" side, so every row is the
portfolio as it actually stood and rows compare like with like. The reweighting
lives inside the payload. A test gives before and after different values so the
projection has to pick the right one rather than happening to.

### 2026-09-11 — Sanity check that caught nothing but would have

A three-large-cap NSE portfolio measured against NIFTY reports beta 0.969. A beta
far from 1 there would have meant the calculation was wrong, not that the
portfolio was unusual. Keep a check like this for every new metric.

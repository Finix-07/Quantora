# Stage 5 — Testing Strategy

> Status: **Approved (2026-09-09)**
> Derived from: `ai_quant_software_manager.md` (§19 Testing Strategy),
> `.sdlc/01-requirements/requirements.md` (NFR3, NFR5, NFR6),
> `.sdlc/03-planning/planning.md` (§4 testing milestone gating, confirmed)

---

## 1. Governing Rule

Confirmed in Stage 3: **every milestone's Definition of Done requires its
relevant tests to pass before the next milestone starts.** Testing is not a
final pass at the end — it is a per-milestone gate. Tests exist at every
language boundary (Python, C++, Go, frontend), per the software handoff.

## 2. Test Layers by Language

### 2.1 Python (`services/quant/`, `services/mcp/`)
Framework: **pytest**.
Unit-test:
- indicator calculations (moving averages, MACD, Bollinger, RSI, ATR,
  z-score, rolling volatility, correlation) against hand-computed or
  reference values
- signal generation per strategy (MACD, Bollinger, Dual Thrust, Pair
  Trading)
- position sizing and exit-signal logic
- portfolio mathematics (allocation, volatility, beta, correlation,
  drawdown, Sharpe, Sortino)
- statistical calculations used in pair-trading / regime detection
- data-layer validation rules (OHLC bounds, volume, monotonic/unique
  timestamps, gap/duplicate detection) using both valid and deliberately
  malformed yfinance-shaped fixtures
- **look-ahead-bias regression test** — required in M2's Definition of Done
  per `planning.md`; asserts that a given day's close cannot influence a
  fill simulated at or before that day's open unless the execution model
  explicitly allows it

### 2.2 C++ (`services/execution-cpp/`)
Framework: a standard C++ unit-test framework (e.g., GoogleTest — final
choice made when M4 starts, since M1–M3 don't touch this component).
Unit-test:
- event ordering (Market → Order → Fill → Position sequencing)
- order execution correctness
- position updates
- PnL calculations (realized/unrealized)
- **parity test against the Python execution path** — required in M4's
  Definition of Done per `planning.md`; same inputs must produce matching
  results between the Python and C++ engines before the C++ path is
  trusted
- benchmark harness (not a pass/fail test, but a tracked measurement) is
  part of M4's deliverables per `architecture.md` §7

### 2.3 Go (`apps/api/`)
Framework: **go test** (standard library) + `testing` table-driven style.
Test:
- API handlers (request validation, status codes, error shapes)
- application services (orchestration logic between handlers and
  quant-mcp/DB, with quant-mcp calls mocked at the HTTP boundary)
- request/response validation
- repository behavior against a real PostgreSQL test database (not mocked —
  migrations must actually apply and queries must actually run, since
  `golang-migrate` correctness is part of what's being verified)

### 2.4 Frontend (`apps/web/`)
Framework: **Playwright** for end-to-end critical-flow tests.
Cover the flow named in the software handoff:
```text
open strategy lab -> select strategy -> run backtest -> view result -> save experiment
```
Plus, once built (M8): the full MVP acceptance-criteria walkthrough from
`requirements.md` §5, driven through the UI.

## 3. Golden / Regression Tests

Maintain a small set of known datasets with known expected results for at
least one strategy per family (MACD, Bollinger, Dual Thrust, Pair Trading),
fixed against the confirmed MVP universe (`NIFTY`, `BANKNIFTY`,
`RELIANCE.NS`, `TCS.NS`, `HDFCBANK.NS`, `INFY.NS`, `ICICIBANK.NS`) and a
fixed historical date range. These live under `tests/integration/` or a
dedicated `tests/golden/` folder (finalized at M2 when the first golden
fixture is created). If a future change alters an important result, the
regression test must fail loudly — silent drift is not acceptable
(consistent with NFR5.6, "prefer failing loudly").

## 4. Reproducibility Testing

Directly tests NFR6: a saved experiment (`experiments` table) must be
rerunnable and produce identical metrics given the same `data_version`.
This is validated as part of M3's Definition of Done (experiment
persistence + rerun) and re-verified whenever the backtester or data layer
changes in a way that could affect determinism.

## 5. Integration Tests

`tests/integration/` covers cross-boundary paths that unit tests can't:
- Go API → `quant-mcp` over HTTP/JSON (confirmed transport, Stage 3 §4) —
  contract test ensuring request/response shapes match `packages/contracts/`
- `quant-mcp` → PostgreSQL — experiment save/rerun round-trip
- MCP tool calls (M6+) → underlying quant-mcp capabilities, verifying MCP
  tool outputs match direct-call outputs (no drift between the "AI-facing"
  and "API-facing" surfaces)

## 6. CI Gating

CI (introduced at M1, per `planning.md`) runs on every push:
- `pytest` for whatever exists in `services/quant/` and `services/mcp/`
- `go test ./...` for `apps/api/`
- C++ test suite once `services/execution-cpp/` exists (M4+)
- Playwright critical-flow suite once `apps/web/` has real pages (M8, or
  earlier for whatever flows already exist)
- Lint for each language (deferred to Stage 4 for exact tool choice —
  standard, not a design-level decision: e.g. `ruff`/`black` for Python,
  `golangci-lint` for Go, `eslint` for TypeScript)

A milestone is not considered complete (per §1) until its layer's CI checks
are green.

## 7. Numerical-Claim Validation (AI-specific, M7+)

Per product metrics (`ai_quant_product_manager.md` §9, "AI quality"): track
tool-call accuracy and the percentage of AI answers whose numerical claims
map back to a real tool output (`source` field, per `architecture.md` §12
example). At minimum, M7's tests must include a case verifying the
guardrail: when a required tool result is missing, the AI states
insufficient evidence rather than inventing a number (NFR5.4) — this is a
correctness test, not just a product metric.

## 8. Mandatory Test Categories (confirmed, non-negotiable)

Confirmed on approval: the following remain **mandatory**, not optional or
trimmable during Stage 4 implementation under time pressure:

- Look-ahead-bias regression test (§2.1)
- Python/C++ parity test (§2.2)
- Golden/regression datasets per strategy family (§3)
- Reproducibility testing for saved experiments (§4)
- Cross-boundary integration tests (§5)
- CI gating on every push (§6)
- AI numerical-claim / guardrail validation (§7)

Exact test-runner/lint tool pins (pytest plugins, GoogleTest vs. Catch2,
ESLint config) remain implementation-level detail deferred to Stage 4, not
an architecture or planning decision.

---

**Status: Approved (2026-09-09).** Proceeding to Stage 6 — Deployment.

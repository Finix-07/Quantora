# Stage 4 — Implementation Plan

> Status: **Active — execution stage (not review-gated the same way Stages 1–3/5–7 are)**
> Derived from: all approved SDLC stages (`01-requirements` through `07-maintenance-operations`)

---

## 1. How to Read This Plan

This breaks the approved milestone roadmap (`planning.md` §2) into
**sequential, dependency-ordered implementation units**. Each unit is
small enough to be one focused work session and one git commit (per the
Git Commit Discipline confirmed in `planning.md` §6 — commit per completed
unit, with a proper explanatory message, not just at milestone boundaries).

Unit ID format: `M<milestone>.<sequence>`. Within a milestone, units are
listed in the order they must be built. Across milestones, `depends_on`
marks the hard prerequisite. Where the dependency graph
(`planning.md` §3) allows real parallelism — **M4 and M5, both only
depending on M3** — those milestones' units are executed by parallel
subagents rather than sequentially.

Every unit inherits, without restating per-unit: the reliability rules
(`requirements.md` §3 NFR5 / `architecture.md` §18), the mandatory test
categories (`testing.md` §8), and the commit discipline above. A unit is
not "done" until its own tests pass — per-milestone gating
(`planning.md` §4.4) applies at the unit level as the practical mechanism
for achieving it.

## 2. Execution Model

```text
M1 (sequential, foundation — nothing else can start first)
   |
   v
M2 (sequential — data layer must exist before strategies)
   |
   v
M3 (sequential within itself — 4 strategies + experiments)
   |
   +------------------------+
   |                        |
   v                        v
M4 (parallel subagent A)  M5 (parallel subagent B)
   |                        |
   +------------------------+
                |
                v
             M6 (sequential — MCP needs M4+M5's real capabilities)
                |
                v
             M7 (sequential — AI orchestrator needs MCP tools)
                |
                v
             M8 (sequential — UI needs everything)
                |
                v
             M9 (post-MVP, not planned here — see planning.md §2 M9)
```

## 3. M1 — Repository & Environment Foundation

**Depends on:** nothing. **Parallelism:** low-value here — foundation
pieces are small and interdependent (compose file needs service
skeletons to reference); build sequentially.

- **M1.1 — Monorepo skeleton.** Create `apps/web`, `apps/api`,
  `services/quant`, `services/mcp`, `packages/contracts`, `infra/docker`,
  `infra/compose`, `db/migrations`, `notebooks`, `docs/{architecture,research,decisions}`,
  `tests/{integration,end_to_end}`, `scripts` per `architecture.md` §16.
  Add root `.gitignore`, `.env.example`.
- **M1.2 — Go API skeleton.** `apps/api`: Go module, `GET /healthz`,
  structured logger, config loader reading `.env`. No business logic yet.
- **M1.3 — Python quant-mcp skeleton.** `services/quant/` and
  `services/mcp/` as two packages inside one deployable unit (per Stage 2
  §19 confirmed decision); `GET /healthz` per Stage 7 §3 (checks DB
  reachability + `GEMINI_API_KEY` presence if `LLM_PROVIDER=gemini`, does
  not call the external API).
- **M1.4 — Next.js skeleton.** `apps/web`: empty app shell, no real pages,
  points at `apps/api` base URL from env.
- **M1.5 — PostgreSQL + golang-migrate wiring.** `db/migrations/` with an
  initial empty/no-op migration; `db` Compose service; migration trigger
  mechanism decided here (deferred decision from `deployment.md` §9) —
  **decision: explicit script/Makefile target (`make migrate`)**, not
  automatic on `api` startup, so a broken migration doesn't crash the API
  container in a confusing way; `api` still refuses to serve traffic if
  migrations haven't been applied (fail loudly, NFR5.6).
- **M1.6 — Docker Compose stack.** `infra/compose/docker-compose.yml`
  wiring `web`, `api`, `quant-mcp`, `db`, and `ollama` as an **optional
  `local-llm` profile** (per Stage 6/7 amendment — `docker compose up`
  never starts `ollama`). `GEMINI_API_KEY`, `LLM_PROVIDER` wired via
  `.env`.
- **M1.7 — CI.** GitHub Actions (or equivalent) running `go test`,
  `pytest`, and lint per language on every push, scoped to whatever exists
  (per `testing.md` §6).

**Definition of Done (M1):** `docker compose up` brings up `web`, `api`,
`quant-mcp`, `db`; each responds to its health check; CI is green on the
skeleton commit. **Commit checkpoint:** one commit per unit above (7
commits), each explaining what was scaffolded and why.

## 4. M2 — Data Layer + First Strategy (Walking Skeleton)

**Depends on:** M1. **Parallelism:** low — this is one tight vertical
slice; splitting it across agents would create integration risk for the
project's first correctness-critical path. Sequential.

- **M2.1 — yfinance data client.** `services/quant/data/`:
  `get_prices(symbol, start, end, interval)`, `get_universe(name)`
  defaulting to the confirmed 7-instrument universe
  (`NIFTY, BANKNIFTY, RELIANCE.NS, TCS.NS, HDFCBANK.NS, INFY.NS, ICICIBANK.NS`).
  Wraps yfinance; returns OHLCV + provenance metadata
  (`architecture.md` §4.4).
- **M2.2 — Validation layer.** OHLC bound checks, volume ≥ 0, monotonic +
  unique timestamps, gap/duplicate detection (`architecture.md` §4.3).
  Fails loudly (raises/returns explicit error) rather than silently
  dropping/patching bad rows.
- **M2.3 — Data layer tests.** pytest: valid-fixture pass, malformed-fixture
  (bad OHLC ordering, negative volume, duplicate timestamp) each rejected
  with a specific, assertable error (`testing.md` §2.1).
- **M2.4 — Indicator library (minimum for MACD).** `services/quant/indicators/`:
  `moving_average.py`, `macd.py`, each unit-tested against hand-computed
  reference values.
- **M2.5 — Strategy contract.** `services/quant/strategies/base.py`
  defining `Strategy.generate_signals/position_size/exit_signal`
  (`architecture.md` §5), framework-agnostic.
- **M2.6 — MACD strategy.** `services/quant/strategies/macd.py`
  implementing the contract; unit-tested signal generation.
- **M2.7 — Execution simulator (Python, v1).** `services/quant/backtest/`:
  signal → position sizing → order intent → execution sim → portfolio
  state → PnL, modeling commission/slippage/spread/cash constraints
  (`architecture.md` §6).
- **M2.8 — Look-ahead-bias regression test.** Mandatory per `testing.md`
  §2.1/§8 — explicit test asserting a bar's close cannot fill an order
  simulated at/before that bar's open unless the execution model allows it.
- **M2.9 — Backtest result contract.** Serialize to the JSON shape in
  `architecture.md` §8; unit-tested for schema correctness.
- **M2.10 — Wire through Go API (minimal).** `POST /api/backtests`,
  `GET /api/backtests/:id` in `apps/api`, calling `quant-mcp` over
  HTTP/JSON (confirmed transport, `planning.md` §4), returning the same
  result contract, no persistence yet.

**Definition of Done (M2):** `data -> MACD -> signal -> backtest -> metrics`
runs end-to-end via the Go API, backed by real yfinance data for the
confirmed universe, all M2 tests green including the look-ahead-bias test.
**Commit checkpoint:** one commit per unit (10 commits).

## 5. M3 — Generalized Strategy Framework + Experiments

**Depends on:** M2. **Parallelism:** moderate — the three new strategies
(M3.1–M3.3) are independent of each other and of the experiment-persistence
work (M3.4–M3.6); a fork/subagent per strategy is reasonable here since
each only touches its own file plus its own test file. Experiment
persistence and comparison (M3.4+) depend on having ≥1 strategy beyond
MACD to be meaningful, so it's sequenced after, not parallel to, the
strategy units.

- **M3.1 — Bollinger Bands (mean reversion) strategy** *(parallelizable)*.
  `services/quant/indicators/bollinger.py` +
  `services/quant/strategies/bollinger.py`, unit-tested.
- **M3.2 — Dual Thrust (breakout) strategy** *(parallelizable)*.
  `services/quant/strategies/dual_thrust.py`, unit-tested.
- **M3.3 — Pair Trading (stat-arb) strategy** *(parallelizable)*.
  `services/quant/indicators/zscore.py`, `.../correlation.py` +
  `services/quant/strategies/pair_trading.py`, unit-tested (this one needs
  correlation/z-score indicators M2 didn't build — included here).
- **M3.4 — `experiments` table + migration.** `db/migrations/`: strategy,
  parameters, universe, timeframe, start/end, cost model, data version,
  code version, results, created_at (`architecture.md` §15/§9).
- **M3.5 — Experiment persistence + rerun.** `apps/api`
  `POST /api/experiments` (save), `POST /api/experiments/:id/rerun`;
  Go repository layer per `architecture.md` §15 (domain models, not
  ORM-coupled structs).
- **M3.6 — Reproducibility test.** Save an experiment, rerun it, assert
  identical metrics given the same `data_version` (`testing.md` §4,
  mandatory).
- **M3.7 — `compare_strategies`.** `POST /api/strategies/compare`: run
  ≥2 strategies on the same instrument/date range, return metrics
  side-by-side (CAGR, Sharpe, Sortino, max drawdown, win rate, profit
  factor, trade count, turnover, exposure, equity/drawdown curves).
- **M3.8 — Golden regression fixtures.** One fixed dataset + expected
  result per strategy family, pinned to the confirmed universe
  (`testing.md` §3, mandatory).

**Definition of Done (M3):** all four strategies run through the shared
backtester; an experiment is saved and rerun with identical results;
`compare_strategies` returns a valid side-by-side comparison; golden
fixtures exist and pass. **Commit checkpoint:** one commit per unit (8
commits — M3.1–M3.3 committed independently even if built in parallel).

## 6. M4 — C++ Execution Engine  *(parallel track A, runs alongside M5)*

**Depends on:** M3. **Parallelism:** this whole milestone is one of the
two parallel tracks — run by a dedicated subagent concurrently with M5.
Internally sequential (event model must exist before the loop, the loop
before the benchmark).

- **M4.1 — Event model.** `services/execution-cpp/`: `MarketEvent`,
  `OrderEvent`, `FillEvent`, `PositionEvent` structs (`architecture.md` §7).
- **M4.2 — Event-driven simulator loop.** Updates cash/positions/orders/
  fills/realized+unrealized PnL from a stream of events.
- **M4.3 — C++ unit tests.** Event ordering, order execution, position
  updates, PnL calculations (`testing.md` §2.2).
- **M4.4 — Python binding layer.** Thin binding exposing the stable C++
  API to `services/quant/` (only once M4.1–M4.3 are stable, per
  `architecture.md` §7's explicit ordering rule).
- **M4.5 — Parity test.** Same inputs through Python (M2.7) and C++
  engines must produce matching results (`testing.md` §2.2/§8, mandatory)
  before the C++ path is trusted anywhere else.
- **M4.6 — Benchmark.** Documented benchmark of C++ vs. Python on an
  equivalent large-event-throughput workload (`architecture.md` §7/§10
  requirement — no unproven performance claims).

**Definition of Done (M4):** parity test passes; benchmark results are
documented showing a measurable difference for the target workload.
**Commit checkpoint:** one commit per unit (6 commits).

## 7. M5 — Portfolio & Risk Engine  *(parallel track B, runs alongside M4)*

**Depends on:** M3 only (not M4 — portfolio math doesn't need the C++
engine). **Parallelism:** dedicated subagent, concurrent with M4.
Internally mostly sequential (holdings model before metrics before
scenarios).

- **M5.1 — Holdings/portfolio model.** `services/quant/portfolio/`:
  allocation, position concentration.
- **M5.2 — Risk metrics.** Portfolio return, volatility, beta, correlation,
  max drawdown, Sharpe, Sortino, sector exposure where data permits.
- **M5.3 — `portfolios`/`positions` tables + migration.**
- **M5.4 — Scenario analysis.** Reweight → recalculate → before/after
  comparison (`architecture.md` §10).
- **M5.5 — Saved risk reports.** `risk_reports` table + persistence.
- **M5.6 — Go API wiring.** `GET /api/portfolio`, `POST /api/portfolio/scenario`.
- **M5.7 — Portfolio/risk unit tests.** Per `testing.md` §2.1 (portfolio
  mathematics category).

**Definition of Done (M5):** a portfolio can be defined, risk metrics
computed, a scenario applied, before/after comparison and saved report
inspected via the API. **Commit checkpoint:** one commit per unit (7
commits).

**Merge point after M4 + M5:** both tracks' subagents report back; verify
no integration conflicts (they touch disjoint files —
`services/execution-cpp/` + `services/quant/backtest/` bindings vs.
`services/quant/portfolio/` — so conflict risk is low by construction), run
the full test suite together once before proceeding to M6.

## 8. M6 — MCP Layer

**Depends on:** M2–M5 all complete (needs real capabilities to expose).
**Parallelism:** low — one server, tools share a common request/response
convention; sequential.

- **M6.1 — MCP server scaffold.** Inside `services/mcp/`, domain-tool
  registration mechanism.
- **M6.2 — `get_market_data`, `calculate_indicators`.** Thin wrappers over
  M2 capabilities.
- **M6.3 — `run_backtest`, `compare_strategies`.** Wrappers over M2/M3.
- **M6.4 — `get_portfolio`, `analyze_portfolio_risk`.** Wrappers over M5.
- **M6.5 — `get_experiment`.** Wrapper over M3.5.
- **M6.6 — `find_pairs`, `run_monte_carlo`, `detect_market_regime` (stubs
  acceptable).** Per `planning.md` M6 deliverables — full implementation is
  Phase 6/deferred product scope; stubs return a clear
  "not yet implemented" structured response, never a fabricated result.
- **M6.7 — MCP contract tests.** Assert MCP tool outputs match direct-call
  outputs (no drift), per `testing.md` §5.

**Definition of Done (M6):** an external MCP client can call each tool
directly and get correct, traceable results (or an honest "not
implemented" for the three stubbed tools). **Commit checkpoint:** one
commit per unit (7 commits).

## 9. M7 — AI Researcher (Gemini/Ollama Orchestration)

**Depends on:** M6. **Parallelism:** low — orchestration logic is one
coherent state machine; sequential.

- **M7.1 — `LLMClient` interface.** `complete()`/`toolCall()` abstraction
  (`architecture.md` §3.6).
- **M7.2 — `GeminiClient` implementation.** Default provider; model
  `gemini-3.6-flash` (verified working 2026-09-09 — see
  `architecture.md` §3.6 note); reads `GEMINI_API_KEY`; fails loudly at
  startup if missing while `LLM_PROVIDER=gemini` (NFR5.6).
- **M7.3 — `OllamaClient` implementation.** Optional fallback provider,
  active only when `LLM_PROVIDER=ollama` and the `local-llm` Compose
  profile is running.
- **M7.4 — Orchestration loop.** Query → Intent/Plan → Tool Selection → MCP
  Tool Calls → Structured Results → Validation/Context Assembly →
  Explanation (`architecture.md` §12).
- **M7.5 — Guardrail enforcement.** Missing tool result → state
  insufficient evidence or request another tool call, never fabricate
  (NFR5.4) — this is a correctness test per `testing.md` §7, not just
  a behavior.
- **M7.6 — Structured + NL output format.** Every AI answer pairs a
  natural-language explanation with the structured `{metric, value,
  period, source}` shape (`architecture.md` §12 example).
- **M7.7 — AI-quality tests.** Guardrail test from M7.5, plus a
  tool-call-accuracy smoke test on a fixed example query.

**Definition of Done (M7):** a natural-language research question (e.g.,
"Compare MACD and Bollinger for RELIANCE.NS over 5 years including costs")
produces a traceable, tool-backed answer via either provider.
**Commit checkpoint:** one commit per unit (7 commits).

## 10. M8 — Product Integration (Next.js ↔ Go API)

**Depends on:** M2–M7 (UI surfaces everything). **Parallelism:**
moderate — the five UI views are largely independent React surfaces once
the Go API contracts are stable; can be split across subagents per view if
useful, but each is small enough that sequential is also reasonable. Judgment
call at execution time based on remaining context budget.

- **M8.1 — API client + shared types.** `packages/contracts/` consumed by
  `apps/web`.
- **M8.2 — Research workspace view.** NL input, recent
  questions/experiments, portfolio snapshot.
- **M8.3 — Market view.** Instrument selector, price chart, indicators,
  signal status.
- **M8.4 — Strategy Lab view.** Strategy selector, params, date range,
  cost assumptions, run backtest, comparison table + charts.
- **M8.5 — Portfolio & Risk view.** Holdings, allocation, risk metrics,
  correlation matrix, scenario analysis.
- **M8.6 — Experiments/Journal view.** Saved backtests, trade history,
  AI-generated analysis.
- **M8.7 — Playwright E2E: critical flow.** `open strategy lab -> select
  strategy -> run backtest -> view result -> save experiment`
  (`testing.md` §2.4).
- **M8.8 — Playwright E2E: full MVP acceptance walkthrough.** All 12 items
  in `requirements.md` §5, driven through the UI.

**Definition of Done (M8):** the full MVP Definition of Done
(`requirements.md` §5, 12 items) passes through the UI. **Commit
checkpoint:** one commit per unit (8 commits).

## 11. M9 — Paper Trading Foundation

**Explicitly not planned into units here.** Per `planning.md` §2 M9 and
`requirements.md` §4, this is post-MVP and only begins once M1–M8 are
stable and the user explicitly asks to pull it into active scope.

## 12. Total Unit Count & Parallelism Summary

| Milestone | Units | Execution |
|---|---|---|
| M1 | 7 | Sequential |
| M2 | 10 | Sequential |
| M3 | 8 | Sequential overall; M3.1–M3.3 parallelizable |
| M4 | 6 | **Parallel track A** (concurrent with M5) |
| M5 | 7 | **Parallel track B** (concurrent with M4) |
| M6 | 7 | Sequential |
| M7 | 7 | Sequential |
| M8 | 8 | Sequential (or per-view parallel, execution-time call) |

**60 implementation units total**, each ending in its own git commit with
an explanatory message.

## 13. Next Step

Execution begins at **M1.1**. Given M1's units are small and
interdependent, I'll work through them directly rather than spawning
subagents — parallel subagents are reserved for M4/M5 (the one point in
the roadmap where the dependency graph genuinely allows concurrent,
non-conflicting work) and optionally M8's independent view components.

# Stage 3 — Planning

> Status: **Approved (2026-09-09)**
> Derived from: `ai_quant_software_manager.md` (§22 Milestones), `ai_quant_product_manager.md` (§10 Roadmap),
> `.sdlc/01-requirements/requirements.md`, `.sdlc/02-architecture-design/architecture.md`

---

## 1. Purpose of This Stage

Turn the approved requirements and architecture into a milestone-ordered
roadmap with clear deliverables, dependencies, and Definition-of-Done per
milestone. This stage does **not** break work into implementation units/PRs
— that is Stage 4 (Implementation), which only starts once every SDLC stage
here is approved.

## 2. Milestone Roadmap

Milestones are sequential; each depends on the previous one being functionally
complete. This mirrors the software handoff's milestone list, aligned to the
resolved architecture (yfinance, Docker Compose, single `quant-mcp` service,
Ollama).

### M1 — Repository & Environment Foundation
**Depends on:** nothing (first milestone)
**Deliverables:**
- Monorepo structure per `architecture.md` §16 (`apps/`, `services/`, `packages/`, `infra/`, `db/`, `notebooks/`, `docs/`, `tests/`, `scripts/`, `.sdlc/`)
- Docker Compose stack: `web`, `api`, `quant-mcp`, `db`, `ollama` (execution-cpp added at M4)
- PostgreSQL running with an initial empty migration set
- Next.js skeleton (empty app shell, no real pages yet)
- Go API skeleton (health check endpoint only)
- Python `quant-mcp` service skeleton (importable package structure: `services/quant/`, `services/mcp/`)
- CI checks wired (lint + test run on push, scoped to whatever exists at this milestone)
**Definition of Done:** `docker compose up` brings up all services locally; each service responds to a basic health check; CI passes on an empty/skeleton commit.

### M2 — Data Layer + First Strategy (walking skeleton)
**Depends on:** M1
**Deliverables:**
- yfinance-backed `get_prices(symbol, start, end, interval)` / `get_universe(name)` with validation (OHLC bounds, volume, monotonic/unique timestamps) and provenance metadata
- Indicator library: moving averages, MACD (minimum needed for the first strategy)
- MACD/momentum strategy implementing the `Strategy` contract
- Python backtester (no C++ yet): signal → position sizing → order intent → execution sim → portfolio state → PnL, with commission/slippage/spread/cash constraints modeled
- Explicit look-ahead-bias prevention + a test proving it
- Backtest result contract (per `architecture.md` §8) returned and unit-tested
**Definition of Done:** `data -> MACD -> signal -> backtest -> metrics` runs end-to-end locally and is covered by unit tests, including a look-ahead-bias regression test.

### M3 — Generalized Strategy Framework + Experiments
**Depends on:** M2
**Deliverables:**
- Bollinger Bands (mean reversion), Dual Thrust (breakout), Pair Trading (stat-arb) strategies
- Common strategy interface hardened across all four families
- Experiment persistence in PostgreSQL (strategy, params, universe, timeframe, cost model, data version, code version, results, created_at)
- Rerun-from-saved-config capability
- `compare_strategies` capability (metrics side-by-side across strategies)
**Definition of Done:** all four strategy families run through the same backtester; an experiment can be saved and rerun to produce identical results; two+ strategies can be compared on one instrument/date range.

### M4 — C++ Execution Engine
**Depends on:** M3 (Python backtester must be correct first)
**Deliverables:**
- Event-driven C++ simulator (`MarketEvent`/`OrderEvent`/`FillEvent`/`PositionEvent` → cash/positions/orders/fills/PnL)
- Python binding layer once the C++ API is stable
- Benchmark comparing C++ vs. Python execution path on an equivalent workload, with results documented
**Definition of Done:** C++ engine produces results matching the Python engine on the same inputs (parity test), and a benchmark demonstrates a measurable performance difference for the target workload (large event throughput / large-scale simulation).

### M5 — Portfolio & Risk Engine
**Depends on:** M3 (needs experiments/backtests as risk input, does not need C++)
**Deliverables:**
- Holdings model, allocation, concentration, correlation, beta, volatility, max drawdown, Sharpe, Sortino, sector exposure (data-permitting)
- Scenario analysis (reweight → recalc → before/after comparison)
- Saved risk reports
**Definition of Done:** a portfolio can be defined, risk metrics computed, a scenario applied, and the before/after comparison + saved report inspected.

### M6 — MCP Layer
**Depends on:** M2–M5 (needs real capabilities to expose as tools)
**Deliverables:**
- MCP server (within `quant-mcp` service) exposing domain tools per `architecture.md` §11/§3.5: `get_market_data`, `calculate_indicators`, `run_backtest`, `compare_strategies`, `get_portfolio`, `analyze_portfolio_risk`, `find_pairs` (stub acceptable if pair-trading screening isn't fully built), `run_monte_carlo` (stub acceptable — Monte Carlo is Phase 6/deferred per product roadmap), `detect_market_regime` (stub acceptable, same reason), `get_experiment`
**Definition of Done:** an external MCP client (not the web UI) can call these tools directly and get correct, traceable results.

### M7 — AI Researcher (Ollama orchestration)
**Depends on:** M6
**Deliverables:**
- `LLMClient` abstraction wired to Ollama (per `architecture.md` §3.6/§12)
- Multi-step tool-using workflow: question → plan → MCP tool calls → structured results → explanation
- Guardrail enforcement: never fabricate missing data; request another tool call or state insufficient evidence
**Definition of Done:** a natural-language research question (e.g., "Compare MACD and Bollinger for RELIANCE over 5 years including costs") produces a traceable, tool-backed answer.

### M8 — Product Integration (Next.js ↔ Go API)
**Depends on:** M2–M7 (UI surfaces everything built so far)
**Deliverables:**
- Research workspace, Market view, Strategy Lab, Portfolio & Risk view, Experiments/Journal view (per `architecture.md` §14)
- Full click-through demo: select instrument → inspect data/indicators → run backtest → compare strategies → inspect portfolio risk → save experiment → ask AI a question → see traceable answer → rerun experiment
**Definition of Done:** the full MVP acceptance criteria from `requirements.md` §5 (all 12 items) pass through the UI, not just via API calls.

### M9 — Paper Trading Foundation (post-MVP, explicitly out of MVP scope per requirements §4)
**Depends on:** M1–M8 stable
**Deliverables:** signal → risk validation → paper order → simulated fill → portfolio update → journal
**Definition of Done:** not part of MVP Definition of Done; tracked here only for roadmap continuity. Not scheduled into Stage 4 implementation units unless the user explicitly asks to pull it into MVP scope.

## 3. Milestone Dependency Graph

```text
M1 (foundation)
  |
  v
M2 (data + MACD walking skeleton)
  |
  v
M3 (4 strategies + experiments) ---------+
  |                                      |
  v                                      v
M4 (C++ engine)                    M5 (portfolio & risk)
  |                                      |
  +------------------+-------------------+
                      |
                      v
                 M6 (MCP layer)
                      |
                      v
                 M7 (AI researcher)
                      |
                      v
                 M8 (product integration / demo)
                      |
                      v
                 M9 (paper trading — post-MVP)
```

M4 and M5 can proceed in parallel once M3 is done (both depend only on M3,
not on each other) — this is where Stage 4 will use parallel subagents.

## 4. Planning Decisions — Confirmed

1. **API ↔ quant-mcp transport: HTTP/JSON.** Go calls `quant-mcp` over
   localhost HTTP. Simplest to debug locally, keeps `quant-mcp`
   independently runnable/testable, matches the REST style already used for
   the Go public API.
2. **DB migration tool: golang-migrate.** SQL-file based, lightweight, fits
   the Go + PostgreSQL architecture without ORM coupling (consistent with
   `architecture.md` §15's domain-model/repository-interface approach).
3. **MVP universe: 7 instruments, confirmed.**
   `NIFTY`, `BANKNIFTY`, `RELIANCE.NS`, `TCS.NS`, `HDFCBANK.NS`, `INFY.NS`,
   `ICICIBANK.NS`. These are the exact yfinance-compatible tickers to use
   in the data layer (M2) and `get_universe(name)` default set.
4. **Testing milestone gating: confirmed.** Every milestone's Definition of
   Done requires its relevant tests to pass before the next milestone
   starts.

## 5. Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| yfinance rate-limits or silently changes/drops NSE data | Data layer breaks or returns bad data undetected | Validation rules (§4.3 of architecture.md) run on every fetch; fail loudly (NFR5.6) rather than caching stale/bad data silently |
| C++ engine (M4) introduces result drift vs. Python engine | Backtest results become inconsistent depending on engine used | Parity test required in M4 Definition of Done before C++ path is trusted |
| Ollama model quality insufficient for reliable tool-planning | AI researcher (M7) gives poor plans or misuses tools | `LLMClient` abstraction (already in architecture) means the model can be swapped without redesigning M6/M7; guardrails catch missing-data fabrication regardless of model |
| Scope creep into Monte Carlo / regime detection / options before core loop is solid | MVP delayed | Product roadmap already defers these to Phase 6; M6 ships them as stubs only |
| Single local Docker Compose stack becomes hard to develop against as services grow | Developer friction | Explicitly deferred: no premature service splitting (quant-mcp stays one container until scaling/isolation is actually needed, per Stage 2 §19) |

## 6. Git Workflow / Commit Discipline

Binding on Stage 4 implementation: commit to git whenever a component or a
significant individual implementation unit is completed — not only at
milestone boundaries. Each such commit's message must carry a proper brief
and explanation (what the unit does and why it was built that way), not a
terse label — future sessions and reviewers should be able to understand
the change from the commit message alone. This applies per-unit within a
milestone (e.g., "MACD indicator implemented + tested" can be its own
commit, ahead of the rest of M2 being done).

---

**Status: Approved (2026-09-09).** Proceeding to Stage 5 — Testing.
(Stage 4 — Implementation — is deferred until all of Stages 1–7 are
approved, per the SDLC approval workflow in `.sdlc/README.md`.)

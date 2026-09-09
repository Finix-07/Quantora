# Stage 2 — Architecture & Design

> Status: **Approved (2026-09-09); amended (2026-09-09)** for LLM provider
> (§1 table, §3.6, §12, §17) — Gemini free tier default, Ollama optional
> local fallback.
> Derived from: `ai_quant_software_manager.md`, `.sdlc/01-requirements/requirements.md`
> Incorporates resolved decisions: data provider = **yfinance**, deployment =
> **local Docker Compose only**, LLM = **Gemini API free tier (default) with
> Ollama as an optional local fallback** (amended 2026-09-09), pluggable
> behind MCP.

---

## 1. Architecture Principles

Priority order (unchanged from requirements): **correctness → reproducibility
→ clear interfaces → performance → autonomy.** No enterprise-scale or
HFT-latency engineering.

Each language is used where it adds real engineering value, not for résumé
diversity:

| Component | Technology | Why |
|---|---|---|
| Quant research, statistics, indicators, strategies | Python | pandas/numpy/scipy/sklearn ecosystem, fast iteration |
| Application API / orchestration | Go | concurrency, clean service boundaries |
| Backtest/execution simulation (perf-sensitive path) | C++ | memory/perf-aware systems work, benchmarked vs. Python |
| Web UI | Next.js / TypeScript | professional research/trading UI |
| Database | PostgreSQL | reliable relational persistence |
| Cache/job coordination | Redis (deferred — add only if MVP shows repeated-query or background-job load) | avoid speculative infra |
| MCP server | Python | close to the research engine; exposes domain tools |
| LLM runtime | **Gemini API free tier** (default), **Ollama** local (optional fallback) | amended Q3 — free-tier cloud API by default, zero-cost local fallback if quota is hit or the user wants no external dependency; interface stays pluggable |
| Containerization | Docker Compose | resolved Q2 — single local stack, no cloud target in MVP |

## 2. System Diagram

```text
                         USER (local machine only)
                               |
                 +-------------+-------------+
                 |                           |
                 v                           v
          Next.js Web UI                 MCP Client
        (localhost:3000)              (e.g. local chat UI,
                 |                      CLI, or Next.js itself)
                 +-------------+-------------+
                               |
                               v
                        Go Application API
                        (localhost:8080)
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
       Portfolio Service   Research API    Experiment API
              |                |                |
              +----------------+----------------+
                               |
                +--------------+--------------+
                |                             |
                v                             v
          Python Quant Engine          C++ Execution Engine
          (indicators, strategies,     (event-driven backtest
           data layer via yfinance,     simulator, introduced
           portfolio math, ML)          after Python version is
                |                       correct + benchmarked)
      +---------+---------+                    |
      |         |         |                    |
      v         v         v                    v
   Data     Indicators  Backtest ------> (optional call-out to
  (yfinance)  library   orchestrator      C++ binding once stable)
      |         |         |
      +---------+---------+
                |
                v
          PostgreSQL (experiments, portfolios,
                       trades, metadata)
                |
                v
       MCP Server (Python) <---- Ollama (local LLM runtime)
       exposes domain tools        pluggable behind MCP —
       to the AI orchestrator      swappable later
```

All services run locally via Docker Compose. No component talks to any
external network service except yfinance's public data endpoints — there is
no cloud deployment target for the MVP (resolved Q2).

## 3. Component Responsibilities

### 3.1 Python — Quantitative Computing

Owns: market data retrieval/validation (yfinance), indicators, strategy
implementations, portfolio mathematics, statistical calculations, Monte
Carlo, regime detection, ML experimentation, and the Python-side backtest
orchestrator that will later call into the C++ engine.

### 3.2 Go — Application Backend

Owns: REST/HTTP API, request orchestration, experiment lifecycle
(create/persist/rerun), portfolio service, structured logging with
request/experiment IDs, service health/config. Handlers never contain
quantitative formulas — they call domain services which call into Python
(via subprocess/RPC boundary, decided in Stage 3) or read from PostgreSQL.

### 3.3 C++ — Execution Simulation (introduced after Python backtester is correct)

Owns: event-driven backtest loop processing `MarketEvent` / `OrderEvent` /
`FillEvent` / `PositionEvent`, updating cash/positions/orders/fills/PnL with
low overhead. Exposed to Python via a thin binding layer only once the C++
API is stable. Must ship with a benchmark against the equivalent Python path
before being adopted — no unproven performance claims (NFR1, engineering
rule from the software handoff).

### 3.4 Next.js / TypeScript — UI

Owns: Research workspace, Market view, Strategy Lab, Portfolio & Risk view,
Experiments/Journal view. Consumes only the Go API — never touches Postgres
or the Python engine directly.

### 3.5 MCP Server (Python)

Exposes domain-level actions only (`get_market_data`, `calculate_indicators`,
`run_backtest`, `compare_strategies`, `get_portfolio`,
`analyze_portfolio_risk`, `find_pairs`, `run_monte_carlo`,
`detect_market_regime`, `get_experiment`). No `run_sql`, no
`execute_arbitrary_python`, no raw DB row access — the AI must never need to
know the schema.

### 3.6 LLM Orchestrator (Gemini free tier default, Ollama optional fallback, pluggable)

Plans multi-step research (`User Query → Intent/Plan → Tool Selection → MCP
Tool Calls → Structured Results → Validation/Context Assembly → LLM
Explanation`). Runs against the **Gemini API free tier by default**; a
**local Ollama model is an optional fallback** for when the free-tier quota
is hit or the user wants zero external dependency. The orchestration code
must depend only on a thin, provider-agnostic interface (a `LLMClient`
abstraction with `complete()`/`toolCall()` semantics) with two
implementations — `GeminiClient` and `OllamaClient` — selected by
configuration, not by changing application code. Guardrail: if a required
tool result is missing, the agent states the evidence is insufficient
rather than fabricating a number
(NFR5.4).

**Verified 2026-09-09:** `GEMINI_API_KEY` was tested directly against the
Gemini REST API. `gemini-3.8-flash` does not exist and `gemini-2.0-flash`
is deprecated/404s; the working current model via
`v1beta/models/{model}:generateContent` is **`gemini-3.6-flash`** — use
this as the default model name when Stage 4 implements `GeminiClient`.

## 4. Data Layer Design

### 4.1 Provider

**yfinance**, used for NIFTY, BANKNIFTY, and 5–10 NSE large-cap equities
(resolved Q1). Known constraints to design around:
- Unofficial API wrapping Yahoo Finance — no formal SLA, rate limits are
  informal, and endpoints/behavior can change without notice.
- Adjustment policy (splits/dividends) and timezone handling must be
  normalized explicitly and recorded in provenance metadata (FR1) — do not
  trust yfinance defaults blindly.
- Gaps and stale data are expected for NSE symbols retrieved through a
  Yahoo-backed source; gap/duplicate detection is mandatory, not optional.

### 4.2 Interface (language-agnostic contract, implemented in Python)

```text
get_prices(symbol, start, end, interval) -> OHLCV[+provenance]
get_universe(name) -> [symbols]
```

### 4.3 Validation rules

```text
low <= open <= high
low <= close <= high
volume >= 0
timestamps are monotonic and unique
```

### 4.4 Provenance metadata (attached to every dataset)

```text
provider: "yfinance"
symbol, interval, timezone
retrieval_timestamp
adjustment_policy
```

A result without this metadata is not considered complete (NFR4).

### 4.5 Caching

Deferred for MVP. If repeated-query load becomes noticeable during
Milestone 2/3 development, introduce Redis as a read-through cache in front
of `get_prices`/`get_universe` — not before, per the "no speculative infra"
principle.

## 5. Domain Model

```text
Asset
MarketData
Indicator
Strategy
StrategyConfig
Signal
Order
Fill
Position
Portfolio
Backtest
Trade
Experiment
MarketRegime
RiskReport
```

### Strategy contract

Strategies are independent of HTTP, DB, MCP, and LLM concerns:

```python
class Strategy:
    def generate_signals(self, data): ...
    def position_size(self, context): ...
    def exit_signal(self, context): ...
```

Invariant: a strategy must be executable from Python directly, from unit
tests, from a backtest run, and through the API — without knowing who
called it.

## 6. Backtesting Engine Design

```text
Historical Data (yfinance, validated)
       |
       v
Signal Generation (Strategy.generate_signals)
       |
       v
Position Sizing (Strategy.position_size)
       |
       v
Order Intent
       |
       v
Execution Simulator (Python first; C++ later)
       |
       v
Portfolio State
       |
       v
PnL / Metrics
```

Minimum execution assumptions modeled: commission, slippage, spread, cash
constraints, position sizing, long/short support, explicit order
timestamps.

**Critical correctness requirement — no look-ahead bias.** A daily close
must not be used to simulate an earlier fill on the same bar unless the
execution model explicitly allows it. Time alignment is made explicit in
code and covered by dedicated tests (this is a Stage 5/Testing artifact
requirement, tracked here as a design constraint).

## 7. C++ Execution Engine (introduced at Milestone 4, not MVP-blocking for first walking skeleton)

Event-driven simulator receiving compact events (`MarketEvent`,
`OrderEvent`, `FillEvent`, `PositionEvent`) and updating cash, positions,
orders, fills, realized/unrealized PnL. Exposed to Python via a thin binding
layer only once the C++ API is stable and a benchmark exists showing a
measurable advantage over the Python path for the target workload
(large-scale simulation / high event throughput).

## 8. Backtest Result Contract

```json
{
  "experiment_id": "...",
  "strategy": "macd",
  "symbol": "NIFTY",
  "start": "2020-01-01",
  "end": "2025-12-31",
  "parameters": {},
  "cost_model": {},
  "metrics": {
    "cagr": 0.0,
    "sharpe": 0.0,
    "sortino": 0.0,
    "max_drawdown": 0.0,
    "win_rate": 0.0,
    "profit_factor": 0.0
  },
  "trades": [],
  "equity_curve": [],
  "drawdown_curve": [],
  "data_version": "..."
}
```

Must be serializable, persistable (PostgreSQL), and reproducible (NFR6).

## 9. Experiment System Design

Persisted fields: strategy, strategy parameters, instrument/universe,
timeframe, start/end, cost model, data version, code/version identifier,
results, created_at. Rerun must not require the user to manually remember
configuration (FR5, NFR6).

## 10. Portfolio & Risk Engine Design

Minimum calculations: allocation, position concentration, portfolio return,
volatility, beta, correlation, max drawdown, Sharpe, Sortino, sector
exposure (where data permits).

Scenario analysis flow:

```text
current portfolio -> modify one or more weights -> recalculate risk
                   -> compare before/after
```

Deferred beyond MVP: Monte Carlo, VaR/CVaR, factor exposure,
regime-conditioned risk (tracked for Stage 3 roadmap placement, not
removed from product scope).

## 11. MCP Layer Design

Domain actions only (see §3.5). Example composed workflow:

```text
get_market_data()
       |
       +--> run_backtest(MACD)
       |
       +--> run_backtest(Bollinger)
       |
       +--> compare_strategies()
       |
       v
final explanation
```

## 12. AI Agent Architecture

```text
User Query -> Intent/Plan -> Tool Selection -> MCP Tool Calls
     -> Structured Results -> Validation/Context Assembly -> LLM Explanation
```

Backing model: **Gemini API free tier by default**, with **Ollama (local)
as an optional fallback**, selected at the orchestrator's `LLMClient`
boundary (see §3.6). Guardrails: never fabricate missing data;
if a tool result is missing, say evidence is insufficient or request
another tool call (NFR5.4). Tool outputs return structured data alongside a
concise natural-language description, e.g.:

```json
{
  "metric": "max_drawdown",
  "value": -0.214,
  "period": "2020-2025",
  "source": "backtest:exp_123"
}
```

## 13. Go Backend API (initial surface)

```text
GET  /api/market/:symbol
POST /api/backtests
GET  /api/backtests/:id
POST /api/strategies/compare
GET  /api/portfolio
POST /api/portfolio/scenario
GET  /api/experiments
POST /api/experiments/:id/rerun
```

Layering: `HTTP Handler -> Application Service -> Quant/Portfolio Interface
-> Python or C++ implementation`. No quant formulas in handlers.

## 14. Next.js Frontend Structure

```text
app/
  research/
  market/[symbol]/
  strategies/
  portfolio/
  experiments/
  journal/
```

Requirements: responsive layout, interactive charts, loading/error states,
explicit backtest assumptions shown in-UI, experiment comparison, drill-down
into individual trades. Consumes Go API only (§3.4).

## 15. Database Design

Initial PostgreSQL tables:

```text
assets
prices_metadata
strategies
strategy_configs
backtests
backtest_trades
experiments
portfolios
positions
orders
fills
signals
market_regimes
risk_reports
journal_entries
```

Migrations from the start. Python objects and Go structs are not tightly
coupled to the SQL schema — domain models + repository interfaces sit
between them (decouples §5 domain model from persistence detail; exact
schema/migration tool choice is a Stage 3 planning decision).

## 16. Repository Structure

```text
ai-quant-terminal/
|
+-- apps/
|   +-- web/                    # Next.js / TypeScript
|   +-- api/                    # Go backend
|
+-- services/
|   +-- quant/                  # Python quant engine
|   +-- mcp/                    # MCP server / AI integration
|   +-- execution-cpp/          # C++ performance-sensitive engine
|
+-- packages/
|   +-- contracts/              # Shared API/schema definitions
|
+-- infra/
|   +-- docker/
|   +-- compose/
|
+-- db/
|   +-- migrations/
|
+-- notebooks/
|
+-- docs/
|   +-- architecture/
|   +-- research/
|   +-- decisions/
|
+-- tests/
|   +-- integration/
|   +-- end_to_end/
|
+-- scripts/
|
+-- .sdlc/                      # this SDLC documentation
|
+-- README.md
```

A simpler structure is acceptable for the earliest milestones; ownership
clarity matters more than folder count. Exact repo layout is finalized in
Stage 3 (Planning) as part of Milestone 1.

## 17. Deployment Design (MVP)

Fully local, single Docker Compose stack (resolved Q2):

```text
docker-compose.yml
  - web       (Next.js)
  - api       (Go)
  - quant-mcp (single Python service: quant engine + MCP server,
               logically separated modules, one container)
  - db        (PostgreSQL)
  - ollama    (local LLM runtime — OPTIONAL profile, not required to bring
               the stack up; amended 2026-09-09, see §3.6)
  - execution-cpp (built as a binary/shared lib, not a standalone network service)
```

No cloud provider is hosted for this application, no self-hosted public
ingress, in MVP scope. The one deliberate exception: `quant-mcp` may call
the external **Gemini API** (free tier, default LLM provider) over HTTPS —
this is an outbound call to a third-party API, not application hosting, and
is optional at runtime (the user can run `ollama` locally instead and
disable the Gemini call entirely). The Go `api` service calls into
`quant-mcp` via a defined interface (exact transport — HTTP, gRPC, or
subprocess — is a Stage 3 decision, resolved as HTTP/JSON); per the
software handoff's guidance, no network service is created merely for
appearance.

## 18. Reliability Rules (carried from requirements, binding on design)

1. Never silently use incomplete market data.
2. Never hide transaction-cost assumptions.
3. Never mix future information into historical simulations.
4. Never let an LLM invent quantitative outputs.
5. Never allow paper/live execution to bypass risk checks.
6. Prefer failing loudly over returning a plausible-but-incorrect result.

## 19. Open Questions — Resolved

1. **Python quant engine vs. MCP server deployment:** for the MVP, they run
   as a **single Python service/container**. They stay logically separated
   at the module/interface level (`services/quant/` and `services/mcp/`
   remain distinct packages with a clean boundary between them — the MCP
   layer calls the quant engine as a library, not over the network), but
   are not deployed as independent services unless independent scaling or
   isolation is later needed. §17's Docker Compose stack is updated
   accordingly: the `mcp` service embeds/imports the `quant` package rather
   than the two running as separate containers.

---

**Status: Approved (2026-09-09).** Proceeding to Stage 3 — Planning.

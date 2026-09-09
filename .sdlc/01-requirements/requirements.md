# Stage 1 — Requirements

> Status: **Approved (2026-09-09)**
> Derived from: `ai_quant_product_manager.md`, `ai_quant_software_manager.md`

---

## 1. Problem Statement

An individual trader / market researcher currently has to jump between multiple
disconnected tools to get historical data, compute indicators, test strategies,
backtest, analyze portfolio risk, and journal trades. This project builds a
single-user AI Quant Terminal that unifies these workflows behind a
conversational, AI-orchestrated interface, while keeping every analysis
deterministic, reproducible, and inspectable.

**Product thesis:** AI operates the quantitative system; it does not invent
quantitative results.

## 2. Primary User

A technically comfortable individual trader/researcher who values trust
(traceable numbers), speed, exploration, learning, and control (no autonomous
real-money actions in the MVP). Single-user product — no multi-tenant auth,
no enterprise billing, no institutional infra.

## 3. In-Scope Capabilities (MVP)

### Functional requirements

- **FR1 — Market data:** retrieve, validate, and normalize historical OHLCV
  data for a small liquid universe (NIFTY, BANKNIFTY, 5–10 large-cap
  equities); detect gaps/duplicates; retain data provenance (provider,
  symbol, interval, timezone, retrieval timestamp, adjustment policy).
- **FR2 — Indicators:** compute moving averages, MACD, Bollinger Bands, RSI,
  ATR, z-score, rolling volatility, correlation — independent of strategies.
- **FR3 — Strategies:** implement four reusable strategy families behind a
  common contract (HTTP/DB/MCP/LLM-agnostic): MACD/momentum,
  Bollinger/mean-reversion, Dual Thrust/breakout, Pair Trading/stat-arb.
- **FR4 — Backtesting:** simulate the trading lifecycle (signal → position
  sizing → order intent → execution simulation → portfolio state → PnL)
  with commission, slippage, spread, cash constraints, and explicit
  long/short rules. Must prevent look-ahead bias (time alignment tested
  explicitly).
- **FR5 — Experiments:** persist every backtest as a reproducible unit
  (strategy, parameters, universe, timeframe, cost model, data version,
  code version, results, timestamp). Must be rerunnable without manual
  reconfiguration.
- **FR6 — Strategy comparison:** run/compare multiple strategies on the same
  instrument/date range and present CAGR, Sharpe, Sortino, max drawdown,
  win rate, profit factor, trade count, turnover, exposure, equity curve,
  drawdown curve, rolling performance.
- **FR7 — Portfolio & risk:** allocation, concentration, correlation, beta,
  volatility, drawdown, Sharpe/Sortino, sector exposure (where data
  permits), and "what-if" scenario analysis (reweight → recalc → compare).
- **FR8 — Trade journal:** record paper/live trades and analyze performance
  by strategy, regime, asset, holding period, entry/exit reason.
- **FR9 — Conversational research:** natural-language research workspace
  where the AI plans, selects MCP tools, assembles structured results, and
  explains them — never fabricating missing data.
- **FR10 — MCP surface:** expose domain-level actions (`get_market_data`,
  `calculate_indicators`, `run_backtest`, `compare_strategies`,
  `get_portfolio`, `analyze_portfolio_risk`, `find_pairs`,
  `run_monte_carlo`, `detect_market_regime`, `get_experiment`) — never raw
  DB/SQL/arbitrary-code tools.

### Non-functional requirements

- **NFR1 — Correctness first:** priority order is correctness →
  reproducibility → clear interfaces → performance → autonomy. No
  HFT-latency optimization in scope.
- **NFR2 — Traceability:** every meaningful research request carries a
  traceable request/experiment ID; latency measured separately per stage
  (data retrieval, quant calc, C++ sim, DB persistence, AI orchestration).
- **NFR3 — Testability:** unit tests at every language boundary (Python,
  C++, Go, frontend E2E) plus golden/regression tests for known
  strategy/dataset pairs.
- **NFR4 — Explainability:** every result must let the user inspect data
  period, instrument/universe, strategy, parameters, cost assumptions,
  backtest config, and calculations used.
- **NFR5 — Reliability rules (hard constraints):**
  1. Never silently use incomplete market data.
  2. Never hide transaction-cost assumptions.
  3. Never mix future information into historical simulations.
  4. Never let an LLM invent quantitative outputs.
  5. Never allow paper/live execution to bypass risk checks.
  6. Prefer failing loudly over returning a plausible-but-incorrect result.
- **NFR6 — Reproducibility:** a saved experiment must be rerunnable and
  produce the same result given the same data version.
- **NFR7 — Single-user scale:** design clean interfaces that *could* support
  multi-user/enterprise later, but do not build for that scale now.

## 4. Explicitly Out of Scope (MVP)

- Autonomous real-money trading / order execution.
- High-frequency trading infrastructure.
- Advanced deep-learning price forecasting.
- Complex broker routing / institutional exchange connectivity.
- Multi-user access control, enterprise billing.
- Paper trading itself (Phase/Milestone 9 — foundation only after the
  research system is stable; not part of this requirements pass).

## 5. Acceptance Criteria (Definition of Done for MVP)

The MVP is complete only when, end-to-end, a user can:

1. Open the Next.js application.
2. Select an instrument and date range.
3. Inspect market data and indicators.
4. Run at least four strategy types as a backtest, including transaction
   cost/slippage assumptions.
5. See both return and risk metrics for the backtest.
6. Compare multiple strategies reproducibly.
7. Inspect portfolio risk computed from holdings.
8. Save an experiment and rerun it later with identical results.
9. Ask the AI a natural-language research question.
10. Watch the AI call MCP tools and receive a traceable, evidence-backed
    answer (numerical claims map back to tool outputs).
11. Inspect the assumptions behind any result.
12. Do all of the above without the system ever placing a real-money order.

## 6. Technology Constraints (given, not negotiable for this project)

| Layer | Technology | Rationale |
|---|---|---|
| Quant research / ML | Python | pandas/numpy/scipy/sklearn ecosystem |
| Backend API / orchestration | Go | matches target role, concurrency, clean services |
| Performance-sensitive backtest/execution sim | C++ | systems-programming depth, benchmarked vs. Python |
| Web UI | Next.js / TypeScript | matches JD, professional research UI |
| Database | PostgreSQL | reliable relational persistence |
| Cache/job coordination | Redis (optional MVP) | only once repeated-query/background-job load appears |
| MCP server | Python initially (Go gateway optional later) | keeps AI boundary close to research engine |
| Containerization | Docker | reproducible local setup |
| Testing | go test, pytest, C++ test framework, Playwright | tests at every boundary |

Rule: technology choice must be justified by engineering fit per component —
not used to inflate stack diversity. The C++ component in particular requires
a benchmark demonstrating measurable benefit before/alongside its
introduction.

## 7. Open Questions — Resolved

1. **Data provider(s):** **yfinance**. Free, sufficient for the prototype.
   Used for NIFTY, BANKNIFTY, and NSE large-cap equities. The data layer
   must account for yfinance's rate limits, occasional gaps, and lack of
   an SLA — validation/gap-detection (FR1) is not optional.
2. **Deployment target:** **fully local via Docker Compose**. No cloud
   hosting in the MVP.
3. **LLM provider:** **Gemini API free tier** as the default, with
   **Ollama as an optional local fallback** (amended 2026-09-09 — see
   §7.1 below). The AI orchestrator interface must stay pluggable behind
   the MCP boundary so the backing model can be swapped without changing
   MCP tool contracts or application code.

### 7.1 Amendment (2026-09-09) — LLM provider changed to Gemini-default / Ollama-fallback

Originally resolved as Ollama-only. Revised after review: the Gemini
Developer API has a genuine free tier (free input/output tokens on
eligible models, no billing required to use it) with published rate
limits (requests/minute, tokens/minute, requests/day) that Google can
change over time, and some features (e.g. certain grounding capabilities)
restricted on the free tier. Because those limits are real but modest and
not guaranteed permanent, the provider is now dual:

```text
LLM Provider
     |
     +-- Gemini (free tier) <- DEFAULT
     |
     +-- Ollama (local)     <- OPTIONAL FALLBACK
```

The project still works with zero cost and no cloud dependency at all
(Ollama-only) if the user prefers, or hits Gemini's free-tier limits.
This changes NFR7-adjacent deployment posture: Gemini is an **optional**
external network dependency, not a mandatory one — see
`.sdlc/06-deployment/deployment.md` §2/§3 for the resulting environment
and secrets handling. The "fully local, no cloud hosting" constraint
(§7 item 2 above) is about *where the application runs*, not about
whether it may optionally call an external LLM API; it does not need to
be reopened.

---

**Status: Approved (2026-09-09); amended (2026-09-09) for §7 item 3.**
Proceeding to Stage 2 — Architecture & Design.

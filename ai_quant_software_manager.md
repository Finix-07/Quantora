# AI Quant Terminal — Software Manager / Engineering Handoff

> **Audience:** Software engineering intern  
> **Purpose:** Define the system architecture, implementation responsibilities, technical constraints, and engineering milestones for a single-user AI quant research product.

---

## 1. Engineering Goal

Build a modular, testable, single-user quantitative research platform that can:

1. retrieve and validate market data,
2. calculate indicators and quantitative features,
3. run reusable trading strategies,
4. perform realistic backtests,
5. analyze portfolio risk,
6. save reproducible experiments,
7. expose core capabilities through MCP,
8. let an LLM orchestrate multi-step research,
9. and later support paper trading without rewriting the quant core.

The engineering priority is:

> **Correctness → reproducibility → clear interfaces → performance → autonomy.**

Do not optimize for enterprise scale or HFT latency during the internship.

---

## 2. Technology Strategy

The attached internship JD emphasizes **Golang, C++, Next.js**, strong CS fundamentals, and work involving performant/reliable trading infrastructure.

Do **not** force every subsystem into those languages. The project should deliberately use each technology where it makes engineering sense.

| Component | Recommended technology | Why |
|---|---|---|
| Quant research, statistics, ML | **Python** | Best ecosystem for pandas/numpy/scipy/sklearn and fast research iteration |
| Core backend APIs / orchestration | **Go** | Matches the role, strong concurrency, clean services, production-oriented backend development |
| Performance-sensitive backtesting / execution simulation | **C++** | Demonstrates systems programming, memory/performance awareness, and trading-infrastructure fundamentals |
| Web application | **Next.js / TypeScript** | Directly matches the JD and provides a professional trading/research UI |
| Database | **PostgreSQL** | Reliable relational persistence for experiments, portfolios, trades and metadata |
| Cache / job coordination | **Redis** (optional for MVP) | Useful once repeated data queries and background jobs appear |
| MCP server | **Python initially**, optionally a Go gateway | MCP sits at the AI/tool boundary; Python keeps integration close to the research engine while Go can own production-style service boundaries |
| Containerization | **Docker** | Reproducible local setup across services |
| Testing | Go test + pytest + C++ tests + Playwright | Tests should exist at every boundary |

### Important rule

The goal is not to claim that the entire product was written in Go/C++.

The goal is to show that the intern understands **where Go and C++ add value** and can work across the exact technologies expected by the company while still using the standard ecosystem for quantitative research and ML.

---

## 3. High-Level Architecture

```text
                         USER / AI CLIENT
                               |
                 +-------------+-------------+
                 |                           |
                 v                           v
          Next.js Web UI                 MCP Client
                 |                           |
                 +-------------+-------------+
                               |
                               v
                        Go Application API
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
          Python Quant Engine          C++ Engine
                |                             |
      +---------+---------+            +------+------+
      |         |         |            |             |
      v         v         v            v             v
   Data     Indicators  ML/Stats   Backtester   Execution Sim
      |         |         |            |             |
      +---------+---------+------------+-------------+
                               |
                               v
                         PostgreSQL / Cache
```

The exact service split can remain small in the first version. Avoid creating many network services merely for appearance.

A sensible initial deployment can still be:

```text
Next.js
Go API
Python quant service/library
C++ backtest library/module
PostgreSQL
MCP server
```

running locally through Docker Compose.

---

## 4. Responsibilities by Language

### Python — Quantitative Computing

Own:

- indicators,
- statistical calculations,
- research notebooks,
- strategy prototypes,
- portfolio mathematics,
- Monte Carlo,
- regime detection,
- ML experimentation.

Python should be the **researcher's language**, not the language for everything.

### Go — Application / Trading-System Backend

Own:

- REST/HTTP API,
- application orchestration,
- experiment lifecycle,
- portfolio service,
- authentication boundary if needed later,
- background job coordination,
- structured logging,
- service health and configuration.

The Go layer should expose clean interfaces and translate between API/domain objects and the quantitative engine.

### C++ — Performance-Sensitive Quant Infrastructure

Use C++ selectively for components where its strengths are meaningful:

- execution simulation,
- event-driven backtest loop,
- large-scale strategy simulation,
- low-overhead order/position processing.

The first C++ component should have a measurable reason to exist. Include a small benchmark comparing an equivalent implementation rather than inserting C++ arbitrarily.

### Next.js / TypeScript — User Interface

Build:

- research workspace,
- charts,
- strategy lab,
- portfolio dashboard,
- risk views,
- experiment history,
- trade journal.

The UI should consume Go APIs rather than directly accessing the database or Python internals.

---

## 5. Suggested Repository Structure

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
+-- README.md
```

A simpler structure is acceptable early on. The goal is clear ownership, not folder count.

---

## 6. Domain Model

Core entities:

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

Keep strategies independent of HTTP, databases, MCP and LLMs.

Conceptually:

```python
class Strategy:
    def generate_signals(self, data): ...
    def position_size(self, context): ...
    def exit_signal(self, context): ...
```

The exact interfaces can change, but the principle should not:

> **A strategy should be executable from Python, from tests, from a backtest, and through an API without knowing who called it.**

---

## 7. Data Layer

### Responsibilities

- retrieve historical data,
- normalize symbols,
- normalize timezones,
- validate OHLCV records,
- detect gaps/duplicates,
- cache repeated requests,
- expose stable domain-level objects.

### Minimal interface

```python
get_prices(symbol, start, end, interval)
get_universe(name)
```

### Validation examples

```text
low <= open <= high
low <= close <= high
volume >= 0
timestamps are monotonic
timestamps are unique
```

Every dataset should retain metadata such as:

```text
provider
symbol
interval
timezone
retrieval timestamp
adjustment policy
```

A research result without data provenance is not considered complete.

---

## 8. Quant Layer

Start with four strategy families:

1. MACD / momentum
2. Bollinger Bands / mean reversion
3. Dual Thrust / breakout
4. Pair Trading / statistical arbitrage

The goal is to extract reusable patterns rather than copy individual scripts.

### Indicators

Start with:

```text
moving averages
MACD
Bollinger Bands
RSI
ATR
z-score
rolling volatility
correlation
```

Keep indicators independent from strategies.

Example:

```text
indicators/
    moving_average.py
    macd.py
    bollinger.py
    rsi.py
    atr.py
    zscore.py
```

---

## 9. Backtesting Engine

A backtest should simulate a simplified trading lifecycle:

```text
Historical Data
       |
       v
Signal Generation
       |
       v
Position Sizing
       |
       v
Order Intent
       |
       v
Execution Simulator
       |
       v
Portfolio State
       |
       v
PnL / Metrics
```

### Minimum execution assumptions

- commission,
- slippage,
- spread,
- cash constraints,
- position sizing,
- supported long/short rules,
- order timestamps.

### Critical correctness requirement

Prevent look-ahead bias.

For example, information from a daily close must not be used to simulate an earlier fill on the same bar unless the execution model explicitly says that it is possible.

Make time alignment explicit and test it.

---

## 10. C++ Backtesting Component

The C++ component should be introduced after the Python version is correct.

### Recommended implementation

Build an event-driven simulator that receives compact events such as:

```text
MarketEvent
OrderEvent
FillEvent
PositionEvent
```

and updates:

```text
cash
positions
orders
fills
realized PnL
unrealized PnL
```

Expose it to Python using a thin binding layer only after the C++ API is stable.

### Why this matters for the internship

This creates a concrete discussion around:

- memory layout,
- object allocation,
- event processing,
- deterministic simulation,
- benchmarking,
- interoperability between C++ and Python.

### Benchmark requirement

Demonstrate the workload where C++ provides a measurable benefit. Do not claim performance improvements without a benchmark.

---

## 11. Backtest Result Contract

Every completed run should return a structured object similar to:

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

The precise schema can evolve, but the result must be serializable, persistable, and reproducible.

---

## 12. Experiment System

An experiment is the reproducible unit of research.

Store:

```text
strategy
strategy parameters
instrument/universe
timeframe
start/end
cost model
data version
code/version identifier
results
created_at
```

### Requirement

A saved experiment must be rerunnable without manually remembering how it was configured.

This is important for both scientific validity and product usability.

---

## 13. Portfolio & Risk Engine

Minimum calculations:

```text
allocation
position concentration
portfolio return
volatility
beta
correlation
maximum drawdown
Sharpe
Sortino
sector exposure where data permits
```

Add scenario analysis:

```text
current portfolio
        |
modify one or more weights
        |
recalculate risk
        |
compare before/after
```

Later add:

- Monte Carlo,
- VaR/CVaR,
- factor exposure,
- regime-conditioned risk.

---

## 14. MCP Layer

The MCP surface should expose **domain actions**, not database operations.

Good:

```text
get_market_data
calculate_indicators
run_backtest
compare_strategies
get_portfolio
analyze_portfolio_risk
find_pairs
run_monte_carlo
detect_market_regime
get_experiment
```

Bad:

```text
run_sql
get_database_row
execute_arbitrary_python
```

The AI should never need to know how the database is structured.

### Example MCP workflow

User asks:

> “Compare MACD and Bollinger strategies for RELIANCE over five years, including transaction costs.”

The AI should be able to compose:

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

---

## 15. AI Agent Architecture

The LLM is a planner/orchestrator.

```text
User Query
    |
    v
Intent / Plan
    |
    v
Tool Selection
    |
    v
MCP Tool Calls
    |
    v
Structured Results
    |
    v
Validation / Context Assembly
    |
    v
LLM Explanation
```

### Guardrail

Never allow the LLM to fabricate missing data.

If a required tool result is missing, the agent should say that the evidence is insufficient or request another tool call.

### Tool output discipline

Return structured data alongside concise natural-language descriptions.

Example:

```json
{
  "metric": "max_drawdown",
  "value": -0.214,
  "period": "2020-2025",
  "source": "backtest:exp_123"
}
```

This makes the AI layer easier to evaluate.

---

## 16. Go Backend API

The Go backend should own the application-facing API.

Example endpoints:

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

Do not let handlers contain quantitative formulas. They should call domain services.

Conceptually:

```text
HTTP Handler
     |
     v
Application Service
     |
     v
Quant / Portfolio Interface
     |
     v
Python or C++ implementation
```

---

## 17. Next.js Frontend

Recommended application structure:

```text
app/
  research/
  market/[symbol]/
  strategies/
  portfolio/
  experiments/
  journal/
```

Important UI requirements:

- responsive layout,
- interactive charts,
- loading/error states,
- explicit backtest assumptions,
- experiment comparison,
- easy drill-down into individual trades.

The frontend should feel closer to a **professional trading/research workstation** than a marketing website.

---

## 18. Database Design

PostgreSQL tables can start with:

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

Use migrations from the beginning.

Do not tightly couple Python objects or Go structs directly to SQL schema; use domain models and repository interfaces.

---

## 19. Testing Strategy

### Python

Unit-test:

- indicator calculations,
- signal generation,
- portfolio mathematics,
- statistical calculations.

### C++

Unit-test:

- event ordering,
- order execution,
- position updates,
- PnL calculations.

### Go

Test:

- API handlers,
- application services,
- validation,
- repository behavior.

### Frontend

Use end-to-end tests for critical flows:

```text
open strategy lab
     |
select strategy
     |
run backtest
     |
view result
     |
save experiment
```

### Golden/regression tests

Maintain known datasets with known expected results for a few strategies. If a future change alters an important result, the test should make that obvious.

---

## 20. Observability

Every meaningful research request should have a traceable ID.

Example:

```text
request_id
experiment_id
tool_calls
data source
strategy
execution time
result status
```

Useful logs:

```text
INFO  backtest started
INFO  data loaded
INFO  strategy completed
INFO  execution simulation completed
INFO  metrics calculated
INFO  experiment persisted
```

Measure latency separately for:

- data retrieval,
- quant calculation,
- C++ simulation,
- database persistence,
- AI/tool orchestration.

This creates strong material for discussing performance in an interview.

---

## 21. Reliability Rules

1. Never silently use incomplete market data.
2. Never hide transaction-cost assumptions.
3. Never mix future information into historical simulations.
4. Never let an LLM invent quantitative outputs.
5. Never allow paper/live execution to bypass risk checks.
6. Prefer failing loudly over returning a plausible but incorrect result.

---

## 22. Development Milestones

### Milestone 1 — Repository and environment

Deliver:

- monorepo structure,
- Docker Compose,
- PostgreSQL,
- Next.js skeleton,
- Go API skeleton,
- Python quant module,
- testing setup,
- CI checks.

### Milestone 2 — Data + one strategy

Deliver:

```text
data -> MACD -> signal -> backtest -> metrics
```

Everything must be testable.

### Milestone 3 — Generalized strategy framework

Add:

- Bollinger,
- Dual Thrust,
- Pair Trading.

Add a common strategy interface and experiment persistence.

### Milestone 4 — C++ execution engine

Implement event-driven execution simulation and benchmark it against the Python implementation.

### Milestone 5 — Portfolio / risk

Add holdings, portfolio metrics, scenario analysis and saved risk reports.

### Milestone 6 — MCP

Expose stable domain tools and resources.

### Milestone 7 — AI researcher

Build a multi-step tool-using workflow:

```text
question -> plan -> tools -> results -> explanation
```

### Milestone 8 — Product integration

Connect the Next.js UI to the Go API and provide a complete demo workflow.

### Milestone 9 — Paper trading foundation

Only after the research system is reliable:

```text
signal
  -> risk validation
  -> paper order
  -> simulated fill
  -> portfolio update
  -> journal
```

---

## 23. Definition of Done

The internship project should be considered complete when a new user can:

1. open the Next.js application,
2. select an instrument,
3. inspect market data and indicators,
4. run a strategy backtest,
5. see realistic execution assumptions,
6. compare multiple strategies,
7. inspect portfolio risk,
8. save an experiment,
9. ask the AI a research question,
10. watch the AI call the MCP tools,
11. receive a traceable answer,
12. rerun the same experiment and obtain reproducible results.

---

## 24. Reference Material / Technical Starting Point

### Primary strategy/reference repository

**GitHub — `je-suis-tm/quant-trading`**  
https://github.com/je-suis-tm/quant-trading

Use it as a **research and implementation reference**, not as the architecture of the final product.

The repository contains examples covering:

- MACD oscillator,
- pair trading / statistical arbitrage,
- Heikin-Ashi,
- London Breakout,
- Awesome Oscillator,
- Oil Money / quantamental research,
- Dual Thrust,
- Bollinger Bands pattern recognition,
- options straddle,
- Monte Carlo,
- portfolio optimization,
- and other technical-analysis strategies.

The repository describes these as historical backtests/forward tests and explicitly notes assumptions such as frictionless trading, with no slippage, surcharge, or illiquidity. That is useful context: **our implementation should retain the strategy ideas while building a more realistic and reusable research/backtesting framework.**

### Specific starting examples

Use these as initial references:

```text
MACD Oscillator backtest.py
Bollinger Bands Pattern Recognition backtest.py
Dual Thrust backtest.py
Pair trading backtest.py
Options Straddle backtest.py
```

Do not copy the scripts into the production architecture unchanged. Extract the underlying algorithm, write tests, define a common strategy contract, and integrate it with the new backtesting engine.

### Engineering references

The intern should also study the standard documentation for the selected stack:

- Go documentation and testing tools
- C++ standard library and benchmarking/profiling tools
- Next.js / TypeScript documentation
- PostgreSQL documentation
- Python scientific stack documentation
- MCP specification/SDK documentation appropriate to the chosen implementation

Use official documentation for implementation decisions whenever possible.

---

## 25. Final Interview Demo

The strongest final demo is one connected scenario:

```text
User:
"Analyze my portfolio and tell me what is driving risk."

        ↓

AI chooses tools

        ↓

Portfolio + market + risk calculations

        ↓

AI identifies concentration / correlation risk

        ↓

User:
"Find alternative strategies that worked better in this regime."

        ↓

Regime + strategy backtests

        ↓

C++ execution simulation + realistic costs

        ↓

User:
"Would reallocating 10% improve the portfolio?"

        ↓

Scenario analysis

        ↓

Experiment saved

        ↓

User can inspect every assumption and rerun it
```

This demonstrates the exact combination that should stand out in a trading-platform interview:

**Go backend + C++ systems work + Next.js product engineering + Python quantitative research + databases + networking/API design + AI/MCP integration + testing and performance awareness.**

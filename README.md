# AI Quant Terminal

A single-user, fully local research terminal for quantitative trading
research: market data → indicators → strategies → backtests → portfolio risk
→ reproducible experiments, with an AI researcher that *operates* the
quantitative system rather than inventing quantitative results.

> **Product thesis:** AI operates the quantitative system; it does not invent
> quantitative results. Every number a user sees traces back to a real
> calculation over real, validated data.

## What this is (and is not)

- **Is:** a local research workbench. Historical data, deterministic
  backtests, portfolio/risk analysis, saved-and-rerunnable experiments, and a
  natural-language research interface backed by MCP tools.
- **Is not:** an execution venue. Nothing here places a real-money order.
  Paper trading is post-MVP (M9) and not implemented.

## Repository layout

```text
apps/
  web/                  Next.js / TypeScript UI (consumes the Go API only)
  api/                  Go application API (HTTP, orchestration, persistence)
services/
  quant/                Python quant engine (data, indicators, strategies,
                        backtest, portfolio math)
  mcp/                  MCP server exposing domain tools to the AI layer
  execution-cpp/        C++ event-driven execution simulator (M4)
packages/
  contracts/            Shared API/schema definitions
infra/
  docker/               Dockerfiles
  compose/              docker-compose.yml
db/
  migrations/           golang-migrate SQL migrations
notebooks/              Exploratory research notebooks
docs/
  architecture/         Architecture notes
  research/             Research write-ups, benchmarks
  decisions/            Decision log / engineering memory
tests/
  integration/          Cross-boundary tests
  end_to_end/           Playwright E2E
  golden/               Golden/regression fixtures
scripts/                Developer scripts
.sdlc/                  Approved SDLC documents (source of truth)
```

## Architecture at a glance

```text
Next.js UI ──▶ Go API ──▶ Python quant-mcp ──▶ yfinance / PostgreSQL
                  │              │
                  │              └──▶ C++ execution engine (binding)
                  ▼
             PostgreSQL
```

The Go API never contains quantitative formulas; the UI never touches
PostgreSQL or Python directly; the AI layer reaches the quant engine only
through MCP domain tools (never raw SQL or arbitrary code execution).

## Getting started

```bash
cp .env.example .env      # then fill in GEMINI_API_KEY (or set LLM_PROVIDER=ollama)
docker compose -f infra/compose/docker-compose.yml up --build
make migrate              # apply database migrations (explicit, never automatic)
```

Services:

| Service     | URL                     | Health check |
|-------------|-------------------------|--------------|
| `web`       | http://localhost:3000   | `/`          |
| `api`       | http://localhost:8080   | `/healthz`   |
| `quant-mcp` | http://localhost:8000   | `/healthz`   |
| `db`        | localhost:5432          | `pg_isready` |
| `ollama`    | http://localhost:11434  | optional `local-llm` profile |

`ollama` is **not** started by a plain `docker compose up`. Use
`docker compose --profile local-llm up` and set `LLM_PROVIDER=ollama` if you
want a zero-external-dependency setup.

## Development

See [`docs/implementation-memory.md`](docs/implementation-memory.md) for the
engineering memory (known-good commands, resolved failures, environment
quirks) and `.sdlc/` for the approved requirements, architecture, plan,
testing strategy, deployment model, and operations runbook.

## Reliability rules (binding)

1. Never silently use incomplete market data.
2. Never hide transaction-cost assumptions.
3. Never mix future information into historical simulations.
4. Never let an LLM invent quantitative outputs.
5. Never allow paper/live execution to bypass risk checks.
6. Prefer failing loudly over returning a plausible-but-incorrect result.

# Stage 7 — Maintenance & Operations

> Status: **Approved (2026-09-09)**
> Derived from: `ai_quant_software_manager.md` (§20 Observability, §21 Reliability Rules),
> `.sdlc/01-requirements/requirements.md` (NFR2, NFR5),
> `.sdlc/02-architecture-design/architecture.md` (§17 Deployment, §18 Reliability Rules),
> `.sdlc/06-deployment/deployment.md`

---

## 1. Scope

This is the final SDLC stage before Stage 4 (Implementation) planning
begins. It defines how the running system is observed, kept healthy, and
evolved once milestones are live — for a **single-user, fully local**
product (no on-call rotation, no SRE team, no SLA to external customers).

## 2. Observability

### 2.1 Traceability

Every meaningful research request carries a traceable ID chain, per
`architecture.md` §12/§20 intent:

```text
request_id
experiment_id
tool_calls
data_source
strategy
execution_time
result_status
```

### 2.2 Structured logging

Minimum log events, emitted by the relevant service (Go `api` or Python
`quant-mcp`):

```text
INFO  backtest started
INFO  data loaded
INFO  strategy completed
INFO  execution simulation completed
INFO  metrics calculated
INFO  experiment persisted
```

Plus, given the Stage 2/6 LLM amendment:

```text
INFO  llm request started {provider: gemini|ollama}
WARN  llm request failed {provider, reason}
INFO  llm tool_call issued {tool_name}
```

### 2.3 Latency measurement

Measured and logged separately per stage, not as one aggregate number:

```text
data retrieval
quant calculation
C++ simulation (once M4 lands)
database persistence
AI/tool orchestration (including which LLM provider served the request)
```

This is deliberately kept as structured logs (e.g., JSON lines to stdout,
captured by `docker compose logs`), not a paid observability SaaS —
consistent with `deployment.md` §8 ("no monitoring/alerting SaaS
integrations" for MVP).

## 3. Health Checks

Each Compose service exposes a basic health endpoint/check (introduced at
M1 per `planning.md`):
- `api`: `GET /healthz` (DB reachable, `quant-mcp` reachable)
- `quant-mcp`: `GET /healthz` (DB reachable; if `LLM_PROVIDER=gemini`,
  does **not** call the external API on every health check — checking API
  key presence is enough, to avoid burning free-tier quota on health
  pings)
- `web`: Next.js default health/readiness behavior
- `db`: standard `pg_isready`

## 4. Operational Runbook (single-user local)

| Situation | Response |
|---|---|
| A service won't start | `docker compose logs <service>`; check `.env` for missing/invalid values (esp. `GEMINI_API_KEY` if `LLM_PROVIDER=gemini`, per NFR5.6 fail-loudly rule) |
| yfinance data looks wrong or a fetch fails | Do not silently substitute cached/stale data (NFR5.1); surface the validation failure to the user; retry manually once the underlying issue (rate limit, symbol change) is understood |
| Gemini free-tier quota exhausted / `GEMINI_API_KEY` issue | `GEMINI_API_KEY`/quota issue → set `LLM_PROVIDER=ollama` in `.env` → `docker compose --profile local-llm up` to start `ollama` → restart `quant-mcp`. No code changes required (per the `LLMClient` abstraction). **Confirmed: Gemini is the default LLM provider; Ollama is an optional local fallback activated only through the `local-llm` Compose profile. The system must not require Ollama to run the standard MVP stack** — a plain `docker compose up` never starts it. |
| A backtest result looks wrong | Check the experiment's stored `data_version`, parameters, and cost model (§9 backtest result contract, `architecture.md`); rerun the experiment to check reproducibility (NFR6) before assuming a bug |
| Migration fails on startup | Do not force/skip it; `golang-migrate` failures block startup deliberately — inspect `db/migrations/` and the Postgres logs |
| Local Postgres data needs preserving before a risky change | Snapshot the Docker volume before rebuilding, per `deployment.md` §6 rollback guidance |

## 5. Change Management / Evolving the System

- Every change to a component that affects a completed milestone's
  behavior should re-run that milestone's test suite (Stage 5) before being
  considered done — the per-milestone gate from Stage 3 §4 applies
  retroactively to changes, not just first builds.
- Commit discipline from `planning.md` §6 applies here too: a fix or
  operational change is its own commit with an explanatory message.
- Golden/regression test failures (Stage 5 §3) are the primary signal that
  a change altered strategy/backtest behavior unintentionally — treat a
  failure there as blocking, not advisory.
- SDLC documents in `.sdlc/` are living documents: if a future engineering
  decision changes something already approved here (as happened with the
  LLM provider amendment on 2026-09-09), amend the relevant stage doc in
  place with a dated amendment note, rather than letting docs and reality
  drift apart.

## 6. Data Retention & Backup

- PostgreSQL is the system of record for experiments, portfolios, trades,
  and journal entries — all reproducibility guarantees (NFR6) depend on
  this data surviving. For a local single-user setup, back up the Docker
  volume periodically (manual, e.g. before major schema changes); no
  automated offsite backup service in MVP scope, consistent with
  `deployment.md` §8.
- Market data fetched via yfinance is not the system of record — it is
  re-fetchable, but each fetch's provenance metadata (§4.4,
  `architecture.md`) must be persisted alongside any experiment that used
  it, so a rerun can detect if source data has since changed.

## 7. Deprecation / Sunset (forward-looking, not MVP-blocking)

Not applicable to a single-user local MVP with no external users to
migrate. If the project is later extended (e.g., toward Milestone 9 paper
trading or beyond), this section should be revisited to define how
breaking changes to the experiment schema or strategy contract are
communicated — deferred until that becomes real, not designed speculatively
now.

## 8. Open Questions

None blocking.

---

**Status: Approved (2026-09-09). All 7 SDLC stages are now approved.**
Proceeding to Stage 4 — Implementation Planning: breaking the approved
milestone roadmap (`planning.md` §2) into sequential, dependency-ordered
implementation units, using parallel subagents where the dependency graph
in `planning.md` §3 allows it (e.g., M4/M5 after M3).

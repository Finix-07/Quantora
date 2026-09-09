# Stage 6 — Deployment

> Status: **Approved (2026-09-09), amended (2026-09-09)** — Gemini
> free tier is the default LLM provider; Ollama is an optional local
> fallback (Compose profile), not a mandatory service.
> Derived from: `ai_quant_software_manager.md` (§2 Technology Strategy, §3 Architecture),
> `.sdlc/02-architecture-design/architecture.md` (§17 Deployment Design),
> `.sdlc/01-requirements/requirements.md` (resolved Q2: fully local, no cloud)

---

## 1. Deployment Model

**Single-user, fully local deployment only.** No cloud hosting, no public
ingress, no multi-tenant infrastructure, for the entire MVP scope (M1–M8).
This is a confirmed, binding constraint from Stage 1, not a default that
Stage 4 implementation can silently expand.

## 2. Deployment Topology

One Docker Compose stack, brought up with a single `docker compose up`,
running entirely on the user's machine:

```text
docker-compose.yml
  - web         (Next.js, localhost:3000)
  - api         (Go, localhost:8080)
  - quant-mcp   (Python: quant engine + MCP server, one container,
                 logically separated modules — confirmed Stage 2 §19)
  - db          (PostgreSQL, localhost:5432)
  - ollama      (local LLM runtime, localhost:11434 — OPTIONAL Compose
                 profile, amended 2026-09-09; not started by a plain
                 `docker compose up`, only via
                 `docker compose --profile local-llm up`)
  - execution-cpp (built as a binary/shared lib linked into quant-mcp at
                    M4+, not a standalone container/network service)
```

`execution-cpp` is not its own Docker service — per `architecture.md` §7 it
is exposed to Python via a binding layer, so it ships as a compiled
artifact inside the `quant-mcp` image once M4 lands, not as network
infrastructure.

**LLM provider (amended 2026-09-09):** default is the **Gemini API free
tier**, called from `quant-mcp` over HTTPS — the one deliberate outbound
network dependency in an otherwise fully local stack (see
`architecture.md` §17 for why this doesn't reopen the "no cloud" deployment
constraint). `ollama` remains available as a same-stack, zero-cost, fully
local fallback: a user can run `docker compose --profile local-llm up` to
start it and set `LLM_PROVIDER=ollama` in `.env` to switch, with no code
changes, per the `LLMClient` abstraction in `architecture.md` §3.6.

## 3. Environment Configuration

- A single `.env` (or `.env.example` committed, `.env` gitignored) at the
  repo root holding: Postgres credentials, `LLM_PROVIDER` (`gemini` default
  or `ollama`), `GEMINI_API_KEY` (required only when `LLM_PROVIDER=gemini`),
  Ollama model name/endpoint (used only when `LLM_PROVIDER=ollama`),
  yfinance-related config if any (none required — yfinance needs no API
  key), and any per-service ports.
- **One real secret now exists: `GEMINI_API_KEY`** (amended 2026-09-09).
  It is a single free-tier API key for a single local user, so a dedicated
  secret-manager service remains unwarranted — it is kept in the gitignored
  `.env` like the rest of local config, never committed, and read only by
  `quant-mcp`. `.env.example` documents the variable name with a placeholder,
  not a real key. If `GEMINI_API_KEY` is unset and `LLM_PROVIDER=gemini`,
  `quant-mcp` must fail loudly at startup (NFR5.6), not silently fall back.
- Config is read the same way in every environment (local dev == local
  "prod" for this project) — there is no separate staging/production
  environment to keep in sync, since there is no deployment target beyond
  the user's machine.

## 4. Build & Release Process

- **No release pipeline to an external registry or hosting provider.**
  "Release" for this project means: the repo is in a state where
  `docker compose up` produces a working stack matching the latest approved
  milestone.
- CI (per Stage 5 §6) validates that each language layer's tests pass on
  every push. CI does **not** deploy anywhere — it is a correctness gate,
  not a deployment trigger, consistent with "no cloud target."
- Docker images are built locally on demand (`docker compose build`); there
  is no image registry to push to for the MVP.

## 5. Database Migrations in Deployment

- `golang-migrate` (confirmed, Stage 3) runs against the `db` service on
  stack startup or via an explicit `make migrate` / script step (exact
  trigger mechanism — startup hook vs. manual command — decided in Stage 4
  when `db/migrations/` is first populated).
- No production data migration concerns exist (single local user, no
  existing production dataset to migrate around).

## 6. Rollback / Recovery

- Because deployment is local-only with no shared state beyond the local
  Postgres volume, "rollback" means: `git checkout` a previous commit,
  `docker compose down && docker compose up --build`, and if needed,
  restore the Postgres volume from a local backup/snapshot (docker volume,
  not a managed backup service).
- No blue/green, canary, or zero-downtime requirements — this is explicitly
  out of scope per the "small-user product, not enterprise platform"
  principle (`ai_quant_product_manager.md` §4.4).

## 7. Local Development Workflow

```text
git clone (or already-local repo)
        |
docker compose up (brings up web, api, quant-mcp, db, ollama)
        |
golang-migrate applies db/migrations/
        |
developer iterates: edit code -> re-run relevant test suite (Stage 5)
        -> commit per completed unit with explanatory message
           (Stage 3 §6, Git Commit Discipline)
        |
docker compose build (when Dockerfiles change) -> docker compose up again
```

## 8. What Is Explicitly Out of Scope for Deployment (MVP)

- Cloud provider accounts/infra (AWS/GCP/Azure), Kubernetes, load
  balancers, autoscaling.
- CDN, public DNS, TLS/certificate management.
- Secret managers, vault services.
- Multi-environment promotion (dev → staging → prod).
- Zero-downtime deploys, blue/green, canary releases.
- Monitoring/alerting SaaS integrations (observability itself is covered
  structurally in Stage 7 — Maintenance & Operations — via logging, not via
  external paid tooling).

These may become relevant if the product is later extended beyond
single-user local use (e.g., before Milestone 9 paper trading were ever
turned into anything handling real money), but are not part of this
project's SDLC scope today.

## 9. Open Questions

None blocking. One implementation-level detail deferred to Stage 4 (not a
deployment-architecture decision): whether `golang-migrate` runs
automatically on `api` container startup or via an explicit script/Makefile
target.

---

**Status: Approved (2026-09-09), with the Gemini-default/Ollama-optional
amendment folded in.** Proceeding to Stage 7 — Maintenance & Operations,
the final SDLC stage before Stage 4 (Implementation) planning begins.

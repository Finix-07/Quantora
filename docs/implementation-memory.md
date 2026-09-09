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

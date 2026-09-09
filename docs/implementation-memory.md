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

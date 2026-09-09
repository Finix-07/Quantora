# AI Quant Terminal — developer entry points.
#
# Every target is safe to run from the repository root and assumes only Docker
# plus the local toolchains. `make help` lists what is available.

COMPOSE_FILE := infra/compose/docker-compose.yml
COMPOSE      := docker compose -f $(COMPOSE_FILE) --env-file .env
PY           := .venv/bin/python
GO           := go

.DEFAULT_GOAL := help
.PHONY: help up down logs ps build migrate migrate-down migrate-version db-shell \
        test test-go test-py test-e2e lint lint-go lint-py lint-web fmt venv

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- stack ----

up: ## Start the local stack (never starts ollama; see `up-llm`)
	$(COMPOSE) up -d --build

up-llm: ## Start the stack including the optional local Ollama runtime
	$(COMPOSE) --profile local-llm up -d --build

down: ## Stop the stack, keeping the database volume
	$(COMPOSE) down

logs: ## Follow logs for all services
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

build: ## Rebuild all images
	$(COMPOSE) build

# ------------------------------------------------------------ migrations ----

# Migrations are explicit, never automatic on service startup: a failing
# migration inside a container's boot sequence is an opaque crash loop, whereas
# `make migrate` prints the actual SQL error. The API still refuses to serve
# traffic until migrations have been applied (NFR5.6).
migrate: ## Apply all pending database migrations
	$(COMPOSE) --profile tools run --rm migrate up

migrate-down: ## Roll back the most recent migration
	$(COMPOSE) --profile tools run --rm migrate down 1

migrate-version: ## Print the current schema version
	$(COMPOSE) --profile tools run --rm migrate version

db-shell: ## Open psql against the local database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-quant} -d $${POSTGRES_DB:-quant}

# ----------------------------------------------------------------- tests ----

test: test-py test-go ## Run all non-E2E test suites

# TEST_DATABASE_URL comes from .env (host-facing DSN). Tests that need a real
# database skip cleanly when it is unset, so this target works with no stack up.
test-go: ## Run the Go test suite
	cd apps/api && $(GO) test ./...

test-py: ## Run the Python test suite (excluding tests that need the network)
	$(PY) -m pytest -m "not network" services tests

test-e2e: ## Run the Playwright end-to-end suite (requires a running stack)
	cd apps/web && npx playwright test

# ------------------------------------------------------------------ lint ----

lint: lint-py lint-go lint-web ## Lint every language

lint-go: ## Vet the Go module
	cd apps/api && $(GO) vet ./...

lint-py: ## Lint and format-check Python
	$(PY) -m ruff check services tests
	$(PY) -m ruff format --check services tests

lint-web: ## Lint and typecheck the web app
	cd apps/web && npm run lint && npm run typecheck

fmt: ## Auto-format Python and Go
	$(PY) -m ruff format services tests
	cd apps/api && $(GO) fmt ./...

# --------------------------------------------------------------- tooling ----

venv: ## Create the Python virtualenv and install dependencies
	uv venv --python 3.13
	uv pip install -e ".[dev]"

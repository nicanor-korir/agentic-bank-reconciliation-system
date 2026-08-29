# Phase 1 targets are live. Later targets are declared but fail with a clear
# message until their phase lands -- better than a confusing stack trace.
SHELL := /bin/bash
DC := docker compose
# The api container has no .git mount, and a run that cannot name its own
# commit cannot claim to be replayable -- so pass it in.
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_DIRTY := $(shell test -n "$$(git status --porcelain 2>/dev/null)" && echo true || echo false)
EXEC := $(DC) exec -T -e GIT_SHA=$(GIT_SHA) -e GIT_DIRTY=$(GIT_DIRTY) api

.PHONY: help up down logs ps seed index reset shell test lint typecheck check run resume continue eval eval-baseline replay demo

help:
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Bring the stack up and apply migrations
	@test -f .env || cp .env.example .env
	$(DC) up -d --build
	@echo "waiting for api..."
	@for i in $$(seq 1 60); do \
	  curl -sf http://localhost:8000/health >/dev/null && break || sleep 1; \
	done
	$(EXEC) recon migrate
	@echo "up. api http://localhost:8000  web http://localhost:5173"

down: ## Stop the stack (keeps volumes)
	$(DC) down

reset: ## Stop the stack and destroy all data
	$(DC) down -v

logs: ## Tail all logs
	$(DC) logs -f

ps: ## Show service status
	$(DC) ps

seed: ## Generate the seeded dataset, ingest it, and build the retrieval index
	$(EXEC) recon seed
	$(EXEC) recon index --rebuild
	$(EXEC) recon stats

index: ## Rebuild the Weaviate index from Postgres
	$(EXEC) recon index --rebuild

shell: ## psql into the database
	$(DC) exec postgres psql -U $${POSTGRES_USER:-recon} -d $${POSTGRES_DB:-recon}

test: ## Run the test suite
	$(EXEC) pytest -q

lint: ## ruff
	$(EXEC) ruff check src tests
	$(EXEC) ruff format --check src tests

typecheck: ## mypy
	$(EXEC) mypy src

check: lint typecheck test ## lint + typecheck + test

PERIOD ?= 2026-06

run: ## Execute a reconciliation run through the graph
	$(EXEC) recon run --period $(PERIOD)

resume: ## Resume a paused run: make resume RUN_ID=... [SIMULATE=1]
	$(EXEC) recon resume $(RUN_ID) $(if $(SIMULATE),--simulate-reviewer,)

continue: ## Resume a run whose process died: make continue RUN_ID=...
	$(EXEC) recon resume $(RUN_ID) --continue

eval: ## Score the golden set, print the table, write evals/report-<sha>.json
	$(EXEC) recon eval

eval-baseline: ## Score and record the result as the regression baseline
	$(EXEC) recon eval --set-baseline

replay: ## Replay a stored run and diff it: make replay RUN_ID=...
	$(EXEC) recon replay $(RUN_ID)

demo: ## Full seeded demo from a clean slate: reset, seed, eval, run
	@echo "==> clean slate"
	$(MAKE) reset
	$(MAKE) up
	@echo "\n==> seed the month and build the retrieval index"
	$(MAKE) seed
	@echo "\n==> the numbers that go in the deck"
	$(MAKE) eval
	@echo "\n==> reconcile the month"
	$(EXEC) recon run --period $(PERIOD)
	@echo "\n==> ready. Exception queue: http://localhost:5173"
	@echo "    Walk the script in DEMO.md."

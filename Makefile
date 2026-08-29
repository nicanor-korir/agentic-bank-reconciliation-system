# Phase 1 targets are live. Later targets are declared but fail with a clear
# message until their phase lands -- better than a confusing stack trace.
SHELL := /bin/bash
DC := docker compose
EXEC := $(DC) exec -T api

.PHONY: help up down logs ps seed reset shell test lint typecheck check run eval replay demo

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

seed: ## Generate the seeded dataset and ingest it
	$(EXEC) recon seed
	$(EXEC) recon stats

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

run: ## (Phase 4) Execute a reconciliation run
	@echo "not implemented until Phase 4"; exit 1

eval: ## (Phase 2) Score the golden set
	@echo "not implemented until Phase 2"; exit 1

replay: ## (Phase 6) Replay a stored run
	@echo "not implemented until Phase 6"; exit 1

demo: ## (Phase 6) Full seeded demo scenario
	@echo "not implemented until Phase 6"; exit 1

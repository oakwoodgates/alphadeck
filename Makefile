# Alpha Deck — dev/prod Docker Compose wrappers. Full model, ports table + gotchas: docs/DEV_PROD.md.
#
# Windows note: `make` is frequently NOT installed on Windows. Every target here is a thin wrapper over a
# raw `docker compose` command — if you have no `make`, run those directly, or use the PowerShell forms
# documented in docs/DEV_PROD.md. All targets are non-interactive and safe to re-run.
#
# Run from the MAIN checkout root, NOT a worktree — worktrees never run the stack (see docs/DEV_PROD.md).
# Requires `.env` (prod) and `.env.dev` (dev) at this root; copy `.env.example`.

COMPOSE ?= docker compose
# The dev invocation: base + dev override, its own project name (namespaces containers/network/volumes),
# and the dev env file for ${VAR} interpolation (--env-file REPLACES the auto-loaded .env for dev).
DEV := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev

.PHONY: prod-up dev-up dev-down refresh-dev

prod-up: ## PROD stack up (project alphadeck | 8080/8000/5544 | cron ON). Auto-loads .env. Detached.
	$(COMPOSE) up -d --build

dev-up: ## DEV stack up BESIDE prod (project alphadeck_dev | 8081/8001/5545 | cron OFF). Uses .env.dev.
	$(DEV) up -d --build

dev-down: ## Stop + remove the DEV containers/network. KEEPS the alphadeck_dev_* volumes (dev data survives).
	$(DEV) down

refresh-dev: ## One-way prod->dev data refresh: pg_dump READ from prod -> restore into the dev DB. Never writes prod.
	bash scripts/refresh-dev.sh

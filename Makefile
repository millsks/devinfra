# devinfra — local development infrastructure
#
# DEPRECATED. pixi is the task surface: run `pixi task list` to see every task,
# `pixi run <task>` to run one. This file survives for one release so existing
# muscle memory keeps working; every target below forwards to its pixi task and
# prints a notice on stderr, leaving stdout exactly as it was.
#
# No recipe here contains logic. Anything that needed a loop, a conditional or a
# `read` now lives in scripts/, where it can be run standalone and tested.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Emitted on stderr so a pipeline consuming a target's stdout is unaffected.
NOTICE = echo "make $@ is deprecated — run 'pixi run $@'. Forwarding." >&2

.PHONY: help
help: ## Show this help
	@$(NOTICE)
	@pixi task list

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
.PHONY: init
init: ## Create .env from the template if it does not exist
	@$(NOTICE)
	@pixi run init

.PHONY: up
up: ## Start the stack (profiles from COMPOSE_PROFILES)
	@$(NOTICE)
	@pixi run up

.PHONY: up-core
up-core: ## Start only postgres, redis, keycloak, minio, mailpit
	@$(NOTICE)
	@pixi run up-core

.PHONY: down
down: ## Stop and remove containers, PRESERVING all data volumes
	@$(NOTICE)
	@pixi run down

.PHONY: stop
stop: ## Stop containers without removing them
	@$(NOTICE)
	@pixi run stop

.PHONY: restart
restart: ## Recreate the stack, preserving data
	@$(NOTICE)
	@pixi run restart

.PHONY: destroy
destroy: ## DELETE ALL DATA — remove containers and every named volume
	@$(NOTICE)
	@pixi run destroy

.PHONY: pull
pull: ## Pull newer images for every pinned tag
	@$(NOTICE)
	@pixi run pull

.PHONY: wait
wait: ## Block until every container is running and every healthcheck is healthy
	@$(NOTICE)
	@pixi run wait

# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
.PHONY: ps
ps: ## Show container status and health
	@$(NOTICE)
	@pixi run ps

.PHONY: logs
logs: ## Tail logs for all services (or one: make logs S=keycloak)
	@$(NOTICE)
	@pixi run logs $(S)

.PHONY: smoke
smoke: ## Run the end-to-end smoke test against the running stack
	@$(NOTICE)
	@pixi run smoke

.PHONY: urls
urls: ## Print every service endpoint
	@$(NOTICE)
	@pixi run urls

# ---------------------------------------------------------------------------
# Shells
# ---------------------------------------------------------------------------
.PHONY: psql
psql: ## Open a psql shell (make psql DB=keycloak)
	@$(NOTICE)
	@pixi run psql $(DB)

.PHONY: redis-cli
redis-cli: ## Open a redis-cli shell (make redis-cli N=1 to select a db)
	@$(NOTICE)
	@pixi run redis-cli $(N)

.PHONY: mc
mc: ## Open a shell with the MinIO client configured
	@$(NOTICE)
	@pixi run mc

# ---------------------------------------------------------------------------
# Data operations
# ---------------------------------------------------------------------------
.PHONY: backup
backup: ## Dump all Postgres databases to backups/
	@$(NOTICE)
	@pixi run backup

.PHONY: restore
restore: ## Restore a dump: make restore F=backups/postgres-....sql.gz
	@$(NOTICE)
	@pixi run restore $(F)

.PHONY: keycloak-reimport
keycloak-reimport: ## Re-import the realm after editing the JSON (DESTROYS realm state)
	@$(NOTICE)
	@pixi run keycloak-reimport

.PHONY: keycloak-export
keycloak-export: ## Export the live realm back to services/keycloak/seed/
	@$(NOTICE)
	@pixi run keycloak-export

.PHONY: token
token: ## Mint an access token for user dev via the CLI client
	@$(NOTICE)
	@pixi run token $(or $(U),dev) $(or $(P),dev)

.PHONY: config
config: ## Render the fully resolved compose configuration
	@$(NOTICE)
	@pixi run config

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
.PHONY: lint
lint: ## Run every validation check
	@$(NOTICE)
	@pixi run lint

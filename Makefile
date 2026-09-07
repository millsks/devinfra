# devinfra — local development infrastructure
#
# `make help` lists every target.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
ENV_FILE := .env

# Load .env so targets can reference credentials without duplicating defaults.
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif

POSTGRES_USER ?= devinfra
POSTGRES_DB ?= devinfra
REDIS_PASSWORD ?= devinfra
KEYCLOAK_REALM ?= devinfra

.PHONY: help
help: ## Show this help
	@echo "devinfra — local development infrastructure"
	@echo
	@# firstword, not MAKEFILE_LIST: including .env would make grep prefix
	@# every line with a filename and clobber the target column.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Profiles: admin, observability (set COMPOSE_PROFILES in .env)"

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
$(ENV_FILE):
	@cp .env.example $(ENV_FILE)
	@echo "Created $(ENV_FILE) from .env.example — review it before going further."

.PHONY: init
init: $(ENV_FILE) ## Create .env from the template if it does not exist

.PHONY: up
up: $(ENV_FILE) ## Start the stack (profiles from COMPOSE_PROFILES)
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory wait
	@$(MAKE) --no-print-directory urls

.PHONY: up-core
up-core: $(ENV_FILE) ## Start only postgres, redis, keycloak, minio, mailpit
	COMPOSE_PROFILES= $(COMPOSE) up -d
	@COMPOSE_PROFILES= $(MAKE) --no-print-directory wait

.PHONY: down
down: ## Stop and remove containers, PRESERVING all data volumes
	$(COMPOSE) --profile admin --profile observability down

.PHONY: stop
stop: ## Stop containers without removing them
	$(COMPOSE) --profile admin --profile observability stop

.PHONY: restart
restart: down up ## Recreate the stack, preserving data

.PHONY: destroy
destroy: ## DELETE ALL DATA — remove containers and every named volume
	@echo "This permanently deletes every devinfra volume (databases, objects, telemetry)."
	@read -p "Type 'destroy' to confirm: " ans && [ "$$ans" = "destroy" ] || (echo "Aborted."; exit 1)
	$(COMPOSE) --profile admin --profile observability down -v

.PHONY: pull
pull: ## Pull newer images for every pinned tag
	$(COMPOSE) --profile admin --profile observability pull

.PHONY: wait
wait: ## Block until every container is running and every healthcheck is healthy
	@echo "Waiting for healthchecks..."
	@for i in $$(seq 1 60); do \
		bad=$$($(COMPOSE) ps --format '{{.Name}} {{.State}} {{.Health}}' 2>/dev/null \
			| awk '$$2 == "restarting" || $$2 == "dead" || $$2 == "paused" \
			       || ($$3 != "" && $$3 != "healthy") {print $$1}'); \
		if [ -z "$$bad" ]; then echo "All containers running, all healthchecks passing."; exit 0; fi; \
		sleep 5; \
	done; \
	echo "Timed out. Not ready:"; \
	$(COMPOSE) ps --format 'table {{.Name}}\t{{.State}}\t{{.Health}}'; \
	exit 1

# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
.PHONY: ps
ps: ## Show container status and health
	@$(COMPOSE) ps --format 'table {{.Name}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'

.PHONY: logs
logs: ## Tail logs for all services (or one: make logs S=keycloak)
	$(COMPOSE) logs -f --tail=100 $(S)

.PHONY: smoke
smoke: ## Run the end-to-end smoke test against the running stack
	@./scripts/smoke-test.sh

.PHONY: urls
urls: ## Print every service endpoint
	@printf '\n\033[1mEndpoints\033[0m\n'
	@printf '  %-16s postgresql://%s@localhost:%s/%s\n' "PostgreSQL" "$(POSTGRES_USER)" "$(POSTGRES_PORT)" "$(POSTGRES_DB)"
	@printf '  %-16s redis://:%s@localhost:%s\n' "Redis" "$(REDIS_PASSWORD)" "$(REDIS_PORT)"
	@printf '  %-16s http://localhost:%s  (admin console: /admin)\n' "Keycloak" "$(KEYCLOAK_PORT)"
	@printf '  %-16s http://localhost:%s/realms/%s/.well-known/openid-configuration\n' "  OIDC discovery" "$(KEYCLOAK_PORT)" "$(KEYCLOAK_REALM)"
	@printf '  %-16s http://localhost:%s  (API: localhost:%s)\n' "MinIO console" "$(MINIO_CONSOLE_PORT)" "$(MINIO_API_PORT)"
	@printf '  %-16s http://localhost:%s  (SMTP: localhost:%s)\n' "Mailpit" "$(MAILPIT_UI_PORT)" "$(MAILPIT_SMTP_PORT)"
	@printf '  %-16s http://localhost:%s\n' "pgAdmin" "$(PGADMIN_PORT)"
	@printf '  %-16s http://localhost:%s\n' "RedisInsight" "$(REDISINSIGHT_PORT)"
	@printf '  %-16s http://localhost:%s\n' "Flower" "$(FLOWER_PORT)"
	@printf '  %-16s http://localhost:%s\n' "Grafana" "$(GRAFANA_PORT)"
	@printf '  %-16s http://localhost:%s\n' "Prometheus" "$(PROMETHEUS_PORT)"
	@printf '  %-16s grpc://localhost:%s  http://localhost:%s\n' "OTLP ingest" "$(OTEL_GRPC_PORT)" "$(OTEL_HTTP_PORT)"
	@printf '\n'

# ---------------------------------------------------------------------------
# Shells
# ---------------------------------------------------------------------------
.PHONY: psql
psql: ## Open a psql shell (make psql DB=keycloak)
	@$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(or $(DB),$(POSTGRES_DB))

.PHONY: redis-cli
redis-cli: ## Open a redis-cli shell (make redis-cli N=1 to select a db)
	@$(COMPOSE) exec redis redis-cli -a $(REDIS_PASSWORD) --no-auth-warning $(if $(N),-n $(N),)

.PHONY: mc
mc: ## Open a shell with the MinIO client configured
	@$(COMPOSE) exec minio sh -c 'mc alias set local http://127.0.0.1:9000 "$$MINIO_ROOT_USER" "$$MINIO_ROOT_PASSWORD" >/dev/null && exec sh'

# ---------------------------------------------------------------------------
# Data operations
# ---------------------------------------------------------------------------
.PHONY: backup
backup: ## Dump all Postgres databases to backups/
	@mkdir -p backups
	@f=backups/postgres-$$(date +%Y%m%d-%H%M%S).sql.gz; \
	$(COMPOSE) exec -T postgres pg_dumpall -U $(POSTGRES_USER) | gzip > $$f; \
	echo "Wrote $$f ($$(du -h $$f | cut -f1))"

.PHONY: restore
restore: ## Restore a dump: make restore F=backups/postgres-....sql.gz
	@[ -n "$(F)" ] || (echo "Usage: make restore F=backups/postgres-....sql.gz"; exit 1)
	@gunzip -c $(F) | $(COMPOSE) exec -T postgres psql -U $(POSTGRES_USER) -d postgres
	@echo "Restored from $(F)"

.PHONY: keycloak-reimport
keycloak-reimport: ## Re-import the realm after editing the JSON (DESTROYS realm state)
	@echo "Drops the 'keycloak' database and re-imports docker/keycloak/realms/."
	@echo "All realm changes made through the admin console will be lost."
	@read -p "Type 'reimport' to confirm: " ans && [ "$$ans" = "reimport" ] || (echo "Aborted."; exit 1)
	$(COMPOSE) stop keycloak
	$(COMPOSE) exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) \
		-c 'DROP DATABASE IF EXISTS keycloak WITH (FORCE);' \
		-c 'CREATE DATABASE keycloak OWNER $(POSTGRES_USER);'
	$(COMPOSE) up -d keycloak
	@$(MAKE) --no-print-directory wait

.PHONY: keycloak-export
keycloak-export: ## Export the live realm back to docker/keycloak/realms/
	$(COMPOSE) exec keycloak /opt/keycloak/bin/kc.sh export \
		--dir /tmp/kc-export --realm $(KEYCLOAK_REALM) --users realm_file
	$(COMPOSE) cp keycloak:/tmp/kc-export/$(KEYCLOAK_REALM)-realm.json \
		docker/keycloak/realms/$(KEYCLOAK_REALM)-realm.json
	@echo "Exported to docker/keycloak/realms/$(KEYCLOAK_REALM)-realm.json"
	@echo "Note: the client secret is now a literal, not \$${KEYCLOAK_CLIENT_SECRET}."

.PHONY: token
token: ## Mint an access token for user dev via the CLI client
	@curl -s -X POST "http://localhost:$(KEYCLOAK_PORT)/realms/$(KEYCLOAK_REALM)/protocol/openid-connect/token" \
		-d grant_type=password -d client_id=devinfra-cli \
		-d username=$(or $(U),dev) -d password=$(or $(P),dev) -d scope=openid

.PHONY: config
config: ## Render the fully resolved compose configuration
	@$(COMPOSE) --profile admin --profile observability config

.PHONY: lint
lint: ## Deprecated: forwards to `pixi run lint`
	@echo "make lint is deprecated — run 'pixi run lint'. Forwarding." >&2
	@pixi run lint

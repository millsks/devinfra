#!/usr/bin/env bash
# Print every service endpoint the stack publishes.
#
#   ./scripts/urls.sh
#
# Falls back to the same defaults compose.yaml interpolates, so the list is
# complete whether or not .env exists.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# Port defaults mirror compose.yaml's ${VAR:-N} forms one for one. A service
# missing from this list is a service a developer cannot find.
: "${POSTGRES_PORT:=5432}"
: "${REDIS_PORT:=6379}"
: "${KEYCLOAK_PORT:=8080}"
: "${MINIO_API_PORT:=9100}"
: "${MINIO_CONSOLE_PORT:=9101}"
: "${MAILPIT_SMTP_PORT:=1025}"
: "${MAILPIT_UI_PORT:=8025}"
: "${PGADMIN_PORT:=5050}"
: "${REDISINSIGHT_PORT:=5540}"
: "${FLOWER_PORT:=5555}"
: "${GRAFANA_PORT:=3000}"
: "${PROMETHEUS_PORT:=9090}"
: "${OTEL_GRPC_PORT:=4317}"
: "${OTEL_HTTP_PORT:=4318}"

printf '\n\033[1mEndpoints\033[0m\n'
printf '  %-16s postgresql://%s@localhost:%s/%s\n' "PostgreSQL" "$POSTGRES_USER" "$POSTGRES_PORT" "$POSTGRES_DB"
printf '  %-16s redis://:%s@localhost:%s\n' "Redis" "$REDIS_PASSWORD" "$REDIS_PORT"
printf '  %-16s http://localhost:%s  (admin console: /admin)\n' "Keycloak" "$KEYCLOAK_PORT"
printf '  %-16s http://localhost:%s/realms/%s/.well-known/openid-configuration\n' \
    "  OIDC discovery" "$KEYCLOAK_PORT" "$KEYCLOAK_REALM"
printf '  %-16s http://localhost:%s  (API: localhost:%s)\n' "MinIO console" "$MINIO_CONSOLE_PORT" "$MINIO_API_PORT"
printf '  %-16s http://localhost:%s  (SMTP: localhost:%s)\n' "Mailpit" "$MAILPIT_UI_PORT" "$MAILPIT_SMTP_PORT"
printf '  %-16s http://localhost:%s\n' "pgAdmin" "$PGADMIN_PORT"
printf '  %-16s http://localhost:%s\n' "RedisInsight" "$REDISINSIGHT_PORT"
printf '  %-16s http://localhost:%s\n' "Flower" "$FLOWER_PORT"
printf '  %-16s http://localhost:%s\n' "Grafana" "$GRAFANA_PORT"
printf '  %-16s http://localhost:%s\n' "Prometheus" "$PROMETHEUS_PORT"
printf '  %-16s grpc://localhost:%s  http://localhost:%s\n' "OTLP ingest" "$OTEL_GRPC_PORT" "$OTEL_HTTP_PORT"
printf '\n'

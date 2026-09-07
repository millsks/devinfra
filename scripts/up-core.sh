#!/usr/bin/env bash
# Start only the core services: postgres, redis, keycloak, minio, mailpit.
#
#   ./scripts/up-core.sh
#
# Clears COMPOSE_PROFILES for both the start and the health wait, so a .env that
# selects admin/observability does not drag them in.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

export COMPOSE_PROFILES=

compose up -d
exec ./scripts/wait-healthy.sh

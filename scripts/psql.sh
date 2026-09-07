#!/usr/bin/env bash
# Open a psql shell inside the postgres container.
#
#   ./scripts/psql.sh              # POSTGRES_DB
#   ./scripts/psql.sh keycloak     # a named database
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

db="${1:-$POSTGRES_DB}"

compose exec postgres psql -U "$POSTGRES_USER" -d "$db"

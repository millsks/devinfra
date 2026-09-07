#!/usr/bin/env bash
# Start only the core Modules: postgres, redis, keycloak, minio, mailpit.
#
#   ./scripts/up-core.sh
#
# The five are requested by name and resolved to their dependency closure (AD-16), for both
# the start and the health wait, so a .env that selects admin/observability does not drag
# them in. Clearing COMPOSE_PROFILES used to do that job and no longer can: every service
# now carries its own Module profile, so an empty value selects nothing at all.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

select_profiles postgres redis keycloak minio mailpit

compose up -d
exec ./scripts/wait-healthy.sh

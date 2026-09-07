#!/usr/bin/env bash
# Tail logs for every service, or for one named service.
#
#   ./scripts/logs.sh
#   ./scripts/logs.sh keycloak
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The pixi task quotes its interpolation so a service name may contain spaces,
# which means "no service" arrives as one empty argument rather than none.
argv=(logs -f --tail=100)
for service in "$@"; do
    if [[ -n "$service" ]]; then
        argv+=("$service")
    fi
done

compose "${argv[@]}"

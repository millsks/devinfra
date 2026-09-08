#!/usr/bin/env bash
# Start only the `core` Bundle.
#
#   ./scripts/up-core.sh
#
# The Bundle is requested by name and resolved to its dependency closure (AD-16), for both
# the start and the health wait, so a .env that selects admin/observability does not drag
# them in. `core` is registered in the root compose.yaml's x-bundles: and every Module in it
# declares `core` in its own profiles:, so the name resolves to exactly the five and the
# five are already dependency-closed — this script names no service list of its own to drift
# (ADR 0014). Clearing COMPOSE_PROFILES used to do this job and no longer can: every service
# now carries its own Module profile, so an empty value selects nothing at all.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

select_profiles core

compose up -d
exec ./scripts/wait-healthy.sh

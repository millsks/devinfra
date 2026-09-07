#!/usr/bin/env bash
# Run the container-runtime command with the arguments given, over a resolved Selection.
#
#   ./scripts/compose.sh config
#   DEVINFRA_SELECT_ALL=1 ./scripts/compose.sh down
#
# It exists only so a pixi task can reach the DEVINFRA_COMPOSE seam. A task body cannot
# expand ${DEVINFRA_COMPOSE:-docker compose} itself — pixi.toml has no shell expansion
# anywhere — so a task that named `docker compose` directly would ignore the seam and run
# Docker no matter what the environment selected. Every lifecycle task routes through here
# instead, which is what makes the Podman job a real run of the same tasks rather than a
# second definition of them.
#
# The same argument now applies to profiles. A task body cannot expand a Selection either,
# so the five lifecycle tasks that must act on the whole stack used to carry a hard-coded
# `--profile admin --profile observability` — a second, drifting statement of what the stack
# contains, and one that a thirteenth Module would have silently escaped. They set
# DEVINFRA_SELECT_ALL=1 instead and the all-Modules request is resolved here (AD-16).
# Everything else takes the ambient Selection from COMPOSE_PROFILES.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

if [[ "${DEVINFRA_SELECT_ALL:-}" == "1" ]]; then
    select_profiles --all
else
    select_ambient
fi

compose "$@"

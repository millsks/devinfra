#!/usr/bin/env bash
# Run the container-runtime command with the arguments given.
#
#   ./scripts/compose.sh --profile admin config
#
# It exists only so a pixi task can reach the DEVINFRA_COMPOSE seam. A task body
# cannot expand ${DEVINFRA_COMPOSE:-docker compose} itself — pixi.toml has no
# shell expansion anywhere — so a task that named `docker compose` directly would
# ignore the seam and run Docker no matter what the environment selected. Every
# lifecycle task routes through here instead, which is what makes the Podman job
# a real run of the same tasks rather than a second definition of them.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

compose "$@"

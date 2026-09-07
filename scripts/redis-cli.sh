#!/usr/bin/env bash
# Open a redis-cli shell inside the redis container.
#
#   ./scripts/redis-cli.sh      # no database selected, as redis-cli defaults
#   ./scripts/redis-cli.sh 1    # the Celery broker database
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

n="${1:-}"

# Built as one never-empty array: an empty array expanded under `set -u` is an
# error on the bash 3.2 that ships with macOS.
argv=(exec redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning)
if [[ -n "$n" ]]; then
    argv+=(-n "$n")
fi

compose "${argv[@]}"

#!/usr/bin/env bash
# Create .env from .env.example if it does not already exist.
#
#   ./scripts/init-env.sh
#
# Never overwrites an existing .env — that file holds the developer's own
# credentials and port choices.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

if [[ -f .env ]]; then
    echo ".env already exists — leaving it alone."
    exit 0
fi

cp .env.example .env
echo "Created .env from .env.example — review it before going further."

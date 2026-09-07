#!/usr/bin/env bash
# Remove every container AND every named volume. Irreversible.
#
#   ./scripts/destroy.sh
#
# Requires the exact word "destroy" on stdin. Anything else — including an empty
# or absent stdin — aborts before a volume is touched.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

echo "This permanently deletes every devinfra volume (databases, objects, telemetry)."
confirm_word destroy

compose --profile admin --profile observability down -v

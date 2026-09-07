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

# Every Module, not the ambient Selection: a destroy that honoured a narrowed Selection
# would leave the volumes it was not asked about behind while reporting that it removed
# every one. The all-Modules request is what the hard-coded --profile flags used to say.
select_profiles --all

echo "This permanently deletes every devinfra volume (databases, objects, telemetry)."
confirm_word destroy

compose down -v

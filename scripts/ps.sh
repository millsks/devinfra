#!/usr/bin/env bash
# Show container status, health and published ports.
#
#   ./scripts/ps.sh
#
# A script rather than an inline task because the Go template braces compose
# needs collide with pixi's own `{{ }}` task templating.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'

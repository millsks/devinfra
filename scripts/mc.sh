#!/usr/bin/env bash
# Open a shell inside the object-storage container with `mc` already aliased.
#
#   ./scripts/mc.sh
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

# The credentials are read inside the container, from its own environment, so
# they are deliberately not interpolated here.
# shellcheck disable=SC2016
compose exec minio sh -c \
    'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && exec sh'

#!/usr/bin/env bash
# Restore a pg_dumpall archive produced by scripts/backup.sh.
#
#   ./scripts/restore.sh backups/postgres-20260906-101500.sql.gz
#
# Refuses a missing or nonexistent path before psql is invoked: a restore that
# starts and fails halfway is worse than one that refuses.
set -euo pipefail

# Resolved BEFORE common.sh cds to the repository root, so a relative path is
# read against the directory the developer ran the command from. Otherwise
# `cd /elsewhere && /path/to/scripts/restore.sh ./dump.gz` would look for the
# dump at the repository root — missing it, or worse, finding a different file
# of the same name.
f="${1:-}"
if [[ -n "$f" && "$f" != /* ]]; then
    f="$PWD/$f"
fi

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

if [[ -z "$f" ]]; then
    echo "Usage: ./scripts/restore.sh backups/postgres-....sql.gz" >&2
    exit 1
fi

if [[ ! -f "$f" ]]; then
    echo "No such backup file: $f" >&2
    exit 1
fi

# The ambient Selection, resolved to its dependency closure before Compose sees it.
# After the path checks, so a missing or nonexistent archive is still named as itself.
select_ambient

gunzip -c "$f" | compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres
echo "Restored from $f"

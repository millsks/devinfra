#!/usr/bin/env bash
# Dump every Postgres database to a timestamped gzip under backups/.
#
#   ./scripts/backup.sh
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

mkdir -p backups
f="backups/postgres-$(date +%Y%m%d-%H%M%S).sql.gz"

# Written under a partial name and moved into place only on success. A plain
# redirection creates the .gz before the pipeline runs, so a pg_dumpall that
# fails halfway leaves a truncated archive that looks valid — and that
# restore.sh's existence check would happily accept.
partial="${f}.partial"
if ! compose exec -T postgres pg_dumpall -U "$POSTGRES_USER" | gzip >"$partial"; then
    rm -f "$partial"
    echo "pg_dumpall failed; no backup written." >&2
    exit 1
fi
mv "$partial" "$f"

echo "Wrote $f ($(du -h "$f" | cut -f1))"

#!/usr/bin/env bash
# Prove the backup/restore round trip against a real runtime.
#
#   ./scripts/verify-restore.sh
#
# Plants marker rows in two Postgres databases and marker objects in two object-storage
# buckets, backs the stack up, destroys every volume, brings the stack back, restores, and
# then asserts all four markers returned before running the strict smoke suite.
#
# The markers are what make this a restore test rather than a re-provisioning test: a stack
# whose volumes were destroyed and recreated passes every smoke check on seed data alone, so
# without something that only the restore could have brought back, "the suite is green"
# proves nothing about the backup.
#
# *Two* of each, and never the first alone, because a capture narrowed to the connected
# database or the first bucket would still return one marker — the destroyed volumes re-seed
# the rest, so a single marker cannot tell a full capture from a partial one. The names come
# from the server, the same way scripts/backup.sh asks for them, not from MINIO_BUCKETS or
# POSTGRES_EXTRA_DATABASES: those say what the stack was provisioned with, not what it holds
# (ADR 0018). A server naming fewer than two of either is refused rather than half-checked.
#
# The marker table is this script's own. `smoke_probe` is truncated by
# services/postgres/smoke.sh on every run, so a marker planted there would be a marker the
# suite itself can erase.
#
# Destructive by design: this deletes every volume. It is what `pixi run ci-stack-restore`
# runs, after `pixi run ci-stack` has left a started stack behind. Run from a terminal it
# asks for the same confirmation `pixi run destroy` asks for; run without one — which is how
# CI runs it — it proceeds.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

# Both Modules must be in the Selection. A round trip that silently checked one of them
# would report a green restore for a backup that captured half a stack.
for module in postgres minio; do
    if ! selected "$module"; then
        echo "verify-restore: ${module} is not in the Selection (${COMPOSE_PROFILES})." >&2
        echo "  This round trip plants and reads markers in both Postgres and object" >&2
        echo "  storage; it cannot report on a Selection that excludes either." >&2
        exit 1
    fi
done

marker="restore-marker-$(date +%Y%m%d-%H%M%S)"

# The container-side programs. Credentials are read inside the container from its own
# environment, so they are deliberately not interpolated by this shell.
# shellcheck disable=SC2016
MC_LIST_BUCKETS='
    mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    exec mc ls --json local
'
# shellcheck disable=SC2016
MC_PUT_MARKER='
    mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    printf "%s" "$2" > /tmp/restore-marker.txt
    exec mc cp /tmp/restore-marker.txt "local/$1/restore-marker.txt"
'
# shellcheck disable=SC2016
MC_CAT_MARKER='
    mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    exec mc cat "local/$1/restore-marker.txt"
'

# The same question backup.sh asks the server, so what is checked is what is captured.
databases=""
databases="$(compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -tAc \
    "select datname from pg_database where datallowconn and not datistemplate and datname <> 'postgres' order by datname")"
read -r -a database_names <<<"${databases//$'\n'/ }"
if ((${#database_names[@]} < 2)); then
    echo "verify-restore: the server names ${#database_names[@]} database(s) to capture." >&2
    echo "  Two are needed: one marker cannot tell a full capture from one narrowed to the" >&2
    echo "  connected database, because the destroyed volumes re-seed everything else." >&2
    exit 1
fi

listing=""
listing="$(compose exec -T minio sh -c "$MC_LIST_BUCKETS" sh)"
bucket_names=()
while IFS= read -r bucket; do
    [[ -n "$bucket" ]] && bucket_names+=("$bucket")
done < <(printf '%s\n' "$listing" | sed -n 's/.*"key":"\([^"]*\)".*/\1/p' | tr -d '/')
if ((${#bucket_names[@]} < 2)); then
    echo "verify-restore: the server lists ${#bucket_names[@]} bucket(s) to capture." >&2
    echo "  Two are needed: one marker cannot tell a full capture from one narrowed to the" >&2
    echo "  first bucket, because the destroyed volumes re-provision the rest." >&2
    exit 1
fi

marker_databases=("${database_names[0]}" "${database_names[1]}")
marker_buckets=("${bucket_names[0]}" "${bucket_names[1]}")

echo "== Planting markers (${marker})"
for database in "${marker_databases[@]}"; do
    compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$database" -c \
        "create table if not exists restore_probe(v text); delete from restore_probe; insert into restore_probe values ('${marker}');"
    echo "  postgres: ${database}.restore_probe"
done
for bucket in "${marker_buckets[@]}"; do
    compose exec -T minio sh -c "$MC_PUT_MARKER" sh "$bucket" "$marker"
    echo "  minio: ${bucket}/restore-marker.txt"
done

echo "== Backing up"
# The path backup.sh itself named, not the last directory that happens to sort highest in an
# untracked backups/ that may hold anything. Read as the backup runs, so its output still
# reaches this run's log line by line; a backup that failed prints no `Wrote` line, which is
# the empty-archive refusal below.
archive=""
while IFS= read -r line; do
    printf '%s\n' "$line"
    case "$line" in
    "Wrote "*) archive="${line#Wrote }" ;;
    esac
done < <(./scripts/backup.sh)
if [[ -z "$archive" || ! -d "$archive" ]]; then
    echo "verify-restore: the backup named no directory on stdout; nothing to restore." >&2
    exit 1
fi

echo "== Destroying every volume"
# The same confirmation destroy.sh requires, asked here because this script is the thing a
# developer types. Only when there is a terminal to ask: CI runs with no tty, and the piped
# word below is what destroy.sh reads either way.
if [[ -t 0 ]]; then
    echo "This permanently deletes every devinfra volume (databases, objects, telemetry)"
    echo "and then restores ${archive} over the empty stack."
    confirm_word destroy
fi
printf 'destroy\n' | ./scripts/destroy.sh

echo "== Bringing the stack back on empty volumes"
./scripts/compose.sh up -d
./scripts/wait-healthy.sh

echo "== Restoring ${archive}"
./scripts/restore.sh "$archive"

echo "== Asserting the markers came back"
for database in "${marker_databases[@]}"; do
    row=""
    row="$(compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$database" -tAc \
        "select v from restore_probe where v = '${marker}';")"
    if [[ "$row" != *"$marker"* ]]; then
        echo "verify-restore: the Postgres marker did not come back from '${database}'." >&2
        echo "  expected '${marker}' in restore_probe; read: ${row}" >&2
        exit 1
    fi
    echo "  postgres: ${marker} present in ${database}.restore_probe"
done
for bucket in "${marker_buckets[@]}"; do
    object=""
    object="$(compose exec -T minio sh -c "$MC_CAT_MARKER" sh "$bucket")"
    if [[ "$object" != *"$marker"* ]]; then
        echo "verify-restore: the object-storage marker did not come back from '${bucket}'." >&2
        echo "  expected '${marker}' in ${bucket}/restore-marker.txt; read: ${object}" >&2
        exit 1
    fi
    echo "  minio: ${marker} present in ${bucket}/restore-marker.txt"
done

echo "== Strict smoke suite over the restored stack"
SMOKE_STRICT=1 exec ./scripts/smoke-test.sh

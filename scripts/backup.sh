#!/usr/bin/env bash
# Capture every stateful Module in the current Selection into one timestamped directory.
#
#   ./scripts/backup.sh
#
# Three components, each captured only when its Module is in the resolved Selection and
# recorded as skipped when it is not (AD-16): the Postgres databases, the object-storage
# bucket contents and the Keycloak realm. Redis is deliberately excluded — cache and
# in-flight task state, neither meaningful to restore.
#
# The archive is a directory, not a stream:
#
#   backups/<ts>/manifest.txt          what was captured, what was skipped and why
#   backups/<ts>/postgres/<db>.sql.gz  one pg_dump --create --clean --if-exists per database
#   backups/<ts>/minio/<bucket>/…      the current version of every object in every bucket
#   backups/<ts>/keycloak/<realm>-realm.json
#
# One dump per database rather than one `pg_dumpall` stream, because restore applies these
# under `psql -v ON_ERROR_STOP=1` and a cluster dump opens with `CREATE ROLE`, which aborts
# against any initialized cluster — including an empty one, whose bootstrap role initdb
# creates from POSTGRES_USER. See docs/adr/0018.
#
# Object *versions* are not captured: `mc mirror` moves current versions only. The manifest
# says so, and so does the ADR.
#
# The realm JSON is a portable, diffable artefact — drop it into services/keycloak/seed/ and
# run `pixi run keycloak-reimport` to seed another stack. It is *not* the restore path:
# Keycloak's whole state lives in the `keycloak` database, which the Postgres restore
# rewrites, and re-importing the JSON afterwards would discard exactly what that returned.
#
# Written under a `.partial` name and moved into place only on success, the way the single
# archive was: a directory left behind by a capture that failed halfway looks valid to
# anything that checks for existence.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it. It is
# also the oracle for what to capture: `selected` below is only meaningful after this.
select_ambient

stamp="$(date +%Y%m%d-%H%M%S)"
dest="backups/${stamp}"
partial="${dest}.partial"

mkdir -p backups
if [[ -e "$dest" || -e "$partial" ]]; then
    echo "backup: ${dest} already exists; refusing to write over an existing backup." >&2
    exit 1
fi
mkdir "$partial"

# The staging directories this run creates inside the containers. Named before the trap is
# installed, so the cleanup below can reach them whichever step the run died in.
minio_stage=""
keycloak_stage=""

# Every exit path removes the partial directory. On success it has already been moved, so
# this is a no-op; on any failure — a refusal here, a non-zero capture step, an interrupt —
# it is what keeps a half-written directory from ever being offered to restore.
#
# The container-side staging directories go the same way, on the exit path rather than at
# the end of their own step: a failed run would otherwise leak a full copy of the objects or
# the realm into a container's writable layer, where nothing ever removes it. Their removal
# is a warning rather than a failure, because a capture that has already been written and
# moved into place is not made wrong by a leftover directory in /tmp, and turning that into
# a non-zero exit would report a good backup as a bad one.
cleanup() {
    rm -rf "${partial}"
    if [[ -n "$minio_stage" ]]; then
        if ! compose exec -T minio rm -rf "$minio_stage"; then
            printf 'backup: warning: %s is left behind inside the minio container.\n' "$minio_stage" >&2
        fi
        minio_stage=""
    fi
    if [[ -n "$keycloak_stage" ]]; then
        if ! compose exec -T keycloak rm -rf "$keycloak_stage"; then
            printf 'backup: warning: %s is left behind inside the keycloak container.\n' "$keycloak_stage" >&2
        fi
        keycloak_stage=""
    fi
}
trap cleanup EXIT

# A capture that could not finish is a failed backup, never a smaller one. The Module and
# the step are both named, because "backup failed" sends a reader to three containers.
capture_failed() {
    printf 'backup: %s\n' "$1" >&2
    printf 'backup: no backup written; %s removed.\n' "$partial" >&2
    exit 1
}

captured=()
skipped=()

# --- Postgres ---------------------------------------------------------------------------
if selected postgres; then
    # `postgres` is the maintenance database restore connects to, so it is the one database
    # this cannot capture (see below). A stack that keeps its application tables *in* it —
    # legal, and the upstream image's own default — would otherwise be backed up to an
    # archive holding none of them, at exit 0. Refused before a single dump is written; the
    # exit trap takes the empty partial directory away again.
    if [[ "$POSTGRES_DB" == "postgres" ]]; then
        echo "backup: POSTGRES_DB is 'postgres', the maintenance database this capture must" >&2
        echo "  exclude: restore connects to it, and DROP DATABASE cannot run against the" >&2
        echo "  connection applying it. Every application table would then live in the one" >&2
        echo "  database the archive does not hold. Give the application a database of its own." >&2
        exit 1
    fi
    mkdir "${partial}/postgres"
    # The database list comes from the server, never from POSTGRES_EXTRA_DATABASES: a
    # database an application created is state too, and a list in this file would silently
    # stop at whatever it was last edited to say.
    #
    # `postgres` itself is excluded deliberately. It is the maintenance database restore
    # connects to, and `pg_dump --create` emits `DROP DATABASE IF EXISTS postgres`, which
    # fails against the connection applying it — under ON_ERROR_STOP=1 that ends the whole
    # restore. It holds no application data: initdb creates it empty.
    databases=""
    if ! databases="$(compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -tAc \
        "select datname from pg_database where datallowconn and not datistemplate and datname <> 'postgres' order by datname")"; then
        capture_failed "postgres: could not list the databases to capture"
    fi
    # Word-split on purpose: psql -tA prints one unquoted name per line.
    read -r -a database_names <<<"${databases//$'\n'/ }"
    if ((${#database_names[@]} == 0)); then
        capture_failed "postgres: the server named no database to capture"
    fi
    for database in "${database_names[@]}"; do
        if ! compose exec -T postgres pg_dump -U "$POSTGRES_USER" \
            --create --clean --if-exists -d "$database" | gzip >"${partial}/postgres/${database}.sql.gz"; then
            capture_failed "postgres: pg_dump of database '${database}' failed"
        fi
    done
    captured+=("postgres: ${database_names[*]}")
else
    skipped+=("postgres (not in the Selection)")
fi

# --- Object storage ---------------------------------------------------------------------
if selected minio; then
    minio_stage="/tmp/devinfra-backup-${stamp}"
    # The two container-side programs, written out here rather than inline at the call:
    # the credentials are read inside the container from its own environment, so they are
    # deliberately not interpolated by this shell — the idiom scripts/mc.sh established —
    # and $1/$2 are the positional arguments `sh -c … sh <stage> <bucket>` supplies.
    # shellcheck disable=SC2016
    MC_LIST_BUCKETS='
        mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
        mkdir -p "$1"
        exec mc ls --json local
    '
    # `mkdir -p` first, so an empty bucket survives the round trip as an empty directory
    # rather than vanishing from the archive.
    # shellcheck disable=SC2016
    MC_MIRROR_OUT='
        mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
        mkdir -p "$1/$2"
        exec mc mirror "local/$2" "$1/$2"
    '
    listing=""
    if ! listing="$(compose exec -T minio sh -c "$MC_LIST_BUCKETS" sh "$minio_stage")"; then
        capture_failed "minio: could not list the buckets to capture"
    fi
    # Bucket names come from the server, not from MINIO_BUCKETS, so a bucket an application
    # created is captured too. Parsed on the host: the object-storage image ships no awk,
    # sed, grep or find, which is also why the capture travels as a directory rather than a
    # tar stream.
    # `mc ls --json` names each bucket in a `"key":"<name>/"` field. A bucket name may not
    # contain a slash, so deleting every slash is exactly "drop the trailing one". Read
    # through a process substitution rather than a here-string over a captured value: the
    # loop then runs in this shell, so the array it fills survives it.
    bucket_names=()
    while IFS= read -r bucket; do
        [[ -n "$bucket" ]] && bucket_names+=("$bucket")
    done < <(printf '%s\n' "$listing" | sed -n 's/.*"key":"\([^"]*\)".*/\1/p' | tr -d '/')
    # A listing that said something this parse turned into nothing is a changed `mc ls
    # --json` shape, not an empty server: capturing zero buckets from it and reporting
    # success is the silent skip this repository keeps removing.
    if ((${#bucket_names[@]} == 0)) && [[ "$listing" =~ [^[:space:]] ]]; then
        refusal="the bucket listing was not empty but named no bucket: 'mc ls --json local'"
        refusal+=" answered, and the \"key\" field this reads it by was in none of what it"
        refusal+=" said. That is a changed listing shape, not an empty server — capturing"
        refusal+=" nothing from it and reporting success is the silent skip this repository"
        refusal+=" keeps removing."
        capture_failed "minio: ${refusal}"
    fi
    # Guarded on the count because `"${array[@]}"` on an empty array is an
    # unbound-variable error under `set -u` in older bash. A server that really lists no
    # bucket is recorded with an explicit marker rather than an empty list, so the manifest
    # can never be read as "the bucket names went missing".
    bucket_list="(no buckets)"
    if ((${#bucket_names[@]} > 0)); then
        bucket_list="${bucket_names[*]}"
        for bucket in "${bucket_names[@]}"; do
            if ! compose exec -T minio sh -c "$MC_MIRROR_OUT" sh "$minio_stage" "$bucket"; then
                capture_failed "minio: mirroring bucket '${bucket}' failed"
            fi
        done
    fi
    # One copy out for the whole staging tree; the tree itself is removed on the exit path.
    # The image has no tar and no gzip, so the objects travel as files.
    if ! compose cp "minio:${minio_stage}" "${partial}/minio"; then
        capture_failed "minio: copying the staged objects out of the container failed"
    fi
    captured+=("minio: ${bucket_list}")
else
    skipped+=("minio (not in the Selection)")
fi

# --- Keycloak realm -----------------------------------------------------------------------
if selected keycloak; then
    mkdir "${partial}/keycloak"
    keycloak_stage="/tmp/devinfra-realm-${stamp}"
    # --http-management-port 9999: the export is a second JVM in the same container and
    # tries to bind the management interface, which the running server holds on 9000.
    # Without a free port it exits 1 with `Unable to start the management interface on
    # 0.0.0.0:9000`, the same collision scripts/keycloak-reimport.sh documents for import.
    if ! compose exec -T keycloak /opt/keycloak/bin/kc.sh export \
        --dir "$keycloak_stage" --realm "$KEYCLOAK_REALM" --users realm_file \
        --http-management-port 9999; then
        capture_failed "keycloak: kc.sh export of realm '${KEYCLOAK_REALM}' failed"
    fi
    if ! compose cp "keycloak:${keycloak_stage}/${KEYCLOAK_REALM}-realm.json" \
        "${partial}/keycloak/${KEYCLOAK_REALM}-realm.json"; then
        capture_failed "keycloak: copying the exported realm out of the container failed"
    fi
    captured+=("keycloak: ${KEYCLOAK_REALM}")
else
    skipped+=("keycloak (not in the Selection)")
fi

# A backup that captured nothing is not a backup. Refused rather than written, so a
# Selection holding nothing stateful cannot leave an empty directory that restore would
# later accept as a valid archive.
if ((${#captured[@]} == 0)); then
    printf 'backup: the Selection %s holds no stateful Module — nothing to capture.\n' \
        "${COMPOSE_PROFILES}" >&2
    printf 'backup: postgres, minio and keycloak are what this captures; redis is excluded\n' >&2
    printf '  deliberately (cache and in-flight task state). Nothing was written.\n' >&2
    exit 1
fi

# --- The manifest -------------------------------------------------------------------------
# The record of what this archive holds, and restore's own input: it reads the component
# list from here and refuses a component whose files are missing, so a truncated archive
# cannot restore quietly.
{
    echo "# devinfra backup"
    echo "created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "selection: ${COMPOSE_PROFILES}"
    for line in "${captured[@]}"; do
        echo "$line"
    done
    if ((${#skipped[@]} > 0)); then
        for line in "${skipped[@]}"; do
            echo "skipped: ${line}"
        done
    fi
    echo "note: object versions are not captured; mc mirror moves current versions only."
    echo "note: cluster globals — roles, their passwords and grants, and tablespaces — are"
    echo "  not captured; pg_dump is per database and pg_dumpall cannot be restored under"
    echo "  ON_ERROR_STOP=1. A restore relies on the cluster's own initdb bootstrap role."
    echo "note: keycloak/<realm>-realm.json is a portable artefact, not the restore path —"
    echo "  the realm's state is restored with the 'keycloak' database."
    echo "note: redis is excluded deliberately — cache and in-flight task state, neither"
    echo "  meaningful to restore."
    echo "note: this captures postgres, minio and keycloak. Eight other Modules own a named"
    echo "  volume and are deliberately not captured, because what they hold is derived"
    echo "  rather than authored: grafana (dashboards and datasources are provisioned from"
    echo "  files, so the volume holds only per-user preference and session state), loki,"
    echo "  tempo and prometheus (telemetry the stack regenerates), and flower, pgadmin,"
    echo "  redisinsight and mailpit (admin-UI state and captured test mail). Restoring any"
    echo "  of them would return stale derived data over freshly generated data. They are"
    echo "  named here rather than left absent: an uncaptured volume nobody wrote down is"
    echo "  the silent skip this repository keeps removing."
} >"${partial}/manifest.txt"

mv "$partial" "$dest"
echo "Wrote ${dest}"

#!/usr/bin/env bash
# Restore a backup directory produced by scripts/backup.sh.
#
#   ./scripts/restore.sh backups/20260909-153000
#
# Refuses a missing path, a path that is not a backup directory, a pre-3.4
# `postgres-*.sql.gz` cluster archive, and any component whose Module the current Selection
# excludes — each before the runtime is touched. A restore that starts and fails halfway is
# worse than one that refuses.
#
# The Postgres write follows AD-12's stop → write → restart ordering: every service in the
# Selection that transitively depends on `postgres` is stopped first, because
# `DROP DATABASE keycloak` fails while Keycloak holds a connection to it, and started again
# afterwards — including when a write failed, so a failed restore never leaves the stack
# half down. Which services those are comes from the resolver
# (`./scripts/select.sh --dependents postgres`), never from a list here.
#
# Every `psql` runs with `-v ON_ERROR_STOP=1`. Without it psql reports success for a stream
# that half-applied, which is exactly the partial success NFR-5 forbids.
#
# The captured realm JSON is deliberately *not* imported. Keycloak's state lives in the
# `keycloak` database, which the Postgres restore rewrites; `kc.sh import --override` is
# remove-and-recreate, so importing the JSON afterwards would discard what the database
# restore just returned. The file is a portable artefact — copy it into
# services/keycloak/seed/ and run `pixi run keycloak-reimport` to seed another stack.
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
    echo "Usage: ./scripts/restore.sh backups/<timestamp>" >&2
    exit 1
fi

if [[ ! -e "$f" ]]; then
    echo "No such backup file: $f" >&2
    exit 1
fi

# A backup is a directory now. A file is either the pre-3.4 cluster archive — which cannot
# be applied under ON_ERROR_STOP=1 at all, because a pg_dumpall stream opens with
# `CREATE ROLE devinfra;` and every initialized cluster already has that role — or something
# that was never a backup. Both are named rather than half-attempted (ADR 0018).
if [[ ! -d "$f" ]]; then
    echo "Not a backup directory: $f" >&2
    case "$(basename "$f")" in
    postgres-*.sql.gz)
        echo "  That is a pre-3.4 pg_dumpall archive. It cannot be restored by this script:" >&2
        echo "  a cluster-wide dump opens with CREATE ROLE, and that aborts under" >&2
        echo "  ON_ERROR_STOP=1 against any initialized cluster — and dropping" >&2
        echo "  ON_ERROR_STOP=1 to accept it would mean counting a half-applied restore as" >&2
        echo "  a success. Take a fresh backup with 'pixi run backup'; to read the old one," >&2
        echo "  gunzip it and apply it by hand, knowing it may only partly land." >&2
        ;;
    *)
        echo "  Expected a directory written by 'pixi run backup', carrying manifest.txt." >&2
        ;;
    esac
    exit 1
fi

manifest="${f}/manifest.txt"
if [[ ! -f "$manifest" ]]; then
    echo "Not a backup directory: $f" >&2
    echo "  It carries no manifest.txt, so there is no record of what it holds." >&2
    exit 1
fi

# The ambient Selection, resolved to its dependency closure before Compose sees it.
# After the path checks, so a missing or nonexistent archive is still named as itself.
select_ambient

refuse() {
    printf 'Refusing to restore: %s\n' "$1" >&2
    printf '  Nothing has been written.\n' >&2
    exit 1
}

# What the archive holds, read from its own manifest rather than from a glob: each
# component's line names what it carries — the databases, the buckets, the realm — and the
# marker `(no buckets)` is how a capture from a server with none says so.
postgres_line=""
minio_line=""
keycloak_line=""
postgres_named=0
minio_named=0
keycloak_named=0
if grep -q "^postgres: " "$manifest"; then
    postgres_named=1
    postgres_line="$(sed -n 's/^postgres: *//p' "$manifest" | head -n 1)"
fi
if grep -q "^minio: " "$manifest"; then
    minio_named=1
    minio_line="$(sed -n 's/^minio: *//p' "$manifest" | head -n 1)"
fi
if grep -q "^keycloak: " "$manifest"; then
    keycloak_named=1
    keycloak_line="$(sed -n 's/^keycloak: *//p' "$manifest" | head -n 1)"
fi

components=()
((postgres_named)) && components+=("postgres")
((minio_named)) && components+=("minio")
((keycloak_named)) && components+=("keycloak")
if ((${#components[@]} == 0)); then
    refuse "${manifest} names no captured component."
fi

# A component whose Module is outside the current Selection is refused, not skipped: writing
# it would act on a Module the caller did not ask for, and skipping it would report a
# restore that did not happen (NFR-5). Checked for every component before anything is
# written, so the refusal costs no half-restored stack.
for module in "${components[@]}"; do
    if ! selected "$module"; then
        echo "Refusing to restore: ${f} holds a '${module}' component, and the current" >&2
        echo "  Selection (${COMPOSE_PROFILES}) does not include ${module}." >&2
        echo "  Widen the Selection — COMPOSE_PROFILES, or 'pixi run select ${module}' to" >&2
        echo "  see what it needs — and run this again. Nothing has been written." >&2
        exit 1
    fi
    if [[ ! -d "${f}/${module}" ]]; then
        refuse "${manifest} names a '${module}' component, but ${f}/${module}/ is not there — this archive is truncated."
    fi
done

# …and the other direction. A component directory the manifest does not name never reaches
# the Selection check above, so without this a `postgres/` dropped into an archive whose
# manifest says nothing about it would be applied to a stack that never agreed to it.
for module in postgres minio keycloak; do
    if [[ -d "${f}/${module}" ]] && ! grep -q "^${module}: " "$manifest"; then
        refuse "${f}/${module}/ is there, but ${manifest} names no '${module}' component — the archive and its manifest disagree."
    fi
done

# Name by name, both ways: every database, bucket and realm the manifest records must have
# its file, and what is applied below is exactly that list. A manifest naming three
# databases with one dump on disk would otherwise restore one of the three, print
# "Restored from" and exit 0 — the partial success NFR-5 forbids.
postgres_dumps=()
if ((postgres_named)); then
    read -r -a postgres_names <<<"$postgres_line"
    if ((${#postgres_names[@]} == 0)); then
        refuse "${manifest} names a 'postgres' component but no database."
    fi
    for name in "${postgres_names[@]}"; do
        if [[ ! -f "${f}/postgres/${name}.sql.gz" ]]; then
            refuse "${manifest} names database '${name}', but ${f}/postgres/${name}.sql.gz is not there — this archive is truncated."
        fi
        postgres_dumps+=("${f}/postgres/${name}.sql.gz")
    done
fi

bucket_names=()
if ((minio_named)); then
    if [[ -z "$minio_line" ]]; then
        refuse "${manifest} names a 'minio' component but neither a bucket nor the '(no buckets)' marker."
    fi
    if [[ "$minio_line" != "(no buckets)" ]]; then
        read -r -a bucket_names <<<"$minio_line"
        for name in "${bucket_names[@]}"; do
            if [[ ! -d "${f}/minio/${name}" ]]; then
                refuse "${manifest} names bucket '${name}', but ${f}/minio/${name}/ is not there — this archive is truncated."
            fi
        done
    fi
fi

realm_name=""
if ((keycloak_named)); then
    realm_name="$keycloak_line"
    if [[ -z "$realm_name" || ! -f "${f}/keycloak/${realm_name}-realm.json" ]]; then
        refuse "${manifest} names realm '${realm_name}', but ${f}/keycloak/${realm_name}-realm.json is not there — this archive is truncated."
    fi
fi

# The services that would break if Postgres were rewritten underneath them, scoped to the
# Selection. `compose stop` speaks services, not Modules, so the resolver answers in
# services: a Module that grows a helper must not be half-stopped.
stopped=""
if ((${#postgres_dumps[@]} > 0)); then
    stopped="$(./scripts/select.sh --dependents postgres)" || exit 1
fi

# The staging directory this run copies the objects into, named before the trap so the
# cleanup can reach it whichever step the run died in.
stage=""

# Started again on every exit path, including a failed write: a restore that died mid-stream
# must not also leave the stack half down. The staging directory goes on the same path,
# rather than at the end of its own step, because a failed run would otherwise leak a full
# copy of the objects into the container's writable layer where nothing removes it — and its
# removal is a warning rather than a failure, so a restore that has fully applied is never
# reported as a failed one (which would also skip the health wait below). Both are cleared
# as they run, so the explicit call on the happy path is not repeated by the trap.
on_exit() {
    if [[ -n "$stage" ]]; then
        local target="$stage"
        stage=""
        if ! compose exec -T minio rm -rf "$target"; then
            printf 'restore: warning: %s is left behind inside the minio container.\n' "$target" >&2
        fi
    fi
    local names="$stopped"
    stopped=""
    if [[ -n "$names" ]]; then
        local argv=()
        read -r -a argv <<<"${names//,/ }"
        compose start "${argv[@]}"
    fi
}
trap on_exit EXIT

if [[ -n "$stopped" ]]; then
    stop_argv=()
    read -r -a stop_argv <<<"${stopped//,/ }"
    compose stop "${stop_argv[@]}"
fi

# --- Postgres ---------------------------------------------------------------------------
# One dump per database, each carrying its own DROP DATABASE IF EXISTS / CREATE DATABASE.
# ON_ERROR_STOP=1 on every invocation: psql otherwise reports 0 for a stream whose
# statements errored, and the restore would be counted as applied.
for dump in ${postgres_dumps[@]+"${postgres_dumps[@]}"}; do
    echo "Restoring $(basename "$dump")"
    gunzip -c "$dump" | compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres
done

# --- Object storage ---------------------------------------------------------------------
if ((minio_named)); then
    stage="/tmp/devinfra-restore-$$"
    # The container-side program, written out here rather than inline at the call: the
    # credentials are read inside the container from its own environment, so they are
    # deliberately not interpolated by this shell — the idiom scripts/mc.sh established.
    # `mc mb --ignore-existing` recreates a bucket that was deleted since the capture, and
    # `--remove` takes away objects that are not in it, so the bucket ends up as captured
    # rather than as a union of the two.
    # shellcheck disable=SC2016
    MC_MIRROR_IN='
        mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
        mc mb --ignore-existing "local/$2"
        exec mc mirror --overwrite --remove "$1/$2" "local/$2"
    '
    # Cleared before the copy, not only after it: `docker cp` into a path that already
    # exists nests one level, so a leftover directory from a recycled PID would put the
    # objects at $stage/minio/<bucket> and make every mirror path below miss. This one is
    # fatal, unlike the cleanup on the exit path — it is correctness, not tidiness.
    compose exec -T minio rm -rf "$stage"
    compose cp "${f}/minio" "minio:${stage}"
    for bucket in ${bucket_names[@]+"${bucket_names[@]}"}; do
        echo "Restoring bucket ${bucket}"
        compose exec -T minio sh -c "$MC_MIRROR_IN" sh "$stage" "$bucket"
    done
fi

# --- Keycloak realm -----------------------------------------------------------------------
# Not imported, on purpose — see the header. Named so a reader of the output knows the realm
# came back with its database rather than wondering why the JSON was ignored.
if ((keycloak_named)); then
    echo "Keycloak realm '${realm_name}' is restored with the 'keycloak'"
    echo "  database; ${f}/keycloak/ is kept as a portable artefact, not imported."
fi

# What the archive never held, said out loud. backup.sh records every Module the Selection
# excluded at capture time, and a restore that says nothing about them leaves the reader to
# assume a Postgres-only archive covered the object storage and the realm it did not.
while IFS= read -r line; do
    [[ -n "$line" ]] && echo "Not in this archive: ${line}"
done < <(sed -n 's/^skipped: *//p' "$manifest")

on_exit
echo "Restored from $f"
# common.sh has already made the repository root the working directory. `exec` replaces this
# process, so the EXIT trap does not run — `on_exit` was already called above, which is why
# it clears what it has handled rather than assuming it runs exactly once.
exec ./scripts/wait-healthy.sh

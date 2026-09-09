# PostgreSQL — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here; a Module that is not in the current Selection never
# reaches this file.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

assert_contains "server is PostgreSQL 17" "PostgreSQL 17" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'select version();' 2>&1)"

assert_contains "pgvector distance operator works" "2.828" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select '[1,2,3]'::vector <-> '[3,2,1]'::vector;" 2>&1)"

assert_contains "pg_stat_statements is loaded" "pg_stat_statements" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'select extname from pg_extension;' 2>&1)"

assert_contains "custom postgresql.conf is in effect" "logical" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select current_setting('wal_level');" 2>&1)"

assert_contains "data checksums enabled" "on" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'show data_checksums;' 2>&1)"

for db in ${POSTGRES_EXTRA_DATABASES//,/ }; do
    assert_contains "extra database '${db}' exists" "1" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select count(*) from pg_database where datname='${db}';" 2>&1)"
done

assert_contains "write/read round-trip" "smoke-ok" \
    "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
        "create table if not exists smoke_probe(v text); truncate smoke_probe; insert into smoke_probe values ('smoke-ok'); select v from smoke_probe;" 2>&1)"

# The highest-severity gotcha in this Module's register, asserted rather than only
# described: the PGDATA path is version-specific — 17 keeps it at
# /var/lib/postgresql/data, 18 moved it to /var/lib/postgresql/18/docker — and a
# mount that does not cover it leaves the database on the container layer, where
# `down` throws it away with no error anywhere.
#
# The comparison runs inside the container, in awk, so no shell quoting crosses
# the boundary: the data directory is handed over with -v and /proc/mounts is read
# where it means something. "/" is excluded deliberately — it is the container
# layer, and it is a prefix of every path, so accepting it would make this assert
# nothing at all.
PGDATA_PATH="$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'show data_directory;' 2>&1)"
# $1 and $2 are awk's fields, not the shell's. Single quotes are what keeps them
# that way, which is the whole point of handing the path over with -v.
# shellcheck disable=SC2016
assert_contains "data directory sits inside a mounted volume" "mounted:" \
    "$(dc postgres awk -v d="${PGDATA_PATH}" '
        $2 != "/" && substr(d, 1, length($2)) == $2 &&
            (length(d) == length($2) || substr(d, length($2) + 1, 1) == "/") {
            print "mounted:" $2 " holds " d
            found = 1
            exit
        }
        END { if (!found) print "no mount covers " d }
    ' /proc/mounts 2>&1)"

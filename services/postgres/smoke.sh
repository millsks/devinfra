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

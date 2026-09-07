#!/usr/bin/env bash
# Create the databases listed in POSTGRES_EXTRA_DATABASES, each owned by
# POSTGRES_USER and seeded with the same extension set as the default database.
#
# Runs once, only while the data volume is empty. Comma-separated, no spaces:
#   POSTGRES_EXTRA_DATABASES=keycloak,app_test
set -euo pipefail

if [[ -z "${POSTGRES_EXTRA_DATABASES:-}" ]]; then
    echo "initdb: POSTGRES_EXTRA_DATABASES is unset; nothing to do"
    exit 0
fi

IFS=',' read -ra databases <<<"${POSTGRES_EXTRA_DATABASES}"

for database in "${databases[@]}"; do
    database="$(echo "${database}" | tr -d '[:space:]')"
    [[ -z "${database}" ]] && continue

    echo "initdb: creating database '${database}' owned by '${POSTGRES_USER}'"
    psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-SQL
	CREATE DATABASE "${database}" OWNER "${POSTGRES_USER}";
	SQL

    psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${database}" <<-'SQL'
	CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
	CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
	CREATE EXTENSION IF NOT EXISTS pg_trgm;
	CREATE EXTENSION IF NOT EXISTS btree_gin;
	CREATE EXTENSION IF NOT EXISTS vector;
	SQL
done

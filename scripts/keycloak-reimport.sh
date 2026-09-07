#!/usr/bin/env bash
# Drop the Keycloak database and re-import the realm JSON. Irreversible.
#
#   ./scripts/keycloak-reimport.sh
#
# Requires the exact word "reimport" on stdin. Keycloak's --import-realm only
# creates realms that do not already exist, so dropping the database is the only
# way an edited realm file takes effect.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

echo "Drops the 'keycloak' database and re-imports docker/keycloak/realms/."
echo "All realm changes made through the admin console will be lost."
confirm_word reimport

compose stop keycloak
compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c 'DROP DATABASE IF EXISTS keycloak WITH (FORCE);' \
    -c "CREATE DATABASE keycloak OWNER \"$POSTGRES_USER\";"
compose up -d keycloak
# common.sh has already made the repository root the working directory.
exec ./scripts/wait-healthy.sh

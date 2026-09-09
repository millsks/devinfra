#!/usr/bin/env bash
# Re-import the realm JSON over the live realm, replacing it. Irreversible.
#
#   ./scripts/keycloak-reimport.sh
#
# Requires the exact word "reimport" on stdin. `start-dev --import-realm` only
# creates realms that do not already exist, so an edited seed file needs this
# separate `kc.sh import --override` run to take effect. `--override` is
# remove-and-recreate rather than a merge: the named realm is replaced by what
# the JSON says, and runtime state in it that the JSON does not carry is lost.
# The `keycloak` database and every other realm survive — this drops nothing.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

echo "Replaces the '${KEYCLOAK_REALM}' realm from services/keycloak/seed/."
echo "Realm state not present in that JSON — users, sessions and clients added"
echo "through the admin console — will be lost. The 'keycloak' database and every"
echo "other realm are kept."
confirm_word reimport

# The import runs inside the container that is already up, which is what AD-12's
# stop-dependents → write → restart ordering comes to here: the write needs the
# server running, and the only dependent whose state goes stale is that server.
#
# --http-management-port 9999: the import is a second JVM in the same container
# and tries to bind the management interface, which the running server holds on
# 9000. Without a free port it exits non-zero after committing the realm, so
# `set -e` reports a failure for an import that actually landed. 9999 is a free
# in-container port; nothing publishes it.
compose exec -T keycloak /opt/keycloak/bin/kc.sh import \
    --file "/opt/keycloak/data/import/${KEYCLOAK_REALM}-realm.json" \
    --override true \
    --http-management-port 9999

# Mandatory and unconditional. The import commits to the database as a separate
# JVM that never attaches to the running server's cache, so without this restart
# the database and the admin API disagree silently — verified: Postgres returned
# the new realm while the admin API kept serving the old one (AD-12).
compose restart keycloak
# common.sh has already made the repository root the working directory.
exec ./scripts/wait-healthy.sh

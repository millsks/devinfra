#!/usr/bin/env bash
# Export the live realm back over services/keycloak/seed/.
#
#   ./scripts/keycloak-export.sh
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

# --http-management-port 9999: the export is a second JVM in the same container and tries to
# bind the management interface, which the running server holds on 9000. Without a free port
# it exits 1 with `Unable to start the management interface on 0.0.0.0:9000 / Address already
# in use` — the same collision scripts/keycloak-reimport.sh documents for `import` — after
# writing the file, so `set -e` reported a failure for an export that actually landed. 9999
# is a free in-container port; nothing publishes it.
compose exec keycloak /opt/keycloak/bin/kc.sh export \
    --dir /tmp/kc-export --realm "$KEYCLOAK_REALM" --users realm_file \
    --http-management-port 9999
compose cp "keycloak:/tmp/kc-export/${KEYCLOAK_REALM}-realm.json" \
    "services/keycloak/seed/${KEYCLOAK_REALM}-realm.json"
echo "Exported to services/keycloak/seed/${KEYCLOAK_REALM}-realm.json"
# The variable name is the message; expanding it would defeat the point.
# shellcheck disable=SC2016
echo 'Note: the client secret is now a literal, not ${KEYCLOAK_CLIENT_SECRET}.'

#!/usr/bin/env bash
# Export the live realm back over docker/keycloak/realms/.
#
#   ./scripts/keycloak-export.sh
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

compose exec keycloak /opt/keycloak/bin/kc.sh export \
    --dir /tmp/kc-export --realm "$KEYCLOAK_REALM" --users realm_file
compose cp "keycloak:/tmp/kc-export/${KEYCLOAK_REALM}-realm.json" \
    "docker/keycloak/realms/${KEYCLOAK_REALM}-realm.json"
echo "Exported to docker/keycloak/realms/${KEYCLOAK_REALM}-realm.json"
# The variable name is the message; expanding it would defeat the point.
# shellcheck disable=SC2016
echo 'Note: the client secret is now a literal, not ${KEYCLOAK_CLIENT_SECRET}.'

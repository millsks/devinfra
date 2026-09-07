#!/usr/bin/env bash
# Mint an access token from Keycloak via the CLI client's password grant.
#
#   ./scripts/token.sh            # user dev, password dev
#   ./scripts/token.sh alice s3cr3t
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

: "${KEYCLOAK_PORT:=8080}"

user="${1:-dev}"
password="${2:-dev}"

# Overridable so the request this builds can be asserted without a live realm.
read -r -a curl_argv <<<"${DEVINFRA_CURL:-curl}"

"${curl_argv[@]}" -s -X POST \
    "http://localhost:${KEYCLOAK_PORT}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token" \
    -d grant_type=password \
    -d client_id=devinfra-cli \
    -d "username=${user}" \
    -d "password=${password}" \
    -d scope=openid

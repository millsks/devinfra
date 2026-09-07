# Keycloak (OpenID Connect) — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here. KC and TOKEN_URL are built inside this file rather than
# outside a gate, which is where the central script had them.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

KC="http://${BIND}:${KEYCLOAK_PORT}"
TOKEN_URL="${KC}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"

DISCOVERY="$(curl -sf "${KC}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" 2>&1)"
assert_contains "OIDC discovery document is served" '"issuer"' "${DISCOVERY}"

# A stable issuer is what lets clients validate tokens against the
# discovery document; if KC_HOSTNAME drifts, every client breaks. The issuer
# is pinned to localhost by KC_HOSTNAME regardless of the bind address, so
# it is asserted against localhost rather than ${BIND}.
KC_ISSUER="http://localhost:${KEYCLOAK_PORT}/realms/${KEYCLOAK_REALM}"
assert_contains "issuer is pinned to ${KC_ISSUER}" "\"issuer\":\"${KC_ISSUER}\"" "${DISCOVERY}"
assert_contains "PKCE S256 is advertised" "S256" "${DISCOVERY}"

assert_contains "client_credentials grant (devinfra-api)" "access_token" \
    "$(curl -s -X POST "${TOKEN_URL}" \
        -d grant_type=client_credentials \
        -d client_id=devinfra-api \
        -d "client_secret=${KEYCLOAK_CLIENT_SECRET}" 2>&1)"

USER_TOKEN="$(curl -s -X POST "${TOKEN_URL}" \
    -d grant_type=password -d client_id=devinfra-cli \
    -d username=dev -d password=dev -d scope=openid 2>&1)"
assert_contains "password grant for user 'dev' (devinfra-cli)" "access_token" "${USER_TOKEN}"
assert_contains "refresh token issued" "refresh_token" "${USER_TOKEN}"

# Decode the access token payload to confirm the realm's mappers fired.
CLAIMS="$(printf '%s' "${USER_TOKEN}" |
    sed -n 's/.*"access_token":"[^.]*\.\([^.]*\)\..*/\1/p' |
    tr '_-' '/+' | { read -r p; printf '%s' "${p}$(printf '%*s' $(((4 - ${#p} % 4) % 4)) '' | tr ' ' '=')"; } |
    base64 -d 2>/dev/null)"
assert_contains "audience mapper puts devinfra-api in aud" 'devinfra-api' "${CLAIMS}"
assert_contains "realm roles present in token" 'app_admin' "${CLAIMS}"

assert_contains "health endpoint reports UP" '"status": "UP"' \
    "$(curl -sf "http://${BIND}:${KEYCLOAK_MGMT_PORT}/health/ready" 2>&1)"

#!/usr/bin/env bash
# Resolve a Selection to the Modules it needs, and print them (AD-16).
#
#   ./scripts/select.sh keycloak            # keycloak,mailpit,postgres
#   ./scripts/select.sh postgres redis      # postgres,redis
#   ./scripts/select.sh admin               # the admin group's closure
#   ./scripts/select.sh --all               # every Module
#   ./scripts/select.sh                     # whatever COMPOSE_PROFILES asks for
#   ./scripts/select.sh --selections        # the Selections this repository validates,
#                                           # one request per line — every Module, every
#                                           # group, then --all. scripts/lint-compose.sh
#                                           # and scripts/assert_config.py enumerate from
#                                           # this instead of the profile power set.
#
# A Selection names Modules or groups; what Compose has to be given is every Module in the
# transitive `depends_on` closure of that request. This is the supported way to reach
# Compose inside this repository: a raw `docker compose --profile keycloak` bypassing it
# exits 1 with `service "keycloak" depends on undefined service "postgres"`, and that is
# correct behaviour rather than a defect — Postgres cannot carry the `keycloak` profile
# without Keycloak editing Postgres's file, which AD-15 forbids.
#
# Output is one line on stdout: the Module names, sorted and comma-joined. There are three
# refusals — an empty request, a name no Module and no profile answers to, and a
# `depends_on` edge no Module owns — and each is exit 1 with a diagnostic on stderr and
# nothing at all on stdout, so a caller substituting this command never receives a partial
# Selection (AD-18, NFR-5).
#
# The closure itself is scripts/resolve_selection.py: it reads two `depends_on` spellings,
# the service-to-Module ownership rule and a profile index out of the module files, which
# bash parses badly and mypy --strict and the self-test hold to account there.
#
# Overridable seam, following the DEVINFRA_<TOOL> convention scripts/token.sh set:
#   DEVINFRA_PYTHON  the interpreter that runs the resolver  (default "python3")
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

read -r -a DEVINFRA_PYTHON_ARGV <<<"${DEVINFRA_PYTHON:-python3}"

# With no arguments the request comes from the environment, which is how every script that
# resolves the ambient Selection reaches this. An argument that is empty counts as none
# given: pixi task arguments always arrive as one argument even when the developer typed
# nothing, so `pixi run select` would otherwise refuse rather than print what the current
# .env asks for. An empty COMPOSE_PROFILES then reaches the resolver as an empty request,
# which is the refusal AD-18 asks for.
argv=()
for name in "$@"; do
    if [[ -n "$name" ]]; then
        argv+=("$name")
    fi
done
if ((${#argv[@]} == 0)); then
    argv=("${COMPOSE_PROFILES:-}")
fi

exec "${DEVINFRA_PYTHON_ARGV[@]}" scripts/resolve_selection.py "${argv[@]}"

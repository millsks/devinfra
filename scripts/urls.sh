#!/usr/bin/env bash
# Print the endpoints the ambient Selection publishes (ADR 0017).
#
#   ./scripts/urls.sh                       # whatever COMPOSE_PROFILES asks for
#   ./scripts/urls.sh postgres redis        # an explicit Selection
#   ./scripts/urls.sh --all                 # every Module
#
# The list is generated, never hand-maintained. scripts/endpoints.py reads each Module's
# own top-level `x-endpoints:` block and the root compose.yaml's `x-app-variables:`
# registry, and interpolates them against the environment scripts/lib/common.sh has
# already loaded .env into — so what prints here is your values, at your ports.
#
# A Module missing from this list is a Module missing an `x-endpoints:` block, which
# `pixi run lint-config` already refuses. The three copies this script used to keep — the
# fourteen port defaults, the twelve printf lines, and the header claiming completeness
# while omitting LOKI_PORT, TEMPO_PORT and KEYCLOAK_MGMT_PORT — are gone; the same
# generator writes docs/ENDPOINTS.md, and `pixi run lint-endpoints` fails the build when
# the two disagree.
#
# A wrapper rather than a deletion: `pixi run urls`, `make urls` and this path run
# standalone are three documented entry points, and the Makefile forwarding is pinned by
# set equality in scripts/lint_selftest.py.
#
# Overridable seam, following the DEVINFRA_<TOOL> convention scripts/token.sh set:
#   DEVINFRA_PYTHON  the interpreter that runs the generator  (default "python3")
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

read -r -a DEVINFRA_PYTHON_ARGV <<<"${DEVINFRA_PYTHON:-python3}"

# An argument that is empty counts as none given: pixi task arguments always arrive as one
# argument even when the developer typed nothing, so `pixi run urls` would otherwise ask
# the generator for a Selection named "".
argv=()
for name in "$@"; do
    if [[ -n "$name" ]]; then
        argv+=("$name")
    fi
done

# Two exec lines rather than one with `"${argv[@]}"`: expanding an empty array under
# `set -u` is an error on bash 3.2, which is still what `env bash` finds on a stock macOS.
if ((${#argv[@]} == 0)); then
    exec "${DEVINFRA_PYTHON_ARGV[@]}" scripts/endpoints.py
fi
exec "${DEVINFRA_PYTHON_ARGV[@]}" scripts/endpoints.py "${argv[@]}"

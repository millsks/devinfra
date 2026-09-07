#!/usr/bin/env bash
# Validate the Compose model for every Selection this repository can name.
#
#   ./scripts/lint-compose.sh
#
# `docker compose config -q` is this repository's dependency gate (ADR 0002):
# `depends_on` defaults to required, so a service naming a target the Selection
# under test does not define fails here and Compose itself names the undefined
# service.
#
# One Selection proves one selection. The Selections are the ones the resolver can
# name (ADR 0013): every Module's own closure — AD-6's closure-validity, which is
# what makes a Module liftable — every group's closure, and every Module at once.
# That replaced the power set over the declared profiles: fifteen declared profiles
# is 32 768 renders, which would never finish, and a cap would be arbitrary.
#
# This is the one script that does not resolve through scripts/lib/common.sh's
# select_profiles: it drives the Selection under test itself, one per iteration, so
# the value COMPOSE_PROFILES carries is exactly what is being validated rather than
# whatever the ambient environment asked for.
#
# Every Selection runs even after one fails: stopping at the first would report one
# defect where there may be four.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# This read no longer supplies the enumeration — that comes from `select.sh --selections`
# below, which parses the module files — and it has exactly two jobs left.
#
# First, `config --profiles` resolves the whole model, so a structurally invalid file fails
# here rather than in the loop, and Compose has already named the offending service on
# stderr by the time this runs. Second, its answer feeds the emptiness guard immediately
# after: a model that declares no profile at all cannot be right once every service carries
# its Module name, and treating that as "no profiles" is how this used to validate one
# combination and report success (DW-32).
#
# What it does NOT do is reconcile the model's profile list against the Selections. A group
# profile that the resolver cannot name would be a Selection nothing validates, and that gap
# is closed in scripts/assert_config.py's main(), which compares `declared_profiles()`
# against `resolve_selection.Graph.names()` and fails on the difference. Do not delete that
# reconciliation believing this read covers it; it does not.
#
# Cleared before the read: the ambient value is not what is being enumerated. Set with
# `export` rather than as an assignment prefix on the `compose` call — in bash a prefix
# on a function call persists past it, which is a trap worth not relying on either way.
# The loop below assigns its own value on every iteration.
export COMPOSE_PROFILES=""

declared=""
if ! declared="$(compose config --profiles)"; then
    printf 'lint-compose: could not read the declared profiles — the model above does not resolve.\n' >&2
    exit 1
fi

profiles=()
while IFS= read -r line; do
    if [[ -n "$line" ]]; then
        profiles+=("$line")
    fi
done <<<"$declared"

# A profile list that was read successfully and is empty is a failure in its own right:
# with a Module profile on every service the model declares at least one profile per
# Module, so an empty answer means the enumeration found nothing to validate and the
# Selections below would be built from a catalog nothing confirmed.
if ((${#profiles[@]} == 0)); then
    printf 'lint-compose: the model declares no profiles at all.\n' >&2
    printf '  Every service carries its own Module profile, so this cannot be right —\n' >&2
    printf '  a Selection list built from it would validate nothing.\n' >&2
    exit 1
fi

# The Selections, from the resolver rather than from a list here: one request per
# Module, one per group profile, and the all-Modules request. A list restated in this
# file would be the hand-maintained closure ADR 0013 exists to remove.
requests=()
while IFS= read -r line; do
    if [[ -n "$line" ]]; then
        requests+=("$line")
    fi
done < <(./scripts/select.sh --selections)

if ((${#requests[@]} == 0)); then
    printf 'lint-compose: the resolver named no Selections — there is nothing to validate.\n' >&2
    exit 1
fi

failed=()
resolved=""
for request in "${requests[@]}"; do
    # Assigned separately and then exported: `export X="$(cmd)"` takes the exit status of
    # `export`, so a resolver failure would be swallowed and the previous Selection would
    # be validated twice under the new name.
    resolved="$(./scripts/select.sh "$request")"
    export COMPOSE_PROFILES="$resolved"

    printf 'lint-compose: Selection %s -> %s\n' "$request" "$resolved"
    if ! compose config -q; then
        failed+=("$request")
    fi
done

if ((${#failed[@]} > 0)); then
    printf 'lint-compose: %d of %d Selection(s) failed:\n' "${#failed[@]}" "${#requests[@]}" >&2
    printf '  %s\n' "${failed[@]}" >&2
    exit 1
fi

printf 'lint-compose: OK — %d Selection(s) validated\n' "${#requests[@]}"

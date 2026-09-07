#!/usr/bin/env bash
# Validate the Compose model for every combination of the profiles it declares.
#
#   ./scripts/lint-compose.sh
#
# `docker compose config -q` is this repository's dependency gate (ADR 0002):
# `depends_on` defaults to required, so a service naming a target no profile
# combination defines fails here and Compose itself names the undefined service.
#
# One combination proves one selection. The profiles are read back from the model
# with `config --profiles` and every subset of them is checked, so a profile added
# to compose.yaml later cannot go unvalidated.
#
# Every combination runs even after one fails: stopping at the first would report
# one defect where there may be four.
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# Cleared for every child below, so the combination under test is exactly the
# --profile flags this script passes. Compose unions COMPOSE_PROFILES with
# --profile, so a .env selecting both profiles would otherwise make all four
# runs identical and the enumeration would prove nothing.
export COMPOSE_PROFILES=""

# Read the declared profiles from the model rather than hard-coding them, so a
# profile added to compose.yaml later cannot go unvalidated. `config --profiles`
# resolves the whole model, so a structurally invalid file fails here rather than
# in the loop — Compose has already named the offending service on stderr by the
# time this runs. An enumeration that could not be read is never treated as "no
# profiles": that would validate exactly one combination and report success.
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

count=${#profiles[@]}
combinations=$((1 << count))
failed=()

for ((mask = 0; mask < combinations; mask++)); do
    # argv is built with the subcommand appended last so it is never an empty
    # array: bash 3.2 errors on "${empty[@]}" under `set -u`.
    argv=()
    label=""
    for ((i = 0; i < count; i++)); do
        if ((mask & (1 << i))); then
            argv+=(--profile "${profiles[i]}")
            label="${label:+${label},}${profiles[i]}"
        fi
    done
    argv+=(config -q)
    [[ -n "$label" ]] || label="(none)"

    printf 'lint-compose: profiles %s\n' "$label"
    if ! compose "${argv[@]}"; then
        failed+=("$label")
    fi
done

if ((${#failed[@]} > 0)); then
    printf 'lint-compose: %d of %d profile combination(s) failed:\n' "${#failed[@]}" "$combinations" >&2
    printf '  %s\n' "${failed[@]}" >&2
    exit 1
fi

printf 'lint-compose: OK — %d profile combination(s) validated\n' "$combinations"

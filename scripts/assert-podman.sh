#!/usr/bin/env bash
# Prove the containers this run created are Podman's, by asking Podman.
#
#   DOCKER_HOST=unix:///run/podman/podman.sock ./scripts/assert-podman.sh
#
# A CI job that sets DOCKER_HOST and then quietly reaches a Docker daemon anyway
# — a stale environment, a socket that never bound, a task that bypassed the
# DEVINFRA_COMPOSE seam — starts the stack, passes every healthcheck and passes
# the smoke suite. Everything is green and nothing was verified on Podman.
#
# So the check is not "is podman installed" and not "what does the runtime call
# itself": both are answered by a string. It reads the containers Compose says it
# created, then asks Podman's own API to list what it is running. A container
# Docker created is not in Podman's list, and this fails naming it.
#
# Overridable seams, following the DEVINFRA_<TOOL> convention scripts/token.sh set:
#   DEVINFRA_PODMAN_URL  the Podman API endpoint  (falls back to DOCKER_HOST)
#   DEVINFRA_PODMAN      the podman command       (default "podman")
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

# DOCKER_HOST is what the rest of the job already points at the socket, so it is
# the natural default; DEVINFRA_PODMAN_URL exists for a run that wants to check a
# different endpoint than the one Compose used.
url="${DEVINFRA_PODMAN_URL:-${DOCKER_HOST:-}}"
if [[ -z "$url" ]]; then
    printf 'assert-podman: no Podman API endpoint configured.\n' >&2
    printf '  Set DEVINFRA_PODMAN_URL, or DOCKER_HOST, to the Podman socket.\n' >&2
    exit 1
fi

# The expected set comes through the compose seam, so it is exactly what the
# lifecycle tasks in this same run created — not a list restated here that could
# drift from the model. Captured into a variable rather than read from a process
# substitution, so a runtime that failed to answer fails this script instead of
# reading as "no containers".
listing="$(compose ps --all --format '{{.Name}}')"

expected=()
while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    expected+=("$line")
done <<<"$listing"

# An empty expected set is the silent skip this epic exists to remove: with no
# containers to look for, every comparison below passes and the job reports that
# Podman ran a stack it never started.
if ((${#expected[@]} == 0)); then
    printf 'assert-podman: the compose model reports no containers at all.\n' >&2
    printf '  There is nothing to verify. That is a failure, not a pass.\n' >&2
    exit 1
fi

read -r -a podman_argv <<<"${DEVINFRA_PODMAN:-podman}"

# --url implies --remote, so this talks to the socket's API rather than to any
# local state the CLI might have. `ps --all --format '{{.Names}}'` prints one bare
# container name per line.
observed="$("${podman_argv[@]}" --url "$url" ps --all --format '{{.Names}}')"

missing=()
for name in "${expected[@]}"; do
    if ! printf '%s\n' "$observed" | grep -qxF -- "$name"; then
        missing+=("$name")
    fi
done

if ((${#missing[@]} > 0)); then
    printf 'assert-podman: Podman at %s does not report %d of the %d container(s) this run created:\n' \
        "$url" "${#missing[@]}" "${#expected[@]}" >&2
    printf '  %s\n' "${missing[@]}" >&2
    printf 'The stack did not run under Podman.\n' >&2
    exit 1
fi

printf 'assert-podman: OK — Podman at %s reports all %d container(s) this run created\n' "$url" "${#expected[@]}"

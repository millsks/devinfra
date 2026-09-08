#!/usr/bin/env bash
# Block until every service the resolved Selection selects is up and healthy.
# Exits non-zero on timeout, listing what was not ready.
#
#   ./scripts/wait-healthy.sh
#
# This never returns optimistically. `compose ps` lists only containers that
# exist, so a service that crashed or was never created is simply absent from
# it — which is why readiness is judged against `compose config --services`,
# the set the resolved Selection actually selects, rather than against whatever
# happens to be running. `config --services` honours active profiles, so the
# Selection is resolved to its dependency closure first (AD-16); `compose ps`
# below does not, and needs nothing.
#
# Overridable for testing, defaults preserving the Makefile's bound:
#   WAIT_ATTEMPTS     polling attempts             (default 60)
#   WAIT_INTERVAL     seconds between attempts     (default 5)
#   DEVINFRA_COMPOSE  the compose command          (default "docker compose")
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The ambient Selection, resolved to its dependency closure before Compose sees it.
select_ambient

attempts="${WAIT_ATTEMPTS:-60}"
interval="${WAIT_INTERVAL:-5}"
not_ready=""

# The expected set. Fixed for the run: profiles do not change mid-wait.
expected="$(compose config --services)"
if [[ -z "${expected//[[:space:]]/}" ]]; then
    echo "No services are selected by the active profiles — nothing to wait for." >&2
    exit 1
fi

echo "Waiting for healthchecks..."

for _ in $(seq 1 "$attempts"); do
    # --all so a crashed or completed container is visible rather than absent.
    # Pipe-delimited because {{.Health}} is empty for a service without a
    # healthcheck, and whitespace splitting would then shift every later field.
    observed="$(compose ps --all --format '{{.Service}}|{{.Name}}|{{.State}}|{{.Health}}|{{.ExitCode}}')"

    # Both sets go through one awk pass on stdin, separated by a marker: BSD awk
    # rejects a multi-line value passed with -v.
    not_ready="$(
        {
            printf '===observed===\n%s\n===expected===\n%s\n' "$observed" "$expected"
        } | awk '
            $0 == "===observed===" { mode = "observed"; next }
            $0 == "===expected===" { mode = "expected"; next }
            NF == 0 { next }
            mode == "observed" {
                split($0, f, "|")
                seen[f[1]] = 1; name[f[1]] = f[2]
                state[f[1]] = f[3]; health[f[1]] = f[4]; code[f[1]] = f[5]
                next
            }
            {
                svc = $1
                if (!(svc in seen)) { print svc " (no container)"; next }
                label = (name[svc] == "" ? svc : name[svc])
                # A one-shot init container that exited 0 has done its job.
                if (state[svc] == "exited") {
                    if (code[svc] != "0") print label " (exited " code[svc] ")"
                    next
                }
                if (state[svc] != "running") { print label " (" state[svc] ")"; next }
                if (health[svc] != "" && health[svc] != "healthy") print label " (" health[svc] ")"
            }
        '
    )"

    if [[ -z "$not_ready" ]]; then
        echo "All containers running, all healthchecks passing."
        exit 0
    fi

    sleep "$interval"
done

echo "Timed out. Not ready:" >&2
printf '%s\n' "$not_ready" >&2
compose ps --all --format 'table {{.Name}}\t{{.State}}\t{{.Health}}' >&2
exit 1

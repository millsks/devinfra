#!/usr/bin/env bash
# End-to-end smoke test for the devinfra stack — the driver.
#
# Proves each service is not merely running but actually usable: real queries,
# real auth flows, a real object round-trip, a real email, and a real OTLP
# trace/log/metric traversing the collector into its backends.
#
#   ./scripts/smoke-test.sh
#
# The checks themselves are not here. Every Module owns its own
# `services/<module>/smoke.sh`, and this file enumerates them with a glob: it
# holds the counters, the helpers and the preflights, and knows no Module by
# name. Adding a Module to the catalog therefore adds its checks to this suite
# without editing Core — which is the whole point, and why a hard-coded module
# name here is a defect the self-test fails on.
#
# The module scripts are *sourced*, not executed. The counters are shell globals
# and the checks read .env values loaded below; a subprocess would need a
# counting protocol over stdout or exit codes to report anything at all.
#
# Exits non-zero if any check fails. Modules that are not currently running are
# skipped rather than failed — FR-5, and what a developer running a partial
# Selection wants. The `running` oracle is observational: it asks the runtime
# what is up, never COMPOSE_PROFILES or a resolver. A runtime that could not
# answer is not an observation, though, so a non-zero `ps` is fatal there rather
# than read as "nothing is running".
#
#   SMOKE_STRICT=1 ./scripts/smoke-test.sh
#
# Strict mode is the opposite bargain, and it is what CI runs. CI starts every
# profile, so a service that is not running is evidence the stack did not come
# up, not a selection the caller made. Under SMOKE_STRICT=1 a skip is recorded as
# a failure naming the absent service; nothing else about any check changes.
set -uo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

# The interpreter a check written in Python runs under, following the DEVINFRA_<TOOL>
# convention scripts/select.sh documents. Word-split once, deliberately, so an override
# may carry arguments. Module scripts read this; the driver itself only preflights it.
read -r -a DEVINFRA_PYTHON_ARGV <<<"${DEVINFRA_PYTHON:-python3}"

# Tools this suite cannot run without: curl for every HTTP check, openssl for the
# trace and span IDs the OTLP round-trip is identified by, base64 for decoding the
# OIDC access token whose claims the realm mappers are asserted on, and the
# interpreter above for the checks whose logic is too large to write twice in shell.
# A missing one fails here, naming it, before a single check runs — the alternative
# is a run in which a dozen checks fail for one reason nothing reports. This is a
# fail-loud preflight, not a presence branch: there is no arm that passes having
# checked nothing.
REQUIRED_TOOLS=(curl openssl base64 "${DEVINFRA_PYTHON_ARGV[0]}")
MISSING_TOOLS=""
for tool in "${REQUIRED_TOOLS[@]}"; do
    type -P "$tool" >/dev/null 2>&1 || MISSING_TOOLS="${MISSING_TOOLS:+${MISSING_TOOLS}, }${tool}"
done
if [[ -n "${MISSING_TOOLS}" ]]; then
    printf 'smoke-test: required tool not found on PATH: %s\n' "${MISSING_TOOLS}" >&2
    exit 1
fi

# The runtime itself. Without this, an unreachable runtime makes every `running`
# call answer "not running": the default suite would then exit 0 having checked
# nothing, and the strict suite would fail a dozen checks without once naming the
# actual cause. Compose's own diagnostic is left on stderr.
if ! compose version >/dev/null; then
    printf 'smoke-test: container runtime not reachable: %s\n' "${DEVINFRA_COMPOSE:-docker compose}" >&2
    exit 1
fi

# The catalog, read from the filesystem rather than from a list in this file:
# Core learns nothing about which Modules exist. A glob that matched nothing
# expands to itself, so the first entry is then a path that does not exist —
# a repository with no Modules, not a clean run. A suite that walked an empty set
# has verified nothing, which is the silent skip this repository keeps removing,
# so it is the third preflight rather than a quiet zero.
DRIVER_MODULE_SMOKES=(services/*/smoke.sh)
if [[ ! -f "${DRIVER_MODULE_SMOKES[0]}" ]]; then
    printf 'smoke-test: found no services/*/smoke.sh — a suite over zero Modules verifies nothing\n' >&2
    exit 1
fi

# Read by the module scripts this driver sources, never by the driver itself,
# which is all shellcheck can see from here.
# shellcheck disable=SC2034
BIND="${BIND_ADDRESS:-127.0.0.1}"
PASS=0
FAIL=0
SKIP=0

green() { printf '\033[32m%s\033[0m' "$1"; }
red() { printf '\033[31m%s\033[0m' "$1"; }
dim() { printf '\033[2m%s\033[0m' "$1"; }

pass() {
    printf '  %s  %s\n' "$(green PASS)" "$1"
    PASS=$((PASS + 1))
}
fail() {
    printf '  %s  %s\n' "$(red FAIL)" "$1"
    [[ -n "${2:-}" ]] && printf '        %s\n' "$(dim "$2")"
    FAIL=$((FAIL + 1))
}
skip() {
    # FR-5 and FR-16 disagree only about who is asking. A developer running a
    # partial selection wants a skip; CI, which starts every profile, must treat
    # one as evidence the stack did not come up. One branch, not a second suite.
    if [[ "${SMOKE_STRICT:-}" == "1" ]]; then
        fail "$1" "strict mode: nothing may be skipped"
        return
    fi
    printf '  %s  %s\n' "$(dim SKIP)" "$(dim "$1")"
    SKIP=$((SKIP + 1))
}

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# The service list is captured before it is filtered rather than piped straight
# into grep: shellcheck cannot see a sourced function through a pipeline, and the
# runtime call is the one thing here that must stay on the compose seam.
#
# A non-zero `ps` is fatal, and its stderr is left alone. `ps` ignores active
# profiles, but it still has to *load the project*, and an unresolved Selection —
# COMPOSE_PROFILES naming a Module without its dependencies — fails that with
# `depends on undefined service`. Discarding that status read as "nothing is
# running": every Module skipped and the suite exited 0 having verified nothing,
# which is the silent pass the three preflights above exist to remove. This file
# still consults no resolver (AD-16's documented exemption); it reports that the
# project did not load and names the variable to fix.
running() {
    local names status=0 shown="unset or empty"
    names="$(compose ps --services --filter status=running)" || status=$?
    if ((status != 0)); then
        # Built in a variable rather than nested expansions: `${V:+'$V'}${V:-unset}`
        # fires *both* arms for a set value and prints it twice.
        if [[ -n "${COMPOSE_PROFILES:-}" ]]; then
            shown="'${COMPOSE_PROFILES}'"
        fi
        printf '\nsmoke-test: the compose project did not load (ps exited %d).\n' "$status" >&2
        printf '  COMPOSE_PROFILES is %s.\n' "$shown" >&2
        printf '  A Selection naming a Module without its dependencies leaves a depends_on\n' >&2
        printf '  target undefined, and every check here would then read as "not running".\n' >&2
        # The command is the message; expanding it here would defeat the point.
        # shellcheck disable=SC2016
        printf '  Resolve it first: export COMPOSE_PROFILES="$(./scripts/select.sh <names>)".\n' >&2
        exit 1
    fi
    printf '%s\n' "$names" | grep -qx "$1"
}

# assert <label> <expected-substring> <actual>
assert_contains() {
    if [[ "$3" == *"$2"* ]]; then
        pass "$1"
    else
        fail "$1" "expected to contain '$2', got: ${3:0:160}"
    fi
}

dc() { compose exec -T "$@"; }

# A counted readiness assertion, for a Module whose image can carry no Docker
# healthcheck: its healthcheck.none marker says why, and this is then the only
# readiness gate the stack has for it. Strict mode fails on it.
assert_ready() {
    local label="$1" url="$2"
    for _ in $(seq 1 60); do
        if curl -sf "$url" >/dev/null 2>&1; then
            pass "$label"
            return
        fi
        sleep 2
    done
    fail "$label" "${url} did not report ready within 120s"
}

# The silent half of the same idea, and deliberately not a check: it counts
# nothing, prints nothing and never fails. Modules run in glob order, so a Module
# that fans out to a backend alphabetically after it would otherwise read a
# not-yet-ready backend as its own lost data. Restoring that diagnostic is what
# this is for; it is never a substitute for a counted assertion.
await_url() {
    # The same 120s budget assert_ready spends. A shorter one here would silently move
    # the bar: a backend that becomes ready between the two would fail a counted check in
    # the Module that pre-waited on it, where the old central ordering passed.
    for _ in $(seq 1 60); do
        curl -sf "$1" >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

# Register a check to run after every Module's script has been sourced, rather than
# where it was written.
#
# The seam exists for one shape of check: one whose subject is a side effect *another*
# Module's checks produce. The Modules are enumerated by a glob, so their order is
# alphabetical and nothing may reorder or rename them to fix it; a Module whose
# verification depends on what a later Module does would otherwise have to emit that
# side effect a second time itself, which duplicates a payload, doubles a wait and
# invents a coupling that no `depends_on` edge declares. Registering the function here
# instead states the real constraint — this check runs last because it needs the whole
# suite to have run — and costs the driver ten lines that name no Module.
#
# The function is called in registration order, in this same shared namespace, and its
# `pass`/`fail`/`skip` calls count exactly as an inline one's do. See
# docs/adr/0015-deferred-smoke-checks.md.
#
#   defer <function-name>
DRIVER_DEFERRED=()
defer() { DRIVER_DEFERRED+=("$1"); }

# Liveness over HTTP, un-gated: the driver has already established the Module is
# running before it sources the file that calls this.
check_http() {
    # `local`, because every module script this driver sources writes into the same
    # namespace: a plain global here is a name a Module could capture.
    local driver_http_code
    driver_http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$2" 2>&1)"
    if [[ "${driver_http_code}" =~ ^(200|302)$ ]]; then
        pass "$1 responds on $2 (HTTP ${driver_http_code})"
    else
        fail "$1 responds on $2" "HTTP ${driver_http_code}"
    fi
}

# ===========================================================================
# Each Module in turn, in glob order. A Module that is not in the current
# Selection reports `skip`, never a pass and never a fail — and under
# SMOKE_STRICT=1 that skip is a failure naming it.
# ===========================================================================
# Every name the driver keeps across an iteration is prefixed, because a sourced module
# script writes into this very namespace: a Module assigning a bare `module=` would
# otherwise break enumeration for every Module after it in glob order, silently.
for driver_module_smoke in "${DRIVER_MODULE_SMOKES[@]}"; do
    driver_module="$(basename "$(dirname "${driver_module_smoke}")")"
    section "${driver_module}"
    if running "${driver_module}"; then
        # A module script that does not parse must not vanish. `source` would return
        # non-zero, the loop would carry on, and that Module would contribute no pass, no
        # fail and no skip while the suite still exited 0 — the silent skip in its purest
        # form. Parsed first rather than judged by the source's own exit status, which is
        # only whatever the file's last command happened to return.
        if bash -n "${driver_module_smoke}" 2>/dev/null; then
            # Sourced, not executed; the path is only known at runtime.
            # shellcheck source=/dev/null
            source "${driver_module_smoke}"
        else
            fail "${driver_module} smoke checks could not be sourced" \
                "${driver_module_smoke} is not readable as bash — none of its checks ran"
        fi
    else
        skip "${driver_module} not running"
    fi
done

# ===========================================================================
# The deferred checks, in registration order and before the summary, so their results
# are counted like every other. A registered function that never ran would be a check
# the suite reported nothing about while still exiting 0 — the silent skip this
# repository keeps removing — so the self-test pins that these lines execute.
#
# A Module that was not in the Selection never had its script sourced, so it registered
# nothing: an empty set here is the normal case and not an error, unlike the empty
# Module set above. Guarded on the count because `"${array[@]}"` on an empty array is
# an unbound-variable error under `set -u` in older bash.
#
# A name that is not a defined function is a failure naming it, never a bare
# `command not found` on stderr: a typo, or a module script that stopped part-way
# through and never got as far as the definition, would otherwise leave the registered
# check reporting nothing while the suite exited 0.
if ((${#DRIVER_DEFERRED[@]} > 0)); then
    section "deferred"
    for driver_deferred in "${DRIVER_DEFERRED[@]}"; do
        if declare -F "${driver_deferred}" >/dev/null 2>&1; then
            "${driver_deferred}"
        else
            fail "deferred check '${driver_deferred}'" \
                "no function by that name is defined — the check was registered and never ran"
        fi
    done
fi

# ===========================================================================
printf '\n\033[1m%s\033[0m\n' "Summary"
if ((FAIL > 0)); then
    FAIL_TEXT="$(red "${FAIL}")"
else
    FAIL_TEXT="0"
fi
printf '  %s passed, %s failed, %s skipped\n\n' "$(green "${PASS}")" "${FAIL_TEXT}" "${SKIP}"
((FAIL == 0)) || exit 1

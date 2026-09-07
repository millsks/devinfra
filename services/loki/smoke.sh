# Loki — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here.
#
# This Module carries no Docker healthcheck — the pinned image is distroless, and
# services/loki/healthcheck.none says so — which makes this the only readiness
# gate the stack has for it. Under SMOKE_STRICT=1 it is a failure, not a skip.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

assert_ready "Loki reports ready" "http://${BIND}:${LOKI_PORT}/ready"

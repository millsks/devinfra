# Prometheus — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here.
#
# This check used to live inside the collector's block, gated on
# `running otel-collector` and carrying no `else` arm at all: with the collector
# absent it neither passed, failed nor skipped, and nothing said so. It is
# Prometheus's own property, so it lives here, and the `else` arm it never had is
# now the driver's Module gate.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

DOWN="$(curl -sf "http://${BIND}:${PROMETHEUS_PORT}/api/v1/targets?state=active" 2>/dev/null |
    tr ',' '\n' | grep -c '"health":"down"')"
if [[ "${DOWN}" == "0" ]]; then
    pass "all Prometheus scrape targets healthy"
else
    fail "all Prometheus scrape targets healthy" "${DOWN} target(s) down — see http://${BIND}:${PROMETHEUS_PORT}/targets"
fi

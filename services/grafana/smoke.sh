# Grafana — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate on Grafana here. The `running` calls that remain gate only the
# checks that genuinely reach a backing Module — the datasource *health* calls,
# which Grafana answers by connecting. Grafana can be up with a datasource whose
# backend is out of the current Selection, and that is a skip, not a failure
# (FR-5).
#
# shellcheck shell=bash
# shellcheck disable=SC2154

GF="http://${GRAFANA_ADMIN_USER}:${GRAFANA_ADMIN_PASSWORD}@${BIND}:${GRAFANA_PORT}"

# The driver runs Modules in glob order, so this file precedes services/loki/,
# services/prometheus/ and services/tempo/ and their counted readiness polls. The
# datasource health calls below reach those backends through Grafana, so a
# backend that is up but not yet ready would read as a broken datasource. Silent,
# counts nothing, prints nothing.
running prometheus && await_url "http://${BIND}:${PROMETHEUS_PORT}/-/healthy"
running loki && await_url "http://${BIND}:${LOKI_PORT}/ready"
running tempo && await_url "http://${BIND}:${TEMPO_PORT}/ready"

# Un-gated, deliberately: this loop reads Grafana's own provisioning through Grafana's
# API and never touches the backend, so it answers the same whether or not the backing
# Module is in the current Selection. Gating it would turn four passes into skips for no
# reason and rewrite a check the carve only moved.
for uid in prometheus loki tempo postgres; do
    BODY="$(curl -sf "${GF}/api/datasources/uid/${uid}" 2>/dev/null)"
    assert_contains "datasource '${uid}' provisioned" "\"uid\":\"${uid}\"" "${BODY}"
done
# Tempo's backend implements no health endpoint, so it is checked by
# provisioning presence above rather than by /health.
for uid in prometheus loki postgres; do
    if running "${uid}"; then
        assert_contains "datasource '${uid}' connects" "OK" \
            "$(curl -sf "${GF}/api/datasources/uid/${uid}/health" 2>/dev/null)"
    else
        skip "datasource '${uid}' connects — ${uid} not running"
    fi
done

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

# The dashboard, read back through Grafana rather than off the filesystem. `provisioned`
# is the observable difference between the two ways a dashboard can be present: true means
# the file provider loaded it from the read-only bind mount and re-reads it on every start,
# where a dashboard someone saved into grafana-data reports false and would survive a
# `pixi run down && pixi run up` only because the volume did.
assert_contains "dashboard 'devinfra-overview' provisioned from the bind mount" '"provisioned":true' \
    "$(curl -sf "${GF}/api/dashboards/uid/devinfra-overview" 2>/dev/null)"

# ...and that its panels actually return something. A provisioned dashboard that loads is
# not a dashboard that works: every panel can resolve to empty and Grafana still renders
# three tidy "No data" boxes, which is the state this Module shipped in before.
#
# Deferred, because its subject is telemetry another Module's checks inject and the Modules
# run in glob order, so that injection has not happened yet at this point in the file. The
# alternatives — emitting a second copy of the telemetry from here, or filing Grafana's
# verification under the Module that emits it — are argued out in
# docs/adr/0015-deferred-smoke-checks.md.
#
# MARKER and the deliberate coupling: the driver sources every Module's script into one
# shared global namespace, and MARKER is the `service.name` the observability injector
# tagged the trace, log and metric it posted with. Reading it here is on purpose, not an
# accident of scope — the requirement is that these panels are proved against the telemetry
# the smoke suite itself injected, so the value has to come from whoever injected it. It is
# unset whenever that Module was not in the Selection, which is a skip below.
grafana_dashboard_panels() {
    local absent="" name output signal
    for name in otel-collector prometheus loki tempo; do
        running "${name}" || absent="${absent:+${absent}, }${name}"
    done
    if [[ -n "${absent}" ]]; then
        skip "dashboard panels return data — ${absent} absent from this Selection"
        return
    fi
    if [[ -z "${MARKER:-}" ]]; then
        skip "dashboard panels return data — no telemetry marker was injected to query for"
        return
    fi
    # Queries are read out of the shipped dashboard JSON and run through Grafana's
    # datasource proxy, so a panel edited into a broken query fails here.
    output="$("${DEVINFRA_PYTHON_ARGV[@]}" scripts/check_dashboards.py \
        --dashboards-dir services/grafana/dashboards \
        --grafana-url "${GF}" \
        --service "${MARKER}" 2>&1)"
    for signal in traces logs metrics; do
        assert_contains "dashboard panel for ${signal} returns data" "${signal}: OK" "${output}"
    done
}
defer grafana_dashboard_panels

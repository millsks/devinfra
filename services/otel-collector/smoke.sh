# Observability pipeline (OTLP -> Tempo / Loki / Prometheus) — this Module's
# smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate on the collector here. The `running` calls that remain gate the
# *backends* this Module fans out to, which are separate Modules and may be out
# of the current Selection.
#
# The whole emit-and-poll block stays in one file deliberately: the emitter's
# TRACE_ID and MARKER are what all three verdicts read, and splitting them would
# triple the 60s wait.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

# The driver runs Modules in glob order, so this file precedes services/tempo/
# and services/prometheus/. Their readiness polls therefore no longer run first,
# and a backend that never came up would read as a lost trace rather than as
# itself. `await_url` restores that diagnostic silently: it counts nothing, prints
# nothing and never fails a check — the counted readiness assertions still belong
# to Loki's and Tempo's own smoke.sh.
running loki && await_url "http://${BIND}:${LOKI_PORT}/ready"
running tempo && await_url "http://${BIND}:${TEMPO_PORT}/ready"
running prometheus && await_url "http://${BIND}:${PROMETHEUS_PORT}/-/healthy"

TRACE_ID="$(openssl rand -hex 16)"
SPAN_ID="$(openssl rand -hex 8)"
NOW_S="$(date +%s)"
NOW_NS="${NOW_S}000000000"
END_NS="$((NOW_S + 1))000000000"
MARKER="devinfra-smoke-${TRACE_ID:0:8}"
OTLP="http://${BIND}:${OTEL_HTTP_PORT}"

post_otlp() {
    curl -s -o /dev/null -w '%{http_code}' -X POST "${OTLP}/v1/$1" \
        -H 'Content-Type: application/json' -d "$2"
}

# Build each payload into a variable first. A brace-heavy literal written
# inline inside "$( ... )" gets brace-expanded by the shell, which silently
# shreds the JSON into fragments and fires one request per fragment.
RESOURCE="\"resource\":{\"attributes\":[{\"key\":\"service.name\",\"value\":{\"stringValue\":\"${MARKER}\"}}]}"

TRACE_PAYLOAD="{\"resourceSpans\":[{${RESOURCE},\"scopeSpans\":[{\"scope\":{\"name\":\"smoke\"},\"spans\":[{\"traceId\":\"${TRACE_ID}\",\"spanId\":\"${SPAN_ID}\",\"name\":\"smoke-span\",\"kind\":2,\"startTimeUnixNano\":\"${NOW_NS}\",\"endTimeUnixNano\":\"${END_NS}\",\"status\":{\"code\":1}}]}]}]}"
assert_contains "collector accepts OTLP traces" "200" "$(post_otlp traces "${TRACE_PAYLOAD}")"

LOG_PAYLOAD="{\"resourceLogs\":[{${RESOURCE},\"scopeLogs\":[{\"scope\":{\"name\":\"smoke\"},\"logRecords\":[{\"timeUnixNano\":\"${NOW_NS}\",\"severityNumber\":9,\"severityText\":\"INFO\",\"body\":{\"stringValue\":\"smoke-ok trace_id=${TRACE_ID}\"},\"traceId\":\"${TRACE_ID}\",\"spanId\":\"${SPAN_ID}\"}]}]}]}"
assert_contains "collector accepts OTLP logs" "200" "$(post_otlp logs "${LOG_PAYLOAD}")"

METRIC_PAYLOAD="{\"resourceMetrics\":[{${RESOURCE},\"scopeMetrics\":[{\"scope\":{\"name\":\"smoke\"},\"metrics\":[{\"name\":\"devinfra_smoke_counter\",\"unit\":\"1\",\"sum\":{\"aggregationTemporality\":2,\"isMonotonic\":true,\"dataPoints\":[{\"asInt\":\"1\",\"startTimeUnixNano\":\"${NOW_NS}\",\"timeUnixNano\":\"${NOW_NS}\"}]}}]}]}]}"
assert_contains "collector accepts OTLP metrics" "200" "$(post_otlp metrics "${METRIC_PAYLOAD}")"

# Tempo needs a moment to move the span from WAL into a searchable block,
# and Prometheus needs at least one 15s scrape of the collector.
printf '        %s' "$(dim 'waiting up to 60s for backends to ingest')"
TEMPO_OK=""
LOKI_OK=""
PROM_OK=""
for _ in $(seq 1 12); do
    sleep 5
    printf '.'
    [[ -z "${TEMPO_OK}" ]] && running tempo &&
        curl -sf "http://${BIND}:${TEMPO_PORT}/api/traces/${TRACE_ID}" 2>/dev/null | grep -q 'smoke-span' && TEMPO_OK=1
    if [[ -z "${LOKI_OK}" ]] && running loki; then
        L_END="$(date +%s)000000000"
        L_START="$((NOW_S - 300))000000000"
        curl -sfG "http://${BIND}:${LOKI_PORT}/loki/api/v1/query_range" \
            --data-urlencode "query={service_name=\"${MARKER}\"}" \
            --data-urlencode "start=${L_START}" --data-urlencode "end=${L_END}" 2>/dev/null |
            grep -q 'smoke-ok' && LOKI_OK=1
    fi
    if [[ -z "${PROM_OK}" ]] && running prometheus; then
        # /api/v1/series is step-independent, unlike query_range, so a
        # short-lived series cannot be stepped over.
        curl -sfG "http://${BIND}:${PROMETHEUS_PORT}/api/v1/series" \
            --data-urlencode 'match[]=devinfra_smoke_counter_total' \
            --data-urlencode "start=$((NOW_S - 300))" --data-urlencode "end=$(date +%s)" 2>/dev/null |
            grep -q "${MARKER}" && PROM_OK=1
    fi
    [[ -n "${TEMPO_OK}" && -n "${LOKI_OK}" && -n "${PROM_OK}" ]] && break
done
printf '\n'

if running tempo; then
    if [[ -n "${TEMPO_OK}" ]]; then
        pass "trace stored in Tempo and retrievable by ID"
    else
        fail "trace stored in Tempo and retrievable by ID" "trace ${TRACE_ID} not found"
    fi
else skip "tempo not running"; fi

if running loki; then
    if [[ -n "${LOKI_OK}" ]]; then
        pass "log line stored in Loki and queryable by label"
    else
        fail "log line stored in Loki and queryable by label" "no stream for service_name=${MARKER}"
    fi
else skip "loki not running"; fi

if running prometheus; then
    if [[ -n "${PROM_OK}" ]]; then
        pass "metric scraped from collector into Prometheus"
    else
        fail "metric scraped from collector into Prometheus" "series devinfra_smoke_counter_total absent"
    fi
else skip "prometheus not running"; fi

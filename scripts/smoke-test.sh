#!/usr/bin/env bash
# End-to-end smoke test for the devinfra stack.
#
# Proves each service is not merely running but actually usable: real queries,
# real auth flows, a real object round-trip, a real email, and a real OTLP
# trace/log/metric traversing the collector into Tempo/Loki/Prometheus.
#
#   ./scripts/smoke-test.sh
#
# Exits non-zero if any check fails. Services belonging to a profile that is not
# currently running are skipped rather than failed.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a

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
    printf '  %s  %s\n' "$(dim SKIP)" "$(dim "$1")"
    SKIP=$((SKIP + 1))
}

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

running() { docker compose ps --services --filter status=running 2>/dev/null | grep -qx "$1"; }

# assert <label> <expected-substring> <actual>
assert_contains() {
    if [[ "$3" == *"$2"* ]]; then
        pass "$1"
    else
        fail "$1" "expected to contain '$2', got: ${3:0:160}"
    fi
}

dc() { docker compose exec -T "$@"; }

# ===========================================================================
section "PostgreSQL"
# ===========================================================================
if running postgres; then
    assert_contains "server is PostgreSQL 17" "PostgreSQL 17" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'select version();' 2>&1)"

    assert_contains "pgvector distance operator works" "2.828" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select '[1,2,3]'::vector <-> '[3,2,1]'::vector;" 2>&1)"

    assert_contains "pg_stat_statements is loaded" "pg_stat_statements" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'select extname from pg_extension;' 2>&1)"

    assert_contains "custom postgresql.conf is in effect" "logical" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select current_setting('wal_level');" 2>&1)"

    assert_contains "data checksums enabled" "on" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc 'show data_checksums;' 2>&1)"

    for db in ${POSTGRES_EXTRA_DATABASES//,/ }; do
        assert_contains "extra database '${db}' exists" "1" \
            "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "select count(*) from pg_database where datname='${db}';" 2>&1)"
    done

    assert_contains "write/read round-trip" "smoke-ok" \
        "$(dc postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
            "create table if not exists smoke_probe(v text); truncate smoke_probe; insert into smoke_probe values ('smoke-ok'); select v from smoke_probe;" 2>&1)"
else
    skip "postgres not running"
fi

# ===========================================================================
section "Redis"
# ===========================================================================
if running redis; then
    assert_contains "responds to authenticated PING" "PONG" \
        "$(dc redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning ping 2>&1)"

    assert_contains "rejects unauthenticated clients" "NOAUTH" \
        "$(dc redis redis-cli ping 2>&1)"

    assert_contains "AOF persistence is on" "yes" \
        "$(dc redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning config get appendonly 2>&1)"

    # noeviction matters: this instance is a Celery broker, and an LRU policy
    # would silently discard queued tasks under memory pressure.
    assert_contains "maxmemory-policy is noeviction" "noeviction" \
        "$(dc redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning config get maxmemory-policy 2>&1)"

    dc redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning -n "${REDIS_BROKER_DB}" set smoke-probe smoke-ok >/dev/null 2>&1
    assert_contains "broker db ${REDIS_BROKER_DB} write/read round-trip" "smoke-ok" \
        "$(dc redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning -n "${REDIS_BROKER_DB}" get smoke-probe 2>&1)"
else
    skip "redis not running"
fi

# ===========================================================================
section "Keycloak (OpenID Connect)"
# ===========================================================================
KC="http://${BIND}:${KEYCLOAK_PORT}"
TOKEN_URL="${KC}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"

if running keycloak; then
    DISCOVERY="$(curl -sf "${KC}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" 2>&1)"
    assert_contains "OIDC discovery document is served" '"issuer"' "${DISCOVERY}"

    # A stable issuer is what lets clients validate tokens against the
    # discovery document; if KC_HOSTNAME drifts, every client breaks. The issuer
    # is pinned to localhost by KC_HOSTNAME regardless of the bind address, so
    # it is asserted against localhost rather than ${BIND}.
    KC_ISSUER="http://localhost:${KEYCLOAK_PORT}/realms/${KEYCLOAK_REALM}"
    assert_contains "issuer is pinned to ${KC_ISSUER}" "\"issuer\":\"${KC_ISSUER}\"" "${DISCOVERY}"
    assert_contains "PKCE S256 is advertised" "S256" "${DISCOVERY}"

    assert_contains "client_credentials grant (devinfra-api)" "access_token" \
        "$(curl -s -X POST "${TOKEN_URL}" \
            -d grant_type=client_credentials \
            -d client_id=devinfra-api \
            -d "client_secret=${KEYCLOAK_CLIENT_SECRET}" 2>&1)"

    USER_TOKEN="$(curl -s -X POST "${TOKEN_URL}" \
        -d grant_type=password -d client_id=devinfra-cli \
        -d username=dev -d password=dev -d scope=openid 2>&1)"
    assert_contains "password grant for user 'dev' (devinfra-cli)" "access_token" "${USER_TOKEN}"
    assert_contains "refresh token issued" "refresh_token" "${USER_TOKEN}"

    # Decode the access token payload to confirm the realm's mappers fired.
    CLAIMS="$(printf '%s' "${USER_TOKEN}" |
        sed -n 's/.*"access_token":"[^.]*\.\([^.]*\)\..*/\1/p' |
        tr '_-' '/+' | { read -r p; printf '%s' "${p}$(printf '%*s' $(((4 - ${#p} % 4) % 4)) '' | tr ' ' '=')"; } |
        base64 -d 2>/dev/null)"
    assert_contains "audience mapper puts devinfra-api in aud" 'devinfra-api' "${CLAIMS}"
    assert_contains "realm roles present in token" 'app_admin' "${CLAIMS}"

    assert_contains "health endpoint reports UP" '"status": "UP"' \
        "$(curl -sf "http://${BIND}:${KEYCLOAK_MGMT_PORT}/health/ready" 2>&1)"
else
    skip "keycloak not running"
fi

# ===========================================================================
section "MinIO"
# ===========================================================================
if running minio; then
    dc minio mc alias set smoke "http://127.0.0.1:9000" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null 2>&1
    BUCKET_LIST="$(dc minio mc ls smoke 2>&1)"
    for bucket in ${MINIO_BUCKETS//,/ }; do
        assert_contains "bucket '${bucket}' provisioned" "${bucket}" "${BUCKET_LIST}"
    done

    FIRST_BUCKET="${MINIO_BUCKETS%%,*}"
    assert_contains "object put/get round-trip" "smoke-ok" \
        "$(dc minio sh -c "echo smoke-ok > /tmp/smoke.txt && mc cp /tmp/smoke.txt smoke/${FIRST_BUCKET}/smoke.txt >/dev/null 2>&1 && mc cat smoke/${FIRST_BUCKET}/smoke.txt" 2>&1)"

    assert_contains "versioning enabled on '${FIRST_BUCKET}'" "versioning is enabled" \
        "$(dc minio mc version info "smoke/${FIRST_BUCKET}" 2>&1)"
else
    skip "minio not running"
fi

# ===========================================================================
section "Mailpit"
# ===========================================================================
if running mailpit; then
    BEFORE="$(curl -sf "http://${BIND}:${MAILPIT_UI_PORT}/api/v1/messages?limit=1" 2>/dev/null |
        sed -n 's/.*"messages_count":\([0-9]*\).*/\1/p')"
    BEFORE="${BEFORE:-0}"

    # Speak just enough SMTP over the raw socket to avoid a Python dependency.
    if exec 3<>"/dev/tcp/${BIND}/${MAILPIT_SMTP_PORT}" 2>/dev/null; then
        {
            printf 'EHLO smoke\r\n'
            printf 'MAIL FROM:<smoke@example.com>\r\n'
            printf 'RCPT TO:<dev@example.com>\r\n'
            printf 'DATA\r\n'
            printf 'Subject: devinfra smoke test\r\n\r\nsmoke-ok\r\n.\r\n'
            printf 'QUIT\r\n'
            sleep 1
        } >&3
        cat <&3 >/dev/null 2>&1
        exec 3<&- 3>&-
        sleep 1

        AFTER="$(curl -sf "http://${BIND}:${MAILPIT_UI_PORT}/api/v1/messages?limit=1" 2>/dev/null |
            sed -n 's/.*"messages_count":\([0-9]*\).*/\1/p')"
        AFTER="${AFTER:-0}"
        if ((AFTER > BEFORE)); then
            pass "SMTP message accepted and stored (${BEFORE} -> ${AFTER})"
        else
            fail "SMTP message accepted and stored" "count did not increase (${BEFORE} -> ${AFTER})"
        fi
    else
        fail "SMTP port ${MAILPIT_SMTP_PORT} reachable" "could not open socket"
    fi
else
    skip "mailpit not running"
fi

# ===========================================================================
section "Observability pipeline (OTLP -> Tempo / Loki / Prometheus)"
# ===========================================================================
if running otel-collector; then
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

    if running prometheus; then
        DOWN="$(curl -sf "http://${BIND}:${PROMETHEUS_PORT}/api/v1/targets?state=active" 2>/dev/null |
            tr ',' '\n' | grep -c '"health":"down"')"
        if [[ "${DOWN}" == "0" ]]; then
            pass "all Prometheus scrape targets healthy"
        else
            fail "all Prometheus scrape targets healthy" "${DOWN} target(s) down — see http://${BIND}:${PROMETHEUS_PORT}/targets"
        fi
    fi
else
    skip "otel-collector not running (observability profile off)"
fi

# ===========================================================================
section "Grafana"
# ===========================================================================
if running grafana; then
    GF="http://${GRAFANA_ADMIN_USER}:${GRAFANA_ADMIN_PASSWORD}@${BIND}:${GRAFANA_PORT}"
    for uid in prometheus loki tempo postgres; do
        BODY="$(curl -sf "${GF}/api/datasources/uid/${uid}" 2>/dev/null)"
        assert_contains "datasource '${uid}' provisioned" "\"uid\":\"${uid}\"" "${BODY}"
    done
    # Tempo's backend implements no health endpoint, so it is checked by
    # provisioning presence above rather than by /health.
    for uid in prometheus loki postgres; do
        assert_contains "datasource '${uid}' connects" "OK" \
            "$(curl -sf "${GF}/api/datasources/uid/${uid}/health" 2>/dev/null)"
    done
else
    skip "grafana not running"
fi

# ===========================================================================
section "Admin UIs"
# ===========================================================================
check_http() {
    if running "$1"; then
        CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$2" 2>&1)"
        if [[ "${CODE}" =~ ^(200|302)$ ]]; then
            pass "$1 responds on $2 (HTTP ${CODE})"
        else
            fail "$1 responds on $2" "HTTP ${CODE}"
        fi
    else
        skip "$1 not running"
    fi
}
check_http pgadmin "http://${BIND}:${PGADMIN_PORT}/misc/ping"
check_http redisinsight "http://${BIND}:${REDISINSIGHT_PORT}/"
check_http flower "http://${BIND}:${FLOWER_PORT}/api/workers"

# ===========================================================================
printf '\n\033[1m%s\033[0m\n' "Summary"
if ((FAIL > 0)); then
    FAIL_TEXT="$(red "${FAIL}")"
else
    FAIL_TEXT="0"
fi
printf '  %s passed, %s failed, %s skipped\n\n' "$(green "${PASS}")" "${FAIL_TEXT}" "${SKIP}"
((FAIL == 0)) || exit 1

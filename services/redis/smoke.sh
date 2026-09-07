# Redis — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

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

# Flower — this Module's smoke check.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here — `check_http` no longer carries one either.
#
# /api/workers answers with an empty list when no Celery worker has connected,
# which is this stack's normal state — the check proves the API is reachable with
# the broker attached, not that a worker exists.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

check_http flower "http://${BIND}:${FLOWER_PORT}/api/workers"

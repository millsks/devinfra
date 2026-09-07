# pgAdmin — this Module's smoke check.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here — `check_http` no longer carries one either.
#
# This is liveness, not function: /misc/ping answers 200 from a pgAdmin that
# came up with no server registration at all. See gotchas.md. Upgrading it is a
# change of behaviour story 2-4 deliberately did not make.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

check_http pgadmin "http://${BIND}:${PGADMIN_PORT}/misc/ping"

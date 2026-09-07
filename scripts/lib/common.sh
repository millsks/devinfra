#!/usr/bin/env bash
# Shared configuration resolution for the devinfra lifecycle scripts.
#
# Sourced, never executed. It exists because pixi — unlike make — does not load
# .env into a task's environment, so every script that interpolates a tunable at
# shell level must load it itself. One source of truth for that, and for the
# defaults the Makefile used to supply, keeps the pixi surface behaving exactly
# as the Makefile did.
#
#   # shellcheck source=scripts/lib/common.sh
#   source "$(dirname "$0")/lib/common.sh"
#
# After sourcing: the repository root is the working directory, .env is loaded
# when present, the four documented defaults are set, and `compose` runs the
# container-runtime command.

# Run every script from the repository root, so relative paths (.env,
# docker/keycloak/realms/, backups/) resolve the same way `make` resolved them.
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

# An `if` rather than smoke-test.sh's `[[ -f .env ]] && ...` chain: under
# `set -e` that chain exits the script when .env is absent, which is a supported
# state — compose.yaml's ${VAR:-default} forms are the documented fallback.
if [[ -f .env ]]; then
    # A value already exported by the caller wins over the one in .env, which is
    # the usual environment-beats-dotenv convention and is load-bearing here:
    # up-core.sh clears COMPOSE_PROFILES and then execs wait-healthy.sh, which
    # re-sources .env. Without this the wait would cover the very containers
    # up-core excluded. `export -p` snapshots exactly the exported names and
    # their values; replaying it after the source puts them back.
    devinfra_exported_before="$(export -p)"
    set -a
    # .env is generated from .env.example and is deliberately untracked, so
    # there is nothing for shellcheck to follow.
    # shellcheck disable=SC1091
    source .env
    set +a
    eval "$devinfra_exported_before"
    unset devinfra_exported_before
fi

# The four defaults the Makefile supplied with `?=`. Port defaults live with the
# service that publishes them, in each script that prints or uses one.
: "${POSTGRES_USER:=devinfra}"
: "${POSTGRES_DB:=devinfra}"
: "${REDIS_PASSWORD:=devinfra}"
: "${KEYCLOAK_REALM:=devinfra}"

# The container-runtime command, overridable so the scripts' contracts can be
# exercised without a running stack. Word-split once, deliberately, because the
# default is two words.
read -r -a DEVINFRA_COMPOSE_ARGV <<<"${DEVINFRA_COMPOSE:-docker compose}"

compose() {
    "${DEVINFRA_COMPOSE_ARGV[@]}" "$@"
}

# Refuse to proceed unless stdin carries exactly the confirmation word. Reading
# from stdin (not /dev/tty) keeps it scriptable, but an empty or absent stdin
# reads as the empty string, which is not the word — so a non-interactive run
# aborts rather than silently agreeing.
confirm_word() {
    local word="$1" answer="" rc=0
    # `read` returns 1 at end of input and greater than 1 on a real read error.
    # EOF is not a failure to swallow — it means nobody typed anything, which the
    # comparison below already treats as a refusal. A genuine error is reported.
    # IFS= keeps read from trimming surrounding whitespace: " destroy" must not
    # pass for "destroy". -r keeps a backslash literal for the same reason.
    IFS= read -r -p "Type '${word}' to confirm: " answer || rc=$?
    if ((rc > 1)); then
        echo "Could not read confirmation input (read exited ${rc})." >&2
        exit 1
    fi
    if [[ "$answer" != "$word" ]]; then
        echo "Aborted." >&2
        exit 1
    fi
}

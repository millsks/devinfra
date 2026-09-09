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
# services/keycloak/seed/, backups/) resolve the same way `make` resolved them.
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

# An `if` rather than smoke-test.sh's `[[ -f .env ]] && ...` chain: under
# `set -e` that chain exits the script when .env is absent, which is a supported
# state — compose.yaml's ${VAR:-default} forms are the documented fallback.
if [[ -f .env ]]; then
    # A value already exported by the caller wins over the one in .env, which is
    # the usual environment-beats-dotenv convention and is load-bearing here:
    # up-core.sh requests the five core Modules and then execs wait-healthy.sh,
    # which re-sources .env. Without this the wait would cover the very containers
    # up-core excluded. `export -p` snapshots exactly the exported names and
    # their values; replaying it after the source puts them back.
    #
    # A `declare -x` line naming something that is not a valid shell identifier is
    # dropped rather than replayed: an environment may legitimately carry one — a
    # pixi task with an `env` table leaves a variable named `?` behind — and
    # `declare` refuses it, which under `set -e` kills every script that sources
    # this file. Only the `declare -x` lines are inspected, so a value containing a
    # newline keeps its continuation lines and replays intact.
    devinfra_exported_before=""
    while IFS= read -r devinfra_line; do
        if [[ "$devinfra_line" == "declare -x "* ]] &&
            ! [[ "$devinfra_line" =~ ^declare\ -x\ [A-Za-z_][A-Za-z0-9_]*(=|$) ]]; then
            continue
        fi
        devinfra_exported_before+="${devinfra_line}"$'\n'
    done < <(export -p)
    unset devinfra_line
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

# Resolve a Selection to its dependency closure and export it, before Compose sees it
# (AD-16). With no arguments the request is whatever COMPOSE_PROFILES already holds; with
# arguments it is those names, and `--all` is every Module — the request `down`, `stop`,
# `pull`, `dump-logs`, `config` and `destroy` make, so narrowing a Selection and then
# running `down` cannot orphan the containers you just stopped asking for.
#
# Called from a script body, never at source time: init-env.sh and bootstrap.sh source this
# file before a .env exists, and resolving here would make them refuse to run.
#
# A refusal is fatal by design. The resolver's diagnostic reaches stderr untouched and the
# calling script exits non-zero, so an empty or unreadable Selection stops the run instead
# of half-working (AD-18, NFR-5). `local` is declared before the assignment on purpose:
# `local x="$(...)"` takes the exit status of `local`, not of the substitution, so the
# failure would be swallowed.
select_profiles() {
    local devinfra_selection=""
    devinfra_selection="$(./scripts/select.sh "$@")" || exit 1
    export COMPOSE_PROFILES="$devinfra_selection"
}

# The ambient Selection: whatever the environment — the caller's export, or .env — already
# asked for, resolved. Most scripts take this one. It is a named wrapper rather than a bare
# `select_profiles` at each call site so that exactly one place knows which variable carries
# the request, and so the call reads as the deliberate choice it is.
select_ambient() {
    select_profiles "${COMPOSE_PROFILES:-}"
}

# Is one Module in the Selection that has already been resolved?
#
# Valid only *after* select_ambient or select_profiles: COMPOSE_PROFILES before a resolve is
# the raw request, which may name a Bundle or omit a dependency, so asking this of it would
# answer about something other than what Compose was given. Callers that back up or restore
# one Module at a time need exactly this question — a Module outside the Selection is
# skipped and recorded, never captured and never written (NFR-5).
#
# The comma fences make it an exact membership test: without them a Selection containing
# `redisinsight` would answer yes for `redis`.
selected() {
    case ",${COMPOSE_PROFILES:-}," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
    esac
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

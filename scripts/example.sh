#!/usr/bin/env bash
# Run the worked example against the running stack, then prove the two arrivals it cannot
# see itself (ADR 0019).
#
#   ./scripts/example.sh                    # whatever COMPOSE_PROFILES asks for
#
# Three acts. First the Selection is resolved and the application variables the generator
# emits for it are exported — `scripts/endpoints.py --format env` is the *only* source of a
# connection detail in this repository (ADR 0017), so nothing under examples/ and nothing
# here states a host, a port or a credential for the six integrations. Then
# examples/worked-example/main.py runs on the host under one marker, which is the
# `service.name` resource attribute on every span, log record and metric point it emits.
#
# Then the arrivals. The application hands a message to SMTP and posts OTLP to the
# collector; whether either one landed is a question only another service can answer, and
# the ports that answer it — MAILPIT_UI_PORT, GRAFANA_PORT, GRAFANA_ADMIN_* — are
# Module-tier names, not application ones. Letting the example read them would break the
# property the self-test rests on: that everything the application reads is a contract
# variable. So the split is the one ADR 0015 already draws — whoever emits the telemetry is
# not whoever proves it arrived — and the arrival half lives here.
#
# Grafana rather than Tempo, Loki and Prometheus directly, because the criterion is about
# the shipped dashboard: scripts/check_dashboards.py runs devinfra-overview.json's own panel
# queries through Grafana's datasource proxy and reports `traces: OK`, `logs: OK` and
# `metrics: OK` only when all three return data for this run's marker.
#
# Every step's failure is fatal and named. There is no `|| true`, no `command -v` and no
# skip: a stack that is not up must fail this, not pass it quietly (NFR-5).
#
# Overridable seams, following the DEVINFRA_<TOOL> convention scripts/token.sh set:
#   DEVINFRA_PYTHON  the interpreter that runs the generator, the example and the checker
#   DEVINFRA_CURL    the HTTP client that reads Mailpit's search API
set -euo pipefail

# shellcheck source=scripts/lib/common.sh
source "$(dirname "$0")/lib/common.sh"

read -r -a DEVINFRA_PYTHON_ARGV <<<"${DEVINFRA_PYTHON:-python3}"
read -r -a DEVINFRA_CURL_ARGV <<<"${DEVINFRA_CURL:-curl}"

# The Module-tier names the two arrival checks need, defaulted here exactly as every other
# script defaults the ports it uses. None of these is an application variable, and none of
# them is read by anything under examples/.
: "${BIND_ADDRESS:=127.0.0.1}"
: "${MAILPIT_UI_PORT:=8025}"
: "${GRAFANA_PORT:=3000}"
: "${GRAFANA_ADMIN_USER:=admin}"
: "${GRAFANA_ADMIN_PASSWORD:=admin}"

# Resolved before the generator is asked anything: the Selection decides which application
# variables exist, so asking first and resolving afterwards would export a set that does not
# match the stack that is running (AD-16). A refusal here exits the script.
select_ambient

# One marker per run, in the shape services/otel-collector/smoke.sh established. Minted here
# rather than inside the application because both arrival checks below have to search for
# the same value, and a marker the application generated would have to be parsed back out of
# its stdout.
MARKER="devinfra-example-$(openssl rand -hex 4)"

echo "Selection: ${COMPOSE_PROFILES}"
echo "Marker:    ${MARKER}"
echo

# Every name the registry can publish, whatever this Selection resolves to. Asked for
# separately so the ground can be cleared before the Selection's own render is exported
# over it: a developer who ran the full stack this morning still has DATABASE_URL and
# AWS_ENDPOINT_URL exported in that shell, and without this a narrowed Selection would
# inherit those stale values and connect to them — turning the refusal this example exists
# to produce into a run against a Module that is not in the Selection at all.
published="$("${DEVINFRA_PYTHON_ARGV[@]}" scripts/endpoints.py --all --format env)"
while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    unset "${line%%=*}"
done <<<"${published}"

# A command substitution in an assignment, not a process substitution: under `set -e` the
# assignment carries the generator's exit status, where `while ... done < <(...)` would
# swallow it and this would go on to run the example with nothing exported.
generated="$("${DEVINFRA_PYTHON_ARGV[@]}" scripts/endpoints.py --format env)"
if [[ -z "${generated}" ]]; then
    echo "The generator published no application variable for the Selection ${COMPOSE_PROFILES}." >&2
    echo "The example needs Postgres, Redis, Keycloak, object storage, Mailpit and the collector." >&2
    exit 1
fi
exported=0
while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    # The shellcheck-sanctioned spelling for exporting a NAME=VALUE string: `export "$line"`
    # is SC2163, and this form is what it asks for.
    export "${line?}"
    exported=$((exported + 1))
done <<<"${generated}"
echo "Exported ${exported} application variable(s) from scripts/endpoints.py --format env."
echo

echo "--- The application ---"
"${DEVINFRA_PYTHON_ARGV[@]}" examples/worked-example/main.py --service-name "${MARKER}"
echo

# Mailpit's own search API, which is what a marker lookup wants: /api/v1/messages is a paged
# listing that a busy inbox pushes this run's message off the front of.
echo "--- The mail arrived ---"
query="$("${DEVINFRA_PYTHON_ARGV[@]}" -c 'import sys,urllib.parse;sys.stdout.write(urllib.parse.quote(sys.argv[1]))' "${MARKER}")"
search="http://${BIND_ADDRESS}:${MAILPIT_UI_PORT}/api/v1/search?query=${query}"
# An `if !` rather than a bare command substitution, so a Mailpit that never answered is
# reported with the marker that was searched for. Under `set -e` the assignment alone would
# end the run with no diagnostic at all, which is the failure mode this repository keeps
# removing — the exit status is the same, the sentence a developer reads is not.
# `--max-time`, because a Mailpit that accepts the connection and never answers would
# otherwise hang this step until the CI job's 15-minute cap kills it with no diagnostic at
# all. The search is a local read of an in-memory mailbox; thirty seconds is already
# generous, and exceeding it is the failure below rather than a wait.
if ! found="$("${DEVINFRA_CURL_ARGV[@]}" -sf --max-time 30 "${search}")"; then
    echo "Mailpit's API did not answer ${search} — searched for ${MARKER}." >&2
    exit 1
fi
if [[ "${found}" != *"${MARKER}"* ]]; then
    echo "Mailpit holds no message carrying ${MARKER}." >&2
    echo "The application handed the message to SMTP, so it was accepted and then not stored." >&2
    exit 1
fi
echo "mailpit: OK a message carrying ${MARKER} is in the mailbox"
echo

# The dashboard's own panel queries, run through Grafana's datasource proxy. Exit 0 only
# when traces, logs and metrics all return data for this run's service.name; the checker
# retries an empty signal inside its own budget, which is what Tempo's block flush and
# Prometheus's scrape interval need.
echo "--- The telemetry arrived ---"
"${DEVINFRA_PYTHON_ARGV[@]}" scripts/check_dashboards.py \
    --dashboards-dir services/grafana/dashboards \
    --grafana-url "http://${GRAFANA_ADMIN_USER}:${GRAFANA_ADMIN_PASSWORD}@${BIND_ADDRESS}:${GRAFANA_PORT}" \
    --service "${MARKER}"
echo
echo "The worked example round-tripped every integration and both arrivals under ${MARKER}."

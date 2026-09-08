# 15. A smoke check whose subject is another Module's side effect is deferred by the driver

Date: 2026-09-07 · Status: Accepted

## Context

ADR 0012 carved the smoke suite up: `scripts/smoke-test.sh` holds the counters, the helpers
and the preflights, enumerates `services/*/smoke.sh` with a glob, and knows no Module by name.
Every Module owns its own checks. That works because almost every check is self-contained —
Postgres proves Postgres, Keycloak proves Keycloak — and the two cases that reach across a
Module boundary only need the other side to be *ready*, which `await_url` restores silently.

Grafana's dashboard check is neither. Proving that a panel returns data means running the
panel's query against telemetry that exists, and the requirement (PRD FR-13, UJ-4) is that it
be *the trace, log and metric the smoke suite itself injects* — otherwise the check passes on
whatever a developer's machine happened to have lying in Loki. The only injector in the suite
is `services/otel-collector/smoke.sh`, which posts an OTLP trace, log and metric tagged with a
per-run `MARKER` and then waits up to 60s for all three backends to ingest them.

The glob is alphabetical, so `grafana` runs 2nd and `otel-collector` runs 7th. A panel check
written inline in Grafana's script queries three backends that hold nothing yet, and reports
`EMPTY` on a stack that is working perfectly.

## Decision

**The driver gains a `defer <function-name>` seam.** A Module script may register a function
instead of running the check where it is written; the driver calls the registered functions,
in registration order, after every Module's script has been sourced and before the summary.
The functions run in the same shared global namespace the scripts are sourced into, so their
`pass`/`fail`/`skip` calls count exactly as an inline check's do, and `SMOKE_STRICT=1` treats
their skips exactly as it treats any other.

**The seam is generic and names no Module.** It is about ten lines and it states the real
constraint — this check runs last because its subject is a side effect the rest of the suite
produces — rather than encoding an order. `lint_selftest.py` still asserts the driver mentions
no Module name, prose included.

**The deliberate coupling is documented in both files.** `services/grafana/smoke.sh` reads
`MARKER` out of the shared namespace, which the collector's script set. That is not an
accident of scoping: the requirement is that the panels be proved against the telemetry the
suite injected, so the value has to come from whoever injected it. Both files say so, and the
check `skip`s when `MARKER` is unset — which is precisely when the injecting Module is out of
the current Selection.

**The queries come out of the shipped dashboard JSON.** `scripts/check_dashboards.py` reads
each panel's datasource UID and query from `services/grafana/dashboards/*.json`, substitutes
the `service` variable, and runs them through Grafana's datasource proxy. A panel edited into
a broken query therefore fails the check; a checker carrying its own copy of the query could
not.

## Rejected

**Emitting a second copy of the telemetry from Grafana's script.** It duplicates the OTLP
payload builder, adds a second ~60s ingest wait to every run, and creates a Grafana → collector
port coupling that no `x-requires:`/`depends_on` edge declares — so ADR 0002's rule that a
dependency not expressed as `depends_on` does not exist would drag a real compose edge along
behind a test-only need. Grafana does not depend on the collector, and must not learn to.

**Filing the assertion in `services/otel-collector/smoke.sh`.** It is where the data is, and
it is exactly the shape ADR 0012 removed: Prometheus's scrape-target check used to live inside
the collector's block, where nobody looking at Prometheus would find it. Somebody adding a
dashboard will open `services/grafana/`; the check that their dashboard works has to be there.

**Renaming or reordering the Modules so the collector sorts first.** The order is the glob's,
which is the property that keeps Core free of a catalog. A rename to fix an ordering puts an
ordering table back in Core wearing a directory name, and the next such need has nowhere to go.

**A `depends_on`-style ordering declaration between Module smoke scripts.** A general ordering
solution for a problem with one instance. `defer` says "after everything", which is all this
check needs and all it should be able to ask for; a graph would need cycle detection, a
diagnostic, and its own self-tests to earn its keep.

## Consequences

The suite has one more section, `deferred`, printed after the last Module and before the
summary. Its checks are counted like every other, so `pixi run smoke` still reports one total
and still exits non-zero on a failure anywhere.

A deferred check is harder to read in place: `services/grafana/smoke.sh` no longer runs
top-to-bottom. The comment block above the registration is therefore not optional, and it
carries both the reason and the pointer here.

`scripts/smoke-test.sh` now preflights the Python interpreter alongside `curl`, `openssl` and
`base64`, because a deferred check may be written in Python. It is reached through the same
`DEVINFRA_PYTHON` seam `scripts/select.sh` documents, so an override applies to both.

Nothing obliges a future check to use the seam, and it should stay that way: a check that can
run where it is written belongs where it is written. `defer` is for the case where the subject
does not exist yet, and the self-test pins that a registered function actually runs — a
registration the driver never invoked would be a check reporting nothing while the suite still
exited 0, which is the silent skip this repository keeps removing.

The same applies to a registration that *cannot* run. A name no function answers to — a typo,
or a module script that stopped part-way through and never reached the definition — would
print `command not found` on stderr, count nothing, and leave the suite green. The driver
therefore checks `declare -F` before calling and `fail`s naming the function otherwise.

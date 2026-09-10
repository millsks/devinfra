# 19. The worked example consumes the generated contract

Date: 2026-09-09 · Status: Accepted

## Context

ADR 0017 made `docs/ENDPOINTS.md` generated: fifteen application variables rendered from the
root `compose.yaml`'s `x-app-variables:` registry, with `pixi run lint-endpoints` refusing
any drift between the registry, the document, `.env.example` and the README. What that gate
proves is that the four agree. What it cannot prove is that any of them is *right*.

Nothing in this repository ever connected with a published value. Every Module's `smoke.sh`
speaks that service's own CLI from inside its own container — `psql`, `redis-cli`, `mc`,
`kcadm` — where the address is a container-network name and the credentials come from the
container's environment. The observability checks post hand-built OTLP JSON with `curl` at
an address the script composes itself. So a renamed host port, a rotated credential or a
wrong DSN spelling in a `value:` expression survived `pixi run ci` and every smoke run
intact, and was discovered by the first developer who pasted the published value into an
application. The registry is the repository's contract with its consumers, and it had no
consumer.

The two obvious places to put one are both wrong. A fourteenth Module would be started by
every `pixi run start` and would need a healthcheck, a `smoke.sh`, an `x-endpoints:` block, a
`seed/`, a `gotchas.md`, a profile, a Bundle membership and a pinned image — a service in a
stack whose whole premise is that it ships only what a developer needs running. And a second
list of connection details inside the example would be exactly the hand-maintained copy ADR
0017 exists to forbid, one directory further out.

## Decision

**The worked example lives in its own top-level `examples/` directory and is not a Module.**
No `services/` entry, no compose fragment, no profile, no image, no `smoke.sh`, and
`scripts/smoke-test.sh` is not taught about it — the driver may name no Module (AD-10) and
this is not one. It runs on the host, from the one pixi environment, as `pixi run example`.

**Its configuration comes from `scripts/endpoints.py --format env` and from nowhere else.**
That generator gains one output format: the application variables the resolved Selection's
Modules own, as `NAME=value` lines with no heading, no indent and no endpoint rows.
`scripts/example.sh` resolves the Selection through `select_ambient`, clears every name the
registry can publish, exports what the generator emits over that cleared ground, and runs
the application. What the self-test enforces is that no **address literal** appears anywhere
under `examples/` — a `host:port` pair in any spelling: bare like `minio:9000`, loopback
like `localhost:5432`, or inside a URL or a DSN. So a changed port or credential either
changes the generator's output, and the example keeps working, or fails `lint-endpoints`
(ADR 0017).

**Every name the application reads is a registry key, and that set is pinned statically.**
`REQUIRED` in `examples/worked-example/main.py` is the whole list of thirteen; the two Celery
names are deliberately unread because the example ships no worker.
`scripts/lint_selftest.py` asserts that set equals the pinned thirteen and is a subset of the
`x-app-variables:` registry, and that no read under `examples/` goes around the table. So
renaming a registry key fails `pixi run test` naming the example's read of it, with no
container runtime involved — which is the half of this story that runs in the `validate` job.

**Each integration is a round trip the service itself answers, through the SDK the registry's
own description names.** A token the realm introspects as `active`, a row read back, a key
read back, an object read back, a message the SMTP server accepts, and three signals the
collector forwards — not six clients that constructed cleanly. The client libraries are
pinned conda-forge dependencies in a second `[feature.example.dependencies]` table that
joins the one environment, so a missing one is impossible and no code branches on
availability (NFR-5), exactly as `pixi.toml` already states for the validation tools.

**Arrival is proved by the runner, not by the application.** Mailpit's API port and Grafana's
port and credentials are Module-tier names (`MAILPIT_UI_PORT`, `GRAFANA_PORT`,
`GRAFANA_ADMIN_*`), not registry names, and letting the application read one would break the
property the static self-test rests on. `scripts/example.sh` reads Mailpit's
`/api/v1/search` for the run's marker and runs `scripts/check_dashboards.py` against the
shipped `devinfra-overview` dashboard's own panel queries, which must report `traces: OK`,
`logs: OK` and `metrics: OK` for that marker. This is the split ADR 0015 already draws:
whoever emits the telemetry is not whoever proves it arrived.

**CI runs it as a step of the existing `stack` job**, between the initial bring-up and the
cycle step — not as a fourth job. The example is only meaningful against a healthy stack, it
shares that job's 15-minute budget, and failing before the cycle and restore steps spends
the least of it.

## Rejected

**A Module inside the stack.** Rejected on cost and on premise. It would need every part of
the Module contract ADR 0012 requires — healthcheck, `smoke.sh`, `x-endpoints:`, `seed/`,
`gotchas.md`, a profile, a Bundle membership and a pinned image — and would then be started
by every `pixi run up`, making a stack that ships only what a developer needs running ship a
demo as well. It would also read its values from the *container* network, where
`docs/ENDPOINTS.md`'s host addresses are exactly what would go untested.

**A stdlib-only client per protocol.** Postgres 17 negotiates SCRAM-SHA-256 and S3 requires
SigV4; hand-writing both plus RESP is several hundred lines of protocol code under
`mypy --strict`, and every bug in it becomes a red build that says "broken contract" while
meaning "broken example" — the opposite of the signal this exists to produce. The stdlib-only
rule that governs `check_dashboards.py` and `check_gotchas.py` is scoped to checks
`smoke-test.sh` may run in a bare checkout through the `DEVINFRA_PYTHON` seam; the example
runs from the pixi environment and is not one of those. Keycloak and SMTP stay on
`urllib.request` and `smtplib` because the standard library already speaks both contracts
fully.

**A container image under `examples/`.** It would need a pinned base, a build step in CI and
a Renovate rule, and it would run on the container network — testing the addresses the
registry does not publish while leaving the ones it does untested.

**A second pixi environment for the client libraries.** Rejected because `pixi run <task>`
resolves against one environment and a second would make "which environment does this task
run in" a question every task body has to answer. A second *feature* table gives the same
separation of statements — validation tools here, client libraries there — while the
repository keeps exactly one environment and one lock.

**Querying Tempo, Loki and Prometheus directly** instead of through Grafana. It would prove a
strictly weaker statement about a surface this story does not mention, and would duplicate
`services/otel-collector/smoke.sh`'s existing arrival poll. The criterion is about the
shipped dashboard, and `check_dashboards.py` already runs that dashboard's own panel
queries.

## Consequences

The published contract now has a consumer that runs on every build, so a wrong value in
`x-app-variables:` fails CI instead of reaching a developer. The static half of that — the
name set — runs in the `validate` job with no runtime at all.

`examples/` joins the linted surface: `pixi run lint-python` covers it, `pyproject.toml`
names it as a source root, and a planted defect there proves the coverage. `mypy --strict`
holds over it with one per-distribution override for boto3, which ships no `py.typed`.

**The client libraries are outside Renovate's watch, by design.** `renovate.json` runs one
`custom.regex` manager over `.env.example` and the compose files, and
`scripts/assert_renovate.py` refuses any other manager — so the five conda pins in
`pixi.toml`'s `example` feature are not proposed for update by the bot. They are pinned to a
minor series (`3.3.*`, `8.1.*`, `1.43.*`, `1.44.*`) and move when `pixi.lock` is regenerated.
This is a documented cost, not an oversight: adding a second manager would reopen the single
question ADR 0010 settled about where an update proposal comes from.

The `stack` job now does four things inside fifteen minutes rather than three. The example
is placed first of the three follow-on steps precisely so a breach shows up as a bring-up or
cycle cost rather than as this step being squeezed; if the bound is ever breached, the answer
is to tier the matrix, never to drop a check.

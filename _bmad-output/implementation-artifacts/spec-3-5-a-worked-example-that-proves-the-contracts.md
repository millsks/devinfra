---
title: 'A worked example that proves the contracts'
type: 'feature'
created: '2026-09-09'
baseline_revision: 'da98a6494e1f24f9efb49e83e7d96604097c592a'
status: 'in-review'
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/AGENTS.md']
warnings: ['oversized']
deferred: []
---

<intent-contract>

## Intent

**Problem:** `docs/ENDPOINTS.md` publishes fifteen application variables and nothing ever
connects with them: every existing proof is a smoke check speaking a service's own CLI
inside its container, or `curl` posting hand-built OTLP JSON. A renamed port, a rotated
credential or a wrong DSN spelling in `x-app-variables:` survives `pixi run ci` intact and
is discovered by the first developer who pastes the published value into an application.

**Approach:** One runnable application under `examples/`, run on the host by
`pixi run example`, that reads *only* the generated application variables — exported into
its environment from `scripts/endpoints.py` itself, never hand-written — and exercises the
six integrations with the SDKs the registry's own descriptions name: authenticates against
Keycloak and has the token introspected, round-trips a row through Postgres, a key through
Redis, an object through object storage, sends mail to Mailpit, and emits traces, logs and
metrics through the OpenTelemetry SDK under one `service.name` marker. A runner script
proves the two arrivals the application cannot see itself — the message in Mailpit and the
three signals on Grafana's own dashboard panels — and CI's `stack` job runs the same task a
developer runs.

## Boundaries & Constraints

**Always:**
- The application reads its configuration from the environment and from nowhere else, and
  every name it reads is a key of `x-app-variables:` in `compose.yaml`. That set is pinned
  by a self-test case, so renaming a registry variable fails `pixi run test` without a
  container in sight.
- The values come from `scripts/endpoints.py`, which is the sole publisher of a connection
  detail (AD-17). The runner exports what the generator emits for the resolved Selection;
  no second list exists anywhere.
- The example lives in its own top-level `examples/` directory. It is not a Module: no
  `services/` entry, no compose fragment, no profile, no image.
- Every failure surfaces and exits non-zero (NFR-5). No `|| true`, no `command -v`, no
  "skipping" — `scripts/lint_selftest.py:55` fails the build on all four strings.
- Client libraries are pinned conda-forge dependencies in the one environment, so a missing
  one is impossible and no code branches on availability — the same rule `pixi.toml:8-9`
  states for validation tools.
- The runner resolves the Selection through `select_ambient` before asking the generator
  anything, and reaches curl and Python through the `DEVINFRA_CURL` / `DEVINFRA_PYTHON`
  seams the existing scripts use, so its shape is testable with no runtime.
- `examples/` becomes part of the linted surface: `lint-python` covers it, `pyproject.toml`
  names it as a source root, and a planted defect there proves the coverage.
- The example says in its own README that it is not a production application template.

**Never:**
- Do not hand-write a connection string, host, port or credential anywhere under
  `examples/`; a `localhost:<port>` literal in the example's Python is a self-test failure.
- Do not add a Module, a compose file, an image or a `services/*/smoke.sh` for the example,
  and do not teach `scripts/smoke-test.sh` about it — the driver may not name a Module and
  the example is not one.
- Do not add a fourth CI job: the example attaches as a step to the existing `stack` job,
  whose 15-minute budget it shares.
- Do not add a `.env.example` tunable, an `x-endpoints:` key or an `x-app-variables:` entry.
  The example consumes the contract; it does not extend it.
- Do not hand-roll a wire protocol. A hand-written Postgres or S3 client turns its own bugs
  into build failures, which is the opposite of the signal this story exists to produce.
- Do not harden the stack: credentials stay trivial, no TLS, no new auth (NFR-8).
- Do not edit `docs/ENDPOINTS.md` by hand, and do not touch
  `_bmad-output/implementation-artifacts/sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full Selection, stack healthy | `COMPOSE_PROFILES` resolves to core+observability; `pixi run example` | One `OK` line per integration, the marker printed, mail found in Mailpit, all three Grafana panels return data; exit 0 | No error expected |
| Selection excludes object storage | Selection is `postgres,redis`; `pixi run example` | Refusal before any connection is opened, naming `AWS_ENDPOINT_URL` and the integration it serves | Exit 1, nothing written |
| `endpoints.py --format env` | A resolved Selection | Only `NAME=value` lines, one per application variable whose owning Module is in the Selection; no headings, no endpoint rows | Exit 1 on any unresolved value |
| `endpoints.py --format env --write docs/ENDPOINTS.md` | pixi appended the argument | Refusal naming the format | Exit 1, document untouched |
| A service is down | Stack up but Redis stopped | The Redis integration reports FAIL with the client's own error; the remaining integrations still run | Exit 1 after telemetry is flushed |
| Telemetry never arrives | Collector up, Tempo down | Runner reports which of traces/logs/metrics was empty | Exit 1 naming the signal |
| Mail never arrives | Mailpit accepted SMTP but the API shows nothing | Runner reports the marker it searched for | Exit 1 |
| Re-run | `pixi run example` twice in a row | Second run passes with a fresh marker; no row, key or object left behind by the first | No error expected |

</intent-contract>

## Code Map

**The generator — one new output format**
- `scripts/endpoints.py` — `DOCUMENT` `:88`; `read_registry()` `:233` and `APP_ENTRY_KEYS` `:78`;
  `interpolate()` `:353`; `select()` `:408`; `render_text()` `:427-461` — its
  `Application variables` block `:454-460` is the exact content the new `env` format emits,
  minus the heading and the indent; `cell()` `:464`; `render_markdown()` `:480`;
  `build_parser()` `:811` with `--format choices=("text","markdown")` `:843-848`;
  `settle()` `:869` — the `--check` refusal `:887-903` and the narrowing refusal `:909-930`
  (`args.format != "markdown"` already covers a new format, verify it does);
  `environment_for()` `:933` — markdown reads `.env.example`, everything else reads
  `os.environ`, which is what `env` must keep; `main()` `:1054`, the
  `COMPOSE_PROFILES` fallback guarded by `args.format == "text"` `:1084-1088` (must also
  admit `env`), and the render dispatch `:1090`.

**The runner's seams and precedents**
- `scripts/lib/common.sh:19` cds to the repository root; `:24-55` loads `.env`; `:97-99`
  `select_ambient`; `:87-91` `select_profiles`.
- `scripts/select.sh:35-58` — the wrapper shape to copy: `set -euo pipefail`, source
  `common.sh`, the `DEVINFRA_PYTHON` seam `:40`, `exec`. `scripts/urls.sh:47-51` is the
  same shape calling `endpoints.py` directly.
- `scripts/token.sh:17` — the `DEVINFRA_CURL` seam; `:19-25` the host-side token request.
- `services/grafana/smoke.sh:71-94` — the exact `check_dashboards.py` invocation to reuse,
  including the three `traces|logs|metrics: OK` assertions and the `GF` userinfo URL
  built at `:17`.
- `scripts/check_dashboards.py` — CLI at `:455-477`: `--dashboards-dir` (default
  `services/grafana/dashboards`), `--grafana-url`, `--service` (required), `--budget-seconds`
  60, `--interval-seconds` 5. `SIGNALS` `:50`. Exit 0 only when all three are OK.
- `services/mailpit/smoke.sh:13-15` — the `/api/v1/messages` read; Mailpit also serves
  `/api/v1/search?query=` which is what a marker lookup should use.
- `services/otel-collector/smoke.sh:30-35` the marker convention `devinfra-smoke-<8 hex>`
  from `openssl rand -hex 16`; `:63-85` the arrival poll this story does *not* duplicate
  (Grafana is the surface the AC names).

**The contract the example consumes**
- `compose.yaml:115-196` `x-app-variables:` — the fifteen names. The thirteen the example
  reads: `DATABASE_URL` `:116-123` (description literally says "SQLAlchemy and psycopg"),
  `REDIS_URL` `:124-128`, `OIDC_ISSUER` `:139-143`, `OIDC_DISCOVERY_URL` `:144-149`,
  `OIDC_CLIENT_ID` `:150-156`, `OIDC_CLIENT_SECRET` `:157-161`, `AWS_ENDPOINT_URL` `:162-166`,
  `AWS_ACCESS_KEY_ID` `:167-171`, `AWS_SECRET_ACCESS_KEY` `:172-176`, `SMTP_HOST` `:177-181`,
  `SMTP_PORT` `:182-186`, `OTEL_EXPORTER_OTLP_ENDPOINT` `:187-191`,
  `OTEL_EXPORTER_OTLP_PROTOCOL` `:192-196` (value `http/protobuf`). `CELERY_BROKER_URL` and
  `CELERY_RESULT_BACKEND` are deliberately unread — the example ships no Celery worker.
- `services/keycloak/seed/devinfra-realm.json` — client `devinfra-api` is confidential with
  service accounts on, which is what makes a client-credentials grant and an introspection
  call possible with the registry's four `OIDC_*` names alone.
- `services/grafana/dashboards/devinfra-overview.json` — the panel queries the arrival check
  runs: tempo `{resource.service.name="$service"}`, loki and prometheus
  `{service_name="$service"}`. The marker must therefore be the `service.name` **resource**
  attribute on all three signals.
- `services/minio/compose.yaml:87` `MINIO_BUCKETS` default `uploads,artifacts,backups` — the
  example must *not* read that name; it lists buckets over S3 and uses what it finds.

**Wiring**
- `pixi.toml:227-229` `[tasks.lint-python]` = `ruff format --check scripts && ruff check
  scripts && mypy scripts` — the three literals to widen; `:10-17`
  `[feature.dev.dependencies]` and its "every tool is pinned" comment `:8-9`; `:19-20`
  `[environments] default = ["dev"]`; `:105-112` `[tasks.smoke]`/`[tasks.smoke-strict]` for
  task style; `:243-249` `[tasks.ci-stack]` and the comment forbidding the workflow from
  becoming a second definition of what CI runs; `:262-271` `[tasks.ci-stack-restore]`.
- `pyproject.toml:4-8` `[tool.ruff] src = ["scripts"]`, `:10-15` the `D` rule and google
  convention, `:17-21` `[tool.mypy] strict = true` with no `exclude` and no overrides.
- `.github/workflows/ci.yml:83-84` the `ci-stack` step, `:91-92` the cycle step, `:99-100`
  the restore step, `:104-110` the two `if: failure()` diagnostics. The new step goes
  between `:84` and `:91`. `:64` the 15-minute bound; `:71` `COMPOSE_PROFILES`.

**Self-test — what breaks and where new cases plug in**
- `scripts/lint_selftest.py:6328-6332` `expected_work` — `"stack"` is compared by **ordered
  list equality** at `:6340`, so the new step must be added there in the same position.
- `:67-83` `APP_VARIABLES` (the fifteen names, set-equality at `:1489-1495`) — the pin the
  new "the example reads only registry names" case compares against.
- `:1142-1224` `defects` — `(task, fixture_path, body)` triples, fixtures named
  `zz_selftest_defect.*`; a new entry under `examples/` is what stops `examples` being
  deleted from the `lint-python` body. `:1234-1273` `empties` — glob-driven tasks only;
  `lint-python` names literal directories, so no entry is owed there.
- `:6169-6191` gitignore coverage, `sources` built at `:6176` from `scripts/**/*.sh`,
  `scripts/**/*.py`, `.githooks/*` — widen with `examples/**/*.py`.
- `:6192-6231` "every script that calls compose resolves its Selection first",
  `resolver_exempt` `:6206` — the runner is not exempt and must call `select_ambient`.
- `:812-826` every task body: no `FORBIDDEN` string, no container runtime named.
- `:6299-6317` every workflow step: `run` must start `pixi run `, `if` must be absent or
  exactly `failure()`; `:6293-6298` `timeout-minutes` ≤ 15.
- `:6405-6421` task-chain assertions; `:6540-6571` `precommit ⊆ lint` — `example` is a
  runtime task and belongs in neither.
- Helpers: `expect(name, condition, detail)` `:726`; `pixi()` `:131`; `run_script()` `:159`;
  `write_recorder()` `:321`; `stub_env()` `:350`; `recorded()` `:406`; `planted()` `:473`;
  `moved_aside()` `:510`. Banner convention `# --- Title. ---` followed by prose saying why
  the defect cannot satisfy the assertion.

**Docs**
- `README.md:272-396` the repository-layout fenced block (path column padded to col 33) —
  `examples/` belongs there; `:398-447` the task table, `| Task | What it does | Deprecated
  equivalent |`, `—` in column 3 for a task with no Makefile ancestor; `:458-462` the CI job
  table and `:464-479` the prose explaining why the round trip is a step and not a job;
  `:172-190` `## Connecting your application`, which may name the example but **no**
  `localhost:<port>` literal (`scripts/endpoints.py:714-808` refuses one outside the
  `## Contents` table).
- `CHANGELOG.md:9` `## [Unreleased]`, `:190` `### Added`; bullets open with a bold noun
  phrase and an em dash and cite the decision as `(ADR 0019)`.
- `docs/adr/README.md:10-29` the index table, highest ADR **0018** — the new one is **0019**,
  appended at `:29`. Template, identical across 0016-0018: `# 19. <Title>` / blank /
  `Date: 2026-09-09 · Status: Accepted` / `## Context` / `## Decision` / `## Rejected` /
  `## Consequences`, bolded lead sentence per sub-decision with `AD-n`/`NFR-n` inline.
- `AGENTS.md:51-58` connection details are generated and the README carries no copies;
  `:66` `pixi run ci` is the done-gate.
- `renovate.json:46-49` watches `.env.example` and compose files only — a conda dependency
  in `pixi.toml` is unwatched by design, and `scripts/assert_renovate.py:34-36` refuses any
  manager but `custom.regex`, so that stays a documented consequence rather than a fix.

## Tasks & Acceptance

**Execution:**
- `pixi.toml` -- add `[feature.example.dependencies]` with conda-forge pins `psycopg = "3.3.*"`,
  `redis-py = "8.1.*"`, `boto3 = "1.43.*"`, `opentelemetry-sdk = "1.44.*"` and
  `opentelemetry-exporter-otlp-proto-http = "1.44.*"` (each verified present on conda-forge at
  those versions; `pixi install` must resolve them on all four declared platforms), extend
  `[environments] default` to `["dev", "example"]`, and add `[tasks.example]` under its own
  banner block invoking the runner -- a second dependency table keeps "validation tools" and
  "the example's client libraries" separate statements while the repository keeps exactly
  one environment, so a missing client library stays impossible.
- `scripts/endpoints.py` -- add `env` to `--format`'s choices and a `render_env()` emitting
  one `NAME=value` line per application variable whose owning Module is in the Selection and
  nothing else; admit `env` to the `COMPOSE_PROFILES` fallback in `main()`; confirm `settle()`
  still refuses `--format env` against `docs/ENDPOINTS.md` and that `environment_for()`
  leaves it on `os.environ` -- the example must consume the generator's own output, and a
  format meant for `export` cannot carry headings or padding.
- `examples/worked-example/main.py` -- new: the application. Read the thirteen registry names
  from the environment and refuse, before opening any connection, naming every one that is
  absent and the integration it serves. Configure the OpenTelemetry tracer, logger and meter
  providers against `OTEL_EXPORTER_OTLP_ENDPOINT`, refusing an `OTEL_EXPORTER_OTLP_PROTOCOL`
  other than `http/protobuf`, with `service.name` set to `--service-name` (default: a
  generated `devinfra-example-<8 hex>`, printed on stdout). Then, each inside its own span
  and each emitting a log record and incrementing a counter: fetch `OIDC_DISCOVERY_URL` and
  assert its `issuer` equals `OIDC_ISSUER`, take a client-credentials token and have the
  realm's introspection endpoint return `active: true` for it; `psycopg.connect(DATABASE_URL)`
  and insert, read back and delete a marker row; `redis.from_url(REDIS_URL)` and set, read
  back and delete a marker key; a boto3 S3 client on `AWS_ENDPOINT_URL` that lists buckets,
  puts, gets and deletes a marker object in the first one; `smtplib` to `SMTP_HOST`/`SMTP_PORT`
  with the marker in the subject. Print one `OK`/`FAIL` line per integration, flush all three
  providers before returning, and exit non-zero if any integration failed -- AC1, AC2.
- `examples/worked-example/README.md` -- new: what it proves, how to run it, what each of the
  six integrations does, and an explicit statement that it is not a production application
  template -- the epic requires the example to say so itself.
- `scripts/example.sh` -- new: source `common.sh`, `select_ambient`, mint the marker, export
  the application variables read from `endpoints.py --format env`, run the application, then
  prove the two arrivals it cannot see — Mailpit's `/api/v1/search` holds a message carrying
  the marker, and `check_dashboards.py --service <marker>` reports `traces: OK`, `logs: OK`
  and `metrics: OK`. Reach curl and Python through `DEVINFRA_CURL`/`DEVINFRA_PYTHON` and exit
  non-zero on any step -- AC1, AC3; arrival on another service's surface is the runner's job,
  not the application's, and the seams make the runner's shape testable without a runtime.
- `pyproject.toml` -- add `examples` to `[tool.ruff] src`, and add the narrowest
  `[[tool.mypy.overrides]]` the new imports actually require, each with a comment naming the
  distribution that ships no `py.typed` -- `strict = true` has no blanket escape today and
  must not gain one.
- `.github/workflows/ci.yml` -- add a `Run the worked example against the stack` step running
  `pixi run example`, between the `ci-stack` step and the cycle step -- the example is only
  meaningful against a healthy stack, and failing before the cycle and restore steps spends
  the least of a 15-minute budget.
- `scripts/lint_selftest.py` -- add cases for: `expected_work["stack"]` carrying the new step
  in position; the set of environment names `examples/**/*.py` reads being exactly the pinned
  thirteen and a subset of `APP_VARIABLES`; no `CONNECTION_STRING` literal anywhere under
  `examples/`; `--format env` emitting only `NAME=value` lines and narrowing with the
  Selection; `--format env --write docs/ENDPOINTS.md` refused with the document untouched;
  the application refusing a Selection that omits a Module, naming the variable and touching
  no runtime; the runner's argv order under stubs (application before Mailpit check before
  `check_dashboards.py`) and its non-zero exit when either arrival check fails; a
  `zz_selftest_defect.py` entry in `defects` under `examples/`; and `examples/**/*.py` added
  to the gitignore-coverage `sources` -- every script is verified like code, and the static
  cases are what make a renamed contract variable fail a build with no container running.
- `docs/adr/0019-the-worked-example-consumes-the-generated-contract.md`, `docs/adr/README.md`
  -- record the decision and the four rejected alternatives (a Module inside the stack; a
  stdlib-only hand-rolled client per protocol; a container image under `examples/`; a second
  pixi environment), and the consequence that the client libraries are outside Renovate's
  watch -- a decision this shaped is not self-evident from the diff.
- `README.md`, `CHANGELOG.md` -- add the `examples/` layout entry, the `pixi run example` task
  row, the example in the `stack` job's row and prose, a pointer from
  `## Connecting your application`, and an `### Added` changelog entry -- documentation is
  generated or verified, never invented, and the README may still state no connection detail.

**Acceptance Criteria:**
- Given a Selection that includes all six Modules and a healthy stack, when `pixi run example`
  runs, then the application authenticates against Keycloak, queries Postgres, caches in
  Redis, stores and retrieves an object, sends mail to Mailpit and emits OTLP telemetry, each
  reported as its own `OK` line, and the command exits 0 — with no edit to the example's code
  between a fresh clone and that run.
- Given each of the six integrations, when the example runs, then it performs a round trip
  the service itself must answer — a token the realm introspects as active, a row read back,
  a key read back, an object read back, a message the SMTP server accepts, and three signals
  the collector forwards — rather than merely constructing a client.
- Given the example has run, when the runner checks Grafana, then the shipped dashboard's
  trace, log and metric panels each return data for that run's `service.name`, and the
  message carrying the run's marker is present in Mailpit.
- Given `pixi run ci`, when it runs, then it exits 0 with the new self-test cases included and
  `lint-python` covering `examples/`.
- Given a renamed or removed `x-app-variables:` key, when `pixi run test` runs, then it fails
  naming the example's read of that variable — with no container runtime involved.
- Given CI, when the `stack` job runs, then `pixi run example` executes between the initial
  bring-up and the cycle step, and a failing integration fails the build.

## Spec Change Log

## Review Triage Log

## Design Notes

**Why the runner exports the generator's output instead of the example reading `.env`.**
`.env` holds `POSTGRES_PORT` and `MINIO_ROOT_USER`; it does not hold `DATABASE_URL` or
`AWS_ENDPOINT_URL`. Those exist only as `value:` expressions in `x-app-variables:` and only
`scripts/endpoints.py` interpolates them. Having the example read the generator's own output
is what makes the epic's rule true in practice — a changed port or credential either changes
that output, and the example keeps working, or fails `lint-endpoints`. Any other source
would be the second hand-maintained list ADR 0017 exists to forbid. The shape:

```
  # A command substitution in an assignment fails the script under `set -e`, where a
  # process substitution would swallow the generator's exit status.
  generated="$("${DEVINFRA_PYTHON_ARGV[@]}" scripts/endpoints.py --format env)"
  while IFS= read -r line; do
      [[ -n "${line}" ]] || continue
      export "${line?}"   # the shellcheck-sanctioned spelling for exporting NAME=VALUE
  done <<<"${generated}"
```

**Why real client SDKs and not stdlib.** Postgres 17 negotiates SCRAM-SHA-256 and S3 requires
SigV4; hand-writing both plus RESP is several hundred lines of protocol code under
`mypy --strict`, and every bug in it becomes a red build that says "broken contract" while
meaning "broken example". The registry's own descriptions name the consumers — `DATABASE_URL`
is documented as "SQLAlchemy and psycopg connection DSN", `AWS_ENDPOINT_URL` as "the S3 API
endpoint every AWS SDK reads" — so the SDKs are what the contract is written for. The
stdlib-only rule that governs `check_dashboards.py` and `check_gotchas.py` is scoped to
checks `smoke-test.sh` may run in a bare checkout through the `DEVINFRA_PYTHON` seam; the
example runs from the pixi environment and is not one of those. Keycloak and SMTP stay on
`urllib.request` and `smtplib` because stdlib already speaks both contracts fully.

**Why the arrival checks live in the runner.** Mailpit's API port and Grafana's port and
credentials are Module-tier names (`MAILPIT_UI_PORT`, `GRAFANA_PORT`, `GRAFANA_ADMIN_*`), not
registry names. Letting the application read them would break the property that makes the
static self-test case possible — that everything the application reads is a contract
variable. The split is the same one ADR 0015 already draws: whoever emits the telemetry is
not whoever proves it arrived.

**Why Grafana rather than Tempo, Loki and Prometheus directly.** The acceptance criterion
names Grafana, and `scripts/check_dashboards.py` already runs the shipped dashboard's own
panel queries through Grafana's datasource proxy. Querying the three backends directly would
prove a strictly weaker statement about a surface the story does not mention, and would
duplicate `services/otel-collector/smoke.sh:63-110`.

**Why not a Module.** A Module would be started by every `pixi run start`, would need a
healthcheck, a `smoke.sh`, an `x-endpoints:` block, a `seed/`, a `gotchas.md`, a profile and a
Bundle membership, and would need a pinned image — turning "one runnable example" into a
fourteenth service in a stack whose whole premise is that it ships only what a developer
needs running. The epic states the placement directly: backup and restore live in `scripts/`,
the worked example lives in its own top-level `examples/` directory.

## Verification

**Commands:**
- `pixi install` -- expected: the five new packages resolve and `pixi.lock` regenerates for
  all four declared platforms. A platform that cannot resolve is a blocking finding, not a
  dropped platform.
- `pixi run lint-python` -- expected: `ruff format --check`, `ruff check` and `mypy --strict`
  clean over `scripts` **and** `examples`.
- `pixi run lint-endpoints` -- expected: unchanged; `docs/ENDPOINTS.md`, `.env.example` and
  the README still agree, and the README still states no connection detail outside its
  `## Contents` table.
- `python scripts/endpoints.py --format env` -- expected: only `NAME=value` lines, thirteen
  or more depending on the Selection; `python scripts/endpoints.py postgres --format env`
  narrows to `DATABASE_URL` alone. (The `endpoints` *task* fixes `--format markdown --write`,
  so `pixi run endpoints --format env` is one of the matrix's refusals, not this check.)
- `pixi run test` -- expected: every new case passes by name, including the contract-variable
  set equality and the `examples/` defect fixture.
- `pixi run ci` -- expected: exit 0. This is the done-gate.
- `pixi run ci-stack` then `pixi run example` -- expected against a real runtime: six `OK`
  lines, the marker printed, the Mailpit message found and all three Grafana panels
  reporting data. If no container runtime is available in this environment, report it as not
  run rather than as passed.
- Mutation check -- expected: renaming one `x-app-variables:` key in `compose.yaml` turns
  `pixi run test` red at the contract-variable case, and reverting turns it green.

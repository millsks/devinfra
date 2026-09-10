# The worked example

One runnable application that connects to every Module this stack publishes a contract for,
using **only** that contract.

> **This is not a production application template.** It opens a connection per integration
> and throws it away, keeps no pool, retries nothing, holds no secret management and has no
> configuration layering. What it demonstrates is that the published connection details are
> correct and complete — not how to structure an application that uses them.

## What it proves

`docs/ENDPOINTS.md` publishes fifteen application variables. Before this example existed
nothing ever connected with them: every check in the repository either speaks a service's
own CLI from inside its container or posts hand-built JSON with `curl`. So a renamed port, a
rotated credential or a wrong DSN spelling in the root `compose.yaml`'s `x-app-variables:`
registry survived the whole gate, and was found by the first developer who pasted the
published value into an application.

This runs that developer on every CI build. If it goes green, the values in
`docs/ENDPOINTS.md` are values an SDK can actually connect with.

Two properties make that claim hold rather than merely sound good:

- **Nothing here states a connection detail.** Every value arrives in the environment,
  exported by `scripts/example.sh` from `scripts/endpoints.py --format env` — the same
  generator that writes `docs/ENDPOINTS.md` (ADR 0017, ADR 0019). The self-test enforces
  that as a ban on **address literals**: a `<host>:<port>` pair anywhere in this directory —
  bare, loopback, or inside a URL or a DSN — fails `pixi run test`. The rule is pinned from
  both sides, so it catches every one of those spellings and leaves this repository's own
  `<file>:<line>` citations alone.
- **Every name it reads is a registry key.** `REQUIRED` in `main.py` is the whole list, and
  `scripts/lint_selftest.py` pins it against `x-app-variables:` by set equality. Rename a
  registry key and `pixi run test` goes red naming the example's read of it — with no
  container runtime involved.

## Running it

```sh
pixi run up          # or any Selection covering the six Modules below
pixi run example
```

`pixi run example` resolves the ambient Selection, mints one marker, exports the generated
application variables, runs the application, and then proves the two arrivals the
application cannot see itself. CI runs the same task as a step of the `stack` job.

A Selection that leaves out one of the six Modules is refused before any connection is
opened, naming each absent variable and the integration it serves.

## The six integrations

Each one is a **round trip the service itself has to answer** — a client that merely
constructed cleanly proves nothing — and each runs inside its own span, emitting its own log
record and incrementing its own counter under the run's `service.name`.

| Integration | Variables | The round trip |
|---|---|---|
| Keycloak | `OIDC_ISSUER`, `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Fetch discovery, check its `issuer` against `OIDC_ISSUER`, take a client-credentials token, and have the realm's own introspection endpoint report it `active: true` |
| Postgres | `DATABASE_URL` | `psycopg.connect()`, then insert a marker row, read it back and delete it |
| Redis | `REDIS_URL` | `redis.from_url()`, then set a marker key with a TTL, read it back and delete it |
| Object storage | `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | A boto3 S3 client that lists buckets, then puts, gets and deletes a marker object in the first one |
| Mailpit | `SMTP_HOST`, `SMTP_PORT` | `smtplib` hands the server one message carrying the marker in its subject |
| Telemetry | `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` | The OpenTelemetry SDK exports the run's traces, logs and metrics over OTLP/HTTP, and the run does not finish until all three providers have flushed |

The bucket comes from the listing, never from `MINIO_BUCKETS`: that is a Module-tier tunable,
not a contract variable, and reading it here would make this directory depend on something
`docs/ENDPOINTS.md` never published.

`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are the two registry names deliberately
unread — this example ships no Celery worker.

## The two arrivals

The application can tell you it handed a message to SMTP and posted OTLP to the collector.
It cannot tell you either one landed, and it must not try: the ports that answer that
question — `MAILPIT_UI_PORT`, `GRAFANA_PORT`, `GRAFANA_ADMIN_*` — are Module-tier names, and
an application reading one would break the property the self-test rests on. So
`scripts/example.sh` asks instead:

- **Mailpit** holds a message carrying the run's marker, read back through
  `/api/v1/search`.
- **Grafana** answers the shipped `devinfra-overview` dashboard's own trace, log and metric
  panel queries with data for that marker — `scripts/check_dashboards.py` runs the panels'
  real queries through Grafana's datasource proxy, not a query of its own.

That split is the one ADR 0015 already draws: whoever emits the telemetry is not whoever
proves it arrived.

## What it leaves behind

Nothing a second run could read as its own. The row, the key and the object are each deleted
by the integration that created them, and the Redis key carries a TTL so a run killed
half way expires rather than lingering. The Postgres table `devinfra_example` is created if
absent and left in place, empty — dropping it would race a concurrent run. The mail and the
telemetry stay, as evidence, under a marker that is unique to the run that produced them.

## Why real SDKs

Postgres 17 negotiates SCRAM-SHA-256 and S3 requires SigV4; hand-writing both plus RESP is
several hundred lines of protocol code under `mypy --strict`, and every bug in it would
become a red build saying "broken contract" while meaning "broken example". The registry's
own descriptions name the consumers — `DATABASE_URL` is documented as "SQLAlchemy and
psycopg connection DSN", `AWS_ENDPOINT_URL` as "the S3 API endpoint every AWS SDK reads" — so
the SDKs are what the contract is written for. Keycloak and SMTP stay on `urllib.request`
and `smtplib` because the standard library already speaks both contracts fully.

The client libraries are pinned conda-forge dependencies in `pixi.toml`'s `example` feature,
which joins the one environment, so a missing one is impossible and no code here branches on
availability.

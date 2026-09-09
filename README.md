# devinfra

A Docker Compose stack of infrastructure components for local development. Every
stateful service persists to a named volume, so `docker compose down` and back up
preserves your data — only an explicit `pixi run destroy` throws it away.

> ### ⚠ Upgrading from a pre-Selection checkout — read this first
>
> **If your `.env` has no `COMPOSE_PROFILES` line, this stack now starts nothing.**
> Every service carries its own profile, so an unset or empty `COMPOSE_PROFILES`
> selects no service at all. The scripts refuse it rather than starting nothing and
> reporting success, so you get an exit 1 that names the variable *and prints the
> line to add* — not a silently empty stack. That line is:
>
> ```sh
> COMPOSE_PROFILES=core,admin,observability
> ```
>
> That resolves to all thirteen Modules — exactly what a bare `docker compose up`
> started before Selection existed. Once the line is there, `pixi run select` prints
> what your `.env` starts; run against a `.env` that still lacks it, it exits 1 and
> tells you the same line. If your `.env` carries the older
> `COMPOSE_PROFILES=admin,observability`, that value is still legal but no longer
> means what it did: it now resolves to ten Modules and quietly leaves out
> Keycloak, object storage and Mailpit. See [`CHANGELOG.md`](CHANGELOG.md) and
> [Selection](#selection).

## Contents

| Service | Version | Purpose | Endpoint |
|---|---|---|---|
| **PostgreSQL** | 17 (+pgvector 0.8.6) | Primary datastore, Celery result backend, Keycloak persistence | `localhost:5432` |
| **Redis** | 8.10 | Cache, Celery broker, Celery result backend | `localhost:6379` |
| **Keycloak** | 26.7 | OpenID Connect provider | http://localhost:8080 |
| **Silo** | 2026-09-03 | S3-compatible object storage (maintained MinIO fork) | http://localhost:9101 (API `:9100`) |
| **Mailpit** | 1.31 | Catches all outbound SMTP | http://localhost:8025 (SMTP `:1025`) |
| **pgAdmin** | 9.17 | PostgreSQL web console | http://localhost:5050 |
| **RedisInsight** | 3.8 | Redis web console | http://localhost:5540 |
| **Flower** | 2.1 | Celery task monitoring | http://localhost:5555 |
| **OTel Collector** | 0.160 | Single OTLP ingest point | `localhost:4317` (gRPC) / `:4318` (HTTP) |
| **Prometheus** | 3.14 | Metrics | http://localhost:9090 |
| **Loki** | 3.7 | Logs | http://localhost:3100 |
| **Tempo** | 3.0 | Traces | http://localhost:3200 |
| **Grafana** | 13.2 | Dashboards over all three signals; the `devinfra overview` dashboard is provisioned from the tracked JSON on every start | http://localhost:3000 |

All ports bind to `127.0.0.1` by default, so the stack is not exposed to your
network. Change `BIND_ADDRESS` in `.env` if you need otherwise.

## Requirements

- **Docker** (or a compatible engine) with the Compose plugin — runs the stack,
  and resolves the model for `pixi run lint`: `lint-compose` and `lint-config`
  both ask Compose to render the configuration, so the lint surface needs it too.
- **Podman 5.0 or newer** works too, and CI proves it on every change — the
  Compose plugin is still the client, pointed at Podman's API socket. 5.x is the
  floor because the compatibility argument rests on it: `--url` implying
  `--remote`, `start_period` honoured by the healthcheck loop, and unknown log
  options stored rather than rejected. See
  [Running under Podman](#running-under-podman).
- **[pixi](https://pixi.sh)** — provisions the validation tooling (`shellcheck`,
  `yamllint`, `python`, `ruff`, `mypy`, `pyyaml`) from the committed `pixi.lock`,
  so `pixi run lint` checks the same versions on every machine, and CI checks
  those same versions again.

## Quick start

```sh
pixi install       # fetch the pinned tooling (once per clone)
pixi run bootstrap # install the commit-time hooks (once per clone)
pixi run init      # create .env from the template
pixi run up        # start everything, wait for health, print endpoints
pixi run smoke     # verify every service actually works
```

`pixi run up` typically takes under two minutes on a cold start, most of it
Keycloak booting and importing its realm.

`pixi task list` shows every task. The `make` targets still work — each forwards
to its pixi task and prints a deprecation notice on stderr — but they are going
away, so prefer `pixi run`.

### Selection

`COMPOSE_PROFILES` in `.env` is a **Selection**: the Modules and Bundles you want.
Every service carries its own Module name in `profiles:`, so nothing starts unless
the Selection asks for it — there is no set of core services that always start any
more. An empty Selection is refused, not started as nothing: the scripts exit 1
naming the variable and printing the line to add, rather than reporting success
over an empty stack.

A Selection is expanded to its transitive `depends_on` closure before Compose sees
it, so you name what you want and the resolver adds what it needs:

```sh
pixi run select keycloak        # keycloak,mailpit,postgres
pixi run select postgres,redis  # postgres,redis — two containers, nothing else
pixi run select core            # keycloak,mailpit,minio,postgres,redis
pixi run select admin           # flower,pgadmin,postgres,redis,redisinsight
pixi run select                 # what your current .env asks for
```

Several names travel as one comma-separated argument — the same spelling
`COMPOSE_PROFILES` uses. `./scripts/select.sh postgres redis` takes them
separately if you prefer.

The resolver reads the module files with PyYAML, which the pixi environment
supplies. Reaching `scripts/select.sh` — or any lifecycle script, since they all
resolve now — outside `pixi run` therefore needs an interpreter that has it;
`DEVINFRA_PYTHON` names one (default `python3`), following the same
`DEVINFRA_<TOOL>` convention as `DEVINFRA_COMPOSE`.

#### Bundles

Module names are the thirteen directories under `services/`. Everything else you
can ask for is a **Bundle**, and every one that exists is registered in the root
`compose.yaml`'s `x-bundles:` block — the only place a Bundle name becomes legal.
A `profiles:` entry naming anything the registry does not register fails
`pixi run lint-config`, so a typo cannot quietly invent a fifth Bundle.

| Bundle | Memory | Services |
|---|---|---|
| `minimal` | ~60 MB | `postgres`, `redis` |
| `core` | ~800 MB | `minimal` plus `keycloak`, `minio`, `mailpit` |
| `admin` | ~450 MB | `pgadmin`, `redisinsight`, `flower`, plus the `minimal` data layer they read |
| `observability` | ~725 MB | `otel-collector`, `prometheus`, `loki`, `tempo`, `grafana` |

Footprints are resident-set at idle, rounded; a stack under load wants more. pgAdmin
and the OTel Collector — the two largest single residuals — were measured standalone
rather than under this stack's own configuration, so `admin` and `observability` are
the softer two figures. They are declared in the registry rather than only here, so CI
can require one, and the self-test pins this table against it Bundle by Bundle.

**The Bundles overlap, so do not add these up.** `minimal` is wholly inside `core`, and
`core` and `admin` both count PostgreSQL and Redis. Summing the rows double-counts the
data layer twice over; the shipped default `core,admin,observability` is the whole
stack, which is roughly **1.9 GB**.

Every Bundle is **dependency-closed by declaration**: the services in it already
include everything they depend on, so the name alone is the whole answer. That is
why `admin` lists PostgreSQL and Redis — the three consoles read them, so they
belong to the Bundle rather than being added by the resolver at runtime.

Membership is not listed in the registry. Each service declares the Bundles it
joins in its own `profiles:`, so what a Bundle contains cannot drift from what
actually starts. See [ADR 0014](docs/adr/0014-bundles-are-a-core-owned-registry.md).

```sh
COMPOSE_PROFILES=minimal pixi run up          # exactly two containers
COMPOSE_PROFILES=keycloak pixi run up         # keycloak, and what it needs
COMPOSE_PROFILES=core,admin pixi run up       # Bundles compose
pixi run up-core                              # the core Bundle, by name
```

A one-shot prefix like that is resolved for the task it runs, but it leaves the
*unresolved* value in your environment. `pixi run smoke` is the one task that
reads the runtime rather than the resolver (ADR 0013 exempts it), and loading the
project with an unresolved Selection fails — so it exits 1 naming the variable
rather than skipping every Module and reporting a clean pass. Export a resolved
value if you want both: `export COMPOSE_PROFILES="$(./scripts/select.sh keycloak)"`.

The `observability` Bundle is the expensive one — five containers, and the largest
footprint in the table above. Leave it out of your Selection when you are not using it.

The shipped `.env.example` default is `core,admin,observability`, which resolves to
every Module, so a fresh checkout starts the whole stack. **If your `.env` predates
Selection, check it** — see the callout at the top of this file and
[`CHANGELOG.md`](CHANGELOG.md) for the one-line fix. `docker compose --profile
keycloak` run by hand, bypassing the resolver, is expected to fail on the
dependency it did not select; use `pixi run` tasks, or `scripts/select.sh`,
instead (ADR 0013).

## Connecting your application

Default credentials are in `.env.example`. They are deliberately trivial; this
stack is for local development and binds to loopback only.

```sh
DATABASE_URL=postgresql://devinfra:devinfra@localhost:5432/devinfra

REDIS_URL=redis://:devinfra@localhost:6379/0          # cache
CELERY_BROKER_URL=redis://:devinfra@localhost:6379/1  # broker
CELERY_RESULT_BACKEND=redis://:devinfra@localhost:6379/2

OIDC_ISSUER=http://localhost:8080/realms/devinfra
OIDC_CLIENT_ID=devinfra-api
OIDC_CLIENT_SECRET=devinfra-local-secret

AWS_ENDPOINT_URL=http://localhost:9100
AWS_ACCESS_KEY_ID=devinfra
AWS_SECRET_ACCESS_KEY=devinfra123

SMTP_HOST=localhost
SMTP_PORT=1025

OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

### Redis database allocation

One Redis instance serves three roles, separated by logical database:

| DB | Role |
|---|---|
| 0 | Application cache |
| 1 | Celery broker |
| 2 | Celery result backend |

`maxmemory-policy` is deliberately `noeviction`. Because this instance is also
the Celery broker, an LRU policy would let Redis silently discard queued tasks
and unclaimed results under memory pressure — which presents as tasks vanishing
with no error anywhere. If you need a cache that evicts, run a second Redis
rather than relaxing this one.

### Celery

The stack provides the broker and result backend; run your workers on the host
against them:

```sh
celery -A myapp worker --loglevel=info
celery -A myapp beat --loglevel=info
```

Flower is already pointed at the same broker, so workers appear at
http://localhost:5555 as soon as they connect. It shows an empty cluster until
then, which is expected rather than an error.

## Keycloak

The `devinfra` realm is imported on first boot with three clients and two users.

| Client | Type | Flows | Use |
|---|---|---|---|
| `devinfra-api` | Confidential (secret: `devinfra-local-secret`) | Auth code, password, client credentials | Resource server, machine-to-machine |
| `devinfra-spa` | Public | Auth code + PKCE (S256) | Browser and native apps |
| `devinfra-cli` | Public | Password grant | Fetching a token from a script |

| User | Password | Realm roles |
|---|---|---|
| `dev` | `dev` | `app_user`, `app_admin` |
| `user` | `user` | `app_user` |

Admin console: http://localhost:8080/admin (`admin` / `admin`).

Access tokens carry a `roles` claim and an `aud` of `devinfra-api`, so audience
validation works out of the box. Mint one to inspect:

```sh
pixi run token | jq -r .access_token | cut -d. -f2 | base64 -d | jq
```

Keycloak's SMTP is wired to Mailpit, so password-reset and verification emails
land in http://localhost:8025 instead of going nowhere.

### Editing the realm

`start-dev --import-realm` only *creates* realms that do not already exist — the
strategy is hard-coded and no flag or environment variable changes it — so editing
`services/keycloak/seed/devinfra-realm.json` has no effect on a realm that is
already there. Two ways to work:

```sh
pixi run keycloak-reimport   # replace that realm from the JSON, then restart Keycloak
pixi run keycloak-export     # write the live realm back over the JSON
```

`keycloak-reimport` runs `kc.sh import --override true` inside the running
container and then restarts it. `--override` is remove-and-recreate rather than a
merge: the named realm becomes exactly what the JSON says, and realm state the JSON
does not carry — users, sessions and clients added through the admin console — is
lost. Nothing else is: the `keycloak` database and every other realm survive, and no
database is dropped. The restart afterwards is mandatory, not a courtesy — the import
runs as a separate JVM that never attaches to the running server's cache, so without
it the database and the admin API disagree silently. See
`services/keycloak/gotchas.md`.

## Repository layout

```
compose.yaml                    the stack: the `x-bundles:` Bundle registry and the
                                `include:` module registry, plus the volume and network
                                declarations; it declares no services of its own
common/base.yaml                restart, logging and networks; every module's
                                `extends` target, never itself included
.env.example                    every tunable, with defaults
CHANGELOG.md                    release notes; the breaking change leads it
pixi.toml / pixi.lock           validation tasks and their pinned tools
pyproject.toml                  ruff and mypy settings (no package here)
.yamllint.yaml                  YAML lint rules
.gitattributes                  LF line endings on every checkout
.github/workflows/ci.yml        CI: the static gate, and the stack on Docker and Podman
renovate.json                   what the update bot reads: one regex manager over
                                .env.example, compose.yaml and every module file
.githooks/pre-commit            commit-time: execs `pixi run precommit`, nothing else
.githooks/pre-merge-commit      the same, for the merge commits pre-commit never sees
.githooks/commit-msg            commit-time: execs `pixi run commit-msg`, nothing else
Makefile                        deprecated shims forwarding to pixi tasks
scripts/lib/common.sh           .env loading, defaults and Selection resolution, sourced
                                by the rest
scripts/compose.sh              the container runtime, honouring DEVINFRA_COMPOSE, over a
                                resolved Selection
scripts/select.sh               a Selection expanded to its depends_on closure; the
                                supported way to reach Compose (ADR 0013)
scripts/resolve_selection.py    the closure itself: the Module graph, the profile index
                                and the Selections lint-compose and lint-config validate
scripts/wait-healthy.sh         blocks until healthy; non-zero on timeout
scripts/urls.sh                 every service endpoint
scripts/init-env.sh             .env from the template, never overwriting
scripts/bootstrap.sh            points core.hooksPath at .githooks/, then reads it back
scripts/up-core.sh              the core Bundle, requested by name
scripts/ps.sh                   container status, health and ports
scripts/logs.sh                 tail all services or one
scripts/psql.sh                 psql shell in the postgres container
scripts/redis-cli.sh            redis-cli shell in the redis container
scripts/mc.sh                   shell with the S3 client configured
scripts/backup.sh               pg_dumpall to backups/
scripts/restore.sh              restore a dump; refuses a bad path first
scripts/destroy.sh              deletes every volume; requires typing `destroy`
scripts/keycloak-reimport.sh    replaces the realm from the JSON and restarts
                                Keycloak; requires typing `reimport`
scripts/keycloak-export.sh      live realm back over the JSON
scripts/token.sh                mint an access token via the CLI client
scripts/smoke-test.sh           the smoke driver: preflights, counters, helpers, then a
                                glob over services/*/smoke.sh; SMOKE_STRICT=1 forbids skips
scripts/lint-compose.sh         `config -q` for every Selection: each Module, each Bundle,
                                and every Module at once
scripts/assert_config.py        bind address, host-port collisions, image pinning,
                                identifier-only module volume/network stanzas, and the
                                Module contract: a healthcheck or a justified
                                healthcheck.none, a smoke.sh, a gotchas.md, seed/ or a
                                justified seed.none, an x-endpoints: block naming every
                                published port, x-requires: reconciled against the
                                provider's endpoints and depends_on, its own Module name
                                in every owned service's profiles:, and no service a
                                module directory does not own (ADR 0012, 0013)
scripts/lint_json.py            the JSON check, one file per diagnostic
scripts/assert_pins.py          .env.example and every compose fallback must agree
scripts/assert_renovate.py      the bot's own regexes must still detect every pin
scripts/check_commit_msg.py     the commit-message contract, where it can be tested
scripts/check_gotchas.py        every Module's gotchas.md, entry by entry: the four
                                fields, in order, populated, and a live Verified by:
scripts/podman-socket.sh        stops Docker and enables Podman's API socket (CI)
scripts/assert-podman.sh        proves Podman itself reports the running containers
scripts/lint_selftest.py        proves the lint surface and the scripts hold
services/                       one directory per Module, listed in the order
                                compose.yaml's `include:` reads them; every service
                                is extracted, so this is the whole stack. Every Module
                                also carries smoke.sh, a four-field gotchas.md, and
                                seed/ or seed.none — plus healthcheck.none where its
                                image can run no probe. `lint-config` refuses one that
                                does not
  flower/compose.yaml           the Flower service; volume only, no config files
  grafana/compose.yaml          the Grafana service; depends_on prometheus, loki, tempo
  grafana/conf/provisioning/    datasources + dashboard provider
  grafana/dashboards/           devinfra-overview.json, provisioned from this
                                read-only bind mount on every start; drop more
                                dashboard JSON here, picked up within 30s. The
                                smoke check runs every panel targeting the
                                tempo, loki or prometheus datasource, in every
                                file here, and every one of them has to return
                                data for the marker the suite injected. It
                                substitutes only $service — a panel using any
                                other dashboard variable ($__rate_interval,
                                $__range, a variable of your own) sends the raw
                                $name to the backend, gets a 400 and turns
                                `pixi run smoke` red on a healthy stack
  keycloak/compose.yaml         the Keycloak service; depends_on postgres and mailpit
  keycloak/seed/                realm imported on first boot
  loki/compose.yaml             the Loki service
  loki/healthcheck.none         why the distroless image can run no probe
  loki/conf/loki-config.yaml    single-binary config, filesystem storage
  mailpit/compose.yaml          the Mailpit service; no config files, volume only
  minio/compose.yaml            the Silo server plus the one-shot minio-init helper
                                that provisions its buckets (the volume keeps the
                                minio- prefix; see below)
  otel-collector/compose.yaml   the collector; depends_on loki and tempo, no volume
  otel-collector/healthcheck.none  why the distroless image can run no probe
  otel-collector/conf/          collector pipelines
  pgadmin/compose.yaml          the pgAdmin service; depends_on postgres
  pgadmin/conf/servers.json     the pre-registered Postgres connection
  postgres/compose.yaml         the Postgres service, pulled in by `include:`
  postgres/conf/postgresql.conf dev-tuned config (loaded via config_file)
  postgres/seed/                extensions + extra databases, first boot only
  prometheus/compose.yaml       the Prometheus service
  prometheus/conf/              scrape config
  redis/compose.yaml            the Redis service
  redis/conf/redis.conf         AOF + RDB persistence, noeviction
  redisinsight/compose.yaml     the RedisInsight service; depends_on redis
  tempo/compose.yaml            the Tempo service
  tempo/healthcheck.none        why the distroless image can run no probe
  tempo/conf/tempo.yaml         storage + metrics_generator config
```

## Common tasks

`pixi task list` is the full list. The common ones:

| Task | What it does | Deprecated equivalent |
|---|---|---|
| `pixi run init` | Create `.env` from the template | `make init` |
| `pixi run up` | Start, wait for health, print endpoints | `make up` |
| `pixi run up-core` | Start only the `core` Bundle | `make up-core` |
| `pixi run down` / `stop` | Remove or stop containers, keeping data | `make down` / `stop` |
| `pixi run restart` | Recreate the stack, preserving data | `make restart` |
| `pixi run destroy` | Delete containers **and all volumes** | `make destroy` |
| `pixi run pull` | Pull newer images for every pinned tag | `make pull` |
| `pixi run wait` | Block until every healthcheck passes | `make wait` |
| `pixi run ps` | Status and health of every container | `make ps` |
| `pixi run dump-logs` | Bounded, uncoloured log dump for every service | — |
| `pixi run logs keycloak` | Tail one service (omit the name for all) | `make logs S=keycloak` |
| `pixi run smoke` | End-to-end verification | `make smoke` |
| `pixi run smoke-strict` | The same suite with a skip scored as a failure | — |
| `pixi run urls` | Print every endpoint | `make urls` |
| `pixi run psql keycloak` | psql shell against any database | `make psql DB=keycloak` |
| `pixi run redis-cli 1` | redis-cli against the broker db | `make redis-cli N=1` |
| `pixi run mc` | Shell with the S3 client (`mc`) configured | `make mc` |
| `pixi run backup` | `pg_dumpall` to `backups/` | `make backup` |
| `pixi run restore backups/x.gz` | Restore a dump | `make restore F=backups/x.gz` |
| `pixi run keycloak-reimport` | Replace the realm from the JSON, then restart Keycloak | `make keycloak-reimport` |
| `pixi run keycloak-export` | Write the live realm back over the JSON | `make keycloak-export` |
| `pixi run token dev dev` | Mint an access token | `make token U=dev P=dev` |
| `pixi run config` | Render the resolved compose configuration | `make config` |
| `pixi run lint` | Validate compose, rendered config, pins, the update bot, the gotcha registers, shell, YAML, JSON, Python | `make lint` |
| `pixi run select keycloak` | Print the Modules a Selection resolves to | — |
| `pixi run lint-compose` | `config -q` for every Selection: each Module, each Bundle, and every Module at once | — |
| `pixi run lint-config` | Assert the *rendered* config's ports and image tags, each module's identifier-only volume and network stanzas, and the `x-bundles` registry against what the module files declare | — |
| `pixi run lint-pins` | Assert every pin agrees between `.env.example` and the compose files | — |
| `pixi run lint-renovate` | Assert the update bot's regexes still detect every image pin | — |
| `pixi run lint-gotchas` | Assert every Module's `gotchas.md` carries entries in the four-field shape | — |
| `pixi run test` | Prove the checks and scripts hold their contracts | — |
| `pixi run ci` | The done-gate: lint + test | — |
| `pixi run bootstrap` | Install this clone's git hooks (`core.hooksPath`) | — |
| `pixi run precommit` | The offline half of lint: what the pre-commit hook runs | — |
| `pixi run commit-msg <file>` | Check a commit message file against the contract | — |
| `pixi run ci-stack` | Start the stack, wait for health, run the strict smoke suite | — |
| `pixi run ci-stack-podman` | The same, under Podman, proved to have run there | — |
| `pixi run assert-podman` | Assert Podman reports every running container | — |
| `pixi run ci-podman-socket` | Stop Docker and enable Podman's socket (CI only) | — |

Every script behind these tasks also runs standalone — `./scripts/urls.sh`,
`./scripts/wait-healthy.sh` — so none of this logic is trapped in a task runner.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on every pull
request. It declares no tool version and installs nothing: every step is a
`pixi run <task>` invocation, so the checks that run on a hosted runner are the
same ones you run locally, at the versions `pixi.lock` pins.

Three jobs, in parallel:

| Job | Runs | Bound |
|---|---|---|
| `validate` | `pixi run ci` — compose config for every Selection, the rendered-config assertions, shell, YAML, JSON and Python lint, and the self-test | 10 minutes |
| `stack` | `pixi run ci-stack` — starts a Selection resolving to every Module, blocks until every healthcheck passes, then runs the smoke suite in strict mode | 15 minutes |
| `stack-podman` | `pixi run ci-stack-podman` — the same tasks over the same Selection against Podman, then asserts Podman itself is running the containers | 15 minutes |

The two stack jobs share their task list exactly; only the API the Compose client
talks to differs. `stack-podman` pins `ubuntu-24.04` rather than `ubuntu-latest`,
because the label moves to a release with a different Podman and a different
Compose major, which would silently change what the job proves.

The time bound is enforced, not measured: `timeout-minutes` cancels a job that
overruns and turns the run red. If a stack job ever breaches it, split it into
core and full-profile jobs — never drop a check.

Nothing in the workflow can report success having checked nothing. There is no
`continue-on-error`, no `|| true` and no `if: always()`; the only conditional
steps are `if: failure()` diagnostics that print container status and logs after
the job has already failed. `scripts/lint_selftest.py` asserts all of that
against the workflow file, so it holds in the gate rather than by review.

### Where the smoke checks live

`scripts/smoke-test.sh` is a driver, not a suite. It holds the preflights, the
counters and the shared helpers, then globs `services/*/smoke.sh` and sources each
Module's own checks — so it knows no Module by name, and a new Module joins the
suite by existing rather than by editing Core. A Module that is not running reports
a skip; nothing else about the run changes. `lint-config` refuses a Module with no
`smoke.sh`, and `scripts/lint_selftest.py` asserts that the driver names no Module
and that the glob reaches exactly one script per Module.

### Strict smoke mode

`scripts/smoke-test.sh` skips a service that is not running, which is what you
want when you started a partial Selection. CI starts every Module, so there a
skip is evidence the stack did not come up. `SMOKE_STRICT=1` — what
`pixi run smoke-strict` sets — scores every skip as a failure naming the absent
service. Nothing else about any check changes.

## Commit-time checks

The same tasks, one step earlier. `pixi run bootstrap` installs three git hooks in
your clone by pointing `core.hooksPath` at the tracked `.githooks/` directory:

```sh
pixi run bootstrap   # once per clone
```

It is per clone because it has to be: `.git/hooks` is not part of the tree, so a
hook cannot be committed and no clone arrives with one. The installer reads
`core.hooksPath` back after writing it and refuses if a hook file is not
executable — git ignores a hook it cannot run, and says nothing about it. The
setting lands in the repository config that every linked worktree of a clone
shares, so one run installs the hooks for all of them.

| Hook | Runs | Why |
|---|---|---|
| `pre-commit` | `pixi run precommit` — shell, YAML, JSON, Python, pins and the update bot's regexes | The offline half of `pixi run lint`, so a commit is possible with no container runtime running |
| `pre-merge-commit` | `pixi run precommit` | git runs this, and never `pre-commit`, when `git merge` creates a commit — without it every merge lands unchecked |
| `commit-msg` | `pixi run commit-msg <file>` | The Conventional Commits contract |

Each hook's whole body is a `cd` to the work tree root and an `exec pixi run`.
None names a tool and none pins a version: everything they reach comes from
`pixi.lock`, so the checks that run before your commit are the same ones, at the
same versions, that run on the hosted runner.

`precommit` is deliberately a *subset* of `lint`. `lint-compose` and
`lint-config` resolve the compose model through a container runtime, and a hook
that cannot run while Docker is down teaches you to reach for `--no-verify`
permanently. They stay in the gate; `scripts/lint_selftest.py` asserts the
containment, so the hook can never grow a check `pixi run lint` does not run.

### The commit-message contract

`type(optional-scope)!: description` — type from
`build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`,
`style`, `test`; the `!` marks a breaking change; the description may not be
empty.

```
feat(hooks): install commit-time checks via core.hooksPath   # accepted
feat(api)!: drop the v1 endpoint                             # accepted
Merge branch 'topic' into main                               # accepted — git wrote it
Revert "feat(api)!: drop the v1 endpoint"                    # accepted — git wrote it
Reapply "feat(api)!: drop the v1 endpoint"                   # accepted — git wrote it
fixup! feat(hooks): install commit-time checks               # accepted — git wrote it
Merge the two collector configs into one                     # rejected: prose, not git's
updated the readme                                           # rejected: no type
feature: add hooks                                           # rejected: 'feature' is not a type
fix:                                                         # rejected: empty description
```

`git merge` runs `pre-merge-commit` and then `commit-msg`, the second over the
message git generated itself, which is why git's own forms are accepted rather
than parsed. Comment lines and everything below `git commit --verbose`'s scissors
line are dropped before the message is judged. Only the subject is judged: a
`BREAKING CHANGE:` footer is neither required alongside the `!` nor validated.

### Two things to know

**`git commit --no-verify` bypasses the hooks, by design.** These are fast
feedback, not the gate; CI reads the merged tree and stays authoritative. A
check you cannot get past is one you disable permanently.

**The hooks judge the working tree, not the index.** A partial `git add` is
checked against files your commit does not contain, so an unstaged fix can hide
a committed defect, and an unstaged defect can reject a clean commit. Stashing to
correct that is unsafe in a repository with linked worktrees sharing one stash
stack — CI is the answer to it.

See
[`docs/adr/0011`](docs/adr/0011-commit-time-checks-are-git-hooks-invoking-pixi-tasks.md)
for why `core.hooksPath` rather than the `pre-commit` framework.

## Keeping images current

Every pinned tag is watched by [Renovate](https://docs.renovatebot.com), running
as the Mend-hosted GitHub App installed on this repository. Each image whose
upstream has moved arrives as its own pull request, which `ci.yml` validates
exactly like a human's. Nothing is merged automatically — the bot proposes, CI
gates, you decide.

### The annotation contract

Renovate has no manager that can read this stack's pins: its `docker-compose`
manager skips the `repo:${VAR:-tag}` form, and Dependabot cannot read a dotenv
file at all. So a `customManagers` regex in `renovate.json` reads both places a
tag lives, and each declaration in `.env.example` is immediately preceded by an
annotation naming the repository the compose half uses for it:

```sh
# renovate: datasource=docker depName=redis
REDIS_VERSION=8.10.1-alpine
```

```yaml
    image: redis:${REDIS_VERSION:-8.10.1-alpine}
```

One manager reads both files, so one image at one version resolves to one branch
— and therefore one pull request that moves the declaration and the fallback
together. That is the only shape `pixi run lint-pins` accepts. Add a service and
you add its annotation in the same commit; the reasoning is in
[ADR 0010](docs/adr/0010-image-updates-are-proposed-by-regex-over-the-dotenv-template.md).

### `pixi run lint-renovate`

A regex manager that matches nothing opens no pull requests and reports a clean
run, which looks exactly like "everything is current". `pixi run lint-renovate`
— part of `pixi run lint`, and therefore of `pixi run ci` — refuses that. It
applies `renovate.json`'s *own* `matchStrings` to the files its *own*
`managerFilePatterns` select and fails if any pattern matches nothing, if a
declaration is unannotated, if an annotation names a repository no compose file
agrees with, or if `ci.yml`'s `pull_request` trigger ever grows a `paths:`
filter a bot pull request could fall through. It needs no network, no Node and
no token, so CI runs it on every change.

The real extraction, if you want to see Renovate's own count, needs all three
and is run by hand:

```sh
npx --yes renovate --platform=local --dry-run=extract
```

### What the operator must supply

Nothing. The App holds its own installation credentials, so there is no secret to
mint, scope or rotate here — the repository ships `renovate.json` and the App
supplies everything else. That is also why there is no workflow of our own: a
self-hosted run and the App would both read this configuration, propose the same
updates and race each other over identical branch names (`renovate/grafana-loki-3.x`
is one branch, not two), so exactly one of them may exist. The App is the one.

It runs on Mend's schedule rather than one written down here, which means the
cadence is not this repository's to state. What the App is currently seeing is:
the **Dependency Dashboard** issue it maintains lists every dependency the
`customManagers` regexes detected, so if a pin you expect is missing from that
issue, the regex stopped matching it — check the dashboard before assuming a tag
is simply current.

Its pull requests are checked like anyone else's. `renovate[bot]` is a separate
app installation, not Actions' own `GITHUB_TOKEN`, so its pull requests do trigger
`ci.yml` — `validate`, `stack` and `stack-podman` all run on them, and a bump that
breaks the stack is red before you look at it. (A pull request opened with a
workflow's `GITHUB_TOKEN` triggers no `pull_request` workflow at all; that is the
trap the App sidesteps by not being a workflow.)

One thing a bot pull request cannot do for you: the service table at the top of
this README abbreviates versions (`8.10`, `12.2`), so no regex can maintain it.
Every bump PR carries a note reminding you to update its row before merging.

## Running under Podman

The stack runs unmodified under Podman, and CI proves it on every change rather
than asserting it here. The client stays the stock Compose plugin; only the
Docker API it talks to changes, so `depends_on` ordering, health gating and
`ps --format` output are identical on both runtimes. The reasoning, and what was
rejected, is in
[ADR 0009](docs/adr/0009-podman-is-verified-through-the-docker-compatible-socket.md).

**Linux.** Enable Podman's API socket and point `DOCKER_HOST` at it:

```sh
systemctl --user enable --now podman.socket
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
pixi run up
```

Rootful is what CI uses — rootless healthchecks depend on a user systemd manager
that a hosted runner does not reliably provide. It needs one thing more, because
`podman.socket` is created `root:root` mode `0660` and a non-root user cannot
open it: a drop-in handing it to a group you are in.

The drop-in sets `DirectoryMode` as well as `SocketMode`, and both are load-bearing.
`podman.socket`'s runtime directory is created `root:root` `0700`, and a socket
inside a directory you cannot traverse is unreachable however permissive the socket
itself is — worse, `test -e` on that path answers false, so the socket reads as
absent rather than as walled off, and every later step fails naming nothing.
`DirectoryMode` governs only a directory systemd creates, so one that already exists
— which it does whenever `podman.socket` was active before you started — keeps the
mode it has and has to be widened by hand.

```sh
sudo mkdir -p /etc/systemd/system/podman.socket.d
printf '[Socket]\nSocketGroup=docker\nSocketMode=0660\nDirectoryMode=0755\n' \
  | sudo tee /etc/systemd/system/podman.socket.d/devinfra-socket-group.conf
sudo systemctl daemon-reload
sudo systemctl enable podman.socket
sudo systemctl restart podman.socket   # restart, not `enable --now`: an already-active
                                       # socket ignores a new drop-in until it restarts
sudo chmod 0755 /run/podman            # no-op if systemd just created it; the fix if
                                       # the directory was already there at 0700
export DOCKER_HOST=unix:///run/podman/podman.sock
```

`scripts/podman-socket.sh` is this same sequence, and is what CI runs.

**macOS.** `podman machine start` reports the connection details but does not
export anything into your shell, so set it yourself:

```sh
podman machine start
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
```

**Either, without `DOCKER_HOST`.** Every task routes through `scripts/compose.sh`,
which honours `DEVINFRA_COMPOSE`, so this is the equivalent local form:

```sh
DEVINFRA_COMPOSE="podman compose" pixi run up
```

`podman compose` is itself a wrapper that finds `docker-compose` and sets
`DOCKER_HOST`, so the two forms are the same path with one less indirection.

### No service is excluded

No service is excluded from the Podman run, and there is no quiet way to exclude
one. The self-test pins the `stack-podman` job's `COMPOSE_PROFILES` to a Selection
that *resolves to every Module*, so narrowing that job's Selection reds the gate
whichever name is dropped — a Module name or a Bundle's; and `smoke-strict` scores
an absent service as a failure regardless.

So excluding a service is a reviewed change, not a configuration tweak. The route
the gate permits, in one commit: narrow the `stack-podman` job's
`COMPOSE_PROFILES`, update the self-test's resolves-to-every-Module assertion and
the strict smoke suite so both expect the exclusion, and record the service and the
reason here. Never remove it from the Docker job.

Whether every service passes is the job's answer, not this file's: it starts the
whole stack, blocks on health and runs the strict smoke suite, so a service that
misbehaves under Podman turns the run red rather than going unrecorded.

### Deviations worth knowing

- **`max-file` is inert.** `common/base.yaml`'s `defaults` — the single source
  every service reads, through `extends` — sets `max-size: "10m"` and
  `max-file: "3"`. Podman's compat API accepts unknown log options without
  complaint and reads only `path`, `max-size` and `tag`, so under Podman you get
  one rotated log file rather than three. Nothing fails and nothing warns.
- **`restart: unless-stopped` does not survive a reboot** unless you also
  `systemctl enable podman-restart.service`. Podman honours the policy while it
  is running; it has no always-on daemon to reapply it at boot.
- **`pixi run ci-podman-socket` reconfigures the machine.** It stops
  `docker.socket` and `docker.service` and writes a `SocketGroup=docker` drop-in
  under `/etc/systemd/system` — necessary on a hosted runner, wrong on a
  workstation. It refuses to run unless `CI` is truthy or you pass
  `DEVINFRA_ALLOW_RUNTIME_SETUP=1`. You do not need it locally; the two forms
  above are enough. `pixi run ci-stack-podman` chains it, so run that on a
  throwaway machine only. To undo it:

  ```sh
  sudo rm -f /etc/systemd/system/podman.socket.d/devinfra-socket-group.conf
  sudo systemctl daemon-reload
  sudo systemctl disable --now podman.socket
  sudo systemctl start docker.socket docker.service
  unset DOCKER_HOST
  ```

## Data and persistence

Every stateful service writes to a named volume:

```
postgres-data  redis-data     keycloak-data  minio-data     mailpit-data
pgadmin-data   redisinsight-data             flower-data
prometheus-data               loki-data      tempo-data     grafana-data
```

- `pixi run down` / `pixi run stop` — containers go away, **data stays**
- `pixi run destroy` — containers **and all volumes** deleted; requires typing `destroy`

Verified: with markers written into Postgres, Redis, Keycloak, Silo, Mailpit and
Grafana, a full `down` followed by `up` returns every one of them intact.

### Notes on retention

Telemetry backends keep data far longer than you usually need locally: Prometheus
15 days, Tempo 7 days, Loki until compaction. If the volumes grow inconveniently,
`pixi run destroy` is the blunt fix; per-service retention lives in each backend's
config file under `services/<name>/conf/`.

## Gotchas worth knowing

The things that cost time when building this stack live with the code they bite:
each Module's own `services/<name>/gotchas.md`. Read the Module's file before
changing anything under `services/<name>/`.

They are a checked register, not free prose. Every entry is a `###` heading — the
claim, so the file still skims — followed by four fields, in this order and all
populated:

| Field | What it carries |
|---|---|
| `Symptom:` | What you actually observe when it bites |
| `Cause:` | Why it happens |
| `Fix:` | What to do about it |
| `Affected versions:` | A version expression, or the exact phrase `Not version-specific` |

An entry may add a fifth, `Verified by:`, naming the check that catches a
regression — a backticked repo-relative path, and then prose for a reader.

`pixi run lint-gotchas` enforces the shape: a missing or placeholder field, fields
out of order, a bullet outside an entry, a file with no entries, an H1 that does not
match the directory, or a `Verified by:` naming a path that no longer exists is an
exit 1 naming the file and the defect. It reads Markdown and needs nothing running,
so it is in the pre-commit hook as well as in `pixi run lint`. What it does *not*
judge is whether the content is any good; that is still a review's job. See
[ADR 0016](docs/adr/0016-gotcha-entries-carry-a-checked-shape.md).

This section carries no copies of those entries, deliberately. Two copies of a
gotcha drift, and the one in the README is the copy nobody editing
`services/<name>/` will see.

## Security

Local development only. Everything here is insecure by design: trivial
passwords, TLS disabled, Keycloak in `start-dev` mode, anonymous Grafana admin,
and Postgres running with `synchronous_commit = off`. Ports bind to `127.0.0.1`
so none of it is reachable from your network. Do not deploy any of it.

`.env` is gitignored — keep real credentials out of `.env.example`.

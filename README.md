# devinfra

A Docker Compose stack of infrastructure components for local development. Every
stateful service persists to a named volume, so `docker compose down` and back up
preserves your data — only an explicit `make destroy` throws it away.

## Contents

| Service | Version | Purpose | Endpoint |
|---|---|---|---|
| **PostgreSQL** | 17 (+pgvector) | Primary datastore, Celery result backend, Keycloak persistence | `localhost:5432` |
| **Redis** | 8 | Cache, Celery broker, Celery result backend | `localhost:6379` |
| **Keycloak** | 26.4 | OpenID Connect provider | http://localhost:8080 |
| **Silo** | 2026-09-03 | S3-compatible object storage (maintained MinIO fork) | http://localhost:9101 (API `:9100`) |
| **Mailpit** | 1.28 | Catches all outbound SMTP | http://localhost:8025 (SMTP `:1025`) |
| **pgAdmin** | 9.9 | PostgreSQL web console | http://localhost:5050 |
| **RedisInsight** | 2.70 | Redis web console | http://localhost:5540 |
| **Flower** | 2.0 | Celery task monitoring | http://localhost:5555 |
| **OTel Collector** | 0.140 | Single OTLP ingest point | `localhost:4317` (gRPC) / `:4318` (HTTP) |
| **Prometheus** | 3.7 | Metrics | http://localhost:9090 |
| **Loki** | 3.5 | Logs | http://localhost:3100 |
| **Tempo** | 2.9 | Traces | http://localhost:3200 |
| **Grafana** | 12.2 | Dashboards over all three signals | http://localhost:3000 |

All ports bind to `127.0.0.1` by default, so the stack is not exposed to your
network. Change `BIND_ADDRESS` in `.env` if you need otherwise.

## Requirements

- **Docker** (or a compatible engine) with the Compose plugin — runs the stack.
- **[pixi](https://pixi.sh)** — provisions the validation tooling (`shellcheck`,
  `yamllint`, `python`, `ruff`, `mypy`) from the committed `pixi.lock`, so
  `pixi run lint` checks the same versions on every machine.

## Quick start

```sh
pixi install # fetch the pinned validation tooling (once per clone)
make init    # create .env from the template
make up      # start everything, wait for health, print endpoints
make smoke   # verify every service actually works
```

`make up` typically takes under two minutes on a cold start, most of it Keycloak
booting and importing its realm.

### Profiles

Core services (Postgres, Redis, Keycloak, Silo, Mailpit) always start. The rest
are grouped into profiles, selected via `COMPOSE_PROFILES` in `.env`:

| Profile | Services |
|---|---|
| `admin` | pgAdmin, RedisInsight, Flower |
| `observability` | OTel Collector, Prometheus, Loki, Tempo, Grafana |

```sh
make up-core                              # just the essentials
COMPOSE_PROFILES=admin make up            # essentials + admin UIs
```

The observability profile is the expensive one — five containers and roughly a
gigabyte of RAM. Turn it off when you are not using it.

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
make token | jq -r .access_token | cut -d. -f2 | base64 -d | jq
```

Keycloak's SMTP is wired to Mailpit, so password-reset and verification emails
land in http://localhost:8025 instead of going nowhere.

### Editing the realm

`--import-realm` only creates realms that do not already exist, so editing
`docker/keycloak/realms/devinfra-realm.json` has no effect on a realm that is
already there. Two ways to work:

```sh
make keycloak-reimport   # drop the realm and re-import the JSON (destroys realm state)
make keycloak-export     # write the live realm back over the JSON
```

## Repository layout

```
compose.yaml                    the stack
.env.example                    every tunable, with defaults
pixi.toml / pixi.lock           validation tasks and their pinned tools
pyproject.toml                  ruff and mypy settings (no package here)
.yamllint.yaml                  YAML lint rules
.gitattributes                  LF line endings on every checkout
Makefile                        lifecycle, shells, backup/restore
scripts/smoke-test.sh           end-to-end verification
scripts/lint_json.py            the JSON check, one file per diagnostic
scripts/lint_selftest.py        proves the lint surface cannot silently skip
docker/
  postgres/postgresql.conf      dev-tuned config (loaded via config_file)
  postgres/initdb/              extensions + extra databases, first boot only
  redis/redis.conf              AOF + RDB persistence, noeviction
  keycloak/realms/              realm imported on first boot
  minio/                        buckets provisioned by the minio-init container
                                (volume and paths keep the minio- prefix; see below)
  otel/                         collector pipelines
  prometheus/ loki/ tempo/      backend configs
  grafana/provisioning/         datasources + dashboard provider
  grafana/dashboards/           drop dashboard JSON here; picked up within 30s
```

## Common tasks

```sh
make ps                       # status and health of every container
make logs S=keycloak          # tail one service
make psql DB=keycloak         # psql shell against any database
make redis-cli N=1            # redis-cli against the broker db
make mc                       # shell with the S3 client (mc) configured
make backup                   # pg_dumpall to backups/
make restore F=backups/x.gz   # restore a dump
make urls                     # print every endpoint
pixi run lint                 # validate compose, shell, YAML, JSON, Python
pixi run test                 # prove the lint surface cannot silently skip
pixi run ci                   # the done-gate: lint + test
```

## Data and persistence

Every stateful service writes to a named volume:

```
postgres-data  redis-data     keycloak-data  minio-data     mailpit-data
pgadmin-data   redisinsight-data             flower-data
prometheus-data               loki-data      tempo-data     grafana-data
```

- `make down` / `make stop` — containers go away, **data stays**
- `make destroy` — containers **and all volumes** deleted; requires typing `destroy`

Verified: with markers written into Postgres, Redis, Keycloak, Silo, Mailpit and
Grafana, a full `down` followed by `up` returns every one of them intact.

### Notes on retention

Telemetry backends keep data far longer than you usually need locally: Prometheus
15 days, Tempo 7 days, Loki until compaction. If the volumes grow inconveniently,
`make destroy` is the blunt fix; per-service retention lives in the config files
under `docker/`.

When querying Prometheus for a short-lived series, use `/api/v1/series` or a small
`step`. A `query_range` with a large step can land every evaluation point outside
the 5-minute lookback window and report nothing for data that is present.

## Gotchas worth knowing

These are the things that cost time when building this stack:

- **The Postgres volume mount path is version-specific.** 17 keeps `PGDATA` at
  `/var/lib/postgresql/data`; 18 moved it to `/var/lib/postgresql/18/docker` and
  declares the volume one level up. Using the wrong path for your major version
  gives you a database that silently loses everything on `down`, with no error
  anywhere. Change the mount in `compose.yaml` if you ever move to 18.
- **pgAdmin validates `PGADMIN_DEFAULT_EMAIL`** and refuses to start otherwise.
  It rejects both bare `dev@localhost` and special-use TLDs (`.local`, `.test`),
  hence `dev@example.com`.
- **A `clientScopes` array in a realm import replaces Keycloak's built-ins**,
  which strips `profile`, `email`, `roles` and friends from every client. The
  audience and role mappers here are attached per-client instead.
- **A realm-level `passwordPolicy` is enforced against imported users.** A
  `length(4)` policy makes the import of a user with password `dev` fail the
  whole boot.
- **The Keycloak image has `bash` but no `curl`, `wget`, or `nc`**, so its
  healthcheck drives an HTTP request over bash's `/dev/tcp`.
- **Loki 3.x needs `allow_structured_metadata: true`** to accept OTLP at all,
  and ingests via its native `/otlp` endpoint — the dedicated `loki` exporter in
  the OTel Collector is deprecated.
- **Tempo's span metrics need `--web.enable-remote-write-receiver`** on
  Prometheus, or Grafana's service map stays permanently empty.
- **MinIO is archived; object storage runs Silo, a maintained fork.** Both
  `minio/minio` and `minio/mc` were archived upstream in 2026, and the final
  MinIO release — which fixed a privilege-escalation CVE — was never published
  to any registry, so the newest pullable MinIO image is permanently unpatched.
  Silo preserves the `MINIO_*` environment variables and the on-disk format, so
  this was an image swap with no data migration, and it is reversible: MinIO
  reads Silo-written data and vice versa (both directions verified). The volume
  is still named `minio-data` and the config directory is still `docker/minio/`
  — renaming a volume orphans its data, so those names stay. The same image
  also supplies `mc`, which is why there is no separate client image.

## Security

Local development only. Everything here is insecure by design: trivial
passwords, TLS disabled, Keycloak in `start-dev` mode, anonymous Grafana admin,
and Postgres running with `synchronous_commit = off`. Ports bind to `127.0.0.1`
so none of it is reachable from your network. Do not deploy any of it.

`.env` is gitignored — keep real credentials out of `.env.example`.

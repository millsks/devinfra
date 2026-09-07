# devinfra

A Docker Compose stack of infrastructure components for local development. Every
stateful service persists to a named volume, so `docker compose down` and back up
preserves your data — only an explicit `pixi run destroy` throws it away.

## Contents

| Service | Version | Purpose | Endpoint |
|---|---|---|---|
| **PostgreSQL** | 17 (+pgvector 0.8.6) | Primary datastore, Celery result backend, Keycloak persistence | `localhost:5432` |
| **Redis** | 8.10 | Cache, Celery broker, Celery result backend | `localhost:6379` |
| **Keycloak** | 26.7 | OpenID Connect provider | http://localhost:8080 |
| **Silo** | 2026-09-03 | S3-compatible object storage (maintained MinIO fork) | http://localhost:9101 (API `:9100`) |
| **Mailpit** | 1.31 | Catches all outbound SMTP | http://localhost:8025 (SMTP `:1025`) |
| **pgAdmin** | 9.17 | PostgreSQL web console | http://localhost:5050 |
| **RedisInsight** | 2.70 | Redis web console | http://localhost:5540 |
| **Flower** | 2.1 | Celery task monitoring | http://localhost:5555 |
| **OTel Collector** | 0.160 | Single OTLP ingest point | `localhost:4317` (gRPC) / `:4318` (HTTP) |
| **Prometheus** | 3.14 | Metrics | http://localhost:9090 |
| **Loki** | 3.5 | Logs | http://localhost:3100 |
| **Tempo** | 2.9 | Traces | http://localhost:3200 |
| **Grafana** | 12.2 | Dashboards over all three signals | http://localhost:3000 |

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

### Profiles

Core services (Postgres, Redis, Keycloak, Silo, Mailpit) always start. The rest
are grouped into profiles, selected via `COMPOSE_PROFILES` in `.env`:

| Profile | Services |
|---|---|
| `admin` | pgAdmin, RedisInsight, Flower |
| `observability` | OTel Collector, Prometheus, Loki, Tempo, Grafana |

```sh
pixi run up-core                          # just the essentials
COMPOSE_PROFILES=admin pixi run up        # essentials + admin UIs
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
pixi run token | jq -r .access_token | cut -d. -f2 | base64 -d | jq
```

Keycloak's SMTP is wired to Mailpit, so password-reset and verification emails
land in http://localhost:8025 instead of going nowhere.

### Editing the realm

`--import-realm` only creates realms that do not already exist, so editing
`docker/keycloak/realms/devinfra-realm.json` has no effect on a realm that is
already there. Two ways to work:

```sh
pixi run keycloak-reimport   # drop the realm and re-import the JSON (destroys realm state)
pixi run keycloak-export     # write the live realm back over the JSON
```

## Repository layout

```
compose.yaml                    the stack
.env.example                    every tunable, with defaults
pixi.toml / pixi.lock           validation tasks and their pinned tools
pyproject.toml                  ruff and mypy settings (no package here)
.yamllint.yaml                  YAML lint rules
.gitattributes                  LF line endings on every checkout
.github/workflows/ci.yml        CI: the static gate, and the stack on Docker and Podman
.github/workflows/renovate.yml  the update bot, on a schedule; opens image-bump PRs
renovate.json                   what the bot reads: one regex manager over the two files above
.githooks/pre-commit            commit-time: execs `pixi run precommit`, nothing else
.githooks/pre-merge-commit      the same, for the merge commits pre-commit never sees
.githooks/commit-msg            commit-time: execs `pixi run commit-msg`, nothing else
Makefile                        deprecated shims forwarding to pixi tasks
scripts/lib/common.sh           .env loading and defaults, sourced by the rest
scripts/compose.sh              the container runtime, honouring DEVINFRA_COMPOSE
scripts/wait-healthy.sh         blocks until healthy; non-zero on timeout
scripts/urls.sh                 every service endpoint
scripts/init-env.sh             .env from the template, never overwriting
scripts/bootstrap.sh            points core.hooksPath at .githooks/, then reads it back
scripts/up-core.sh              core services only, profiles cleared
scripts/ps.sh                   container status, health and ports
scripts/logs.sh                 tail all services or one
scripts/psql.sh                 psql shell in the postgres container
scripts/redis-cli.sh            redis-cli shell in the redis container
scripts/mc.sh                   shell with the S3 client configured
scripts/backup.sh               pg_dumpall to backups/
scripts/restore.sh              restore a dump; refuses a bad path first
scripts/destroy.sh              deletes every volume; requires typing `destroy`
scripts/keycloak-reimport.sh    drops the realm db; requires typing `reimport`
scripts/keycloak-export.sh      live realm back over the JSON
scripts/token.sh                mint an access token via the CLI client
scripts/smoke-test.sh           end-to-end verification; SMOKE_STRICT=1 forbids skips
scripts/lint-compose.sh         `config -q` for every combination of declared profiles
scripts/assert_config.py        bind address, host-port collisions and image pinning
scripts/lint_json.py            the JSON check, one file per diagnostic
scripts/assert_pins.py          .env.example and the compose.yaml fallback must agree
scripts/assert_renovate.py      the bot's own regexes must still detect every pin
scripts/check_commit_msg.py     the commit-message contract, where it can be tested
scripts/podman-socket.sh        stops Docker and enables Podman's API socket (CI)
scripts/assert-podman.sh        proves Podman itself reports the running containers
scripts/lint_selftest.py        proves the lint surface and the scripts hold
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

`pixi task list` is the full list. The common ones:

| Task | What it does | Deprecated equivalent |
|---|---|---|
| `pixi run init` | Create `.env` from the template | `make init` |
| `pixi run up` | Start, wait for health, print endpoints | `make up` |
| `pixi run up-core` | Start only the core services | `make up-core` |
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
| `pixi run keycloak-reimport` | Drop the realm db and re-import | `make keycloak-reimport` |
| `pixi run keycloak-export` | Write the live realm back over the JSON | `make keycloak-export` |
| `pixi run token dev dev` | Mint an access token | `make token U=dev P=dev` |
| `pixi run config` | Render the resolved compose configuration | `make config` |
| `pixi run lint` | Validate compose, rendered config, pins, the update bot, shell, YAML, JSON, Python | `make lint` |
| `pixi run lint-compose` | `config -q` for every combination of declared profiles | — |
| `pixi run lint-config` | Assert the *rendered* config's ports and image tags | — |
| `pixi run lint-pins` | Assert every pin agrees between `.env.example` and `compose.yaml` | — |
| `pixi run lint-renovate` | Assert the update bot's regexes still detect every image pin | — |
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
| `validate` | `pixi run ci` — compose config for every profile combination, the rendered-config assertions, shell, YAML, JSON and Python lint, and the self-test | 10 minutes |
| `stack` | `pixi run ci-stack` — starts every profile, blocks until every healthcheck passes, then runs the smoke suite in strict mode | 15 minutes |
| `stack-podman` | `pixi run ci-stack-podman` — the same tasks with the same profiles against Podman, then asserts Podman itself is running the containers | 15 minutes |

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

### Strict smoke mode

`scripts/smoke-test.sh` skips a service that is not running, which is what you
want when you started a partial selection. CI starts *every* profile, so there a
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

Every pinned tag is watched by [Renovate](https://docs.renovatebot.com).
`.github/workflows/renovate.yml` runs the bot weekly (and on demand from the
Actions tab); each image whose upstream has moved arrives as its own pull
request, which `ci.yml` validates exactly like a human's. Nothing is merged
automatically — the bot proposes, CI gates, you decide.

### The annotation contract

Renovate has no manager that can read this stack's pins: its `docker-compose`
manager skips the `repo:${VAR:-tag}` form, and Dependabot cannot read a dotenv
file at all. So a `customManagers` regex in `renovate.json` reads both places a
tag lives, and each declaration in `.env.example` is immediately preceded by an
annotation naming the repository `compose.yaml` uses for it:

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
declaration is unannotated, if an annotation names a repository `compose.yaml`
disagrees with, or if `ci.yml`'s `pull_request` trigger ever grows a `paths:`
filter a bot pull request could fall through. It needs no network, no Node and
no token, so CI runs it on every change.

The real extraction, if you want to see Renovate's own count, needs all three
and is run by hand:

```sh
npx --yes renovate --platform=local --dry-run=extract
```

### What the operator must supply

The bot authenticates with a repository secret named **`RENOVATE_TOKEN`** — a
fine-grained personal access token or a GitHub App installation token with
`contents: write`, `pull-requests: write` and `issues: write` (the last for the
dependency dashboard issue) on this repository. It is deliberately *not* the
workflow's own `GITHUB_TOKEN`: a pull request opened with that token triggers no
`pull_request` workflow, so the bot's proposals would arrive looking validated
with nothing having run. Without the secret the bot cannot authenticate and
proposes nothing, so set it before relying on the schedule.

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

```sh
sudo mkdir -p /etc/systemd/system/podman.socket.d
printf '[Socket]\nSocketGroup=docker\nSocketMode=0660\n' \
  | sudo tee /etc/systemd/system/podman.socket.d/devinfra-socket-group.conf
sudo systemctl daemon-reload
sudo systemctl enable podman.socket
sudo systemctl restart podman.socket   # restart, not `enable --now`: an already-active
                                       # socket ignores a new drop-in until it restarts
export DOCKER_HOST=unix:///run/podman/podman.sock
```

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
one. The self-test pins the `stack-podman` job's `COMPOSE_PROFILES` to the
profile set the model declares, so dropping a profile from that job reds the
gate; the five core services carry no profile at all, so they cannot be dropped
that way even in principle; and `smoke-strict` scores an absent service as a
failure regardless.

So excluding a service is a reviewed change, not a configuration tweak. The route
the gate permits, in one commit: give the service its own profile in
`compose.yaml`, leave that profile out of the `stack-podman` job's
`COMPOSE_PROFILES`, update the self-test's profile-set assertion and the strict
smoke suite so both expect the exclusion, and record the service and the reason
here. Never remove it from the Docker job.

Whether every service passes is the job's answer, not this file's: it starts the
whole stack, blocks on health and runs the strict smoke suite, so a service that
misbehaves under Podman turns the run red rather than going unrecorded.

### Deviations worth knowing

- **`max-file` is inert.** `compose.yaml`'s `x-logging` sets `max-size: "10m"`
  and `max-file: "3"`. Podman's compat API accepts unknown log options without
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
`pixi run destroy` is the blunt fix; per-service retention lives in the config files
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

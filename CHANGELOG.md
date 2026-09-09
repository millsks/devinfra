# Changelog

All notable changes to this project are documented here, in the shape
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) describes, with one
deliberate departure: breaking changes are called out under their own `⚠ BREAKING`
headings and lead the release, ahead of `Added`, so nobody has to scroll to find
what will stop working.

## [Unreleased]

### Changed

Four breaking changes, first — read these before upgrading.

#### ⚠ BREAKING — a `.env` without `COMPOSE_PROFILES` now starts nothing

Every service carries its own profile, so an unset or empty `COMPOSE_PROFILES`
selects **no service at all**. Before Selection existed, a bare `docker compose up`
started five core services whether or not anyone asked for them; it now starts
nothing.

**What breaks.** A checkout whose `.env` predates Selection, or whose `.env` lost
the line. Every lifecycle task — `pixi run up`, `start`, `ps`, `psql`, `logs` —
refuses to run rather than starting nothing and reporting success. You get exit 1
naming the variable and the line to add, and the container runtime is never
invoked. Nothing is silently skipped.

**The fix.** Add one line to `.env`:

```sh
COMPOSE_PROFILES=core,admin,observability
```

That resolves to all thirteen Modules — exactly what the stack started before
Selection existed — so pasting it is a no-op upgrade. It is also what
`.env.example` now ships, so `cp .env.example .env` on a fresh clone gets it for
free.

**How to check.** With the line in place, `pixi run select` prints what your `.env`
starts; run against a `.env` that still lacks it, it exits 1 and names the same line.
Narrow it afterwards if you want less: `COMPOSE_PROFILES=minimal` starts Postgres and
Redis and nothing else.

**One value that is legal but no longer means what it did.** If your `.env` carries
`COMPOSE_PROFILES=admin,observability`, that used to run the two groups *alongside*
the five core services. It now resolves to ten Modules and quietly leaves out
Keycloak, object storage and Mailpit, at exit 0. Run `pixi run select` and compare.

#### ⚠ BREAKING — the profile vocabulary is closed, so an unregistered profile now fails `lint-config`

A service may declare its own Module name and registered Bundle names, and nothing
else. Any other string in a `profiles:` list — a custom group, a name from a
long-lived branch, a typo — now fails `pixi run lint-config` (and therefore
`pixi run lint` and `pixi run ci`) naming the Module, the service and the profile.
Before this change such a name passed every check and quietly entered the
resolver's vocabulary as a Selection nothing described.

**What breaks.** Forks, long-lived branches and local checkouts that added a
profile of their own. This is a hard gate failure, not a warning.

**The fix.** Either register the name in the root `compose.yaml`'s `x-bundles:`
block — an entry needs a `description` and a `memory` footprint, and the Modules
that join it must be closed under `depends_on` — or remove it from the `profiles:`
list. The diagnostic names all three of Module, service and profile, and lists the
Bundles that *are* registered.

#### ⚠ BREAKING — `docker compose --profile admin` now starts five containers instead of failing

`admin` gained PostgreSQL and Redis as members, so that it is dependency-closed by
declaration rather than only by the resolver expanding it. Handed straight to
Compose, bypassing `scripts/select.sh`, `--profile admin` used to exit 1 with
`service "redisinsight" depends on undefined service "redis": invalid compose
project`; it now succeeds and starts pgAdmin, RedisInsight, Flower, PostgreSQL and
Redis.

**What breaks.** Anything relying on that failure — a script that treats the
non-zero exit as "the raw profile is not a valid Selection", or a habit of reading
it as a reminder to go through the resolver. The same change applies to `core` and
`minimal`, which are dependency-closed for the same reason.

**The fix.** Nothing is required; the new behaviour is the intended one. If you
depended on the failure as a guard, note that `--profile <module>` for a Module
with dependencies — `--profile keycloak`, say — still fails exactly as before, and
`pixi run select` remains the supported way to turn a request into a value Compose
can act on.

#### ⚠ BREAKING — a backup is a directory now, and `backups/postgres-*.sql.gz` is refused

`pixi run backup` writes `backups/<timestamp>/` — a manifest, one `pg_dump` per
database, the object-storage bucket contents and the Keycloak realm — instead of a
single `pg_dumpall` stream. `pixi run restore` takes that directory.

**What breaks.** Every archive taken before this change. `backups/` is untracked, so
real `postgres-*.sql.gz` files sit in working clones; `pixi run restore` on one now
exits 1 naming the file and the reason, before the runtime is touched.

**Why it cannot be accepted.** A `pg_dumpall` stream opens with `CREATE ROLE devinfra;`,
which aborts under `ON_ERROR_STOP=1` against *any* initialized cluster — including an
empty one, whose bootstrap role initdb creates from `POSTGRES_USER`. Verified against
`pgvector/pgvector:0.8.6-pg17`; `psql` exits 3. Accepting a legacy archive would mean
dropping `ON_ERROR_STOP=1` for that path, which is exactly the half-applied restore
reported as a success that this change exists to remove (NFR-5).

**The fix.** Take a fresh backup: `pixi run backup`. To read an old archive by hand,
`gunzip` it and apply it yourself, knowing it may only partly land.

#### Everything else

- **`pixi run keycloak-export` no longer exits non-zero on an export that landed.**
  `kc.sh export` against a live container is a second JVM and tried to bind the management
  interface the running server holds on 9000, failing with `Unable to start the management
  interface on 0.0.0.0:9000` *after* writing the realm file — the same collision
  `scripts/keycloak-reimport.sh` already documented for `import`. It now passes
  `--http-management-port 9999`. The export-side entry is in `services/keycloak/gotchas.md`
  with a `Verified by:` line, and the self-test asserts the flag.
- **No Selection resolved through `scripts/select.sh` changes what it resolves to.**
  `pixi run select keycloak`, `admin`, `postgres` and every other request are
  byte-identical to before — profiles were added to, never replaced. The change
  above is about the *raw* profile value handed to Compose without the resolver.
- `.env.example` and both CI stack jobs now spell their Selection
  `core,admin,observability`. It resolves to every Module, as the previous value
  did.
- `scripts/up-core.sh` requests the `core` Bundle instead of naming five services,
  so it holds no membership list of its own.
- The empty-Selection refusal now prints the exact line to add, not only the name
  of the missing variable.
- `pixi run lint-compose` and `lint-config` validate eighteen Selections rather
  than sixteen — the two new Bundles arrived through the resolver's own
  enumeration, with no list edited.
- **The Grafana dashboard provider now sets `allowUiUpdates: false`.** The tracked JSON
  is the only source of truth for a provisioned dashboard: a browser "Save dashboard"
  used to fork a second copy into the `grafana-data` volume that no file described.

  **What changes.** Dashboards provisioned from `services/grafana/dashboards/` can no
  longer be saved from the Grafana UI. Editing in the browser still works — exporting the
  JSON back into that directory is how a change is kept.

  **Why.** It is also the only spelling under which Grafana reports `"provisioned":true`
  for a file-provisioned dashboard, which is the observable the new smoke assertion stands
  on. Verified against `grafana/grafana:13.2.1`.

- **`pixi run keycloak-reimport` no longer drops the `keycloak` database.** It now runs
  `kc.sh import --file … --override true --http-management-port 9999` inside the running
  container and then restarts it.

  **What changes.** The named realm is still replaced wholesale — `--override` is
  remove-and-recreate, not a merge, so realm state the seed JSON does not carry is still
  lost. Everything else now survives: the `keycloak` database, every other realm, and every
  row Keycloak had written that is not realm configuration. The task no longer stops the
  container first, because the import runs *inside* it.

  **Why.** The claim that justified the drop was false. `start-dev --import-realm` ignoring
  an existing realm is true; "so dropping the database is the only way an edited realm file
  takes effect" was not — `kc.sh import` has taken `--override` since Keycloak 21.1.0.
  Verified against 26.4.0, including the two failure modes the register now records: the
  import exits non-zero on the management-port collision with the running server unless
  given a free port, and the running server keeps serving cached realm data until it is
  restarted, so the restart afterwards is mandatory rather than a courtesy (AD-12).

- **`pixi run urls` is generated and Selection-scoped.** `scripts/urls.sh` is now a
  thirteen-line wrapper over the new `scripts/endpoints.py`; the fourteen hard-coded port
  defaults and twelve `printf` lines are gone. It prints every key of every Module's
  `x-endpoints:` block — `LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT` included, which
  the hand-maintained list had silently lost — plus the application variables the
  `x-app-variables:` registry declares, at your own `.env` values.

  **What changes.** The listing now covers only the Modules your Selection resolves to,
  rather than every service whether or not it is running. `pixi run urls postgres` and
  `./scripts/urls.sh --all` take a Selection like every other lifecycle entry point.
  `make urls` and `./scripts/urls.sh` run standalone are invoked exactly as before; what
  they print is the new listing, scoped the same way.

- **The README states a connection detail in one place only.** The `## Connecting your
  application` dotenv block is gone and the section points at
  [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md), in the shape `## Gotchas worth knowing` already
  uses. The `## Contents` table keeps its Endpoint column and its thirteen host addresses —
  deliberately, as the at-a-glance index of what the stack runs — but every port literal in
  it is now pinned by `pixi run lint-endpoints` against what the Modules actually publish,
  and a connection string anywhere else in the file is a build failure. See [ADR 0017](docs/adr/0017-endpoint-documentation-is-generated.md).

- **README's `## Gotchas worth knowing` no longer carries entries of its own.** The eight
  bullets it duplicated from Module files, and the Prometheus `query_range` lookback
  paragraph under `### Notes on retention`, are gone from the README and live in the
  affected Module's `gotchas.md`. The heading stays, because it is a stable reference
  readers and links from outside this repository may already point at, and the section now
  describes the register and points at it. A self-test case fails the build
  if a bolded bullet grows back there, because two copies of a gotcha drift and the copy in
  the README is the one nobody editing `services/<name>/` ever sees.

### Added

- **Backup covers everything stateful in the Selection** — `pixi run backup` now captures
  the Postgres databases, the object-storage bucket contents and the Keycloak realm into one
  timestamped directory with a `manifest.txt`, and records each of those three that the
  Selection does not include as skipped, with its reason. One the Selection *does* include
  that cannot be captured is a non-zero exit and no archive at all — never a smaller backup. Redis is
  deliberately excluded: cache and in-flight task state. Database names come from
  `pg_database` and bucket names from `mc ls --json`, never from `POSTGRES_EXTRA_DATABASES`
  or `MINIO_BUCKETS`, so state an application created is captured too. Object *versions* are
  not: `mc mirror` moves current versions only, and the manifest says so (ADR 0018).
- **Restore is ordered, scoped and fail-loud** — `pixi run restore backups/<ts>` reads the
  manifest, refuses any component the current Selection excludes and any component whose
  files are missing, stops every service that transitively depends on Postgres, rewrites the
  databases with `psql -v ON_ERROR_STOP=1`, mirrors the objects back, starts what it stopped
  and waits for health. A statement that errors ends the restore rather than being counted
  as applied, and the dependents are started again on that path too. Nothing in the script
  names a dependent: `./scripts/select.sh --dependents postgres` answers that, in services.
- **`pixi run ci-stack-restore`, and CI runs it** — `scripts/verify-restore.sh` plants
  marker rows in two Postgres databases and marker objects in two buckets, backs up,
  destroys every volume, brings the stack back, restores, asserts all four markers returned
  and then runs the strict smoke suite. Two of each, because the destroyed volumes re-seed
  the rest and one marker cannot tell a full capture from a narrowed one. It attaches to the existing `stack` job as a third step rather than a
  fourth job, because it is only meaningful against a stack that is already up.
- **`./scripts/select.sh --dependents <module>`** — the resolver's third request form, beside
  `--all` and `--selections`. It prints the *services* in the current Selection whose
  transitive `depends_on` closure reaches that Module, comma-joined, and an empty line when
  there are none; an unknown Module name is exit 1 with nothing on stdout. Services rather
  than Modules because `compose stop` speaks services and a Module with a helper must not be
  half-stopped.
- **`selected <module>` in `scripts/lib/common.sh`** — the membership test for an
  already-resolved Selection, documented as valid only after `select_ambient` /
  `select_profiles`.
- **A provisioned Grafana dashboard, `devinfra overview`** — one panel per signal
  (Tempo traces, Loki logs, Prometheus metrics) over a `service` dropdown, pinned to
  the provisioned datasource UIDs. It is loaded by the existing file provider from
  the read-only `services/grafana/dashboards/` bind mount, so it is there on a fresh
  volume, there again after `pixi run down && pixi run up`, and always exactly what
  the tracked JSON says — never a copy living in `grafana-data`. Grafana no longer
  opens on an empty folder while telemetry it can already reach sits in all three
  backends.
- **The smoke suite renders those panels.** `pixi run smoke` now asserts the dashboard
  reports `"provisioned":true`, then runs each panel's *own* query — read out of the
  dashboard JSON by the new `scripts/check_dashboards.py`, never restated — through
  Grafana's datasource proxy against the trace, log and metric the suite itself
  injects. A panel that returns nothing, or a panel edited into a broken query, is a
  named failure rather than a "No data" box nobody notices. A Selection without
  `otel-collector`, `prometheus`, `loki` or `tempo` skips it naming what is absent,
  and `SMOKE_STRICT=1` scores that skip as a failure like any other.
- **`pixi run ci-stack-cycle`** — takes the stack down, brings it back and re-runs the
  strict smoke suite. Containers go and volumes stay, so anything that passed only
  because it was written into a volume on first init fails here. CI's `stack` job runs
  it as a second step, after `ci-stack`.
- **`defer <function-name>` in `scripts/smoke-test.sh`** — a Module script may register
  a check to run after every Module's script has been sourced, for the one shape of
  check whose subject is a side effect another Module's checks produce. The driver
  still names no Module. See
  [ADR 0015](docs/adr/0015-deferred-smoke-checks.md).
- **Bundles** — a Core-owned registry of the Selection names that are not Modules,
  in the root `compose.yaml`'s `x-bundles:` block, each with a description and an
  approximate memory footprint:

  | Bundle | Memory | Services |
  |---|---|---|
  | `minimal` | ~60 MB | `postgres`, `redis` |
  | `core` | ~800 MB | `minimal` plus `keycloak`, `minio`, `mailpit` |
  | `admin` | ~450 MB | `pgadmin`, `redisinsight`, `flower`, plus the `minimal` data layer they read |
  | `observability` | ~725 MB | `otel-collector`, `prometheus`, `loki`, `tempo`, `grafana` |

  The Bundles overlap, so the figures do not add up: the shipped
  `core,admin,observability` default is the whole stack, roughly 1.9 GB.

  `core` and `minimal` are new; `admin` and `observability` already existed as
  ad-hoc group names and are now registered. Membership is declared per service in
  its own `profiles:`, never listed in the registry, so what a Bundle contains
  cannot drift from what starts. See
  [ADR 0014](docs/adr/0014-bundles-are-a-core-owned-registry.md).
- Every registered Bundle is **dependency-closed by declaration** — the name alone
  already selects everything it needs.
- `pixi run lint-config` checks the registry in three directions: a registered
  Bundle no Module joins fails, a `profiles:` entry no registry entry names fails,
  and a Bundle whose members depend outside themselves fails.
- `CHANGELOG.md`, this file.
- **Every `gotchas.md` is now a checked register.** All 77 entries across the thirteen
  Modules were rewritten into a fixed shape: a `###` heading — the claim, so the file still
  skims — followed by `Symptom:`, `Cause:`, `Fix:` and `Affected versions:`, in that order
  and all populated. `Affected versions:` is a version expression or the exact phrase
  `Not version-specific`; `TBD` and friends are refused. An entry may add a fifth field,
  `Verified by:`, naming the check that catches a regression.
- **`pixi run lint-gotchas`** — `scripts/check_gotchas.py`, stdlib only, globbing
  `services/*/gotchas.md` and refusing an empty set. A missing or placeholder field, fields
  out of order, a bullet outside an entry, a register with no entries, an H1 that does not
  name its directory, or a `Verified by:` naming a path that no longer exists is an exit 1
  naming the file and the defect. It reads Markdown and needs no container runtime, so it
  joins the pre-commit hook as well as `pixi run lint`. It does not judge whether the
  content is any good — that is still a review's job, and the reason ADR 0012's rejection of
  a minimum length stands. See
  [ADR 0016](docs/adr/0016-gotcha-entries-carry-a-checked-shape.md).
- **`docs/ENDPOINTS.md`** — every connection detail this stack publishes, in one generated
  file: an `## Application variables` table naming, for each variable an
  SDK reads, the Module that owns it and the endpoint key it reaches, then one section per
  Module listing that Module's own endpoints. It opens with a do-not-edit banner and is
  rendered against the tracked `.env.example`, so it is a function of tracked inputs alone
  rather than of whoever ran the generator.
- **An `x-app-variables:` registry in the root `compose.yaml`** — the Core-owned registry
  [ADR 0003](docs/adr/0003-two-tier-configuration-namespace.md) specified and nothing ever
  built. One entry per externally-dictated variable name, each naming exactly one owning
  Module and one of that Module's `x-endpoints:` keys, so `REDIS_URL`,
  `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are unambiguous about which service they
  reach and where. `BIND_ADDRESS`, `COMPOSE_PROJECT_NAME` and `COMPOSE_PROFILES` are
  deliberately outside it: they are Compose's names, not an application SDK's, and none
  names an endpoint.
- **`pixi run endpoints` and `pixi run lint-endpoints`** — `scripts/endpoints.py`, the only
  thing in the repository that knows a connection string. The first rewrites
  `docs/ENDPOINTS.md`; the second re-renders it from the tracked inputs and refuses on any
  divergence, then pins three more things: `.env.example` must declare exactly the fallback
  each compose `${VAR:-default}` names, every port literal in the README's `## Contents`
  table must be a resolved endpoint, and no connection string may live anywhere else in the
  README. It needs no container runtime, so it joins the pre-commit hook as well as
  `pixi run lint`. See
  [ADR 0017](docs/adr/0017-endpoint-documentation-is-generated.md).
- **Two entries in the Keycloak register recording what was actually measured** against
  26.4.0: `kc.sh import` run against a live container exits non-zero on the management-port
  collision unless given a free `--http-management-port`, and an out-of-band import leaves
  the running server serving stale cached realm data — the database and the admin API
  disagree, silently, until a restart.
- **The Postgres data directory is now asserted, not just described.**
  `services/postgres/smoke.sh` reads `show data_directory;` and proves some mount in the
  container's `/proc/mounts` covers it, so the version-specific `PGDATA` path — 17 keeps it
  at `/var/lib/postgresql/data`, 18 moved it — fails the suite instead of silently losing
  the database on the next `down`.
- **Prometheus's `--web.enable-remote-write-receiver` is pinned by the self-test.** The
  Prometheus and Tempo registers both stand on that flag; without it Tempo's span-metric
  writes are refused and Grafana's service map stays permanently empty, on a stack that is
  green everywhere else.

### Removed

- **The three "missing from `scripts/urls.sh`" gotcha entries.** The Loki, Tempo and
  Keycloak registers each recorded that their endpoint never reached that script, and the
  same note sat in the affected `x-endpoints:` descriptions. The listing is generated from
  those blocks now, so the drift is gone and a register entry describing it would mislead.

- **The claim that `--import-realm` is the only way to overwrite a realm.** It was in
  `services/keycloak/gotchas.md`, in `README.md`'s `### Editing the realm`, in
  `services/keycloak/compose.yaml`'s `command:` comment and in the header of
  `scripts/keycloak-reimport.sh`, and it was wrong in every one of them. What replaces it,
  in the Keycloak register: the startup import hard-codes ignore-existing and no flag
  changes that, while `kc.sh import --override` replaces one realm, remove-and-recreate,
  leaving the database and every other realm intact.

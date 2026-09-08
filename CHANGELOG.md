# Changelog

All notable changes to this project are documented here, in the shape
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) describes, with one
deliberate departure: breaking changes are called out under their own `⚠ BREAKING`
headings and lead the release, ahead of `Added`, so nobody has to scroll to find
what will stop working.

## [Unreleased]

### Changed

Three breaking changes, first — read these before upgrading.

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

#### Everything else

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

### Added

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

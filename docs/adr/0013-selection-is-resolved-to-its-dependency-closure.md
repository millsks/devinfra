# 13. Selection is resolved to its dependency closure

Date: 2026-09-07 · Status: Accepted

## Context

Thirteen Modules existed and two Selections did. The five core services carried no
`profiles:` key at all, so they started whether or not anyone asked for them: a developer who
wanted Postgres and Redis still booted Keycloak, MinIO and Mailpit. `COMPOSE_PROFILES=admin`
and `COMPOSE_PROFILES=observability` were the only two things anyone could ask for, and
neither is a Module.

`COMPOSE_PROFILES=keycloak` could not be made to work by adding profiles. Compose selects a
service when **any** of its profiles is active and does nothing about that service's
`depends_on` targets, so a Selection naming `keycloak` renders Keycloak and not Postgres, and
`config -q` exits 1 with `service "keycloak" depends on undefined service "postgres"`. The
only way to fix that inside Compose is for Postgres's service to carry the `keycloak`
profile — Keycloak editing Postgres's file, which AD-15 forbids and which does not scale past
two consumers anyway.

Nothing computed a closure. The two enumerations that existed — `scripts/lint-compose.sh`
and `scripts/assert_config.py` — each walked the power set of the declared profiles, which
worked only because there were two of them. With a Module profile on every service there are
fifteen, and 2¹⁵ is 32 768 renders: an enumeration that would never finish.

## Decision

**Every service carries its own Module name in `profiles:`, and no other Module's, added to
whatever it already carried.** Another Module's name in the list would make this service part
of that Module's Selection — `pgadmin` writing `profiles: [pgadmin, postgres, admin]` makes
`select.sh postgres` emit `pgadmin,postgres` — so `lint-config` checks both
directions. `postgres` becomes `[postgres]`, `pgadmin` becomes `[pgadmin, admin]`,
`otel-collector` becomes `[otel-collector, observability]`. The `admin` and `observability`
groups are untouched. A helper takes its primary's set exactly: `minio-init` is `[minio]`.

**`scripts/select.sh` expands a requested Selection into its transitive `depends_on` closure
and prints the resulting `COMPOSE_PROFILES` value.** The request may name a Module or any
profile a Module service declares; **the output is always a set of Module names**, sorted and
comma-joined on one line of stdout. `select.sh keycloak` prints `keycloak,mailpit,postgres`.

**The closure is computed from the Module files, never hand-maintained** (AD-6). A service's
`depends_on` targets map to the Module that declares them, which by AD-8 is `<dir>` for a
service `<dir>` or `<dir>-<role>`. Both Compose spellings are read: the mapping form with
conditions and the bare list form.

**It fails loudly, and prints nothing on stdout when it does** (AD-18, NFR-5). Three refusals,
each exit 1 with a diagnostic on stderr: an empty request, a name no Module and no profile
answers to, and a `depends_on` edge no Module owns. There is no fourth "resolved to nothing"
refusal: every accepted name maps to at least one Module by construction, so a non-empty
request cannot resolve to an empty set, and an advertised failure mode no input can trigger is
the same shape this repository keeps removing. What AD-18 calls the empty Selection is the
empty *request*, which is the first refusal.

`select.sh --selections` prints those Selections, one request per line, which is how
`lint-compose.sh` and `assert_config.py` enumerate.

**Every script that calls `compose` resolves first**, through `select_profiles` /
`select_ambient` in `scripts/lib/common.sh`. Two named exceptions: `lint-compose.sh`, which
drives the Selection under test itself, and `smoke-test.sh`, whose oracle is observational by
contract and whose every Compose subcommand ignores active profiles.

**The lifecycle operations that must act on the whole stack take the all-Modules request.**
`down`, `stop`, `pull`, `dump-logs`, `config` and `destroy` used to carry a hard-coded
`--profile admin --profile observability`; they now set `DEVINFRA_SELECT_ALL=1` and resolve
`--all`. Routing them through the ambient Selection instead would mean that narrowing your
Selection and then running `down` silently orphans the containers you just stopped asking
for.

**The profile power set is retired.** Both enumerations now walk the Selections the resolver
can name: one per Module — AD-6's closure-validity, which is what makes a Module liftable —
one per group profile, and one for every Module at once. Sixteen renders, seconds rather than
hours. Every duplicate-published-port collision is still caught, because the full Selection
contains every Module.

## Rejected

**Making Postgres carry every consumer's profile.** The only in-Compose fix, and it inverts
the dependency direction AD-15 exists to protect: Keycloak would edit Postgres's file, and so
would the next four Modules that need a database.

**Capping the power set.** A cap is arbitrary and leaves the choice of *which* combinations
unexplained. The Selections that exist are the ones the resolver can name; anything else is a
combination nobody can ask for.

**Emitting group names from the resolver.** `select.sh admin` emitting `admin` would re-enter
Compose's own profile semantics and select the three admin services without their Postgres and
Redis — the exact failure this decision exists to prevent. Emitting the closure as Module
names makes "exactly two containers" a property of the string, checkable without starting
anything.

**Writing the closure in bash.** `select.sh` stays the entry point AD-16 names, because that
is what `common.sh` and the pixi task surface can call. The closure itself reads YAML — two
`depends_on` forms, service-to-Module ownership, a profile index — which bash parses badly.
`scripts/resolve_selection.py` holds it, under `mypy --strict` and the self-test. It is
deliberately not named `select.py`: that shadows the stdlib `select` module `subprocess`
imports.

**Reading the closure out of `docker compose config`.** ADR 0012 already established that
Compose discards top-level `x-` keys from an included file; more to the point, the rendered
model is exactly what cannot be produced for an unresolved Selection — asking Compose to
resolve `keycloak` is the thing that exits 1. The closure has to be computed from the source
files, which is what `module_composes()` and `read_model()` do. They moved *down* into the
resolver rather than the resolver importing upward, because `assert_config.py` needs the
resolver's Selection list and the import arrow can only point one way.

## Consequences

**This is a breaking change, and it is the unavoidable one AD-18 names.** Today a bare
`docker compose up` with `COMPOSE_PROFILES` unset started the core five. With a profile on
every service it starts nothing and exits 0. Two mitigations land with the change and both are
asserted, not stated:

* `.env.example` ships `COMPOSE_PROFILES=postgres,redis,keycloak,minio,mailpit,admin,observability`,
  and both CI stack jobs set the same value. The self-test pins that each of the three
  resolves to every Module, so a fresh checkout and both CI jobs start exactly what they
  started before.
* An empty request is refused rather than proceeded with. A `.env` that predates this change
  and lost the variable fails loudly instead of half-working. A `.env` that predates it and
  still carries the old `COMPOSE_PROFILES=admin,observability` is a different hazard — that
  value is still legal, but it now resolves to ten Modules and leaves out Keycloak, MinIO and
  Mailpit at exit 0, so the migration note tells a reader to check theirs with
  `pixi run select`.

A consequence to accept rather than work around: `psql.sh`, `ps.sh` and the rest now refuse to
run when the Selection is empty, even though `exec` and `ps` ignore profiles entirely. That is
AD-18 doing its job at the one seam every script shares.

**A raw `docker compose --profile keycloak` still fails, and that is correct.** Within this
repository the resolver is the supported entry point (AD-16). `config -q` exiting 1 with
`depends on undefined service` is the model telling the truth about an unresolved Selection,
not a defect to paper over.

**`pixi run select` is the developer-facing surface.** `pixi run select keycloak` prints the
closure without reading a script, which is what makes a narrowed Selection something a
developer can check before starting anything.

**Bundles are not this decision.** `x-bundles`, a Bundle registry and the `core`/`minimal`
Bundles are story 2.6. The `admin` and `observability` profiles stay exactly as they are; what
lands here is what makes 2.6's "every Bundle is already dependency-closed" a real assertion
rather than a tautology.

**Three pieces of deferred work close here.** The two duplicate enumerations became one
(DW-1); the power set is named as unsurvivable and gone rather than capped (DW-2); and a
successfully-read but empty profile list is now a failure in `lint-compose.sh` rather than a
pass over one combination (DW-32).

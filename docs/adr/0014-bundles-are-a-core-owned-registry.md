# 14. Bundles are a Core-owned registry of names, not of members

Date: 2026-09-07 · Status: Accepted

## Context

ADR 0013 gave every service its own Module profile and a resolver that expands a request
into its `depends_on` closure. What it left behind was a vocabulary nobody owned.

A Selection could be spelled as a Module name or as one of two ad-hoc group names, `admin`
and `observability`, which existed only because some services happened to write them in
`profiles:`. Three things followed from that, and all three were real.

**Nothing declared which group names were legal.** The resolver's vocabulary *is* the profile
index it builds from the module files, so any string in any `profiles:` list became a
requestable name. A typo'd profile alongside the correct ones — `observabilty`, a name from a
branch that never landed — passed every check in the repository and quietly entered that
vocabulary as a Selection nothing described and nothing validated. Story 2-5 recorded this as
a residual gap: the contract leg it added checks that a service carries its own Module name
and no *other Module's* name, which says nothing about a profile that is neither.

**Nothing stated what a group cost.** NFR-7 asks for a footprint per group. The only figure
stated anywhere was one sentence of README prose about observability being "roughly a
gigabyte", which no check could see and which nothing kept honest.

**Neither group was dependency-closed.** `admin` was `{flower, pgadmin, redisinsight}` and all
three depend outward, on Postgres and Redis. The group worked only because `select.sh` expanded
it at runtime; `COMPOSE_PROFILES=admin` handed straight to Compose selects three consoles
without the databases they read, and `config -q` exits 1 on it. Epics AC 2.6-1 requires a
Bundle to be closed *by declaration*, so that the name alone is already the whole answer.

`up-core.sh` said the same thing a fourth way: `select_profiles postgres redis keycloak minio
mailpit`, a hand-maintained list of five services in a shell script, with nothing tying it to
any declaration elsewhere.

## Decision

**The root `compose.yaml` carries an `x-bundles:` registry, and it is the only place a Bundle
name becomes legal.** Four names are registered: `minimal`, `core`, `admin` and
`observability`. Each entry is a mapping of exactly two keys — `description`, one line saying
what the Bundle is for, and `memory`, its approximate footprint (NFR-7) — both non-empty
strings. Names are Core-owned because a Module cannot know that a name exists; membership is
not (AD-7).

**The registry names Bundles and never lists their members.** Membership stays where AD-7 puts
it: in each service's own `profiles:`. A `modules:` key in a registry entry would be a second,
hand-maintained answer to "what starts", free to drift from the profiles that actually decide
it — exactly the drift AD-7 exists to prevent, and the drift `scripts/urls.sh` already
demonstrates in another file. An unexpected key in an entry is therefore a failure, not an
extra.

**Every registered Bundle is dependency-closed by declaration.** For every Bundle `B`, the set
of Modules whose services declare `B` is closed under `depends_on`. `admin` was not, so
Postgres and Redis join it. `core` and `minimal` are declared the same way, by the Modules
that belong to them: the core five gain `core`, and Postgres and Redis additionally gain
`minimal`. Profiles are added to, never replaced — `postgres` goes from `[postgres]` to
`[postgres, minimal, core, admin]` — so **no existing Selection changes what it resolves to**.
`select.sh admin` printed `flower,pgadmin,postgres,redis,redisinsight` before this change and
prints it after; that equality is what proves the change inert, and it is asserted rather than
argued.

**The profile vocabulary is closed.** A service may declare its own Module name and registered
Bundle names, and nothing else. `pixi run lint-config` checks the registry in three
directions: a registered Bundle no Module joins fails, a `profiles:` entry no registry entry
names fails naming the Module, the service and the profile, and a Bundle whose members reach
outside themselves through `depends_on` fails naming the Bundle and the Module reached but not
joined. That closes the gap story 2-5 recorded.

**`up-core.sh` names the Bundle, not the services.** `select_profiles core` replaces the
five-name list, so the script has no membership statement of its own to drift.

**The resolver stays ignorant of the root file.** `scripts/resolve_selection.py` never reads
`compose.yaml`. The registry is a *validation* input, read by `scripts/assert_config.py`
through a reader of its own — `bundle_registry()`, not `root_declarations()`, because the
latter coerces a non-mapping to `{}` and a malformed registry would then read as "no Bundles"
and pass every check over an empty set.

## Rejected

**Listing members in the registry.** The obvious shape, and the one AD-7 exists to forbid. It
reads well and is wrong the first time a Module joins or leaves a Bundle without the registry
being updated — and nothing would catch that, because Compose renders whatever `profiles:`
says. The footgun AD-7 records is adjacent and real: a `profiles:` key on an `include` entry
is silently ignored, so a membership list that *looked* authoritative would be doubly
misleading. What the registry can own is what no service knows: that the name is legal, what
the Bundle is for, and what it costs.

**Teaching the resolver to read the registry.** Every lifecycle script now resolves through
`select.sh`, so anything the resolver reads becomes a runtime dependency of `ps`, `psql` and
`logs`. The resolver's vocabulary after this change already *is* the Module names plus the
registered Bundle names, because `lint-config` refuses any other — so reading the registry
would buy nothing and cost the runtime path a second file it must parse to start a container.
Validation belongs in the checker.

**Leaving `admin` closed by expansion.** It works today, so the change looks like churn. It
is not: a Bundle closed only by the resolver is a name that cannot be handed to Compose, cannot
be reasoned about without running the resolver, and silently becomes wrong the moment anything
uses the raw value. Closing it by declaration costs two profile entries and changes no
resolved Selection.

**Stating the footprint only in the README.** Prose rots and no check can see it. In the
registry it is a declaration CI can require, and the self-test pins the README's table against
it so the two cannot disagree.

**Making the memory figure precise.** It is an approximation, measured resident-set at idle
and rounded, and it is labelled as one. A figure that claimed precision would have to be
re-measured on every image bump; a rounded one answers the question it is actually asked —
"can I afford to start this?".

## Consequences

**Adding or changing a Bundle is a four-part change, and a check names each part.** Register
the name with a description and a footprint, add it to the `profiles:` of every Module that
belongs to it, make sure that set is closed under `depends_on`, and restate the footprint in
the Bundle tables in `README.md`, `.env.example` and `CHANGELOG.md`. `lint-config` names the
first three: miss the second and it says the Bundle is joined by nothing; miss the third and
it names the Module reached but not joined; miss the first and every service that declared
the name fails the vocabulary leg. The fourth is `pixi run test`'s, not `lint-config`'s — the
self-test parses those three tables and requires them to agree with the registry Bundle by
Bundle. Changing *membership* adds prose the checker cannot see: the entry's own
`description:` and each table's Services column name their Modules, and both have to follow.

**Two more Selections are validated.** `lint-compose.sh` and `assert_config.py` both enumerate
from `select.sh --selections`, which derives Bundles as the profiles that are not Module names,
so `core` and `minimal` arrived in both by construction — sixteen Selections became eighteen
with no list edited anywhere.

**`admin` now contains Postgres and Redis, and that is visible.** `pixi run select admin`
resolves to the same five it always did, but the Bundle *says* five now rather than three,
which is what a reader of `compose.yaml` and the README will see. The alternative was a Bundle
whose stated membership and actual behaviour differed.

**The breaking change ships with its release note.** ADR 0013's mitigations — a non-empty
shipped default and a loud refusal — landed with that decision. The third, leading the README
and the release notes with the break, lands here, because the concrete fix the documentation
offers is `COMPOSE_PROFILES=core,admin,observability` — a line that does not exist until this
registry does. The refusal itself now prints that line rather than only naming the variable,
`CHANGELOG.md` exists and leads with the break, and the self-test drives a `.env` with no
`COMPOSE_PROFILES` line at all through the task surface to prove the refusal happens before
the container runtime is reached.

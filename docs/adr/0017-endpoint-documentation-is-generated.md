# 17. Endpoint documentation is generated

Date: 2026-09-09 · Status: Accepted

## Context

Every connection detail in this repository was written down by hand at least three times.
`scripts/urls.sh` carried fourteen `: "${VAR:=default}"` lines and twelve `printf` lines
under a header claiming "a service missing from this list is a service a developer cannot
find" — while omitting `LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT`. `README.md`'s
`## Contents` table restated sixteen ports, and its `## Connecting your application` section
restated fourteen application variables as a dotenv block. A changed port or credential
changed none of the three, and no check noticed; the drift was recorded as three
`gotchas.md` entries rather than fixed.

The checked source of truth already existed and was read by nothing that produced
documentation. ADR 0012 made a top-level `x-endpoints:` block one of the five things every
Module carries, reconciled it against the ports each module file actually publishes, and
said explicitly that "`scripts/urls.sh` is left alone rather than half-migrated …
generating the script from it is a separate change". This is that change.

The other half was never built at all. ADR 0003 split the namespace into Module variables
(`<MODULE>_<CONCERN>`) and contract variables — the unprefixed names an external SDK
dictates, "listed in a Core-owned registry naming exactly one owning module". No such
registry existed in code. `DATABASE_URL`, `AWS_ENDPOINT_URL` and the twelve names beside
them lived only in a README code fence, owned by nobody.

## Decision

**One generator, `scripts/endpoints.py`, is the only thing in this repository that states a
connection string as documentation.** Code that has to reach a service still builds its own
address — `scripts/token.sh` and `services/prometheus/conf/prometheus.yml` both do — and
that is not what drifts; what drifted was the prose restating those addresses for a
reader. It reads the raw `x-endpoints:` blocks of `services/*/compose.yaml`
through `resolve_selection.read_model()`, the root `compose.yaml`'s new `x-app-variables:`
registry, and a dotenv; it interpolates `${VAR}` and `${VAR:-default}` exactly as Compose
does. It renders two surfaces and nothing else spells one out in prose.

**`x-app-variables:` is ADR 0003's Core-owned registry.** One entry per externally-dictated
name, each naming exactly one owning Module, one of that Module's own `x-endpoints:` keys,
the value, and what an application uses it for. Naming the endpoint key is what makes
`REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` unambiguous where the names
look alike. A name carrying the `<MODULE>_` prefix of a Module *other* than its declared
owner is refused: that is the Module tier wearing a contract name, and it is the collision
the two-tier split exists to prevent.

**The three remaining unprefixed contract names are deliberately out of the registry.**
`BIND_ADDRESS`, `COMPOSE_PROJECT_NAME` and `COMPOSE_PROFILES` are dictated by Compose rather
than by an application SDK, and none of them names an endpoint — there is no Module to own
them and no endpoint key to point at.

**`docs/ENDPOINTS.md` is generated, opens with a do-not-edit banner naming
`pixi run endpoints`, and is rendered against the tracked `.env.example`.** Both sides of the
drift check are therefore functions of tracked inputs alone, and the file a reader gets is
the one a fresh clone actually runs.

**`pixi run lint-endpoints` re-renders from those inputs and refuses on any divergence.** It
joins both `[tasks.lint]` and `[tasks.precommit]`: it reads YAML and Markdown and needs no
container runtime, so it can live in the hook that `lint-config` cannot. Beyond the document
comparison it pins three more things, each guarded by an assertion that the parse found
something first: `.env.example`'s declaration must equal the compose `${VAR:-default}`
fallback for every variable the catalog references; every port literal in the README's
`## Contents` table must be a resolved endpoint; and no connection string may appear
anywhere else in the README.

**`scripts/urls.sh` becomes a wrapper.** It keeps `set -euo pipefail`, the
`scripts/lib/common.sh` source and the `DEVINFRA_PYTHON` seam, and execs the generator with
the caller's Selection. The terminal listing is scoped to the ambient Selection and resolves
against the live environment, so a developer sees their own ports for the stack they are
actually running.

## Rejected

**Generating from `docker compose config`.** Compose discards top-level `x-` keys from an
*included* file — verified, and recorded in ADR 0012 — so the rendered model carries neither
`x-endpoints:` nor `x-app-variables:`. The raw module files are the only source.

**A second hand-maintained list.** A `docs/ENDPOINTS.md` written by hand, or a table in the
README kept beside the generated one, is the problem this change removes wearing a new name.
The generated document is the only place a connection string is spelled out for a reader.

**Rendering the committed document against a developer's `.env`.** It would make
`lint-endpoints` fail for everyone who changed a port locally. A check that fails for
everyone is a check that gets disabled, and a document that differs per machine is not a
document. The live `.env` still drives the terminal listing, which is where a developer
wants their own values.

**Deleting `scripts/urls.sh`.** `pixi run urls`, `make urls` and `./scripts/urls.sh` are
three documented entry points; `MAKE_FORWARDS` in `scripts/lint_selftest.py` pins the second
by set equality, and the README promises the third. A short wrapper keeps all three and
makes the change reviewable as "the list moved" rather than as a deletion plus three
call-site edits.

**Asserting the README's Version column.** The README says already that the Version column
is knowingly manual — a rounded, human-readable figure, not the pinned tag. That reasoning
does not extend to ports, and the port pin is what this ADR adds.

**Extending `assert_config.py`.** `lint-config` is runtime-bound and deliberately outside the
pre-commit hook, so folding the drift check into it would make a commit-time check
impossible for no reason at all — the same argument ADR 0016 made for `lint-gotchas`.

## Consequences

**This supersedes ADR 0012's `urls.sh` consequence, and nothing else in it.** The host-versus-
network semantics, the raw-parse requirement and the Module contract all stand unchanged; the
sentence deferring generation to a later change is now spent.

**Adding a Module gets a documented endpoint for free.** `lint-config` already refuses a
Module with no `x-endpoints:` block, and the generator walks the same glob, so a new Module
appears in `pixi run urls` and in `docs/ENDPOINTS.md` the moment it lands — with a
regenerate, which `lint-endpoints` insists on.

**A new application variable is a two-line registry edit plus a regenerate.** The name, its
owning Module and its endpoint key are declared once, and the document, the listing and the
README pin all follow. There is nowhere else to remember to change.

**A port can no longer be changed in one place.** `.env.example` and the compose
`${VAR:-default}` are pinned to each other, so changing either alone fails the build. That is
deliberate: with the document rendered from the template, changing only a compose default
would otherwise leave the document unchanged, the build green, and the two silently
divergent.

**Three `gotchas.md` entries were removed rather than rewritten.** The Loki, Tempo and
Keycloak registers each recorded that their endpoint was missing from `scripts/urls.sh`.
That drift no longer exists, and a register entry describing a fixed defect is a register
entry that misleads.

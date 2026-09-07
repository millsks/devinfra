# 3. Two-tier configuration namespace

Date: 2026-09-06 · Status: Accepted · Spine: AD-4

## Context

Modules are not configuration-independent. Flower reads `REDIS_PASSWORD`,
`REDIS_BROKER_DB` and `REDIS_BACKEND_DB`; RedisInsight reads `REDIS_PASSWORD`; Keycloak
reads `POSTGRES_USER` and `POSTGRES_PASSWORD`.

A parent `.env` is visible inside included files and **wins** on conflict — a child `.env`
can only ever supply a fallback, never an override. So per-module configuration ownership
is not merely undesirable, it is unimplementable.

A single flat `<MODULE>_<CONCERN>` rule also cannot express every variable. `AWS_ENDPOINT_URL`
is dictated by the AWS SDK; both object storage and AWS emulation legitimately need it, and
neither may rename it. The rule is already violated by `BIND_ADDRESS`, `COMPOSE_PROFILES`
and `COMPOSE_PROJECT_NAME`.

## Decision

One namespace, the root `.env`, containing exactly two kinds of variable:

- **Module variables** — `<MODULE>_<CONCERN>`. Owned by the module they are named for,
  readable by any.
- **Contract variables** — unprefixed names dictated by an external contract, listed in a
  Core-owned registry naming exactly one owning module.

Per-module `.env` files are forbidden. CI fails if two modules claim one contract variable,
or if a module uses an unregistered unprefixed name.

## Rejected

**A single flat naming rule.** Cannot express SDK-dictated names, and would make FR-10
unimplementable.

**Per-module `.env` files.** Would encode a falsehood about ownership, given parent-wins
precedence.

## Consequences

Adding a module that needs an SDK-named variable requires a Core registry edit — deliberate
friction, since that is exactly where two modules can collide invisibly.

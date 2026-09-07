# 2. Dependency validation is Compose-native

Date: 2026-09-06 · Status: Accepted · Spine: AD-3, AD-16, AD-17

## Context

Once services are selectable, a selection can be incoherent — Keycloak without Postgres,
Flower without Redis. FR-3 requires the stack to refuse such a selection and name what is
missing, rather than starting into a broken state.

## Decision

Every cross-module dependency is declared as `depends_on`. Validation is
`docker compose config -q`. No bespoke resolver is written.

Because `depends_on` defaults to `required: true`, Compose already refuses an invalid model:

```
$ docker compose config -q ; echo $?
service "flower" depends on undefined service "redis": invalid compose project
1
```

## Rejected

**A hand-written pre-flight dependency checker.** It would duplicate logic Compose already
has, and drift from it.

## Consequences

A dependency not expressed as `depends_on` is unenforceable and therefore does not exist.
This is a real constraint on module authors.

**`config -q` is blind to host-port collisions.** Two modules publishing the same host port
both validate; `up` then half-starts and reports `port is already allocated`. That gap is
covered separately by a Core-owned port allocation table and a check parsing
`config --format json` for duplicate `published` values.

Selection must be resolved to its dependency closure before Compose sees it, since
selecting `keycloak` alone legitimately fails — Postgres does not carry the `keycloak`
profile, and cannot be made to without Keycloak editing Postgres's file.

# 12. Every Module carries its own contract

Date: 2026-09-07 · Status: Accepted

## Context

A Module was a directory with a Compose fragment and nothing else. Nothing obliged one to
prove it works, to say what it publishes, or to record what bites, and it showed: `smoke.sh`,
`gotchas.md`, `x-endpoints:`, `x-requires:` and any no-seed marker existed in zero Modules.
The stack's one verification was a 436-line `scripts/smoke-test.sh` that Core owned, so a new
service entered the catalog by editing Core — and eight Modules shipped no healthcheck at all,
leaving `wait-healthy.sh` able to see only that their containers were running.

Two things had already drifted in the gap. `scripts/urls.sh` is a hand-maintained endpoint
list whose own header says "a service missing from this list is a service a developer cannot
find", and it omits `LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT`. And the central smoke
script carried Prometheus's scrape-target check inside the collector's block, gated on
`running otel-collector` and with no `else` arm at all: a stack without the collector neither
passed, failed nor skipped it, and nothing said so.

## Decision

A Module directory is not a directory with a compose file in it. It carries five things, and
`lint-config` refuses one that does not:

1. a `healthcheck:` on its primary service, or a `healthcheck.none` carrying a justification;
2. a `smoke.sh`, sourced by `scripts/smoke-test.sh`, holding that Module's own checks;
3. a top-level `x-endpoints:` block naming every `*_PORT` variable the file publishes;
4. a `seed/` directory, or a `seed.none` carrying a justification;
5. a `gotchas.md`.

**A Module owns the service named for its directory, plus helpers named `<dir>-<role>`.** No
module file may declare a service outside that shape. With the root `compose.yaml` already
pinned to declare no `services:` key of its own, that closes the loop in the other direction:
a Compose service that no Module directory owns cannot exist.

**`x-requires:` names the provider Module and the provider's own `x-endpoints:` keys**, and
the requirer must already `depends_on` that provider. Reconciling against the provider's own
declaration keeps the rule generic — no per-provider special case, no checker that knows what
a database is — and ADR 0002 supplies the second half: a dependency not expressed as
`depends_on` does not exist, so without the edge the declaration is free to drift.

**The check is presence-based.** It asks whether the five things exist, never whether their
content was warranted. A marker is the one exception worth stating: an empty one is the silent
skip in file form, so a blank or comment-only marker does not count as present.

**Core gains no list of Modules.** `smoke-test.sh` globs `services/*/smoke.sh` and
`assert_config.py` globs `services/*/compose.yaml`; both refuse a run over an empty set,
because a check that walked nothing has verified nothing.

## Rejected

**A registry in Core naming each Module's files.** That is the thing being removed. A Module
would then enter the suite by being named in Core, which is exactly how the 436-line script
grew.

**Executing `smoke.sh` as a subprocess instead of sourcing it.** The counters are shell
globals and the checks read `.env` values the driver loaded, so a subprocess would need a
counting protocol over stdout or exit codes — a redesign, where sourcing is a move.

**Judging content: requiring a minimum number of checks, or a `gotchas.md` above some length.**
Unfalsifiable in a lint check and an invitation to pad. Presence is what can be asserted; a
review is what judges whether the content is any good.

**Writing `healthcheck.none` for every Module that lacked a healthcheck.** Five of the eight
answered a real readiness URL from inside their own container, and writing a marker instead
would have satisfied this check while leaving `wait-healthy.sh` blind — the exact silent skip
being removed. A marker is for an image that genuinely cannot carry a probe.

## Consequences

Three Modules carry `healthcheck.none`: `otel-collector`, `loki` and `tempo`. All three pinned
images are distroless — no shell, no HTTP client — which was verified against the pinned tags
rather than assumed: `grafana/loki:3.5.7` and `grafana/tempo:2.9.0` still shipped `/busybox`,
and the pinned `3.7.7` and `3.0.3` do not. The exemption is therefore a property of the tag,
and re-verifying it belongs in any version bump. Loki's and Tempo's readiness stays asserted
from the host in their own `smoke.sh`, where `SMOKE_STRICT=1` fails on it.

The Modules run in glob order, which is not the order the central script used. Two Modules fan
out to backends that now come after them, so each carries a silent `await_url` pre-wait on
those backends — no counted check, no Core-side ordering table, and the diagnostic value of a
backend failing as itself is preserved.

`scripts/urls.sh` is left alone rather than half-migrated. `x-endpoints:` is now the complete,
checked source; generating the script from it is a separate change, and until then the drift is
recorded in the affected Modules' `gotchas.md` where a reader will meet it.

**`x-endpoints:` describes the host, and `x-requires:` therefore names host-side keys for
container-side traffic.** An entry is keyed by the `*_PORT` variable that publishes it, and its
URL is what a developer types on their own machine. Inter-Module traffic does not use any of
that: it goes over the `devinfra` network on container ports, so Keycloak reaches Postgres at
`postgres:5432`, not `localhost:${POSTGRES_PORT}`, and `flower` reaches Redis at `redis:6379`.
`x-requires: {postgres: [POSTGRES_PORT]}` is therefore a statement about *which Module supplies
which capability*, reconciled through the provider's endpoint keys because those are the names
both files can agree on — it is not a connection string, and nothing should read it as one. A
Module that publishes a port only in-network would have no key to be required by, and would need
this decision revisited.

**Compose discards top-level `x-` keys from an included file — verified: the rendered model
carries neither block.** That is what makes them free (nothing leaks into a service body, and no
profile combination renders differently) and it is also the constraint on everything downstream:
`docker compose config` cannot be the source for documentation generation, a Bundle registry or a
selection resolver. Anything reading `x-endpoints:` or `x-requires:` must parse the raw
`services/*/compose.yaml` files, exactly as `assert_config.py` does through `module_composes()`
and `read_model()`.

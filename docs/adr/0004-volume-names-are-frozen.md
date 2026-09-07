# 4. Volume names are frozen

Date: 2026-09-06 · Status: Accepted (amended 2026-09-07) · Spine: AD-5

## Context

Data durability is the stack's most load-bearing property: `down` then `up` preserves
everything. A renamed named volume is a *new* volume — the old one is orphaned and the
service starts empty, with no error anywhere. This is the same failure class as the
documented Postgres `PGDATA` path gotcha, and it is unrecoverable in the sense that
nothing surfaces it until data is missing.

## Decision

Named volumes keep their identifiers permanently. Renaming a module never renames its
volume — the `object-storage` module keeps `minio-data`. The `<service-name>-data`
convention yields to this wherever the two disagree.

A module's `volumes:` and `networks:` stanzas contain the identifier and **nothing else**.
All driver keys live in the root `compose.yaml`. CI diffs each module's declaration against
the root and fails on any extra key.

> The `networks:` half of that sentence is superseded — a module declares no `networks:`
> stanza at all. See the 2026-09-07 amendment at the end of this record.

## Rejected

**"The root declaration is authoritative."** This was the first formulation and it is
empirically false. The root wins only on keys the root explicitly sets; every key the root
omits is last-include-wins. A module adding `driver_opts: {type: tmpfs}` silently wins —
meaning **reordering the include registry could change a volume's driver**. Identifier-only
is the only form that actually holds.

**Renaming volumes to match tidied module names.** Tidiness is not worth a data-loss class.

## Consequences

Some volume names will not match their module names, permanently. This is correct and
should not be "fixed".

## Amendment — 2026-09-07: the shared network is never redeclared by a module

The Decision above says a module's `volumes:` **and `networks:`** stanzas carry the
identifier and nothing else. That holds for `volumes:`. It is false for `networks:`, and
story 2-1 found out the hard way: `pixi run ci` passed locally and all three CI jobs failed
with `networks.devinfra conflicts with imported resource`.

### What Compose actually enforces

Across `include`, a module may declare a top-level resource **only if the root's declaration
of it is bare**. If the root's declaration carries any key at all, no module may declare that
resource in any form — identifier-only included.

Verified against `services/postgres/compose.yaml` with two runtimes:

| Module declares | Compose v2.29.7 | Compose v5.3.0 |
|---|---|---|
| `volumes: postgres-data:` and `networks: devinfra:` | **fails**, exit 15 | passes |
| `volumes: postgres-data:` only | passes | passes |
| `networks: devinfra: {driver: bridge}` | **fails**, exit 15 | passes |

So it is not about which keys the *module* uses — the identifier-only form fails just as the
keyed one does. It is about the *root*: `volumes: postgres-data:` is bare, so a module may
name it; `networks: devinfra:` carries `name:` and `driver:`, so no module may.

Compose v5 resolves all three without complaint, which is why this was invisible locally. The
GitHub runners are on the v2 line.

### The corrected rule

A module declares its **volumes** bare — identifier and nothing else, exactly as the Decision
states, and for exactly the reasons it gives.

A module declares **no `networks:` stanza**. The shared `devinfra` network is declared once, in
the root `compose.yaml`, with its `name:` and `driver:`, and is never named again. Nothing is
lost: a module service still joins it, because `common/base.yaml` supplies `networks: [devinfra]`
through `extends`, and a network reference resolves against the assembled model.

Generalised, for `configs:` and `secrets:` too: **a module may name a top-level resource only
when the root's declaration of that resource is bare.**

### Enforcement

`scripts/assert_config.py` reads the root file and every `services/*/compose.yaml` as text and
fails when a module names a resource the root declares with keys. It is a static check, not a
rendered-model one, precisely because the rendered model cannot state this: whether the defect
is visible depends on which Compose the developer has installed. `scripts/lint_selftest.py`
covers both directions — a planted module redeclaring the keyed network is rejected, a bare
volume matching the root's bare declaration is accepted.

The original Decision and its Rejected alternatives stand unchanged. This amendment narrows
where the identifier-only form applies; it does not revisit why.

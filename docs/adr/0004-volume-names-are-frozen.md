# 4. Volume names are frozen

Date: 2026-09-06 · Status: Accepted · Spine: AD-5

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

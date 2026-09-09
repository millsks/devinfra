# Architecture Decision Records

Each ADR records one decision: the context that forced it, what was decided, what was
rejected, and what it costs. They are numbered to match the `AD-n` identifiers in the
architecture spine, so a rule in the spine and its reasoning here share an ID.

The spine is the contract; these are the arguments behind it. Change a decision by adding
a superseding ADR, never by editing a decided one.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-modules-via-compose-include.md) | Modules via Compose `include`, sharing config through `extends` | Accepted |
| [0002](0002-dependency-validation-is-compose-native.md) | Dependency validation is Compose-native | Accepted |
| [0003](0003-two-tier-configuration-namespace.md) | Two-tier configuration namespace | Accepted |
| [0004](0004-volume-names-are-frozen.md) | Volume names are frozen | Accepted |
| [0005](0005-pixi-as-the-task-surface.md) | pixi as the single task surface | Accepted |
| [0006](0006-keep-five-container-observability.md) | Keep the five-container observability bundle | Accepted |
| [0007](0007-catalog-admission-policy.md) | Catalog admission policy | Accepted |
| [0008](0008-object-storage-replacement.md) | Object storage replacement | Accepted |
| [0009](0009-podman-is-verified-through-the-docker-compatible-socket.md) | Podman is verified through the Docker-compatible socket | Accepted |
| [0010](0010-image-updates-are-proposed-by-regex-over-the-dotenv-template.md) | Image updates are proposed by regex over the dotenv template | Accepted |
| [0011](0011-commit-time-checks-are-git-hooks-invoking-pixi-tasks.md) | Commit-time checks are git hooks invoking pixi tasks | Accepted |
| [0012](0012-every-module-carries-its-own-contract.md) | Every Module carries its own contract | Accepted |
| [0013](0013-selection-is-resolved-to-its-dependency-closure.md) | Selection is resolved to its dependency closure | Accepted |
| [0014](0014-bundles-are-a-core-owned-registry.md) | Bundles are a Core-owned registry of names, not of members | Accepted |
| [0015](0015-deferred-smoke-checks.md) | A smoke check whose subject is another Module's side effect is deferred by the driver | Accepted |
| [0016](0016-gotcha-entries-carry-a-checked-shape.md) | Gotcha entries carry a checked shape | Accepted |
| [0017](0017-endpoint-documentation-is-generated.md) | Endpoint documentation is generated, not hand-maintained | Accepted |
| [0018](0018-backup-follows-the-selection.md) | Backup follows the Selection, and restore is ordered and fail-loud | Accepted |

## Supersessions

A decided ADR is never edited, so where a later one replaces part of an earlier one the
pointer lives here.

| Superseded | By | What changed |
|---|---|---|
| [0012](0012-every-module-carries-its-own-contract.md) — its `scripts/urls.sh` consequence only | [0017](0017-endpoint-documentation-is-generated.md) | 0012 left `urls.sh` hand-maintained and said generating it from `x-endpoints:` "is a separate change". 0017 is that change: the listing and `docs/ENDPOINTS.md` are both generated. Everything else in 0012 — the host-versus-network semantics, the raw-parse requirement, the Module contract — stands unchanged. |

## Related

- [`../architecture-walkthrough.html`](../architecture-walkthrough.html) — visual walkthrough of the
  spine, including an interactive selection-closure explorer. Open it in a browser.
- `_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md`
  — the spine itself, and `MIGRATION-PLAN.md` beside it.

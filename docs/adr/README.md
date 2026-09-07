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

## Related

- [`../architecture-walkthrough.html`](../architecture-walkthrough.html) — visual walkthrough of the
  spine, including an interactive selection-closure explorer. Open it in a browser.
- `_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md`
  — the spine itself, and `MIGRATION-PLAN.md` beside it.

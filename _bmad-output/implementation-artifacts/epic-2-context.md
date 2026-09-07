# Epic 2 Context: Take only what you need

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Today the stack is one monolithic Compose file whose only granularity is a handful of whole-profile combinations, so a developer who wants Postgres and Redis still boots Keycloak and an observability gigabyte. This epic decomposes every service into a self-contained Module directory that owns its own Compose fragment, config, seed data, smoke check and documentation, and reassembles them through Compose `include`. On top of that it adds a Selection resolver that expands a requested set to its transitive dependency closure, and named Bundles for the sets used together — so asking for one service quietly brings what it depends on, and asking for two starts exactly two containers. The decomposition is the foundation the later epics build on: endpoint documentation is generated from Module metadata, backup scope follows the Selection, and every new catalog service enters through the Module contract established here.

## Stories

- Story 2.1: The shared fragment and the first Module
- Story 2.2: The remaining core Modules
- Story 2.3: The admin and observability Modules
- Story 2.4: Every Module carries its own contract, enforced
- Story 2.5: Selecting a Module brings what it needs
- Story 2.6: Bundles, and a safe path through the breaking change

## Requirements & Constraints

- One Module owns exactly one primary service, including its Compose fragment, config files, environment variables and smoke checks.
- A developer can declare which Modules and Bundles to run and get exactly that set and nothing else; a Bundle is a curated, known-good alternative to enumerating Modules.
- An incoherent Selection is refused before anything starts, with the missing service named, and no container or volume created.
- The smoke suite runs only the checks belonging to Modules in the current Selection and reports the rest as skipped, never as passed or failed. Carving the central script into per-Module checks must leave the suite's pass count unchanged.
- Every Module ships five things: a healthcheck, a smoke check exercising real function (not liveness), an endpoint-contract declaration, either seed data or an explicit justified no-seed marker, and a gotchas file. The check is presence-based only — never a judgement about whether seed data was warranted.
- **Data durability is the overriding constraint.** `down` then `up` must preserve every stateful service's data; only an explicit confirmed destroy removes volumes. Any change that violates this is a defect regardless of what it enables. Capture the volume inventory before and after every extraction — new volumes are suspicious, missing ones are an emergency.
- A full Selection reaches all-healthy in under two minutes on a warm cache, and the start command blocks until healthy rather than returning optimistically.
- Published ports bind to the configured bind address, loopback by default, so nothing here is reachable from the network.
- **The security posture is fixed, not improved.** Reject any change that partially hardens the stack, including one arriving incidentally inside a refactor.
- Every Bundle documents its approximate memory footprint.
- Malformed or missing required configuration fails at startup naming the variable, rather than defaulting into a subtly wrong state.

## Technical Decisions

**Composition mechanics**

- **Microkernel split.** A Core Substrate — the include registry, the shared base fragment, the network, the configuration namespace, volume identity, the host-port table, the Bundle registry, the resolver and the task surface — plus pluggable Service Modules under `services/<name>/`. Every later rule below that says "Core" means that substrate. A Module touches Core solely to add its own registry entry, its identifier-only volume declaration, its port-table row and its Bundle membership; anything more is out of bounds.
- Shared service configuration flows through `extends` pointing at the shared base fragment — never YAML anchors, which are document-scoped and cannot cross `include` boundaries. The base fragment is referenced only by `extends` and never appears in the include list.
- The base fragment may declare only `restart`, `logging` and `networks`. Never `depends_on`, `links`, `volumes_from` or `network_mode: service:*` — `extends` inherits those keys without importing the resources they name. Any one-shot helper extending the base must override `restart: "no"` or it restart-loops.
- All Module paths are relative and rewritten against the Module's own directory — no absolute paths. `project_directory` is never used, and no Module assumes the repository root is the Compose working directory.
- **The rendered `docker compose config` output, captured before an extraction and compared after, is the primary verification.** The two must be identical apart from key ordering.

**Identity and namespaces (current rules — AD-5 was amended 2026-09-07)**

- Volume identifiers are frozen permanently. Renaming a Module never renames its volume: the object-storage Module keeps the volume named `minio-data`, and freezing beats tidiness wherever the naming convention disagrees.
- A Module's top-level `volumes:` stanza contains the identifier and nothing else — no driver, no driver options, no attachable. Every other key lives in Core, because keys Core omits are last-include-wins and reordering the include registry could otherwise change a volume's driver.
- **A Module never declares the shared `networks:` stanza at all.** Core declares the network with keys, and Compose v2 rejects the whole model when any included file names a resource Core declares with keys. The general rule: a Module may name a top-level resource only when Core's declaration is bare. Newer Compose merges instead of rejecting, so the identifier-only form passes locally and fails CI — a static check enforces this on every Compose version.
- Configuration has two tiers in one root `.env`: Module variables named `<MODULE>_<CONCERN>`, and contract variables whose names are dictated externally and are registered to exactly one owning Module. Per-Module `.env` files are forbidden.
- Host ports come from one Core-owned allocation table, published as `${BIND_ADDRESS}:${<MODULE>_PORT}:<container-port>`. `docker compose config -q` is blind to port collisions, so CI parses rendered config for duplicate published values.
- A Module declares what it needs from another Module as an `x-requires:` block in its own file; CI reconciles it against the provider. Endpoint metadata lives per Module as an `x-endpoints:` block naming each connection variable that Module publishes, and is the sole input to the generated Endpoint Contract documentation later. A Module never edits another Module's file. Dependencies point inward only — Core never depends on a Module.

**Selection and correctness**

- Cross-Module dependencies are plain `depends_on` with explicit conditions, and `docker compose config -q` is the validation gate. No bespoke resolver is written for correctness — a dependency not expressed as `depends_on` does not exist.
- Selection is expanded to its transitive closure by the resolver script before Compose sees it, and the resolver emits the profiles value. Every registered Bundle must already be dependency-closed, verified by CI, so no Bundle relies on runtime expansion.
- A Module validates together with its dependency closure, computed by the resolver, never hand-maintained — validating a Module file alone passes vacuously because profiles render an empty service set.
- A Module carries a profile equal to its own name plus its Bundle profiles, and a service starts when any of its profiles is active.
- Bundle names live in a Core-owned registry; membership is declared per service as a profile list. A profile key attached at the include level is silently ignored, so membership must be per-service. CI fails on a Bundle profile no Module joins, or a Module claiming a Bundle absent from the registry.
- The resolver fails loudly on an empty resolved Selection rather than starting nothing at exit 0, and is tested for closure correctness, empty-Selection refusal and unknown-name refusal. It is load-bearing for four separate invariants — but the bar is general: **Core scripts are verified like code, not trusted like config** — `set -euo pipefail`, shellcheck-clean under CI, with tests that may not be skipped.
- A Module owns one primary service plus explicitly declared helpers named `<module>-<role>`, each carrying a profile set identical to its primary's. The contract check runs both directions: every Module directory maps to a service, and every service maps to a Module.
- CI iterates every Module and every Bundle. No step may skip a check because a tool or fixture is unavailable.

**The breaking change**

Giving every service a profile is what makes Selection possible, but a service with profiles does not start unless one is active — so a bare start with no profiles set goes from starting the core five to starting nothing at exit 0. Three mitigations are all required: the shipped example env ships a non-empty default so a fresh copy starts the same services as before; the resolver fails loudly with a message naming the variable and the fix; and the change leads the README and the release notes.

## Cross-Story Dependencies

- Stories are strictly ordered. Postgres is extracted first because it has no dependencies, isolating the extraction mechanism from the closure problem; the remaining core follows because cross-file `depends_on` and the declared-helper pattern surface there, then admin, then observability last because the collector fan-out and the telemetry round-trip are the most entangled. Rollback for an extraction is reverting the commit — no volume is touched.
- The contract check, the Bundle registry and the Selection resolver all require the extraction to be complete — they enumerate Modules and cannot run against inlined services.
- Epic 2 depends on nothing structurally, but is sequenced after Epic 1 so that CI and current image pins exist before the refactor. Debugging a major image upgrade and a file split simultaneously is the worst available ordering.
- Epic 3 builds on this: endpoint documentation is generated from Module metadata and backup scope follows the Selection. Epic 4 admits every new service through the Module contract defined here.

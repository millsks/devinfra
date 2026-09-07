# Epic 2 Context: Take only what you need

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Today the stack is one monolithic Compose file whose only granularity is a few whole-profile combinations, so a developer who wants Postgres and Redis still boots Keycloak and an observability gigabyte. This epic decomposes every service into a self-contained Module directory that owns its Compose fragment, config, seed data, smoke check and documentation, and reassembles them through Compose `include`. On top of that sits a Selection resolver that expands a requested set to its transitive dependency closure, plus named Bundles for the sets used together — so asking for one service quietly brings what it depends on, and asking for two starts exactly two containers. The decomposition is the foundation later epics build on: endpoint documentation is generated from Module metadata, backup scope follows the Selection, and every new catalog service enters through the Module contract established here.

## Stories

- Story 2.1: The shared fragment and the first Module
- Story 2.2: The remaining core Modules
- Story 2.3: The admin and observability Modules
- Story 2.4: Every Module carries its own contract, enforced
- Story 2.5: Selecting a Module brings what it needs
- Story 2.6: Bundles, and a safe path through the breaking change

## Requirements & Constraints

- One Module owns exactly one primary service — its Compose fragment, config files, environment variables and smoke checks.
- A developer declares which Modules and Bundles to run and gets exactly that set and nothing else. A Bundle is a curated, known-good alternative to enumerating Modules.
- An incoherent Selection is refused before anything starts, naming the missing service, with no container or volume created.
- The smoke suite runs only the checks belonging to Modules in the current Selection and reports the rest as skipped, never as passed. Carving the central script into per-Module checks must leave the suite's pass count unchanged.
- Every Module ships five things: a healthcheck, a smoke check exercising real function (not liveness), an endpoint-contract declaration, either seed data or an explicit justified no-seed marker, and a gotchas file. The check is presence-based — never a judgement about whether seed data was warranted.
- **Data durability is the overriding constraint.** `down` then `up` preserves every stateful service's data; only an explicit confirmed destroy removes volumes. Any change violating this is a defect regardless of what it enables. Capture the volume inventory before and after every extraction.
- A full Selection reaches all-healthy in under two minutes on a warm cache, and the start path blocks until healthy rather than returning optimistically.
- Published ports bind to the configured bind address (loopback by default). Every Bundle documents its approximate memory footprint.
- Missing or malformed required configuration fails at startup naming the variable, rather than defaulting into a subtly wrong state.
- **The security posture is fixed, not improved.** Reject any change that partially hardens the stack, including one arriving incidentally inside a refactor.

## Technical Decisions

**Composition mechanics**

- A Core Substrate (include registry, shared base fragment, network, configuration namespace, volume identity, host-port table, Bundle registry, resolver, task surface) plus pluggable Modules under `services/<name>/`. A Module touches Core only to add its own registry entry, identifier-only volume declaration, port-table row and Bundle membership.
- Shared configuration flows through `extends` against the shared base fragment — never YAML anchors, which are document-scoped and cannot cross `include` boundaries. The base fragment is referenced only by `extends` and never appears in the include list.
- The base fragment may declare only `restart`, `logging` and `networks` — never `depends_on`, `links`, `volumes_from` or `network_mode: service:*`, because `extends` inherits those keys without importing the resources they name. Any one-shot helper extending the base must override `restart: "no"` or it restart-loops.
- Module paths are relative to the Module's own directory; no absolute paths, no `project_directory`, no assumption that the repository root is the Compose working directory.
- **Rendered `docker compose config` captured before an extraction and compared after is the primary verification** — identical apart from key ordering.

**Identity and namespaces**

- Volume identifiers are frozen permanently; renaming a Module never renames its volume (the object-storage Module keeps `minio-data`). Freezing beats naming tidiness.
- A Module's top-level `volumes:` stanza contains the identifier and nothing else. Keys Core omits are last-include-wins, so a driver key in a Module could otherwise change behaviour depending on include order.
- **A Module never declares the shared `networks:` stanza at all.** Core declares the network with keys, and Compose v2 rejects the model when an included file names a keyed Core resource; newer Compose merges it, so the identifier-only form passes locally and fails CI. A static check enforces this across versions. General rule: a Module may name a top-level resource only when Core's declaration is bare.
- One root `.env`, two tiers: Module variables `<MODULE>_<CONCERN>`, and externally-dictated contract variables registered to exactly one owning Module. Per-Module `.env` files are forbidden.
- Host ports come from one Core-owned allocation table. `docker compose config -q` is blind to port collisions, so CI parses rendered config for duplicate published values.
- A Module declares what it needs from another as an `x-requires:` block in its own file; CI reconciles it against the provider. `x-endpoints:` names each connection variable the Module publishes and is the sole input to generated endpoint documentation later. A Module never edits another Module's file; dependencies point inward only.

**Selection and correctness**

- Cross-Module dependencies are plain `depends_on` with explicit conditions, and `docker compose config -q` is the correctness gate. No bespoke resolver is written for correctness — a dependency not expressed as `depends_on` does not exist.
- The resolver expands a Selection to its transitive closure and emits the profiles value before Compose sees it; every task that invokes Compose goes through it. A raw profile invocation that bypasses it and fails is correct behaviour, not a defect.
- Every registered Bundle must already be dependency-closed, verified by CI — no Bundle relies on runtime expansion.
- A Module validates together with its closure, computed by the resolver, never hand-maintained: validating a Module file alone passes vacuously because profiles render an empty service set.
- A Module carries a profile equal to its own name plus its Bundle profiles; a service starts when any profile is active. Bundle names live in a Core-owned registry, membership is per-service (a `profiles` key on an include entry is silently ignored). CI fails on a Bundle profile no Module joins, or a Module claiming an unregistered Bundle.
- The resolver fails loudly on an empty resolved Selection rather than starting nothing at exit 0, and is tested for closure correctness, empty-Selection refusal and unknown-name refusal. **Core scripts are verified like code, not trusted like config** — shellcheck-clean with tests that CI may not skip.
- A Module owns one primary service plus explicitly declared helpers named `<module>-<role>`, each carrying a profile set identical to its primary's. The contract check runs both directions: every Module directory maps to a service, and every service maps to a Module.
- CI iterates every Module and every Bundle; no step may skip a check because a tool or fixture is unavailable.

**The breaking change**

Giving every service a profile is what makes Selection possible, but a service with profiles does not start unless one is active — so a bare start with no profiles set goes from starting the core five to starting nothing at exit 0. Three mitigations are all required: the shipped example env carries a non-empty default so a fresh copy starts the same services as before; the resolver fails loudly with a message naming the variable and the fix; and the change leads the README and release notes.

## Cross-Story Dependencies

- Stories are strictly ordered. Postgres is extracted first because it has no dependencies, isolating the extraction mechanism from the closure problem; the remaining core follows because cross-file `depends_on` and the declared-helper pattern surface there; then admin; then observability last, because the collector fan-out and the telemetry round-trip are the most entangled. Rollback for an extraction is reverting the commit — no volume is touched.
- The contract check, the Bundle registry and the Selection resolver all require the extraction to be complete — they enumerate Modules and cannot run against inlined services.
- Epic 2 depends on nothing structurally, but is sequenced after Epic 1 so CI and current image pins exist before the refactor: debugging a major image upgrade and a file split simultaneously is the worst available ordering.
- Epic 3 builds on this (endpoint docs generated from Module metadata, backup scope following the Selection); Epic 4 admits every new service through the Module contract defined here.

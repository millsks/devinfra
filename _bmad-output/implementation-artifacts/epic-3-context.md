# Epic 3 Context: Batteries actually included

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Several things in this stack are provisioned but empty, or documented but unproven: Grafana watches a dashboard directory holding only a placeholder, the README lists connection strings that nothing verifies, backup covers Postgres and nothing else so a destroy loses every bucket and realm regardless, and hard-won failure modes live as README prose rather than as a maintained artifact. This epic closes the gap between provisioned and useful — dashboards over all three signals on first boot, endpoint documentation generated from configuration so it cannot drift, backup and restore covering every stateful service in the current selection with safe ordering, a gotcha register in a fixed four-field shape filed against the module it affects, and one runnable example that exercises every endpoint contract end to end so a broken contract fails a build instead of being discovered mid-project. It builds directly on the modular decomposition: endpoint documentation is generated from module metadata and backup scope follows the selection.

## Stories

- Story 3.1: Grafana opens on working dashboards
- Story 3.2: Gotchas become a maintained register
- Story 3.3: Connection details generated, not hand-maintained
- Story 3.4: Backup covers everything stateful
- Story 3.5: A worked example that proves the contracts

## Requirements & Constraints

- Dashboards over traces, logs and metrics must exist after starting the observability selection with **no manual steps**, be restored from provisioning rather than volume state on a fresh volume, and survive a `down`/`up` cycle.
- Dashboard verification asserts that one panel per signal **renders non-empty** against the trace, log and metric the smoke test itself injects — presence of a dashboard file with a resolving datasource is not sufficient, and a dashboard with broken panels must fail.
- Every service in the catalog gets an endpoint entry generated from module metadata, using the same variable names an application would actually use. A changed port or credential either changes the generated documentation or fails the build; silent divergence is the failure this exists to prevent.
- The endpoint listing covers exactly the services in the current selection and omits the rest. Today's equivalent prints unconditionally and omits two observability services, so this is a behavior change rather than a restatement.
- Endpoint variable naming stays conventional so framework auto-wiring (Spring Boot Compose support, Quarkus Dev Services and similar) can consume the stack rather than fight it.
- Backup captures Postgres databases, object-storage bucket contents and the Keycloak realm; services outside the selection are **skipped, not failed**. Redis is deliberately excluded — it holds cache and in-flight task state, neither meaningful to restore.
- Restore into an empty stack reproduces the captured state and is verified by a smoke run against the restored stack. Any failing step surfaces and exits non-zero — no partial restore may exit 0.
- The existing backup/restore command interface continues to work; a nominal (single-digit GB) backup completes in under 60 seconds.
- Every gotcha currently in the README appears in the register with all four fields populated and named against its module. The register is corrected as well as migrated: the obsolete claim that realm import cannot overwrite an existing realm is removed, and two verified entries added — the import exiting non-zero on a management-port collision against a running container, and a running server serving stale cached realm data after an out-of-band import.
- A gotcha whose fix is mechanically verifiable gets a corresponding smoke check or CI assertion, so a regression is caught rather than re-documented. The version-specific Postgres data-directory path is the highest-severity entry and the strongest candidate — a marker written, stack cycled, marker read back.
- The realm re-import path stops dropping the Keycloak database. This behavior change belongs to the register work: correcting the gotcha requires correcting the target that justified it.
- The worked example runs against a documented selection with **no code changes**, and must genuinely exercise each of its six integrations rather than merely configure them. It exists to prove wiring and states in its own README that it is not a production template. One example in one language is sufficient.
- Data durability is absolute: `down` then `up` preserves every stateful service's data; only an explicit confirmed destroy removes volumes.
- The security posture is fixed, not improved — anonymous Grafana admin and trivial credentials are correct here. Reject hardening that arrives incidentally inside this work.

## Technical Decisions

- **Dashboards are seed data, not volume state.** They live in the Grafana module's seed directory and load through provisioning on every start, which is what makes the fresh-volume and `down`/`up` criteria satisfiable at all.
- **The observability bundle stays five separate containers** — collector, Prometheus, Loki, Tempo, Grafana — each its own module with its existing tuned configuration. The single-image LGTM bundle is explicitly not adopted; it would discard two already-solved gotchas (structured-metadata support in the log backend, and span metrics requiring remote-write into Prometheus) that the dashboards depend on. Weight is addressed by not selecting the bundle, never by replacing it.
- **Endpoint documentation has exactly one input:** the `x-endpoints:` block each module declares in its own Compose fragment, naming the connection variables that module publishes. Generation reads that plus the configuration namespace; nothing is hand-maintained, and a module never edits another module's file.
- **Configuration namespace is two-tier:** module variables prefixed `<MODULE>_<CONCERN>`, and externally-dictated contract variables (SDK- or tool-named) registered to exactly one owning module. Documentation must use the contract names an application would set.
- **Cross-module state mutation follows stop → write → restart.** A Postgres restore rewrites every database including Keycloak's, so it stops Keycloak and every other Postgres dependent first and restarts them after. Realm import uses override plus a free management port and then a **mandatory** container restart — the import commits to the database while the running server keeps serving cached realm data, so skipping the restart leaves database and admin API silently disagreeing. A module may declare pre-restore/post-restore hooks that core scripts invoke.
- `psql` runs with `ON_ERROR_STOP=1`. The current restore pipes into `psql` without it and exits 0 when a drop fails on open connections — the exact silent-partial-restore defect this epic removes.
- **Core scripts are verified like code, not trusted like config.** Every script under `scripts/` is shellcheck-clean with at least one test exercising its contract; the restore script is specifically tested for the stop → write → restart ordering, and those tests may not be skipped in CI.
- **The task surface is pixi**, with every validation tool a pinned dependency. No task branches on tool availability, and non-trivial shell lives in a script invoked by a task rather than inline.
- Selection is resolved to its transitive dependency closure before Compose sees it, and that resolved selection is the single source of truth for which smoke checks run — the same input that scopes the endpoint listing and backup coverage.
- Gotcha entries have four fields — symptom, cause, fix, affected versions — in the affected module's own gotchas file.
- CI fails loudly or not at all: it iterates every module and bundle, exercises the worked example, and no step may skip a check because a tool or fixture is unavailable.

## UX & Interaction Patterns

No user interface is built here. The web consoles in the catalog — Grafana included — are third-party applications the stack wires up and provisions; no UX design contract exists and none is a missing prerequisite. The operator-facing surface is the command line, where the conventions that matter are: selection-scoped output rather than unconditional listings, skipped-not-failed for anything outside the selection, and fail-loud with the offending variable or step named rather than a quiet default into a wrong state.

## Cross-Story Dependencies

- The epic as a whole builds on the module decomposition and selection resolver from Epic 2: endpoint generation consumes per-module `x-endpoints` metadata, and backup scope and dashboard checks follow the resolved selection.
- Story 3.1 is the exception — it is independently deliverable and does not wait on the rest.
- Story 3.5 depends on stable endpoint contracts, which is why it sits last; it also depends on Story 3.1, since proving telemetry end to end means the emitted signals appearing in a dashboard.
- Story 3.4's restore and Story 3.2's realm re-import share the stop → write → restart protocol; build the ordering once rather than twice.
- Story 3.2 has the cheapest independent slice — the non-destructive realm re-import — and can land in any order relative to its siblings.
- Verification for all of these lands in the CI and smoke-runner machinery from Epic 1: dashboard panel checks, endpoint-drift detection, restore ordering tests and the example run are all gates there.

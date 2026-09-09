# Epic 3 Context: Batteries actually included

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Several things in this stack are provisioned but empty, or documented but not demonstrated: Grafana watches a dashboard directory containing only a placeholder, the README lists connection strings that nothing proves, backup covers Postgres and nothing else, and hard-won failure modes live as README prose. Epic 3 closes the gap between provisioned and useful — connection details generated from the configuration itself, dashboards that render real signals on first boot, backup and restore that cover every stateful service in the current selection, gotchas kept as a structured maintained register, and one runnable example that exercises every endpoint contract so a broken contract fails a build instead of surfacing mid-project.

## Stories

- Story 3.1: Grafana opens on working dashboards
- Story 3.2: Gotchas become a maintained register
- Story 3.3: Connection details generated, not hand-maintained
- Story 3.4: Backup covers everything stateful
- Story 3.5: A worked example that proves the contracts

## Requirements & Constraints

- **Nothing may be provisioned-but-empty.** A dashboard must render non-empty panels for traces, logs and metrics against signals the smoke test itself injects; a dashboard present with three broken panels fails. Dashboards must be restored from provisioning on a fresh volume, not from volume state, and survive a `down`/`up` cycle.
- **Documentation is generated or verified, never hand-maintained.** Connection strings and environment variables for every service live in one place, produced from each module's endpoint metadata plus the configuration namespace, using the same variable names an application would use. A changed port or credential either changes the generated output or fails the build — the two cannot silently diverge. An endpoint listing for a partial selection covers exactly that selection.
- **Backup follows the selection.** Backup captures Postgres databases, object-storage bucket contents and the Keycloak realm; services not in the current selection are skipped rather than failing. Restore into an empty stack reproduces the captured state, verified by a smoke run against the restored stack. Redis is deliberately excluded — it holds cache and in-flight task state, neither meaningful to restore. The existing backup/restore entry points keep working.
- **The gotcha register has a fixed shape**: symptom, cause, fix, affected versions — four fields, filed against the module the failure mode affects. Every gotcha currently in the README must appear with all four populated. Gotchas whose fix is mechanically verifiable get a corresponding smoke check or CI assertion, so a regression is caught rather than re-documented.
- **The register is corrected, not merely migrated.** The claim that realm import cannot overwrite an existing realm is obsolete and must be removed; two verified entries replace it (import exiting non-zero on a management-port collision when run against a live container, and a running server serving stale cached realm data after an out-of-band import until restarted). Existing realm gotchas worth preserving: an import's client-scopes array replaces the built-in scopes rather than adding to them, and a realm-level password policy is enforced against imported users. The highest-severity entry is the version-specific Postgres data directory path, which silently discards data when wrong — the strongest candidate for a CI assertion that writes a marker, cycles the stack, and reads it back.
- **The worked example proves rather than configures.** One runnable application authenticates against Keycloak, queries Postgres, caches in Redis, stores an object, sends mail to Mailpit and emits OTLP telemetry visible in Grafana, with no code changes. Each of the six integrations is exercised, not merely wired. CI runs it. It is explicitly not a production application template and says so itself.
- **Fail loud (NFR-5):** any failing step of a restore surfaces and exits non-zero; a partial restore that exits 0 is a defect.
- **Data durability (NFR-1):** no story here may cause a `down`/`up` cycle to lose state; only an explicit confirmed destroy removes volumes.
- **Security posture is fixed, not improved (NFR-8):** trivial credentials, no TLS, anonymous Grafana admin and development-mode Keycloak are correct for the purpose. Reject changes that partially harden the stack.

## Technical Decisions

- **Endpoint documentation input is module metadata.** Each module's compose fragment carries an `x-endpoints:` block naming the connection variables that module publishes; that block plus the environment configuration is the sole input to the generated endpoint documentation. Do not hand-write a second list.
- **Configuration namespace has two tiers.** Module variables are prefixed `<MODULE>_<CONCERN>`; contract variables with externally dictated names (SDK-mandated endpoint/credential names, bind address, compose profile and project names) are registered to exactly one owning module in a core-owned registry. Generated documentation must respect this split, and must state unambiguously which endpoint serves which service where names collide.
- **Cross-module state mutation follows stop → write → restart.** Any operation writing state another module owns stops that module's dependents, writes, then restarts them. A Postgres restore rewrites every database including Keycloak's, so Keycloak and every other Postgres dependent stop first and restart after. `psql` runs with `ON_ERROR_STOP=1`. The realm reimport falls under the same rule: it no longer drops the Keycloak database — it imports with override and then restarts the container, and that restart is mandatory, not advisory, because the import runs as a separate process that does not attach to the running server's cache.
- **Realm import mechanics.** Startup import ignores existing realms and no flag changes that; the import subcommand takes an override option (default on) that is remove-and-recreate rather than merge — other realms and the database survive, but runtime state in the target realm absent from the JSON is lost. Run against a live container it needs a non-conflicting management port to exit 0.
- **Scripts are verified like code.** Everything under the scripts directory is shellcheck-clean and has at least one test exercising its contract; the restore script is specifically tested for the stop → write → restart ordering. These tests run in CI and may not be skipped.
- **CI may not silently skip.** No step may skip a check because a tool, service or fixture is unavailable — it fails instead. Every validation tool is provisioned as a pinned project dependency; no task branches on tool availability.
- **The observability bundle stays five separate containers.** The single-image all-in-one alternative is rejected: it would discard the tuned configuration for structured metadata in Loki and span-metrics remote-write from Tempo to Prometheus, both of which are already-solved gotchas.
- **Dashboards ship as seed data** in the Grafana module directory, following the same seed-declaration convention every module uses.
- **Backup and restore live as scripts in the scripts directory; the worked example lives in its own top-level examples directory.**
- **Reproducibility (NFR-4):** every image stays pinned to an explicit tag; nothing in this epic may introduce a floating tag.

## Cross-Story Dependencies

- **Epic 2 is the substrate for most of this epic.** Endpoint documentation is generated from per-module metadata, the gotcha register is filed per module, and backup scope follows the selection — all of which require the module split and the selection resolver. Story 3.1 (dashboards) is independently deliverable and does not.
- **Within the epic the stories are mutually independent and can ship separately.** The realm-reimport correction in Story 3.2 is the smallest and a good first pick; the worked example (3.5) is last because it depends on the endpoint contracts being stable.
- **Story 3.5 depends on Epic 1's CI**, which must execute the example so a broken contract fails the build. Story 3.1's dashboard check and Story 3.2's verifiable-gotcha assertions likewise land in the existing smoke suite and CI, not in a parallel mechanism.
- **Story 3.4 crosses module boundaries at runtime** — restore touches Keycloak's database through Postgres, so it depends on the module dependency information Epic 2 establishes.

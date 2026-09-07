---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - "_bmad-output/planning-artifacts/prds/prd-devinfra-2026-09-06/prd.md"
  - "_bmad-output/planning-artifacts/prds/prd-devinfra-2026-09-06/addendum.md"
  - "_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md"
  - "_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/MIGRATION-PLAN.md"
---

# devinfra - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for devinfra, decomposing the requirements from the PRD and Architecture requirements into implementable stories.

**Brownfield project.** A working thirteen-service Compose stack already exists. There is **no starter template** — the Architecture spine ratifies conventions already present in the repository rather than scaffolding new ones. Epic 1 Story 1 is therefore not a project-initialization story; it establishes the task surface and validation tooling that every later story depends on.

**Requirement ID note.** `FR-11` is deliberately reserved and unused in the PRD. The inventory below runs FR-1..FR-10 and FR-12..FR-19 — 18 requirements, not 19. The gap is preserved intentionally; downstream references depend on these IDs being stable.

## Requirements Inventory

### Functional Requirements

FR-1: Each Service in the Catalog is defined by exactly one Module that owns its Compose fragment, config files, environment variables, and Smoke Test checks.
FR-2: A developer can declare which Modules and Bundles to run, and start exactly that set and nothing else.
FR-3: The system refuses to start a Selection whose cross-Module dependencies are unsatisfied, and names the missing Module.
FR-4: A developer can select a named Bundle instead of enumerating Modules, and get a curated set that is known to work together.
FR-5: The Smoke Test runs only the checks belonging to Modules in the current Selection, and reports skipped rather than failed for the rest.
FR-6: Every Service added to the Catalog ships with five things: a Healthcheck, a Smoke Test check exercising real function, a documented Endpoint Contract, a Seed Data declaration, and a Gotchas file.
FR-7: A developer can run a dedicated message broker alongside or instead of Redis-as-broker, and reach it via a documented Endpoint Contract.
FR-8: A developer can run a full-text search engine as a Module and reach it via a documented Endpoint Contract.
FR-9: A developer can run OpenBao as a Module, pre-initialized and unsealed, with a known root token.
FR-10: A developer can run AWS service emulation beyond the S3 surface object storage already covers, using only components under an OSI-approved license with no authentication token and no privileged host access.
FR-12: The connection strings and environment variables for every Service in the Catalog are documented in one place, generated from or verified against the actual configuration.
FR-13: Grafana ships with working dashboards over the three signals, provisioned automatically.
FR-14: A runnable example application demonstrates the Endpoint Contracts end to end across Keycloak, Postgres, Redis, object storage, Mailpit and OTLP telemetry.
FR-15: Backup and restore cover every stateful Service in the Selection, not Postgres alone.
FR-16: CI starts the Stack and runs the Smoke Test on every change to the repository, and the result gates merge. No validation check may silently skip.
FR-17: Image tag updates are proposed automatically and validated by CI before merge.
FR-18: Documented infrastructure failure modes are a maintained artifact with a consistent shape — symptom, cause, fix, affected versions.
FR-19: The Stack runs on container runtimes other than Docker Desktop, verified by the Smoke Test actually passing rather than by documentation alone.

### NonFunctional Requirements

NFR-1: **Data durability.** `down` followed by `up` preserves every stateful Service's data. Only an explicit, confirmed destroy removes volumes. Any change violating this is a defect regardless of what it enables.
NFR-2: **Isolation.** All published ports bind to the configured `BIND_ADDRESS` (default `127.0.0.1`). No Service is reachable from the network by default.
NFR-3: **Startup.** A full Selection reaches all-healthy in under two minutes on a warm image cache. The start command blocks until healthy and does not return optimistically.
NFR-4: **Reproducibility.** Every image is pinned to an explicit tag. Two developers on the same commit get the same Stack.
NFR-5: **Fail loud.** Missing or malformed required configuration fails at startup with a message naming the variable, rather than defaulting into a subtly wrong state.
NFR-6: **Configuration honesty.** No `env_file` value is relied upon for Compose interpolation; CI asserts the resolved output of `docker compose config`.
NFR-7: **Resource cost is stated.** Every Bundle documents its approximate memory footprint.
NFR-8: **Security posture is fixed, not improved.** Trivial credentials, no TLS, `start-dev` Keycloak, anonymous Grafana admin and `synchronous_commit = off` are correct for the stated purpose. No change may partially harden the Stack.

### Additional Requirements

Technical requirements from the Architecture spine and Migration plan that constrain how stories are built and sequenced.

**Starter template:** None. Brownfield — the spine ratifies existing repository conventions. No scaffolding story required.

*Composition mechanics*

- Shared service configuration flows through `extends: {file: ../../common/base.yaml, service: defaults}`, never YAML anchors — anchors are document-scoped and cannot cross `include` boundaries (AD-1).
- `common/base.yaml` may declare only `restart`, `logging`, `networks`. Never `depends_on`, `links`, `volumes_from`, or `network_mode: service:*` — `extends` inherits those keys without importing the resources they name (AD-2).
- Any one-shot helper Service extending the base must override `restart: "no"` or it restart-loops (AD-2).
- Every Module validates together with its transitive dependency closure, computed by `scripts/select.sh`, never hand-maintained (AD-6).
- A Module owns one primary Service plus explicitly declared helpers named `<module>-<role>`, each carrying a profile set identical to its primary's (AD-8).

*Identity and namespaces*

- Volume names are frozen permanently. A Module's `volumes:`/`networks:` stanzas contain the identifier and nothing else — every other key lives in Core, because omitted keys are last-include-wins (AD-5).
- The configuration namespace has two tiers: Module variables `<MODULE>_<CONCERN>`, and Contract variables with externally-dictated names registered to exactly one owning Module (AD-4).
- Host ports are allocated from one Core-owned table; `docker compose config -q` is blind to port collisions and CI must check rendered config for duplicates (AD-17).
- A Module declares what it needs from another Module as `x-requires:` in its own file; CI reconciles against the provider (AD-14).

*Selection and correctness*

- Cross-Module dependencies are expressed as `depends_on`; `docker compose config -q` is the validation gate and no bespoke resolver is written (AD-3).
- Selection is expanded to its transitive dependency closure before Compose sees it; every registered Bundle must already be closed (AD-16).
- Bundle names live in a Core-owned `x-bundles` registry; membership is declared per Module (AD-7).
- The default Selection is never empty; the resolver fails loudly rather than starting nothing at exit 0 (AD-18).

*Process and admission*

- pixi is the single task surface and provisions every validation tool as a pinned dependency. No task may branch on `command -v` (AD-9).
- Core scripts are verified like code — shellcheck-clean with tests. `select.sh` is load-bearing for four separate invariants (AD-21).
- CI iterates every Module and every Bundle; no step may skip a check because a tool or fixture is unavailable (AD-19).
- Image tags stay in `.env` with `# renovate:` annotations, driven by Renovate `customManagers`; Renovate's compose manager skips the `repo:${VAR:-tag}` form and Dependabot cannot read `.env` at all (AD-11).
- Any operation writing state another Module owns must stop that Module's dependents, write, then restart them. `psql` runs with `ON_ERROR_STOP=1` (AD-12).
- Catalog admission requires an OSI-approved license, no account/token/licence key, no privileged host access, and active upstream maintenance (AD-20).
- The observability Bundle stays five containers; `grafana/otel-lgtm` is not adopted (AD-13).

*Sequencing constraints from the Migration plan*

- The Make-versus-pixi decision is settled **before** any CI is written, or that CI is authored against Make and then rewritten.
- The eleven stale image pins are bumped **after** CI exists and **before** the module split — debugging a major upgrade and a refactor simultaneously is the worst available ordering.
- Postgres is extracted first because it has no dependencies, isolating the mechanism from the closure problem.
- Comparing rendered `docker compose config` output before and after each extraction is the highest-value verification in the migration.
- Stage 5 carries an unavoidable **breaking change**: giving every Service a profile means a bare `docker compose up` with no `COMPOSE_PROFILES` starts nothing at exit 0, where today it starts the core five.

### UX Design Requirements

**Not applicable.** devinfra ships no user interface that this project builds. The web consoles in the Catalog — pgAdmin, RedisInsight, Flower, Grafana, and the object storage console — are third-party applications the Stack wires up and provisions, not surfaces designed here. The PRD explicitly downscales its user journeys for this reason (§2.3: "developer tooling with a single operator role, so full narrative journeys would be ceremony").

No UX design contract exists, and none is a missing prerequisite.

### FR Coverage Map

Every FR maps to exactly one epic. FR-11 is reserved and intentionally unmapped.

| FR | Epic | Delivers |
| --- | --- | --- |
| FR-1 | Epic 2 | Per-Service Modules — one directory owns one Service |
| FR-2 | Epic 2 | Selection — start exactly the chosen set |
| FR-3 | Epic 2 | Dependency validation — refuse an incoherent Selection |
| FR-4 | Epic 2 | Bundles — curated sets that work together |
| FR-5 | Epic 2 | Modular Smoke Test — checks follow the Selection |
| FR-6 | Epic 2 | Module Completeness Contract — the five-item bar |
| FR-7 | Epic 4 | Message broker Module |
| FR-8 | Epic 4 | Search Module |
| FR-9 | Epic 4 | OpenBao secrets Module |
| FR-10 | Epic 4 | AWS service emulation beyond S3 |
| FR-12 | Epic 3 | Endpoint Contract documentation, generated not hand-written |
| FR-13 | Epic 3 | Grafana dashboards provisioned on first boot |
| FR-14 | Epic 3 | Worked example proving the contracts end to end |
| FR-15 | Epic 3 | Backup covering every stateful Service |
| FR-16 | Epic 1 | Continuous verification that cannot silently skip |
| FR-17 | Epic 1 | Dependency currency via Renovate |
| FR-18 | Epic 3 | Gotcha Register as a maintained artifact |
| FR-19 | Epic 1 | Runtime portability, proven by a passing Smoke Test |

**NFR coverage.** NFRs are cross-cutting and are enforced by acceptance criteria across epics rather than owned by one:
NFR-1 (data durability) gates every story in Epic 2 and FR-15 in Epic 3 · NFR-2 (isolation) and NFR-4 (reproducibility) are asserted by Epic 1's CI · NFR-3 (startup) is verified by Epic 2's Selection stories · NFR-5 and NFR-6 (fail loud, configuration honesty) are Epic 1 CI assertions · NFR-7 (resource cost stated) is an Epic 2 Bundle documentation criterion · NFR-8 (security posture fixed) is an admission gate in Epic 4 and a rejection criterion everywhere.

## Epic List

Four epics, sequenced to match the confirmed MVP ordering: durability → modularity → batteries → catalog.

**A note on Epic 1.** Ordinarily "CI setup" would be a technical-layer epic with no user value. Here it inverts: devinfra's user is the maintainer, and UJ-5 is precisely *"a dependency-update PR bumps Keycloak's tag; CI tells him whether it broke before he merges."* Verification is one of this product's outcomes, not scaffolding beneath it. The epic is framed on that outcome.

**A note on Epic 2.** Extracting thirteen Modules touches `compose.yaml` and every service directory. Splitting that across per-Bundle epics would be exactly the file-churn pattern to avoid, so it is one epic with strictly ordered stories.

### Epic 1: Trust the stack

The maintainer can change anything — an image tag, a config, a service — and know within minutes whether the stack still genuinely works, on more than one container runtime. Validation stops lying about what it checked.

**FRs covered:** FR-16, FR-17, FR-19
**Depends on:** nothing. Delivers complete value against today's monolithic `compose.yaml`.

### Epic 2: Take only what you need

The maintainer starts a project needing Postgres and Redis and gets exactly those two — no Keycloak boot, no observability gigabyte. Asking for one Service quietly brings what it depends on.

**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-5, FR-6
**Depends on:** nothing structurally. Safer after Epic 1, which is why the migration plan sequences it second, but it does not require Epic 1 to function.
**Carries the breaking change:** a bare `docker compose up` with no `COMPOSE_PROFILES` goes from starting the core five to starting nothing. Stories here must cover the migration path for existing `.env` files.

### Epic 3: Batteries actually included

What the stack provisions, it also delivers: connection strings in one place, dashboards on first boot rather than an empty directory, backups covering every stateful Service, and hard-won gotchas kept as a maintained artifact instead of prose.

**FRs covered:** FR-12, FR-13, FR-14, FR-15, FR-18
**Builds on:** Epic 2 — endpoint documentation is generated from Module metadata, and backup scope follows the Selection. FR-13 alone is independently deliverable.

### Epic 4: Grow the catalog safely

The maintainer reaches for a message broker, full-text search, secrets, or AWS emulation and finds it already curated — each arriving complete, verified, and admitted under a policy rather than by whim.

**FRs covered:** FR-7, FR-8, FR-9, FR-10
**Builds on:** Epic 2 — every new Service enters through the Module contract established there.

---

## Epic 1: Trust the stack

The maintainer can change anything — an image tag, a config, a service — and know within minutes whether the stack still genuinely works, on more than one container runtime. Validation stops lying about what it checked.

### Story 1.1: Validation tooling that cannot silently skip

As the maintainer,
I want every validation tool provisioned with the project itself,
So that a check either runs and reports honestly, or fails — never quietly reports success having done nothing.

**Acceptance Criteria:**

**Given** a machine with no system-wide `shellcheck`, `yamllint` or PyYAML installed
**When** I run the lint task
**Then** all three checks actually execute against pinned versions supplied by the project
**And** no task contains a `command -v` branch or any "not installed — skipping" message

**Given** a shell script with a genuine shellcheck violation
**When** I run the lint task
**Then** it exits non-zero and names the file and rule

**Given** the same commit checked out on a second machine
**When** the lint task runs
**Then** it uses identical tool versions, resolved from a committed lockfile (NFR-4)

### Story 1.2: Lifecycle logic extracted into testable scripts

As the maintainer,
I want the non-trivial shell moved out of Makefile recipes into `scripts/`,
So that I can run and test that logic directly rather than only through a build tool.

**Acceptance Criteria:**

**Given** the health-wait loop, the destroy confirmation and the endpoint listing
**When** the extraction is complete
**Then** each lives in its own file under `scripts/`, is shellcheck-clean, and runs standalone
**And** every former `make` target still works, forwarding to the new task surface

**Given** the stack is starting
**When** the health-wait script runs
**Then** it blocks until all containers are healthy and returns non-zero on timeout, listing what was not ready (NFR-3)

### Story 1.3: CI proves the stack works on every change

As the maintainer,
I want CI to start the stack and run the smoke test on every push,
So that I find out within minutes whether a change broke something. (FR-16, realizes UJ-5)

**Acceptance Criteria:**

**Given** a pull request against the current monolithic compose file
**When** CI runs
**Then** it validates `docker compose config -q` for every profile combination, starts the stack, waits for health, and runs the full smoke suite
**And** the result gates merge

**Given** a pull request that deletes a service's `depends_on` target
**When** CI runs
**Then** it fails and names the undefined service

**Given** any check whose tool, service or fixture is unavailable
**When** CI runs
**Then** the run fails rather than skipping that check (FR-16)

**Given** a full CI run on a hosted runner
**When** it completes
**Then** it finishes within 15 minutes, or the matrix is tiered — never by dropping checks

### Story 1.4: Image updates arrive as validated pull requests

As the maintainer,
I want new image tags proposed automatically and checked by CI before I see them,
So that upgrading stops being a manual chore I postpone. (FR-17)

**Acceptance Criteria:**

**Given** an image whose upstream publishes a newer tag
**When** the update bot next runs
**Then** it opens a pull request changing only that version variable in `.env.example`
**And** that PR runs the full CI suite from Story 1.3

**Given** every `*_VERSION` variable
**When** the configuration is reviewed
**Then** each is immediately preceded by a `# renovate: datasource=docker depName=<repo>` annotation
**And** no service resolves to a floating tag (NFR-4)

**Given** the update bot is configured
**When** a dry run is inspected
**Then** it reports a non-zero count of detected dependencies — proving the regex matches rather than silently matching nothing

### Story 1.5: Every image brought current

As the maintainer,
I want the eleven stale pins updated with each upgrade verified,
So that the stack is not accumulating known-fixed bugs.

**Acceptance Criteria:**

**Given** the seven patch and minor bumps
**When** each is applied
**Then** the smoke suite passes and the change ships as its own commit, so a break is attributable

**Given** the Keycloak upgrade across three minor versions
**When** it is applied
**Then** the realm imports, and a minted token still carries the `roles` claim and the `devinfra-api` audience

**Given** the Tempo and Grafana major upgrades
**When** they are applied
**Then** they ship together, because the span-metrics wiring spans both
**And** the OTLP round-trip through the collector into Tempo, Loki and Prometheus still passes

**Given** any bump requiring a config change under `docker/`
**When** it is found
**Then** that config change is a separate, explained commit rather than folded into the version bump

### Story 1.6: The stack runs on a second container runtime

As the maintainer,
I want the stack verified on a runtime other than Docker Desktop,
So that I am not locked to one vendor's tooling. (FR-19)

**Acceptance Criteria:**

**Given** a supported non-Docker-Desktop runtime
**When** CI runs the stack against it
**Then** the stack starts and the smoke suite passes — documentation alone does not satisfy this

**Given** a service that genuinely cannot work on that runtime
**When** the selection is built
**Then** it is excluded explicitly and the reason is documented, rather than failing at start

**Given** the runtime requires deviations such as socket paths or flags
**When** they are needed
**Then** they are stated in the connection documentation

---

## Epic 2: Take only what you need

The maintainer starts a project needing Postgres and Redis and gets exactly those two — no Keycloak boot, no observability gigabyte. Asking for one Service quietly brings what it depends on.

### Story 2.1: The shared fragment and the first Module

As the maintainer,
I want one Service extracted into a self-contained Module directory alongside a shared base fragment,
So that the decomposition mechanism is proven on the simplest case before it is applied thirteen times. (FR-1)

**Acceptance Criteria:**

**Given** `common/base.yaml`
**When** it is written
**Then** it declares only `restart`, `logging` and `networks`, never `depends_on`, `links`, `volumes_from` or `network_mode: service:*` (AD-2)
**And** it is referenced only by `extends` and never appears in the `include` list (AD-1)

**Given** Postgres — chosen first because it has no dependencies
**When** it is moved to `services/postgres/`
**Then** the directory holds its Compose fragment, its config and its seed scripts, with every relative path rewritten against that directory
**And** `project_directory` is not used (AD-6)
**And** no `<<: *alias` referencing another file remains anywhere (AD-1)

**Given** the rendered `docker compose config` output captured before the change
**When** it is compared to the output after
**Then** the two are identical apart from key ordering — this is the primary verification

**Given** the stack is restarted after extraction
**When** the volume inventory is compared to before
**Then** it is unchanged, `postgres-data` is intact, and existing data is queryable (NFR-1, AD-5)

**Given** the Module's `volumes:` stanza
**When** it is inspected
**Then** it names the identifier and nothing else — no driver, no `driver_opts` (AD-5)

### Story 2.2: The remaining core Modules

As the maintainer,
I want Redis, Keycloak, object storage and Mailpit extracted as Modules,
So that the whole core of the stack is composed rather than inlined. (FR-1)

**Acceptance Criteria:**

**Given** each of the four Services
**When** it is extracted
**Then** its `depends_on` edges are preserved exactly and now resolve across files (AD-3)
**And** rendered `docker compose config` still matches the pre-migration capture
**And** the volume inventory is unchanged after each extraction (NFR-1)

**Given** the object storage init container
**When** it moves into the object storage Module
**Then** it is named as a declared helper, carries a profile set identical to its primary, and sets `restart: "no"` explicitly (AD-2, AD-8)

**Given** the object storage Module
**When** its volume is declared
**Then** it keeps the frozen name `minio-data` despite the Module's own name differing (AD-5)

### Story 2.3: The admin and observability Modules

As the maintainer,
I want the remaining eight Services extracted as Modules,
So that nothing is left inlined and the registry is the only place Services are listed. (FR-1)

**Acceptance Criteria:**

**Given** pgAdmin, RedisInsight and Flower
**When** they are extracted
**Then** each keeps its existing profile membership and its `depends_on` edge to Postgres or Redis

**Given** the OTel Collector, Prometheus, Loki, Tempo and Grafana
**When** they are extracted
**Then** the collector's fan-out to Loki and Tempo still resolves and the OTLP round-trip still passes

**Given** the completed extraction
**When** `compose.yaml` is inspected
**Then** it contains the include registry, shared network and volume declarations, and no inline service definitions

### Story 2.4: Every Module carries its own contract, enforced

As the maintainer,
I want each Module to ship its own smoke check and metadata, with CI refusing an incomplete one,
So that a Service cannot enter the stack undocumented or unverifiable. (FR-5, FR-6)

**Acceptance Criteria:**

**Given** each Module directory
**When** the contract check runs
**Then** it requires a healthcheck, a `smoke.sh`, an `x-endpoints` block, either a seed directory or an explicit no-seed marker with a justification, and a gotchas file (FR-6)
**And** the check is presence-based, never a judgement about whether seed data was needed

**Given** a Compose service with no corresponding Module directory
**When** the contract check runs
**Then** it fails — the check runs in both directions (AD-8)

**Given** the existing central smoke script
**When** its per-service sections are carved into each Module's `smoke.sh`
**Then** every check still exercises real function rather than liveness, and the suite's pass count is unchanged

**Given** a Module whose volume stanza carries a driver key, or two Modules publishing the same host port
**When** CI runs
**Then** it fails — rendered config is parsed for duplicate published ports, which `config -q` does not detect (AD-5, AD-17)

**Given** a Module declaring `x-requires` for a resource another Module provisions
**When** CI runs
**Then** the declaration is reconciled against the providing Module's configuration (AD-14)

### Story 2.5: Selecting a Module brings what it needs

As the maintainer,
I want to ask for the Services I want and get their dependencies automatically,
So that a partial stack starts correctly instead of failing on an undefined service. (FR-2, FR-3, realizes UJ-2)

**Acceptance Criteria:**

**Given** a request for `postgres` and `redis`
**When** the stack starts
**Then** exactly two containers run — no Keycloak, no object storage, no Mailpit, no observability

**Given** a request for `keycloak` alone
**When** the resolver runs
**Then** it expands the selection to include Postgres and Mailpit, and all three start (AD-16)

**Given** a selection whose dependencies cannot be satisfied
**When** validation runs
**Then** `docker compose config -q` exits non-zero naming the missing service, and no container or volume is created (FR-3, AD-3)

**Given** an empty resolved selection
**When** the resolver runs
**Then** it fails loudly rather than starting nothing and exiting 0 (AD-18)

**Given** the resolver script
**When** its tests run
**Then** they cover closure correctness over the dependency graph, refusal of an empty selection, and refusal of an unknown Module name (AD-21)

**Given** a changed selection
**When** the stack is restarted
**Then** Services are added and removed without destroying volumes belonging to Services that remain (NFR-1)

### Story 2.6: Bundles, and a safe path through the breaking change

As the maintainer,
I want named Bundles for the sets I use together, and existing checkouts not to break silently,
So that I can ask for "core" instead of listing five Services, and an old `.env` fails loudly rather than starting nothing. (FR-4)

**Acceptance Criteria:**

**Given** the Bundle registry in the Core compose file
**When** it is defined
**Then** at least three Bundles exist — a minimal data-only set, today's core five, and the full observability set
**And** each is already dependency-closed, verified by CI, so no Bundle relies on runtime expansion (AD-16)

**Given** a Bundle profile that no Module joins, or a Module claiming a Bundle absent from the registry
**When** CI runs
**Then** it fails (AD-7)

**Given** a checkout whose `.env` predates this change and sets no `COMPOSE_PROFILES`
**When** the stack is started through the task surface
**Then** it fails with a message naming the variable and the fix, rather than starting nothing and exiting 0 (AD-18, NFR-5)

**Given** the shipped `.env.example`
**When** a new user copies it and starts the stack
**Then** the same Services start as before this epic — the default selection is never empty (AD-18)

**Given** each Bundle
**When** it is documented
**Then** its approximate memory footprint is stated (NFR-7)

---

## Epic 3: Batteries actually included

What the stack provisions, it also delivers: connection strings in one place, dashboards on first boot rather than an empty directory, backups covering every stateful Service, and hard-won gotchas kept as a maintained artifact instead of prose.

### Story 3.1: Grafana opens on working dashboards

As the maintainer,
I want dashboards provisioned automatically over all three signals,
So that pointing an app's exporter at the stack shows me something immediately instead of an empty Grafana. (FR-13, realizes UJ-4)

**Acceptance Criteria:**

**Given** the observability selection started with no manual steps
**When** Grafana is opened
**Then** at least one dashboard exists showing traces, logs and metrics

**Given** a fresh volume
**When** the stack starts
**Then** dashboards are restored from provisioning rather than from volume state, and survive a `down`/`up` cycle (NFR-1)

**Given** the trace, log and metric that the smoke test itself injects
**When** the dashboard check runs
**Then** one panel per signal renders non-empty — a dashboard whose panels are all broken must fail this check

### Story 3.2: Gotchas become a maintained register

As the maintainer,
I want documented failure modes kept in a consistent shape next to the Module they affect,
So that a hard-won lesson costs me an afternoon once rather than every time. (FR-18)

**Acceptance Criteria:**

**Given** every gotcha currently in the README
**When** the register is built
**Then** each appears with symptom, cause, fix and affected versions, filed against the Module it affects

**Given** the obsolete claim that realm re-import cannot overwrite an existing realm
**When** the register is corrected
**Then** it is removed, and two verified entries are added: the import exiting non-zero on a management-port collision, and a running server serving stale cached realm data after an out-of-band import

**Given** the realm re-import task
**When** it runs
**Then** it no longer drops the Keycloak database; it imports with override and restarts the container, and the restart is mandatory rather than advisory (AD-12, NFR-1)

**Given** a gotcha whose fix is mechanically verifiable
**When** the register is reviewed
**Then** it has a corresponding smoke check or CI assertion, so a regression is caught rather than re-documented

### Story 3.3: Connection details generated, not hand-maintained

As the maintainer,
I want the connection strings for every Service produced from the configuration itself,
So that documentation and reality cannot drift apart. (FR-12, realizes UJ-1)

**Acceptance Criteria:**

**Given** each Module's `x-endpoints` metadata and the configuration namespace
**When** the endpoint documentation is generated
**Then** every Service in the catalog has an entry using the same variable names an application would use

**Given** a changed port or credential
**When** CI runs
**Then** either the generated documentation changes with it or the build fails — the two cannot silently diverge

**Given** a partial selection
**When** the endpoint listing is produced
**Then** it covers every Service in that selection and omits the rest

### Story 3.4: Backup covers everything stateful

As the maintainer,
I want backup and restore to cover every stateful Service, ordered safely,
So that returning to a project after months does not depend on which service I remembered to dump. (FR-15, realizes UJ-6)

**Acceptance Criteria:**

**Given** a running selection
**When** a backup runs
**Then** it captures Postgres databases, object storage bucket contents and the Keycloak realm, and skips Services not in the selection rather than failing

**Given** a restore into an empty stack
**When** it completes
**Then** the captured state is reproduced and verified by a smoke run against the restored stack

**Given** a Postgres restore, which rewrites every database including Keycloak's
**When** it runs
**Then** it stops Keycloak and any other Postgres dependent first and restarts them after (AD-12)

**Given** any step of the restore failing
**When** it runs
**Then** the failure surfaces and the command exits non-zero — `psql` runs with `ON_ERROR_STOP=1` rather than exiting 0 on a partial restore (NFR-5)

### Story 3.5: A worked example that proves the contracts

As the maintainer,
I want one runnable application exercising every endpoint contract,
So that a broken contract fails a build instead of being discovered mid-project. (FR-14)

**Acceptance Criteria:**

**Given** a documented selection
**When** the example is run
**Then** it authenticates against Keycloak, queries Postgres, caches in Redis, stores an object, sends mail to Mailpit, and emits OTLP telemetry that appears in Grafana — with no code changes

**Given** each of those six integrations
**When** the example runs
**Then** each is exercised, not merely configured

**Given** the example
**When** CI runs
**Then** it is executed, so a broken endpoint contract fails the build (FR-16)

---

## Epic 4: Grow the catalog safely

The maintainer reaches for a message broker, full-text search, secrets, or AWS emulation and finds it already curated — each arriving complete, verified, and admitted under a policy rather than by whim.

### Story 4.1: A service must earn its place in the catalog

As the maintainer,
I want candidate Services checked against an admission policy before they are wired in,
So that I do not discover a licensing or host-access problem after building on it. (AD-20, NFR-8)

**Acceptance Criteria:**

**Given** a candidate Service
**When** admission is assessed
**Then** it must have an OSI-approved license, require no account, token or licence key to start, require no privileged host access including a Docker socket mount, and be actively maintained upstream

**Given** an archived upstream project retained deliberately
**When** it is admitted
**Then** its Module carries a dated written acceptance naming the unpatched advisories

**Given** any Module declaring `privileged: true` or mounting the Docker socket
**When** CI runs
**Then** it fails

**Given** a change that would partially harden the stack — TLS, non-trivial credentials, production-mode services
**When** it is proposed
**Then** it is rejected: the posture is fixed, not improved (NFR-8)

### Story 4.2: Secrets available locally

As the maintainer,
I want OpenBao running unsealed with a known root token,
So that I can develop against a real secrets API without an initialization ritual. (FR-9)

**Acceptance Criteria:**

**Given** the Module is selected
**When** the stack starts
**Then** OpenBao comes up already initialized and unsealed, with no manual init or unseal step

**Given** the smoke check
**When** it runs
**Then** it writes a secret and reads it back

**Given** the root token
**When** it is needed
**Then** it is documented in the endpoint contract and configurable by environment variable

**Given** the whole catalog
**When** it is inspected
**Then** no HashiCorp Vault image or BUSL-licensed component appears anywhere (AD-20)

### Story 4.3: A dedicated message broker

As the maintainer,
I want a real broker alongside Redis rather than only Redis-as-broker,
So that I can develop against the messaging semantics my projects actually target. (FR-7)

**Acceptance Criteria:**

**Given** the Module is selected
**When** the smoke check runs
**Then** it publishes a message and consumes it — function, not liveness

**Given** the broker and Redis running together
**When** both are selected
**Then** neither collides on a host port, and Redis retains its existing broker role (AD-17)

**Given** the broker's data
**When** the stack is cycled with `down` then `up`
**Then** it persists (NFR-1)

**Given** the existing `noeviction` smoke check on Redis
**When** the suite runs
**Then** it still passes; if the broker role ever moves off Redis, the README rationale is updated in the same change

### Story 4.4: Full-text search

As the maintainer,
I want a search engine as a Module,
So that I can build search features without standing one up per project. (FR-8)

**Acceptance Criteria:**

**Given** the Module is selected
**When** the smoke check runs
**Then** it indexes a document and retrieves it by query

**Given** index data
**When** the stack is cycled
**Then** it persists (NFR-1)

**Given** the Module
**When** it is documented
**Then** its relationship to the existing pgvector capability is stated — this covers lexical search, not vector similarity

### Story 4.5: AWS services beyond object storage

As the maintainer,
I want emulation of the AWS services my projects use beyond S3,
So that I can develop against them locally under an OSI license with no token and no socket mount. (FR-10)

**Acceptance Criteria:**

**Given** the emulation Modules
**When** they start
**Then** no component requires an account, token or licence key, and none mounts the host Docker socket (AD-20)

**Given** at least one non-S3 AWS service
**When** the smoke check runs
**Then** it exercises that service's real function

**Given** object storage already owning the S3 endpoint
**When** the endpoint contract is written
**Then** it states unambiguously which endpoint serves which service, and the contract variable has exactly one owning Module (AD-4)

**Given** RDS and Cognito
**When** scope is assessed
**Then** they are explicitly out — Postgres already serves the RDS role and Keycloak already serves Cognito's

---

## Validation

Checks run against the completed breakdown before handing it to development.

**FR coverage — 18/18, no gaps.** Every FR in the inventory is referenced by at least one story whose acceptance criteria address it. `FR-11` is reserved and correctly absent from the inventory rather than mapped to a placeholder story.

**Starter template — none required.** The Architecture spine specifies no starter or scaffold; devinfra is brownfield and the spine ratifies conventions already present. Epic 1 Story 1 therefore establishes the task surface and validation tooling rather than initializing a project.

**Nothing built upfront that a story does not need.** The analogue of speculative schema creation here would be provisioning Services before something needs them. Epic 2 extracts thirteen Modules, but those Services already exist — that is refactoring, not provisioning. Genuinely new Services arrive one per story in Epic 4, each introduced by the story that requires it.

**Story dependencies — clean.** No story references a later story, in any epic. Each builds only on its predecessors: Epic 1 provisions tooling before CI consumes it; Epic 2 proves the extraction mechanism on a dependency-free Module before applying it, and establishes Modules before Selection resolves over them; Epic 3 and Epic 4 build on the Module contract from Epic 2.

**Epic independence.** Epic 1 delivers complete value against today's monolithic compose file and requires nothing else. Epic 2 is structurally independent of Epic 1 — sequenced after it for safety, not necessity. Epics 3 and 4 build on Epic 2's output, which the dependency rules permit, and neither is required for Epic 2 to be complete.

**File churn — overlap considered, consolidation rejected.** Three epics each touch `.env.example` and `scripts/`. The overlap is additive rather than rework: Epic 1 adds version variables and update annotations, Epic 2 adds the default Selection and the resolver, Epic 4 adds per-Service variables. Consolidation was rejected because real risk boundaries separate these epics — Epic 1's CI must exist before Epic 2's refactor so that breakage is caught, and Epic 2's Module contract must exist before Epic 4 admits Services through it. Merging them would produce a single 22-story epic with no feedback point between the refactor and its safety net.

**Where a story is knowingly large.** Story 2.3 covers eight Module extractions and Story 1.5 covers eleven image bumps. Both are mechanical repetitions of a pattern proven in an earlier story, and both carry acceptance criteria requiring per-item commits so a failure stays attributable. Split either if a single dev session proves too small a container.

---
title: devinfra
status: final
created: 2026-09-06
updated: 2026-09-06
---

# PRD: devinfra

*Local development infrastructure, curated and proven.*

## 0. Document Purpose

This PRD is for the maintainer of `devinfra` and for the downstream `bmad-architecture` and epic-generation workflows that build on it. It is a **brownfield** PRD: a working thirteen-service Compose stack already exists at [compose.yaml](../../../../compose.yaml), and this document specifies what it becomes next, not what it is. Existing behavior is treated as established baseline and described only where an enhancement changes it.

Vocabulary is anchored in §3 Glossary — downstream documents must use those terms verbatim. Features are grouped in §4 with globally-numbered Functional Requirements nested under them, so `FR-N` stays a stable reference even if features are reorganized. Inferences I made without confirmation are tagged `[ASSUMPTION]` inline and indexed in §9.

Technical depth that belongs to the architect rather than this document — considered alternatives, the consumption-model cost analysis, mechanism choices — lives in [addendum.md](addendum.md). Read it alongside this file before writing the architecture.

## 1. Vision

A developer starting a new backend project needs the same seven or eight things every single time: a real database, a cache, an identity provider that issues real tokens, object storage, somewhere for outbound email to land, and — once anything gets interesting — traces, metrics, and logs. Assembling that takes days. Assembling it *correctly* takes longer, because the failure modes are quiet: a Postgres volume mounted at the wrong path for its major version silently discards everything on `down`; a `clientScopes` array in a Keycloak realm import strips the built-in scopes out of every client; Loki 3.x rejects OTLP outright without one config flag. None of these produce an error. They produce a bad afternoon.

**devinfra is a curated, persistent, loopback-bound plane of real backing services that you start once and stop thinking about.** Not emulators — actual Postgres, actual Keycloak, actual MinIO. Not ephemeral per-test containers, and not a Kubernetes inner-loop orchestrator: a long-lived stack that survives `down` and `up` with its data intact, that any language can reach at `localhost`, and that a functional smoke test proves is genuinely working rather than merely running.

Three things make it worth existing rather than being another template repo. First, the components are **wired to each other** — Keycloak persists to Postgres and sends its mail through Mailpit; the collector fans one OTLP endpoint out to three backends; Redis serves cache, broker, and result backend on documented separate databases. Second, `make smoke` mints a real token, round-trips a real object, and pushes a real trace through the pipeline and reads it back, which answers "did it actually work?" — a question `docker compose ps` cannot. Third, the accumulated gotchas are captured in the repository, which makes them a durable asset rather than something the next person rediscovers.

This next phase makes that stack **modular** (take the four services you need, not all thirteen), **broader** (message brokers, search, secrets, cloud emulation, local models), **complete** (the dashboards, seed data, and worked examples that are currently provisioned-but-empty), and **durable** (CI that proves the whole thing still works, and image updates that do not rot). The bet is that a stack you can trust and take pieces of beats a stack you have to take whole.

**Landscape note.** The adjacent tools do not occupy this space. Testcontainers and framework dev-services (Spring Boot's `docker-compose` module, Quarkus Dev Services) bind services to one application's lifecycle and one language. .NET Aspire is the closest conceptual competitor but is C#-centric with in-memory-only telemetry. Tilt, Skaffold, DevSpace and Garden presuppose a Kubernetes cluster. LocalStack and the Supabase CLI each lock you to one vendor's surface. `devenv.sh` runs non-containerized service reimplementations, weakening Keycloak and MinIO fidelity. What remains genuinely underserved: a language-agnostic, project-agnostic, long-lived stack whose identity provider arrives pre-seeded and working. Full analysis in [addendum.md](addendum.md).

## 2. Target User

**The maintainer, primarily — and developers who work the way he does.** This is a personal-stakes project: it succeeds if it keeps earning its place in the maintainer's own workflow. That framing is deliberate, and it sets the bar. Features that only make sense for a hypothetical adopter get cut; features the maintainer would reach for weekly get built. `[ASSUMPTION: the maintainer is the primary and effectively sole user. He confirmed personal/hobby stakes, which implies this, but did not state it directly. If devinfra is meant for others to adopt, §7 Success Metrics and §5 Non-Goals both change materially.]`

### 2.1 Jobs To Be Done

- **When I start a new project, I want real backing services in under two minutes, so I can write application code instead of infrastructure.**
- **When I need a token to test an authenticated endpoint, I want to mint one from a script**, without standing up an identity provider or stubbing auth into oblivion.
- **When I only need Postgres and Redis, I do not want to run Keycloak, MinIO, and five observability containers** to get them.
- **When I come back to a project after two months, I want my data still there** and the stack to start exactly as it did before.
- **When I add instrumentation to an app, I want somewhere for the traces to go** that I can actually query, without signing up for anything.
- **When I hit an infrastructure gotcha, I want it recorded**, so it costs me an afternoon once rather than every time.
- **When I bump an image tag, I want to know within minutes whether the stack still works** end to end.

### 2.2 Non-Users (v1)

- **Anyone deploying this.** Every credential is trivial, TLS is off, Keycloak runs `start-dev`, Grafana allows anonymous admin, and Postgres runs `synchronous_commit = off`. This is unsafe by construction and stays that way — see §5.
- **Teams needing shared or remote development environments.** That is Coder's problem, not this one.
- **Anyone wanting per-test isolated containers.** Testcontainers already does that well; devinfra is deliberately long-lived and shared across a machine's projects.

### 2.3 Key User Journeys

Downscaled to one-line form: this is developer tooling with a single operator role, so full narrative journeys would be ceremony. FRs reference these by ID.

**Brownfield marking matters here.** Four of these six already work today and are recorded so this phase does not break them; only **UJ-2 and UJ-5 are new**. A story generator should scaffold work for those two, and regression coverage for the rest.

- **UJ-1. Cold start.** `[EXISTING]` The maintainer clones devinfra onto a new machine and runs init, start, and smoke. Once images are cached, start-to-healthy is under two minutes (NFR-3); the first run on a new machine is dominated by multi-gigabyte image pulls, and the Smoke Test adds up to a further minute waiting for the observability backends to ingest. The two-minute promise is about *starting*, not about *provisioning a new machine*.
- **UJ-2. Minimal start.** `[NEW]` Starting a small project that needs only Postgres and Redis, he selects those two and starts nothing else — no Keycloak boot, no observability RAM.
- **UJ-3. Auth without pain.** `[EXISTING]` He needs an access token for a protected endpoint; one command returns one, carrying the roles and audience his resource server validates.
- **UJ-4. Telemetry lands somewhere.** `[EXISTING, extended by FR-13]` He points an app's OTLP exporter at `localhost:4318` and opens Grafana. The pipeline and datasources work today; what FR-13 adds is dashboards, so he sees the trace, its logs and its metrics correlated without building them first.
- **UJ-5. Upgrade with confidence.** `[NEW]` A dependency-update PR bumps Keycloak's tag; CI starts the Stack, runs the Smoke Test, and tells him whether it broke before he merges.
- **UJ-6. Return after absence.** `[EXISTING]` He comes back to a project after two months, starts the Stack, and every database, bucket, realm, and dashboard is exactly where he left it.

## 3. Glossary

Downstream workflows and readers use these terms exactly. Introducing a synonym anywhere is a discipline violation.

- **Stack** — the complete set of Services devinfra can run. One Stack per machine by default.
- **Service** — one containerized backing component (Postgres, Keycloak, Grafana, …). The atomic unit of the Catalog.
- **Catalog** — the full set of Services devinfra offers. Today thirteen; §4.2 expands it.
- **Module** — the self-contained Compose file, config directory, environment variables, and smoke checks for exactly one Service. The unit of à-la-carte selection. One Module defines one Service. *New concept — does not exist today.*
- **Bundle** — a named, curated set of Modules that are useful together and are wired to each other (e.g. a `web` Bundle: Postgres + Redis + Keycloak + Mailpit). Successor to today's Profiles. One Bundle references many Modules; a Module may appear in many Bundles.
- **Profile** — the existing Compose-native grouping mechanism (`admin`, `observability`). Retained as an implementation detail; Bundle is the user-facing concept.
- **Selection** — the set of Modules and Bundles a given developer has chosen to run, expressed in configuration.
- **Smoke Test** — a check that exercises a Service's real function (mint a token, round-trip an object, query a trace), not its liveness. Distinct from a Healthcheck.
- **Healthcheck** — the container-level readiness probe Compose uses to gate `depends_on`. Answers "is it up?"; a Smoke Test answers "does it work?"
- **Gotcha** — a documented non-obvious infrastructure failure mode, recorded with its symptom and its fix. Currently a README section; §4.3 makes it a first-class artifact.
- **Endpoint Contract** — the set of connection strings and environment variables an application uses to reach a Service (`DATABASE_URL`, `OIDC_ISSUER`, `AWS_ENDPOINT_URL`, …). The primary interface between devinfra and its consumer.
- **Seed Data** — fixture content loaded into a Service to make it immediately useful (a Keycloak realm, MinIO buckets, extra Postgres databases, Grafana dashboards).
- **Consumer Project** — an application repository that uses devinfra for its backing services.

## 4. Features

### 4.1 Modular Service Catalog

**Description:** Today the Stack is one 424-line [compose.yaml](../../../../compose.yaml) with two Profiles, so the granularity of choice is one of four whole-Profile combinations — everything, core plus admin, core plus observability, or the five core Services alone. A developer who wants Postgres and Redis still boots Keycloak. This feature decomposes the Stack into Modules — one per Service, each self-contained — and reassembles them through Compose `include`, so Selection becomes genuinely à la carte. Bundles preserve the convenience of the current Profiles for people who want a sensible set rather than a shopping trip. Realizes UJ-2.

The decomposition must not cost anything that currently works. Cross-Service wiring is the hard part: Keycloak persists to Postgres and mails through Mailpit; Flower needs Redis; the collector fans out to three backends. A Module whose dependencies are absent from the Selection must fail with a clear message naming what is missing — never boot into a broken state. `[ASSUMPTION: Compose "include" (available since Compose 2.20) is the intended mechanism. Its relative-path resolution semantics make per-Module directories workable, but it does not itself validate cross-Module dependencies — that gate is ours to build.]`

**Functional Requirements:**

#### FR-1: Per-Service Modules

Each Service in the Catalog is defined by exactly one Module that owns its Compose fragment, config files, environment variables, and Smoke Test checks. Realizes UJ-2.

**Consequences (testable):**

- Every Service in the Catalog has a Module directory containing, at minimum, a Compose fragment.
- Adding a Service to the Catalog requires creating one Module and registering it; it requires no edit to any other Module.
- Deleting a Module's directory removes that Service from the Catalog without breaking `docker compose config` for a Selection that excludes it.

#### FR-2: Selection

A developer can declare which Modules and Bundles to run, and start exactly that set and nothing else. Realizes UJ-2.

**Consequences (testable):**

- A Selection of `postgres, redis` starts two containers; no Keycloak, MinIO, Mailpit, or observability container is created.
- `docker compose config` on any valid Selection exits 0.
- The existing Profile names (`admin`, `observability`) survive as Bundle names, producing the same Services they do today, so no existing invocation breaks.
- Changing a Selection and re-running the start command adds and removes Services without destroying data volumes belonging to Services that remain selected.

#### FR-3: Dependency Validation

The system refuses to start a Selection whose cross-Module dependencies are unsatisfied, and names the missing Module.

**Consequences (testable):**

- Selecting `keycloak` without `postgres` fails before any container starts, with a message naming `postgres` as the missing dependency.
- Selecting `flower` without `redis` fails the same way.
- The failure exits non-zero and creates no containers and no volumes.

**Out of Scope:**

- Automatically adding missing dependencies to the Selection. Explicit failure is the v1 behavior; auto-resolution is a v2 question.

#### FR-4: Bundles

A developer can select a named Bundle instead of enumerating Modules, and get a curated set that is known to work together.

**Consequences (testable):**

- At least three Bundles ship: one minimal (data services only), one covering today's core five Services, and one covering the full observability set.
- Selecting a Bundle produces a Selection that passes FR-3 validation with no additional Modules required.
- A Selection may combine Bundles and individual Modules.

#### FR-5: Modular Smoke Test

The Smoke Test runs only the checks belonging to Modules in the current Selection, and reports skipped rather than failed for the rest. *Extends the existing behavior in [scripts/smoke-test.sh](../../../../scripts/smoke-test.sh), which already skips checks per Service via a `running <service>` guard — finer-grained than Profile level, and a good foundation for this.*

**Consequences (testable):**

- A Selection of `postgres, redis` produces a Smoke Test run with zero failures and zero checks attempted against unselected Services.
- Each Module's checks live with that Module, not in a central script.
- The Smoke Test exits non-zero if any check for a selected Module fails.

**Notes:** `[NOTE FOR PM]` This feature and §4.2 are coupled: expanding the Catalog before modularizing means every new Service makes the monolith worse. Sequencing matters — see §6.

---

### 4.2 Expanded Catalog

**Description:** The current Catalog covers the persistence, identity, storage, mail, and observability needs of a typical web backend. It does not cover asynchronous messaging beyond Redis-as-broker, full-text search, secrets management, non-S3 cloud services, columnar analytics, or local model inference — all of which the maintainer hits in real projects. Each new Service arrives as a Module (per §4.1) with a Healthcheck, a Smoke Test check, an Endpoint Contract, a Seed Data declaration, and a Gotchas file. A Service without those five things is not done — FR-6 states the contract and CI enforces it.

Candidate Services, in no committed order: a dedicated message broker, a search engine, **OpenBao** for secrets, AWS service emulation beyond S3 (subject to the licensing constraint in FR-10), a columnar analytics store, local model inference, and a durable workflow engine. `[ASSUMPTION: the specific product within each category is an architecture decision, not a product decision — RabbitMQ vs NATS vs Kafka, OpenSearch vs Meilisearch. The PRD names the capability; the architect names the image. Candidates and trade-offs are recorded in addendum.md.]`

**Constraint, user-stated:** secrets management uses **OpenBao**, not HashiCorp Vault. Vault's relicensing to BUSL puts it outside what this project will depend on; OpenBao is the Linux Foundation fork and the sanctioned choice.

**Functional Requirements:**

#### FR-6: Module Completeness Contract

Every Service added to the Catalog ships with five things: a Healthcheck, at least one Smoke Test check exercising real function, a documented Endpoint Contract, a Seed Data declaration, and a Gotchas file.

**Consequences (testable):**

- A Module lacking any of the five is rejected by CI (per FR-16).
- Seed Data and Gotchas are **declaration-based**, so the CI check is presence-of-declaration rather than a judgment call: a Module either ships a seed directory or an explicit `seed: none` marker with a one-line justification, and either a Gotchas file or an empty one.
- The Smoke Test check exercises function, not liveness: a search Service indexes and retrieves a document; a broker Service publishes and consumes a message.
- Every Endpoint Contract appears in the connection-string documentation (FR-12) using the same variable names an application would use.

#### FR-7: ~~Message Broker~~ WITHDRAWN

**Withdrawn after review.** Redis already brokers Celery, which is the only messaging pattern in use. A dedicated broker would add a container and model semantics nothing needs. The ID is retired and never reused.

A developer can run a dedicated message broker alongside or instead of Redis-as-broker, and reach it via a documented Endpoint Contract.

**Consequences (testable):**

- The broker starts as a Module, passes a publish-and-consume Smoke Test check, and persists its data across `down` and `up`.
- Redis retains its existing broker role; the new Service is additive and the two can run simultaneously without port collision.
- The existing `maxmemory-policy noeviction` Smoke Test check still passes. If the broker role ever moves off Redis, the README rationale for `noeviction` is updated in the same change.

#### FR-8: ~~Search~~ WITHDRAWN

**Withdrawn after review.** Postgres full-text search covers lexical search and pgvector covers vector similarity. A separate engine would buy nothing. The ID is retired and never reused.

A developer can run a full-text search engine as a Module and reach it via a documented Endpoint Contract.

**Consequences (testable):**

- Smoke Test indexes a document and retrieves it by query.
- Index data persists across `down` and `up`.

#### FR-9: Secrets Management (OpenBao)

A developer can run OpenBao as a Module, pre-initialized and unsealed, with a known root token.

**Consequences (testable):**

- OpenBao starts initialized and unsealed with no manual initialization or unseal step.
- Smoke Test writes a secret and reads it back.
- The root token is documented in the Endpoint Contract and configurable via environment variable.
- No HashiCorp Vault image or Vault-licensed component appears anywhere in the Catalog.

*Verified feasible: the official `openbao/openbao` image defaults to dev mode, starts unsealed with a configurable root token, mounts KV v2 at `secret/` with no enable step, ships `wget` for a healthcheck, and listens on 8200 — which does not collide with any port devinfra currently allocates. Details in [addendum.md](addendum.md) §C.*

#### FR-10: ~~Cloud Service Emulation~~ WITHDRAWN

**Withdrawn after review.** Object storage is the only AWS surface in use; no non-S3 AWS service is needed. The ID is retired and never reused. The licensing and host-access rules this FR carried survive in the catalog admission policy (AD-20), which binds every Service.

A developer can run AWS service emulation beyond the S3 surface MinIO already covers, using only components under an OSI-approved license with no authentication token and no privileged host access.

**Consequences (testable):**

- At least one non-S3 AWS service is reachable and exercised by a Smoke Test check.
- The Endpoint Contract does not conflict with MinIO's existing `AWS_ENDPOINT_URL`; the documentation states unambiguously which endpoint serves which service.
- No component requires an account, an authentication token, or a license key to start.
- No component requires mounting the host Docker socket.

**Out of Scope:**

- **LocalStack.** Ruled out on verified evidence, not preference — see §8 Q3. Its free tier is licensed for non-commercial use only, which is incompatible with a stack whose users may work at companies; it requires an auth token; and it requires a Docker socket mount, which is root-equivalent host access and conflicts with FR-19.
- RDS and Cognito emulation. No OSI-licensed option covers them; Keycloak already serves the identity role Cognito would fill.

**Catalog backlog** *(not an FR — no story should be generated from this)*

Columnar analytics, local model inference, and durable workflow orchestration are recorded candidates with no committed requirement behind them. If any is built later it enters as an ordinary Module subject to FR-6, which is why none of them needs its own FR now. **FR-11 is reserved and intentionally unused**, so that FR-12 through FR-19 keep the IDs they were assigned.

---

### 4.3 Batteries In The Box

**Description:** Several things in the repository are provisioned but empty, or documented but not demonstrated. Grafana has a dashboard provider watching a directory containing only `.gitkeep`. The README lists connection strings but nothing proves them. `make backup` covers Postgres and nothing else, so a `make destroy` loses every bucket, realm change, and dashboard regardless. The Gotchas are a README section rather than an artifact. This feature closes the gap between provisioned and useful. Realizes UJ-4, UJ-6.

**Functional Requirements:**

#### FR-20: Image Currency

The Catalog runs no image whose pinned tag is known to be superseded upstream.

**Consequences (testable):**

- Every pinned tag either matches the current upstream release or carries a dated written reason for lagging.
- Currency is verified against upstream, never against what is running — a pin in `.env.example` states what runs, never what is current.
- A tag whose upstream project is archived is treated as a replacement decision, not a bump, and admitted only under FR-10's licensing and host-access rules.

*Added after epics review: Story 1.5 delivers the initial bring-current wave and previously traced to no requirement. FR-17 covers keeping tags current going forward; this covers the state of being current.*

#### FR-21: Graph Queries Over Relational Data

A developer can run graph traversal and path queries over the same data that pgvector indexes, inside one Postgres instance.

**Consequences (testable):**

- A Smoke Test check creates a graph, inserts nodes and edges, and returns the expected path from a traversal.
- Graph, relational and vector data share one database and one transaction boundary — no second datastore, no synchronisation.
- The extension is **not** added to `shared_preload_libraries`; sessions load it explicitly. Preloading breaks databases without the extension installed, and this stack has several.
- The image providing it is built from the pinned upstream Postgres image plus a distribution package, with no compilation from source.

*Added after review: the graph requirement is about data already in Postgres, which rules out a separate graph database — that would create two sources of truth with no JOIN and no shared transaction.*

#### FR-12: Endpoint Contract Documentation

The connection strings and environment variables for every Service in the Catalog are documented in one place, generated from or verified against the actual configuration. Realizes UJ-1.

**Consequences (testable):**

- Every Service in the Catalog has an Endpoint Contract entry.
- A port or credential change in configuration causes the documentation to change or CI to fail — the two cannot silently diverge.
- The endpoint-listing command covers every Service in the current Selection. *Today's `make urls` prints unconditionally regardless of Selection and omits Loki and Tempo entirely, so this is a change, not a restatement.*

#### FR-13: Grafana Dashboards

Grafana ships with working dashboards over the three signals, provisioned automatically. Realizes UJ-4.

**Consequences (testable):**

- After starting the observability Selection with no manual steps, at least one dashboard exists showing traces, logs, and metrics.
- Dashboards survive `down`/`up` and are restored on a fresh volume from provisioning, not from volume state.
- A Smoke Test check asserts that one panel per signal **renders non-empty** against the trace, log and metric the Smoke Test itself injects — not merely that a dashboard file is present and its datasource resolves. A dashboard with three broken panels must fail this check.

#### FR-14: Worked Example

A runnable example application demonstrates the Endpoint Contracts end to end: authenticating against Keycloak, querying Postgres, caching in Redis, storing an object in MinIO, sending mail to Mailpit, and emitting OTLP telemetry that appears in Grafana. It exists to prove the wiring, and is explicitly not a template to build production applications from — it says so in its own README. Realizes UJ-1, UJ-3, UJ-4.

**Consequences (testable):**

- The example runs against a documented Selection with no code changes.
- The example is exercised by CI (FR-16), so a broken Endpoint Contract fails the build.
- Each of the six integrations above is exercised, not merely present.

**Out of Scope:**

- Examples in multiple languages. One is enough to prove the contracts. `[ASSUMPTION: one worked example suffices for v1; per-language examples are a v2 question.]`

#### FR-15: Backup Coverage

Backup and restore cover every stateful Service in the Selection, not Postgres alone. Realizes UJ-6.

**Consequences (testable):**

- A backup captures Postgres databases, MinIO bucket contents, and the Keycloak realm.
- Restoring into an empty Stack reproduces the captured state, verified by a Smoke Test run against the restored Stack.
- Backup skips Services not in the current Selection rather than failing.
- The existing `make backup` / `make restore` interface continues to work.

**Feature-specific NFRs:**

- A backup of a nominal development dataset completes in under 60 seconds. `[ASSUMPTION: "nominal" means single-digit GB; the maintainer has not stated a size.]`

**Notes:** Redis backup is deliberately excluded from the required set — it holds cache and in-flight Celery state, neither of which is meaningful to restore. `[NOTE FOR PM]` Confirm this reasoning holds if Redis is ever used for durable application data.

---

### 4.4 Durability

**Description:** Nothing currently proves the Stack works except a human running `make smoke`. Image tags are pinned in `.env.example` and drift silently until someone runs `make pull` and discovers a breaking change by hand. Both the pain and the fix are well understood; this feature makes correctness continuous instead of occasional. Realizes UJ-5.

**Functional Requirements:**

#### FR-16: Continuous Verification

CI starts the Stack and runs the Smoke Test on every change to the repository, and the result gates merge. Realizes UJ-5.

**Consequences (testable):**

- A pull request that breaks any Service's Smoke Test check fails CI.
- CI validates Compose configuration, shell scripts, YAML and JSON — the checks `make lint` performs today.
- **No validation check silently skips.** A check whose tool is unavailable fails the run; it does not report success. *This is a change from today's behavior — the current lint target skips `shellcheck` and YAML validation when their tools are absent, which means it can pass having verified nothing. See §8 Q8.*
- CI verifies the FR-6 Module Completeness Contract for every Module.
- CI exercises the worked example (FR-14).
- A full CI run completes in **15 minutes or less** on a hosted runner. `[ASSUMPTION: the 15-minute figure is mine — the maintainer stated no budget. That a hard bound exists is not in question; if 15 is wrong, replace the number, do not remove the bound.]`

#### FR-17: Dependency Currency

Image tag updates are proposed automatically and validated by CI before merge. Realizes UJ-5.

**Consequences (testable):**

- A newer image tag for any Service produces an automated pull request within a week of release.
- That pull request runs the full CI suite (FR-16), so a breaking upgrade is visible before merge and never after.
- Tags remain explicitly pinned; no Service resolves to a floating tag at runtime.

#### FR-18: Gotcha Register

Documented infrastructure failure modes are a maintained artifact with a consistent shape — symptom, cause, fix, affected versions — rather than prose in a README section.

**Consequences (testable):**

- Every Gotcha currently in the README appears in the register with all four fields populated.
- Each Gotcha names the Module it affects.
- A Gotcha whose fix is verifiable has a corresponding Smoke Test check or CI assertion, so a regression is caught rather than re-documented.
- The register is **corrected as well as migrated**: the existing "`--import-realm` only creates realms that do not already exist" Gotcha is obsolete on Keycloak 26.4 and is removed, and two newly verified ones are added — `kc.sh import` exiting non-zero on a management-port collision when run against a running container, and a running Keycloak serving stale cached realm data after an out-of-band import. See §8 Q2.
- **`make keycloak-reimport` no longer drops the `keycloak` database.** Correcting the Gotcha requires correcting the target it justified; today it still runs `DROP DATABASE IF EXISTS keycloak WITH (FORCE)`. The replacement preserves the database and every other realm. This is the one behavior change the §8 Q2 resolution mandates, and it belongs to this FR.

#### FR-19: Container Runtime Portability

The Stack runs on container runtimes other than Docker Desktop.

**Consequences (testable):**

- The Stack starts and the Smoke Test passes on at least one non-Docker-Desktop runtime — verified in CI, or by a documented manual run recorded per release. Documentation alone does not satisfy this.
- Any required deviation for that runtime (flags, socket paths, unsupported Services) is stated in the Endpoint Contract documentation.
- Where a Service genuinely cannot work on a supported runtime, it is excluded from that runtime's Selection explicitly and fails per FR-3 rather than starting broken.

**Notes:** `[ASSUMPTION: Podman and Colima/OrbStack on macOS are the runtimes worth supporting. Windows-native is assumed out of scope. Neither confirmed.]`

---

### 4.5 Cross-Cutting NFRs

*Numbered so downstream architecture and story documents can cite them by handle, the way FRs are cited.*

- **NFR-1 Data durability.** `down` followed by `up` preserves every stateful Service's data. Only an explicit, confirmed destroy removes volumes. This is the Stack's single most load-bearing property; any change violating it is a defect regardless of what it enables.
- **NFR-2 Isolation.** All published ports bind to the configured `BIND_ADDRESS` (default `127.0.0.1`). No Service is reachable from the network by default.
- **NFR-3 Startup.** A full Selection reaches all-healthy in under two minutes on a warm image cache. The start command blocks until healthy and does not return optimistically.
- **NFR-4 Reproducibility.** Every image is pinned to an explicit tag. Two developers on the same commit get the same Stack.
- **NFR-5 Fail loud.** Missing or malformed required configuration fails at startup with a message naming the variable, rather than defaulting into a subtly wrong state. `[ASSUMPTION: this is the intent behind the current defaults; the architect should decide which variables are genuinely required, and by what mechanism.]`
- **NFR-6 Configuration honesty.** No `env_file` value is relied upon for Compose interpolation. CI asserts the resolved output of `docker compose config` so the `.env`-interpolates / `env_file`-does-not distinction cannot silently bite a reader who does not know it.
- **NFR-7 Resource cost is stated.** Every Bundle documents its approximate memory footprint, so choosing one is an informed decision rather than a surprise.
- **NFR-8 Security posture is fixed, not improved.** See §5.

## 5. Non-Goals (Explicit)

- **This will never be production-safe, and will not drift toward it.** Trivial credentials, no TLS, `start-dev` Keycloak, anonymous Grafana admin, and `synchronous_commit = off` are correct choices for the stated purpose. Requests to "harden it a bit" are rejected on principle: a stack that is *almost* safe to deploy is more dangerous than one that obviously is not.
- **Not a Kubernetes development environment.** No manifests, no Helm charts, no cluster. Tilt, Skaffold, DevSpace and Garden own that space.
- **Not a replacement for Testcontainers.** Per-test isolated containers are a different, well-solved problem. devinfra is deliberately long-lived and shared.
- **Not a remote or shared team environment.** Loopback-bound, single machine, single developer.
- **Not an application framework or project template.** It provides backing services and the contracts to reach them. What you build against them is yours.
- **Not a general-purpose container orchestrator.** The Catalog is curated. "Any service you want" is explicitly not the goal; the curation *is* the product.
- **No HashiCorp Vault**, or any BUSL-licensed component, anywhere in the Catalog.

## 6. MVP Scope

`[ASSUMPTION: the phasing below is my proposal, not your stated plan. You picked all four directions with no ordering, and the dependency between them is real — expanding the Catalog before modularizing makes the monolith worse, and adding Services before CI means nothing verifies them. If you want a different cut, this is the section to push back on.]`

### 6.1 In Scope

**Sequenced, because the dependencies are real:**

1. **Durability first (§4.4, FR-16 and FR-17).** CI and automated updates come first because everything after this is safer with them and riskier without. Small, and it protects the rest.
   **Gated on §8 Q8.** The Make-versus-pixi decision must be settled *before* CI is written, not after — CI written against Make and then migrated is wasted work, and the `make lint` silent-skip defect that motivates Q8 is precisely what step 1 exists to fix. Settling Q8 is the first task of the first step, not a parallel track.
2. **Modularity (§4.1, FR-1 through FR-5).** The refactor to Modules, Selection, dependency validation, and Bundles — done before the Catalog grows, not after. The Stack's user-visible behavior is unchanged at the end of this step; that is the acceptance bar.
3. **Batteries (§4.3, FR-12, FR-13, FR-15).** Endpoint Contract documentation, Grafana dashboards, and full backup coverage. Each is small and independently valuable.
4. **Catalog expansion (§4.2, FR-6 through FR-9).** The Module Completeness Contract, then message broker, search, and OpenBao — the three the maintainer named or implied most directly.

Plus the Gotcha Register (FR-18), which is cheap and can land anywhere in the sequence.

### 6.2 Out of Scope for MVP

- **The consumption-model decision (see §8, Q1).** Deliberately open. MVP assumes today's model — devinfra is its own checkout — and the modular refactor is designed not to foreclose the other paths. `[NOTE FOR PM]` This is the highest-leverage open question in the document; it becomes expensive to answer once Modules have a shape.
- **Worked example (FR-14).** Valuable, but it depends on the Endpoint Contracts being stable, which they are not until modularity lands. Deferred to immediately after MVP.
- **Cloud service emulation (FR-10).** No longer blocked — the licensing question that deferred it is resolved (§8, Q3), and an OSI-licensed path exists. Still out of MVP purely on sequencing: it is catalog work, and catalog comes last. Ready to build the moment the maintainer wants it.
- **Columnar analytics, local model inference, workflow orchestration** (the §4.2 Catalog backlog). No stated need yet, and deliberately carrying no FR. Each is one Module once FR-6 exists, so deferring costs nothing.
- **Container runtime portability (FR-19).** Real, but not blocking; it also gets easier once CI (FR-16) exists to verify it.
- **Multi-Stack / port conflict resolution.** The maintainer reports no pain here. Noted in §8, Q4 rather than built.

## 7. Success Metrics

Personal-stakes project, so the honest metric is short:

**Primary**

- **SM-1: Still in use.** The maintainer reaches for devinfra when starting a project six months from now, rather than hand-rolling a compose file. Validates the whole document; if this fails, nothing else mattered.

**Secondary**

- **SM-2: Selection is used.** Real projects run partial Selections rather than the whole Stack — evidence that FR-2 solved a real problem rather than an imagined one. Validates FR-1 through FR-5.
- **SM-3: Upgrades stop being scary.** Image-bump pull requests merge on green CI without manual verification. Validates FR-16, FR-17.

**Counter-metrics (do not optimize)**

- **SM-C1: Catalog size.** More Services is not better. The curation is the product; a Catalog that grows past what the maintainer actually uses has failed, not succeeded. Counterbalances §4.2 entirely.
- **SM-C2: Configuration surface.** Every new knob is a cost. If `.env.example` doubles in length, modularity was implemented wrong. Counterbalances FR-2 and FR-4.

## 8. Open Questions

1. ~~**Consumption model — how does a Consumer Project use devinfra?**~~ **RESOLVED — standalone checkout.** A project consumes the services devinfra provides; if they are not already running on the machine, the developer clones devinfra and starts them. Consuming projects are never modified to embed or vendor devinfra, and devinfra never generates files into them. The reusable-base and CLI-scaffold paths are not pursued. AD-6's closure-validity still keeps the reusable-base option reachable at no additional cost, but nothing depends on it.
2. ~~**Does Keycloak 26.4 support non-destructive realm re-import?**~~ **RESOLVED — the database drop is unnecessary, but "non-destructive" needs qualifying.** Verified two ways: empirically against the running 26.4.0 container with a throwaway probe realm, and against the Keycloak 26.4 source.

   **What it means for the product.** Dropping the `keycloak` database is not required in order to re-import a realm, so `make keycloak-reimport` changes — that behavior change is owned by FR-18. But the available overwrite is *remove-and-recreate*, not merge: runtime state in the target realm that is absent from the JSON is still lost, while other realms and the database survive. Two operational caveats matter to whoever builds this — run against a live container the import **exits non-zero** on a management-port collision, and the running server **serves stale cached realm data** until restarted, so the database and the admin API disagree silently. Both become Gotcha Register entries.

   **Remaining decision for the architect:** `kc.sh import` (simple, ships in the image, destroys the target realm) versus **`keycloak-config-cli`** (Apache-2.0, pure Admin REST API, supports 26.4.0) — the only genuinely idempotent realm-as-code option for a Compose setup. `partialImport` is a third path, covering clients, roles, identity providers, groups and users but **not** realm-level settings. Mechanism detail, source citations and the comparison table live in [addendum.md](addendum.md) §D and are deliberately not duplicated here, so the two cannot drift.

3. ~~**Which cloud emulator, and is its free tier sufficient?**~~ **RESOLVED — not LocalStack.** Verified: LocalStack's Community Edition was discontinued 2026-03-23, its GitHub repository is archived read-only, the current unified image requires a `LOCALSTACK_AUTH_TOKEN`, and the free Hobby tier is licensed for **non-commercial use only** (paid tiers from $39/user/month). It additionally requires mounting the host Docker socket — root-equivalent access — and its Podman support is experimental, conflicting with FR-19. **Remaining question is narrower:** confirm `motoserver` (Apache-2.0, covers SNS, Secrets Manager, SSM, Kinesis, EventBridge, Step Functions, IAM, CloudWatch) plus ElasticMQ (Apache-2.0, SQS) as the substitute, with MinIO retained as the S3 endpoint. See [addendum.md](addendum.md) §C.
4. **Do multiple Stacks ever need to run simultaneously?** Fixed ports mean one Stack per machine. The maintainer reports no pain, so this is a "confirm it stays true" question rather than a requirement.
5. **Should the observability Bundle stay five containers?** `grafana/otel-lgtm` packs the Collector, Prometheus, Loki, Tempo and Grafana into a single image for exactly this use case. Lighter and simpler; less configurable, and it would discard the existing tuned configs under `docker/`. An architecture trade-off, recorded for the architect.
6. **What is the CI runtime budget?** FR-16 assumes under 15 minutes. Starting thirteen Services and smoke-testing them on a hosted runner may not fit; if not, CI needs a tiering strategy.
7. **Is the worked example's language settled?** FR-14 assumes one language is enough but does not say which.
8. **Does the task surface stay in Make, or move to pixi?** Grounded in a real defect rather than preference: `make lint` today silently skips `shellcheck` when it is absent and skips YAML validation when PyYAML is absent, reporting success for checks it never ran. Unmanaged tool dependencies undermine FR-16 directly. Pixi provisions those tools reproducibly and helps FR-19; it also adds a prerequisite to a project whose pitch is low friction. Trade-offs in [addendum.md](addendum.md) §E.

## 9. Assumptions Index

Every `[ASSUMPTION]` in this document, surfaced for confirmation. Items marked *(addendum)* originate in [addendum.md](addendum.md) rather than inline here; they are listed so one index covers both documents.

1. **§4.1** — Compose `include` (2.20+) is the intended modularity mechanism; cross-Module dependency validation is ours to build on top of it.
2. **§4.2** — Choosing the specific product within each Catalog category (RabbitMQ vs NATS vs Kafka, OpenSearch vs Meilisearch) is an architecture decision, not a product one.
3. **§4.3, FR-14** — One worked example in one language suffices for v1.
4. **§4.3, FR-15** — "Nominal development dataset" means single-digit GB.
5. **§4.4, FR-16** — 15 minutes is an acceptable CI runtime.
6. **§4.4, FR-19** — Podman and Colima/OrbStack are the runtimes worth supporting; Windows-native is out of scope.
7. **§4.5** — Fail-loud on missing configuration is the intent; the architect decides which variables are genuinely required.
8. ~~**§6** — The four-step MVP sequencing is my proposal.~~ **RESOLVED — confirmed by the maintainer.** CI/durability → modularity → batteries → catalog expansion is the agreed order.
9. **§2** — The maintainer is the primary and effectively sole user; features are justified by his own use, not by hypothetical adopters. Implied by the stated personal/hobby stakes, not directly confirmed.
10. **§8 Q8 / addendum §E.4** *(addendum)* — The recommended task-runner shape (pixi owns the task surface and tool provisioning; non-trivial shell moves to `scripts/*.sh`) is my proposal, not a decision. The underlying defect — `make lint` silently skipping checks — is verified fact, not assumption.
11. **Addendum §A** *(addendum)* — Designing Modules so the reusable-base consumption model stays reachable costs near zero. If it turns out to cost real complexity, §8 Q1 should be decided rather than deferred.

---
id: SPEC-devinfra
companions:
  - "../../planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md"
  - "../../planning-artifacts/architecture/architecture-devinfra-2026-09-06/MIGRATION-PLAN.md"
  - "../../planning-artifacts/epics.md"
  - "../../../AGENTS.md"
sources:
  - "../../planning-artifacts/prds/prd-devinfra-2026-09-06/prd.md"
  - "../../planning-artifacts/prds/prd-devinfra-2026-09-06/addendum.md"
  - "../../../docs/adr/"
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# devinfra

## Why

**A vision to realize, with a mandate attached.** Every backend project needs the same seven or eight backing services, and assembling them correctly takes days — because the failure modes are silent. A Postgres volume mounted at the wrong path for its major version discards everything on `down`. A `clientScopes` array in a Keycloak realm import strips the built-in scopes from every client. Loki rejects OTLP outright without one config flag. None of these produce an error; they produce a bad afternoon.

devinfra already exists as a curated, working thirteen-service Compose stack, loopback-bound and persistent, whose accumulated gotchas are captured in the repository. This work makes it **modular** (take the four services you need, not all thirteen), **broader**, **complete**, and **durable** — because a stack you can trust and take pieces of beats a stack you have to take whole.

The mandate half: nothing currently proves the stack works except a human running one command, and validation that reports success for checks it skipped is worse than no validation. Correctness has to become continuous before the stack grows.

Affected party: the maintainer, and developers who work the same way. Not a team, not a deployment.

## Capabilities

Capability IDs mirror the source requirement IDs exactly, including gaps. **CAP-11 never existed** (FR-11 is reserved); **CAP-7, CAP-8 and CAP-10 are retired** — a dedicated message broker, a search engine and non-S3 AWS emulation were withdrawn once Redis was confirmed sufficient for Celery, Postgres full-text plus pgvector sufficient for search, and S3 confirmed as the only AWS surface in use. Retired IDs are never reused. `epics.md` cites these IDs directly.

- **CAP-1** — intent: Each Service is defined by exactly one Module owning its Compose fragment, config, environment variables and verification. success: Every Service has a Module directory; adding one requires editing no other Module.
- **CAP-2** — intent: A developer declares which Modules and Bundles to run and starts exactly that set. success: Selecting `postgres, redis` starts two containers and creates no other Service or volume.
- **CAP-3** — intent: The system refuses an incoherent Selection and names what is missing. success: Selecting `keycloak` without `postgres` fails before any container starts, naming `postgres`.
- **CAP-4** — intent: A developer selects a named Bundle instead of enumerating Modules. success: At least three Bundles ship — minimal, core, full observability — each already dependency-closed.
- **CAP-5** — intent: Verification runs only the checks belonging to the current Selection. success: A `postgres, redis` Selection produces zero failures and attempts zero checks against unselected Services.
- **CAP-6** — intent: Every Service in the Catalog arrives complete rather than partially wired. success: A Module missing any of healthcheck, smoke check, endpoint metadata, seed declaration or gotchas file is rejected by CI.
- **CAP-9** — intent: A developer runs a secrets service usable immediately. success: It starts initialized and unsealed with no manual step, and a secret written is read back.
- **CAP-12** — intent: Connection details for every Service are available in one place without reading the configuration. success: Every Service has an endpoint entry, and a changed port or credential either updates it or fails the build.
- **CAP-13** — intent: Telemetry is visible immediately rather than after building dashboards. success: With no manual steps, one panel per signal renders non-empty against the trace, log and metric the smoke run injects.
- **CAP-14** — intent: A runnable example proves the endpoint contracts end to end. success: A Python example exercises all six integrations against a documented Selection with no code changes, and runs in CI.
- **CAP-15** — intent: Backup and restore cover every stateful Service in the Selection. success: A backup captures Postgres databases, object storage contents and the Keycloak realm; restoring reproduces them, verified by a smoke run.
- **CAP-16** — intent: Every change is proven against a running stack before merge. success: A change breaking any smoke check fails CI, and no check may skip because a tool or fixture is unavailable.
- **CAP-17** — intent: Image updates are proposed automatically and validated before a human sees them. success: A newer tag produces an automated pull request within a week, which runs the full CI suite.
- **CAP-18** — intent: Documented failure modes are a maintained artifact rather than prose. success: Every existing gotcha carries symptom, cause, fix and affected versions, filed against the Module it affects.
- **CAP-19** — intent: The stack runs under Podman, the preferred runtime, as well as Docker. success: The stack starts and the smoke suite passes under Podman — documentation alone does not satisfy this.
- **CAP-21** — intent: A developer runs graph traversal and path queries over the same data pgvector indexes, in one database. success: A graph is created, nodes and edges inserted, and a traversal returns the expected path — in the same Postgres instance and transaction boundary as the relational and vector data.
- **CAP-20** — intent: The Catalog runs no image whose pinned tag is known to be superseded. success: Every pinned tag matches the current upstream release, or carries a dated written reason for lagging; verified against upstream rather than against what is running.

## Constraints

- **A renamed volume is a new volume.** Named volumes keep their identifiers permanently; the old data orphans silently with no error. The object-storage Module keeps the volume `minio-data` despite the name mismatch. This is the only unrecoverable failure mode in the system.
- **`down` then `up` preserves every stateful Service's data.** Only an explicit, confirmed destroy removes volumes. Any change violating this is a defect regardless of what it enables.
- **YAML anchors cannot cross Compose `include` boundaries** — they are document-scoped and `include` is applied after parsing. Shared service configuration flows through `extends` with a file reference, and the shared fragment may never declare `depends_on`, `links`, `volumes_from` or `network_mode: service:*`.
- **Cross-Module dependencies are expressed as `depends_on`, which is also the validation gate.** A dependency not expressed that way is unenforceable and therefore does not exist. `docker compose config -q` is the mechanism; no bespoke resolver is written.
- **`docker compose config -q` is blind to host-port collisions.** Ports are allocated from one Core-owned table and CI parses rendered config for duplicates.
- **The configuration namespace is flat and global**, owned by the root `.env`; a child `.env` can only ever be a fallback, never an override. Externally-dictated names such as `AWS_ENDPOINT_URL` are registered to exactly one owning Module.
- **No validation step may silently skip.** A check whose tool, service or fixture is unavailable fails the run.
- **All published ports bind to the configured bind address**, defaulting to loopback. No Service is reachable from the network by default.
- **Every image is pinned to an explicit tag.** No Service resolves to a floating tag.
- **AGE is never added to `shared_preload_libraries`.** Its hooks break databases lacking `ag_catalog` — `CREATE EXTENSION pg_stat_statements` and `TRUNCATE` both fail (upstream #2180, #2520). This stack preloads `pg_stat_statements` and creates extra databases without AGE, so preloading would break it. Sessions use `LOAD 'age'`.
- **A full Selection reaches all-healthy in under two minutes on a warm image cache**, and the start command blocks until healthy rather than returning optimistically.
- **Every Bundle documents its approximate memory footprint**, so choosing one is an informed decision rather than a surprise.
- **Catalog admission requires** an OSI-approved license, no account/token/licence key to start, no privileged host access including a Docker socket mount, and active upstream maintenance. HashiCorp Vault is named and excluded.
- **The security posture is fixed, not improved.** Trivial credentials, no TLS, development-mode services and anonymous admin are correct for the purpose. No change may partially harden the stack — a stack that is *almost* safe to deploy is more dangerous than one that obviously is not.
- **devinfra is consumed as a standalone checkout.** A project uses the services devinfra provides; if they are not already running on the machine, the developer clones devinfra and starts them. Consuming projects are never modified to embed or vendor devinfra, and devinfra never generates files into them.
- **Sequencing is not preference:** the task-runner decision is settled before any CI is written, and the stale image pins are bumped after CI exists but before the module split.

## Non-goals

- **Production use, and any drift toward it.** Requests to harden are rejected on principle, not weighed.
- **A Kubernetes development environment.** No manifests, no charts, no cluster.
- **A replacement for per-test isolated containers.** This stack is deliberately long-lived and shared across a machine's projects.
- **A remote or shared team environment.** Loopback-bound, single machine, single developer.
- **An application framework or project template.** It provides backing services and the contracts to reach them.
- **A dedicated message broker.** Redis brokers Celery and that is the only messaging pattern in use. Exchanges, consumer groups and durable replay are not needed.
- **AWS service emulation beyond S3.** Object storage is the only AWS surface in use. SQS, SNS, Secrets Manager and the rest are not needed, so no emulator earns a container.
- **A standalone search engine.** Postgres full-text search covers lexical search and pgvector covers vector similarity. A separate engine would add a container and buy nothing.
- **An uncurated service catalog.** "Any service you want" is explicitly not the goal — the curation is the product.

## Success signal

The maintainer starts a new project six months from now and reaches for devinfra instead of hand-rolling a compose file — and starts only the four services that project needs. Image-bump pull requests merge on green CI without manual verification, because the stack proves itself.

The counter-signal matters as much: if the Catalog has grown past what the maintainer actually uses, or `.env.example` has doubled in length, the work failed even if every capability shipped.

## Assumptions

- The maintainer is the primary and effectively sole user; capabilities are justified by his own use rather than by hypothetical adopters.
- Compose `include` is the modularity mechanism, with cross-Module dependency validation built on `depends_on` rather than on a resolver.
- A CI run completes within 15 minutes; if a full sweep exceeds it, the matrix is tiered rather than checks dropped.
- Podman and Colima are the runtimes worth supporting; Windows-native is out of scope.

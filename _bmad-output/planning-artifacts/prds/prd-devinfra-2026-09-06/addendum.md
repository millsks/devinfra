# devinfra PRD — Addendum

Technical depth captured during PRD discovery that belongs downstream — to `bmad-architecture` primarily. Nothing here is a requirement; it is context, considered alternatives, and trade-offs the architect should not have to rediscover.

---

## A. Consumption model — the deferred fork

§8 Q1 of the PRD leaves this open deliberately. It is the single decision that most constrains Module boundary design, so the architect should design *not to foreclose* rather than design *for one*.

### A.1 Clone and run standalone (today's model)

devinfra is its own checkout. Consumer Projects point environment variables at `localhost`.

- **Cost:** none — it already works.
- **Constraint it imposes:** one Stack per machine, fixed ports, no per-project variation. Version coupling is implicit: whatever commit is checked out is what every project on the machine gets.
- **Failure mode:** two projects needing different Postgres majors, or different realm configurations, cannot coexist.

### A.2 Reusable base pulled in by Consumer Projects

A Consumer Project consumes devinfra via Compose `include` pointing at a vendored path, a git submodule, or published images, and overlays its own services.

- **Enabling mechanism:** Compose `include` resolves relative paths against the included file's own directory ([docs](https://docs.docker.com/reference/compose-file/include/)), which is precisely what makes per-Module directories composable from outside the repo. This is the strongest technical argument for the FR-1 Module shape regardless of which consumption model wins.
- **Costs to solve:** versioning (what does a Consumer Project pin — a tag, a commit, a submodule ref?); port allocation across concurrent Stacks; how a Consumer Project overrides Seed Data without forking.
- **Note:** a submodule and a vendored copy differ mainly in update ergonomics; published images would only help for Services devinfra actually builds, and it currently builds none — every Service is an upstream image plus config. That argues against the image-publishing variant.

### A.3 CLI that scaffolds per-project Stacks

`devinfra init` generates a tailored Stack into a Consumer Project.

- **Cost:** largest by far. Becomes a distributed tool with its own release cadence, install story, and backward-compatibility surface. At personal stakes this is likely over-investment.
- **Observation:** Selection (FR-2) plus Bundles (FR-4) delivers most of the user-visible benefit of a scaffolder without any of the distribution burden. Worth confirming the CLI would add anything the Selection mechanism does not before committing.

**Recommendation for the architect:** design Modules so that A.2 remains reachable — self-contained directories, no absolute paths, no assumption that the repository root is the Compose working directory — while shipping A.1. That keeps the option alive at near-zero cost.

---

## B. Considered alternatives

### B.1 Observability: five containers vs `grafana/otel-lgtm`

Grafana publishes a single image bundling the OTel Collector, Prometheus, Loki, Tempo and Grafana, built for exactly the dev/demo case ([announcement](https://grafana.com/blog/an-opentelemetry-backend-in-a-docker-image-introducing-grafana-otel-lgtm/)).

| | Current five-container profile | `otel-lgtm` |
| --- | --- | --- |
| Configurability | Full — tuned configs under `docker/` | Limited; bundled defaults |
| Resource cost | ~1 GB, five containers | Lower, one container |
| Fidelity to production | Higher — same topology as a real deployment | Lower |
| Existing investment | Retained | Discarded |
| Gotchas already solved | Loki `allow_structured_metadata`, Tempo span metrics needing Prometheus remote-write | Solved upstream |

The Gotchas the maintainer already paid for are solved in the current setup, which weakens the migration case. A middle path worth evaluating: offer `otel-lgtm` as a *lightweight* observability Bundle alongside the full one, letting Selection decide. That fits the Bundle concept cleanly.

### B.2 Whole-category alternatives, and why they do not cover this

Recorded so the architecture document does not have to relitigate positioning.

- **Testcontainers / Testcontainers Desktop** — ephemeral, per-test, language-bound (though Desktop notably supports pinning services to fixed host ports, which is the closest anyone comes to devinfra's model).
- **Spring Boot `spring-boot-docker-compose`, Quarkus Dev Services, Micronaut Test Resources** — auto-discover a `compose.yaml` and wire connection details into the application. Framework-bound and tied to one app's lifecycle. **Relevant design input:** these frameworks expect specific service and env-var shapes; keeping Endpoint Contract naming conventional makes devinfra usable *through* them rather than against them.
- **.NET Aspire** — closest conceptual competitor; C#-centric, and its dashboard telemetry is in-memory only and lost on restart. devinfra's persistence is a genuine differentiator here.
- **Tilt / Skaffold / DevSpace / Garden / Okteto** — presuppose Kubernetes; Garden is widely reported as heavy for small teams.
- **LocalStack** — one vendor's surface. Was complementary rather than competing; now **ruled out** on licensing and host-access grounds, see §C (FR-10) for the verified detail and the substitute.
- **Supabase CLI / Firebase Emulator Suite** — vendor-locked.
- **devenv.sh / Flox** — Nix-based. Notably, Flox itself advocates "runtime on the host, backing services in containers," which is an endorsement of the niche devinfra occupies rather than a competitor to it.
- **Dev Containers** — `devcontainer.json` supports one primary service; multi-service requires hand-rolling a shared compose file, and port forwarding for non-primary containers is a long-standing rough edge.
- **docker/awesome-compose and similar template repos** — uncurated, unversioned, no health gating, no seeded identity. Snippets, not products. This is the category devinfra escapes by having FR-5 and FR-16.

**Where the whitespace is:** language-agnostic + project-agnostic + long-lived, with a pre-seeded working identity provider. Identity is the highest-friction, least-available piece — the existing `devinfra-realm.json` with three clients, two users, and working audience/role mappers is arguably the repository's most valuable single artifact.

---

## C. Catalog candidates — trade-offs per category

Product choice within each category is an architecture decision (PRD Assumption 2). Captured input:

**Message broker (FR-7).** RabbitMQ has the richest management UI and the gentlest local footprint. Kafka is heaviest and most production-representative; KRaft mode removes the ZooKeeper dependency that historically made it painful in Compose. NATS is the lightest, with the smallest operational surface. Decide by which the maintainer's real projects target.

**Search (FR-8).** OpenSearch is Elasticsearch-compatible and heavy (JVM, ~1 GB). Meilisearch and Typesense are dramatically lighter with a smaller API surface. If the goal is local development against a production Elasticsearch, only OpenSearch preserves fidelity; otherwise the lighter options fit the Bundle-weight philosophy better. Note the interaction with pgvector, which already covers vector search — the gap is lexical/full-text, not semantic.

**Secrets (FR-9) — verified, ready to build.** OpenBao, per the maintainer's stated constraint. Everything below was confirmed by running the image, not read from docs.

- **Image:** `openbao/openbao` — official and first-party, published from one Dockerfile to Docker Hub, `quay.io` and `ghcr.io`. Alpine base, non-root user, `EXPOSE 8200`. Variants: alpine (default), `-ubi`, `-distroless`, `-hsm-ubi`.
- **Version:** 2.6.2 (2026-08-18) is current stable. Cadence is roughly a minor every 5–6 months with monthly patches. Pin the exact tag per the reproducibility NFR.
- **License and governance:** MPL-2.0, under the Linux Foundation with OpenSSF as the umbrella (security contact is `openbao-security@lists.openssf.org`). Satisfies the no-BUSL constraint.
- **Dev mode:** the image's default `CMD` is already `server -dev`, and the entrypoint injects `-dev-root-token-id` from `BAO_DEV_ROOT_TOKEN_ID` and `-dev-listen-address` from `BAO_DEV_LISTEN_ADDRESS`. It comes up initialized and unsealed in seconds with zero manual steps — FR-9 satisfied directly. **Do not also pass `-dev-listen-address` in `command:`**; the entrypoint supplies it and it would be duplicated.
- **Healthcheck:** `GET /v1/sys/health`, returns 200 in dev mode. Unlike Keycloak, the alpine image ships BusyBox `wget` *and* `nc`, so no `/dev/tcp` fallback is needed: `wget -q -O /dev/null <http://127.0.0.1:8200/v1/sys/health>`. The distroless variant would need `bao status` instead.
- **Port:** 8200, which collides with nothing devinfra currently allocates.
- **CLI:** the binary is `bao`, with a `vault` symlink. Client env vars are `BAO_ADDR` / `BAO_TOKEN`, falling back to `VAULT_ADDR` / `VAULT_TOKEN`. `BAO_ADDR` **must** be set for exec-based use — the CLI defaults to `https://` and dev mode is plain HTTP, which fails with "server gave HTTP response to HTTPS client".
- **Smoke test:** KV v2 is mounted at `secret/` by default in dev mode, so no enable step is required. `bao kv put -mount=secret smoke value=hello && bao kv get -mount=secret smoke` — verified working.
- **Compose extras:** `cap_add: ["IPC_LOCK"]` is conventional for mlock.

**Cloud emulation (FR-10) — LocalStack ruled out; substitute identified.**

LocalStack is no longer viable for this project, on verified evidence: Community Edition was discontinued 2026-03-23, the GitHub repository is archived read-only, the current unified image requires a `LOCALSTACK_AUTH_TOKEN`, and the free Hobby tier is explicitly licensed for non-commercial use only (paid from $39/user/month). It also requires mounting the host Docker socket — root-equivalent host access, a poor thing to ask of anyone running a dev stack — and its Podman support is documented as experimental, which conflicts with FR-19. Pinned legacy tags such as `4.4.0` still run token-free under Apache-2.0 but receive no security patches, which fails the FR-17 currency requirement.

Recommended substitute, all Apache-2.0, no token, no socket mount:

| Need | Component | Notes |
| --- | --- | --- |
| S3 | **MinIO** (already present) | Retains console and versioned-bucket Seed Data |
| SQS | **ElasticMQ** | SoftwareMill, actively maintained, small JVM container |
| SNS, Secrets Manager, SSM, Kinesis, EventBridge, Step Functions, IAM, CloudWatch | **`motoserver`** | The closest true-OSS LocalStack substitute; broad service breadth |
| DynamoDB | `amazon/dynamodb-local` *or* moto | The AWS image is free-to-use but **not** open source — no public source. Use moto if OSI licensing is a hard requirement. |
| RDS, Cognito | *nothing* | No OSI-licensed option. Postgres already covers the RDS role; Keycloak already covers Cognito's. |

This also resolves the endpoint-collision concern cleanly: MinIO keeps `AWS_ENDPOINT_URL` for S3, and the other emulators get their own distinctly-named endpoint variables, rather than two components both claiming to be "the AWS endpoint."

**Deferred (FR-11).** ClickHouse for columnar analytics; Ollama for local model inference — note Compose now has a top-level `models:` element, which may be the idiomatic path; Temporal for durable workflows, which needs its own Postgres schema and is the heaviest of the three.

---

## D. Mechanism notes for the architect

**Compose `include`** — available since Compose 2.20. Resolves relative paths against the included file's directory, enabling self-contained Module directories. Does **not** validate cross-Module dependencies; FR-3 must be built on top, likely as a pre-flight check in the Makefile or a small script before `docker compose up`.

**Compose provider services** — non-container "platform capabilities" that inject `<<SERVICE>>_<<VAR>>` environment variables into dependents. Potentially relevant to how Endpoint Contracts (FR-12) are delivered rather than documented. The docs do not state an introduction version; verify availability before depending on it.

**Compose CVE-2025-62725** — fixed in 2.40.2. A documented minimum Compose version is warranted regardless, since `include` requires 2.20+.

**`.env` vs `env_file`** — `.env` values interpolate the Compose file; `env_file` values are passed verbatim and are **never** interpolated. This is a widely-reported footgun and directly relevant to the "configuration honesty" NFR. Verify resolved configuration with `docker compose config` in CI (FR-16 already implies this).

**`${VAR:?message}`** — the fail-loud mechanism for the corresponding NFR. Applying it indiscriminately would break the current "works with no `.env`" defaults, so the architect should pick the genuinely-required set deliberately.

**Healthcheck vs Smoke Test** — `depends_on: condition: service_healthy` is the canonical answer to "started ≠ ready" and is already used throughout the current compose file. Preserve this through the Module refactor; it is load-bearing for the sub-two-minute startup NFR.

**Keycloak realm-as-code — fully verified, decision required.** PRD §8 Q2 is resolved. Confirmed both empirically against the running 26.4.0 container and in the 26.4 source tree.

*Established facts:*

- The realm file must be named `<realm>-realm.json` and placed in `/opt/keycloak/data/import`.
- `start-dev --import-realm` hard-codes `Strategy.IGNORE_EXISTING` (`ExportImportManager.java:78-79`) and logs `Realm '%s' already exists. Import skipped` (`ImportUtils.java:111-112`). No flag or environment variable changes this: `AbstractAutoBuildCommand.excludedCategories()` removes `OptionCategory.IMPORT` from `start` and `start-dev`. There is no `KC_IMPORT_REALM` variable, and `KC_OVERRIDE` in a Compose `environment:` block has no effect on startup import.
- `kc.sh import --file <f> | --dir <d>` accepts `--override <true|false>`, **default true**, added in 21.1.0. `--dir` and `--file` are mutually exclusive and exactly one is required.
- **`--override` is remove-and-recreate, not merge** (`ImportUtils.java:115` calls `model.removeRealm()`). Runtime state in the target realm that is absent from the JSON is lost. Other realms and the database survive.
- Keycloak's documentation states all nodes should be stopped before an override import, because the import command runs as a separate JVM against the same database and **does not attach to the cache cluster**. This is the mechanism behind the behavior measured below.

*Measured on the live stack (throwaway probe realm, since deleted):*

- Run via `docker compose exec` against a running container, the import succeeds but **exits non-zero**, failing to bind the management interface on 9000 which the running server holds. `--http-management-port 9999` makes it exit 0. Environment variables such as `KC_DB*` are inherited, so the command otherwise works unmodified.
- The import commits to the database while the running server continues serving **stale cached realm data**: Postgres returned `PROBE-V2` while the admin API returned `PROBE-V1`. A container restart applied it. This silent divergence is a Gotcha in its own right (FR-18).

*Three candidate mechanisms — the architect picks:*

| | `kc.sh import --override` | `keycloak-config-cli` | `partialImport` REST |
| --- | --- | --- | --- |
| Idempotent | No — removes and recreates | **Yes** | Partially (`ifResourceExists`) |
| Covers realm-level settings | Yes | Yes | **No** |
| Covers clients / roles / users / groups / IdPs | Yes | Yes | Yes |
| Server must stop | Yes per docs; restart required in practice | No | No |
| Extra dependency | None — ships in the image | One tool (adorsys, Apache-2.0, supports 26.x with explicit 26.4.0 baselines) | None |
| Complexity | Lowest | Moderate | Low but incomplete |

`keycloak-config-cli` is the only one delivering genuine realm-as-code on Compose. A reasonable shape is `kc.sh import` for bootstrap and `keycloak-config-cli` for ongoing reconciliation — but that is the architect's call. The Keycloak Operator's `KeycloakRealmImport` CRD is Kubernetes-only and creation-only; irrelevant here.

*Existing realm Gotchas to preserve:* a `clientScopes` array in the import replaces Keycloak's built-in scopes rather than adding to them; and a realm-level `passwordPolicy` is enforced against imported users, so a `length(4)` policy fails the import of user `dev`.

**Postgres `PGDATA` path** — version-specific: 17 keeps it at `/var/lib/postgresql/data`; 18 moved it to `/var/lib/postgresql/18/docker`. The wrong path silently discards data on `down`. This is the highest-severity Gotcha in the register and the strongest candidate for FR-18's "verifiable Gotchas get a CI assertion" clause — a test that writes a marker, cycles the Stack, and reads it back would catch it.

**OTel conventions** — `OTEL_EXPORTER_OTLP_ENDPOINT`, ports 4317/4318, collector receivers must bind `0.0.0.0` rather than `localhost`. `deployment.environment.name` is stable as of semantic conventions 1.27; `development` is the conventional value. No OTel convention specific to local development exists.

**Podman (FR-19)** — most Compose v3 files run unmodified, but the Podman-native direction is Quadlet, which is a different artifact shape entirely. Supporting "podman-compose works" is cheap; supporting Quadlet is a separate project.

---

## E. Task runner — Make vs pixi (PRD §8 Q8)

### E.1 The defect that motivates the question

This is not a preference argument. [`Makefile`](../../../../Makefile), `lint` target:

```make
@if command -v shellcheck >/dev/null 2>&1; then \
    shellcheck scripts/*.sh docker/postgres/initdb/*.sh && echo "shellcheck OK"; \
else echo "shellcheck not installed — skipping"; fi
@python3 -c "import json,sys; [json.load(open(f)) for f in sys.argv[1:]]; print('JSON OK')" ...
@python3 -c "import sys,yaml; [yaml.safe_load(open(f)) for f in sys.argv[1:]]; print('YAML OK')" ... \
    || echo "PyYAML not installed — skipping"
```

On a machine without `shellcheck` or PyYAML, `make lint` exits 0 having validated a subset of what it claims. A check that degrades to a no-op and still reports success is the same silent-failure class the README's Gotchas section exists to prevent. It also undermines FR-16 at the root: CI is only as trustworthy as the checks it runs, and "run the same checks locally" is meaningless if the local checks are conditional on ambient tooling.

Separately, the two bare `python3` invocations conflict with the maintainer's standing project convention that every Python invocation is provisioned through pixi.

### E.2 What pixi actually buys

- **Reproducible tool provisioning.** `shellcheck`, `yamllint`, `yq`, `jq`, `python`, and the MinIO client are all available from conda-forge and pinned in `pixi.lock`. Every developer and every CI runner gets identical versions. This is the direct fix for F.1 and the strongest argument in the set.
- **CI/local parity (FR-16).** `prefix-dev/setup-pixi` in the workflow means CI runs the same task definitions against the same tool versions, rather than a parallel set of steps that drift from the Makefile.
- **Cross-platform (FR-19).** Pixi tasks execute on Windows, macOS and Linux without a POSIX shell. Make on Windows requires MSYS2, Git Bash or WSL — a meaningful obstacle if FR-19's scope ever widens beyond alternative container runtimes.
- **Convention alignment.** The maintainer's other projects use a standard task vocabulary (`lint`, `check`, `ci`, `test`, …) with `depends-on` chaining. Matching it makes devinfra legible alongside them.
- **Task arguments.** Pixi tasks accept arguments, so `make psql DB=keycloak` maps to `pixi run psql keycloak` without losing ergonomics.

### E.3 What pixi costs

- **A prerequisite.** Make is present on any developer machine; pixi is a single-binary install, but it is still a step before you can start a database, in a project whose pitch is minimal friction. This is the strongest counterargument.
- **Pixi tasks are commands, not a shell language.** Three things in the current Makefile do not translate directly:
  - the `wait` target's 60-iteration polling loop over `docker compose ps` output,
  - the `destroy` and `keycloak-reimport` targets' `read -p` confirmation prompts,
  - the `urls` target's formatted `printf` block.
- **No file-target dependencies.** Make's `$(ENV_FILE): ; cp .env.example .env` rule, and `up: $(ENV_FILE)` depending on it, has no direct pixi equivalent. Pixi has `depends-on` and `inputs`/`outputs` caching, but the `.env` bootstrap becomes an explicit task rather than an implicit file rule.
- **Environment loading.** The Makefile's `include .env` + `export` gives every target the resolved variables. Pixi would need `[activation.env]`, a `.env`-sourcing wrapper, or reliance on Compose's own `.env` handling.

### E.4 Recommended shape

`[ASSUMPTION: not confirmed by the maintainer.]` Pixi owns the task surface and the tool provisioning; non-trivial shell logic moves out of the Makefile into `scripts/*.sh` that pixi tasks invoke.

That split is worth doing on its own merits regardless of which runner wins. Logic embedded in Makefile recipes — the health-wait loop especially — cannot be unit-tested, cannot be run directly for debugging, and cannot be reused by CI without going through Make. Extracting it to `scripts/` makes it testable, and once extracted, the runner choice becomes a thin question about who invokes those scripts.

Sequencing note: this decision interacts with FR-16 and should be settled *before* CI is built, not after — otherwise CI is written against Make and then rewritten. It is cheap now and annoying later.

Open sub-question for the architect: whether a thin Makefile survives as an alias shim (`make up` → `pixi run up`) for muscle memory and for developers who have Docker but not pixi. That preserves the zero-install path for the common case while giving the validation tasks a managed environment — plausibly the best of both, at the cost of two task definitions to keep in sync.

---

## F. Sources

Landscape research conducted 2026-09-06. Key references:

- Compose `include` — <https://docs.docker.com/reference/compose-file/include/>
- Compose provider services — <https://docs.docker.com/compose/how-tos/provider-services/>
- Compose `develop.watch` — <https://docs.docker.com/reference/compose-file/develop/>
- Keycloak import/export — <https://www.keycloak.org/server/importExport>
- OTel deployment environment semconv — <https://opentelemetry.io/docs/specs/semconv/resource/deployment-environment/>
- `grafana/otel-lgtm` — <https://grafana.com/blog/an-opentelemetry-backend-in-a-docker-image-introducing-grafana-otel-lgtm/>
- Spring Boot dev services — <https://docs.spring.io/spring-boot/reference/features/dev-services.html>
- Testcontainers Desktop — <https://testcontainers.com/desktop/docs/>
- .NET Aspire — <https://aspire.dev/>
- LocalStack — <https://www.localstack.cloud/>
- Flox on runtime-on-host / services-in-containers — <https://flox.dev/blog/a-pattern-for-local-dev-runtime-on-the-host-services-in-containers/>
- Dev Containers multi-service limitation — <https://github.com/microsoft/vscode-remote-release/issues/254>
- `.env` vs `env_file` interpolation — <https://blog.foxxmd.dev/posts/compose-envs-explained/>
- Compose healthcheck practice — <https://last9.io/blog/docker-compose-health-checks/>

Verification pass (open questions Q2, Q3, and OpenBao feasibility), 2026-09-06:

- Keycloak import/export docs — <https://www.keycloak.org/server/importExport>
- Keycloak all-config reference — <https://www.keycloak.org/server/all-config>
- Keycloak 26.4 source (`release/26.4`) — <https://github.com/keycloak/keycloak/tree/release/26.4>
- keycloak-config-cli version compatibility — <https://adorsys.github.io/keycloak-config-cli/compatibility/keycloak-versions/>
- OpenBao Dockerfile — <https://github.com/openbao/openbao/blob/main/Dockerfile>
- OpenBao container entrypoint — <https://github.com/openbao/openbao/blob/main/.release/docker/docker-entrypoint.sh>
- OpenBao developer quick start — <https://openbao.org/docs/get-started/developer-qs/>
- OpenBao server command docs — <https://openbao.org/docs/commands/server/>
- LocalStack licensing and tiers — <https://docs.localstack.cloud/aws/licensing/>
- LocalStack pricing — <https://www.localstack.cloud/pricing>
- LocalStack "the road ahead" (Community EOL) — <https://blog.localstack.cloud/the-road-ahead-for-localstack/>
- LocalStack Podman support — <https://docs.localstack.cloud/aws/capabilities/config/podman/>

In addition, the Keycloak `--override`, management-port and stale-cache findings were verified by direct execution against the running `devinfra-keycloak` container (26.4.0) using a throwaway `kc-override-probe` realm, which was deleted afterward. The `devinfra` realm was not modified.

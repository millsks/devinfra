# Rubric Walker Review — ARCHITECTURE-SPINE.md (devinfra)

**Reviewer:** rubric walker (good-spine checklist, `references/reviewer-gate.md`)
**Target:** `_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md`
**Version reviewed:** the current file — 376 lines, **AD-1 … AD-19**, `.memlog.md` present (45 entries)
**Driving spec:** `prds/prd-devinfra-2026-09-06/prd.md` + `addendum.md`
**Governed repo:** `compose.yaml` (424 lines, 14 Compose services), `Makefile`, `scripts/smoke-test.sh`, `README.md`
**Context:** stakes personal/hobby · altitude feature · purpose build-substrate · authored on the Fast path
**Date:** 2026-09-06

> **Note on timing.** The spine was revised on disk mid-review — it grew from 15 ADs to 19, and `.memlog.md` appeared. This review targets the **current** version. The memlog shows why: an adversarial gate found two self-contradictions (SC-1, SC-2) and eight divergence holes (H-1…H-8), all empirically tested against Compose v5.3.0, and the revision closes them. Findings I had raised against the earlier draft that the revision fixed — the default-Selection regression, the `minio-init` orphan, the flat-namespace vs `AWS_ENDPOINT_URL` collision, the `AD-5 root-is-authoritative` claim, the FR-15 restore ordering, the object-storage inconsistency — are **not** re-listed. What follows is what survives.

---

## Gate verdict

**Does not pass — two blocking gaps, both narrow.**

This is now a strong spine and, in places, an unusually good one. AD-4's two-tier namespace, AD-5's identifier-only volume stanza, AD-14's `x-requires`, AD-16's Selection closure and AD-17's port table are all textbook invariants: each rests on an empirically verified mechanic, each names a divergence a builder could not read off compliant code, and each carries a Rule a CI script can execute. The memlog's rationale is where the skill says rationale belongs. `Deferred` does real work.

It fails the gate on two things, both cheap:

1. **The licensing / host-access / security-posture invariant is still missing** (C1). NFR-8's map row now points at "Paradigm", a section that says nothing about it. This is a user-stated constraint with catalog work next in the sequence.
2. **Zero `[ASSUMPTION]` tags on a Fast-path spine** (C2). The correction mechanism the path depends on was never engaged, and Finalize step 4 has nothing to triage.

Beyond those: eight High findings, mostly breadth holes at the edges of the operational envelope, and a cluster of map rows that assert governance the ADs do not contain.

Calibration: personal/hobby stakes. C1 is one AD. C2 is six tags. The Medium and Low tiers are a checklist, not a blocker.

---

## Dimension verdicts

| Rubric dimension | Verdict |
| --- | --- |
| Fixes the real divergence points for the level below, misses none | **PARTIAL** — the intra-Compose divergences are now covered thoroughly; the licence/host-access, destructive-confirmation, and Endpoint-Contract-artifact divergences are not |
| Every AD's Rule is enforceable and prevents its stated divergence | **PASS with exceptions** — most Rules now name a concrete CI check; 5 still cannot be checked |
| Nothing under `Deferred` lets two units diverge | **PARTIAL** — `Deferred` is well-reasoned, but FR-9/FR-10 are half-decided *outside* it, and Bundle-indexed CI means deferred Modules land uncovered |
| Named tech verified-current | **PARTIAL** — three major upgrades presented as a cold-start pin; Compose floor contradicts the addendum's CVE note; one row carries two versions, one carries none |
| Ratifies rather than contradicts the brownfield codebase | **PARTIAL** — the code sweep is excellent; the healthcheck convention still silently changes three services, and the `object-storage` rename collides with AD-5 |
| Covers the driving spec's capabilities | **PARTIAL** — all FRs and NFRs now have a row; several rows attribute governance to ADs that do not contain it |
| Inherited parent spine not weakened | **N/A** — no parent |
| Every dimension the altitude owns is decided, deferred, or an open question | **FAIL** — three silent dimensions: repo versioning/release, secrets posture, verification of `scripts/` |
| Mermaid validity (3 diagrams) | **PASS** — all three parse |
| Internal consistency (AD IDs, references, conventions) | **PARTIAL** — IDs clean; AD-15 still contradicts AD-4/AD-11; one convention contradicts AD-5 |

**Finding counts:** 2 Critical · 8 High · 12 Medium · 12 Low = 34

---

## Mechanical checks

**AD ID contiguity.** AD-1 … AD-19, contiguous, no duplicates, no reuse, none renumbered from the earlier draft where they survived. Clean.

**AD references resolve.** Every AD cited in the map, the conventions table, the Structural Seed and inside other ADs exists. No phantom AD. Two ADs are cited nowhere in the map: **AD-2** (appears only in a Structural Seed comment and a conventions row) and **AD-15** (the spine-wide dependency-direction rule).

**FR-11.** Correctly absent from `binds:`, the map and the body. No phantom FR-11 anywhere.

**Frontmatter `sources:` paths resolve.** `../../prds/prd-devinfra-2026-09-06/{prd,addendum}.md` from the run folder resolves correctly.

**Mermaid — all three valid.** Verified by rendering, not by eye: extracted each fenced block and ran `mmdc` (mermaid-cli 11.12.0, mermaid 11.x). All three exit 0 and produce SVG.

| Diagram | Location | Result | Nodes rendered |
| --- | --- | --- | --- |
| Core Substrate ↔ Modules | §Design Paradigm | **valid** | 21 nodes, 5 clusters |
| Dependency direction | AD-15 | **valid** | 9 nodes |
| Bundle composition | §Structural Seed | **valid** | 36 nodes, 3 clusters |

Specifically checked and clean: `subgraph ID["label"]` quoted-label form; em-dashes and `…` inside quoted labels; `<br/>` in node and edge labels; the quoted edge-label form `-.->|"…"|`; the `-.text.->` dotted-inline-label form in diagram 3; the `style X fill:#…,stroke:#…` directives in diagram 2; the `B -.-> B` self-loop; and the subgraph id `B` in diagram 3 not colliding with a node id.

Two **semantic** notes (not parse errors): diagram 2 still draws the *forbidden* edge `C -.->|never| B` as a real arrow, so the picture asserts the opposite of the rule it illustrates (M9); and the Core Substrate table lists "the `devinfra` network" as Core-owned but diagram 1 no longer has a network node — it was dropped in the revision that added `SEL`.

---

## Traceability audit — Capability → Architecture Map

PRD carries FR-1..FR-10, FR-12..FR-19 (FR-11 reserved) and NFR-1..NFR-8. **Every one now has a row** — a real improvement; NFR-8 was previously absent entirely. But several rows attribute governance to ADs that do not contain the named mechanism.

| Requirement | Row | Verdict |
| --- | --- | --- |
| FR-1 | AD-1, AD-6, AD-8 | Sound |
| FR-2 | AD-16, AD-7, AD-18 | Sound — AD-18 closes the default-Selection regression |
| FR-3 | AD-3, AD-17 | Sound — AD-3's `config -q` blind spot is now declared and covered |
| FR-4 | AD-7, AD-16 | Sound — `x-bundles` gives CI something to enumerate |
| FR-5 | AD-10 | Sound |
| FR-6 | AD-8, AD-14 | **Incomplete** — AD-8 enforces 3 of FR-6's 5 things (H1) |
| FR-7 / FR-8 | collapsed row | Product choice deferred; acceptable |
| FR-9 | collapsed row | **Half-decided** — pinned in Stack, absent from seed / Bundles / ADs / Deferred (H5) |
| FR-10 | collapsed row | **Ungoverned** — no AD carries its licence / token / socket constraints (C1) |
| FR-11 | correctly absent | Correct |
| FR-12 | AD-4, AD-11, AD-17 | **Cites "Module metadata", which no AD or seed entry defines** (H1) |
| FR-13 | AD-13, AD-10 | Sound |
| FR-14 | AD-4 | `examples/` is now in the seed; the example itself is ungoverned, Q7 unaddressed (L12) |
| FR-15 | AD-12, AD-5 | Sound — AD-12 now genuinely governs restore |
| FR-16 | AD-19, AD-9 | AD-19's CI list omits three FR-16 consequences (H1) |
| FR-17 | AD-11 | Sound — the strongest AD in the spine |
| FR-18 | AD-8, AD-12 | Per-Module only; the "register" as an artifact is undecided (L9) |
| FR-19 | AD-19, AD-9 | AD-19 binds FR-19 but says nothing about a runtime matrix (L5) |
| NFR-1 | **AD-5**, AD-12 | Named, but nothing verifies it (M4) |
| NFR-2 | AD-17, Conventions | Sound |
| NFR-3 | AD-9, AD-18 | "Does not return optimistically" is in no AD (L7) |
| NFR-4 | AD-11 | Sound |
| NFR-5 | AD-12, AD-18, AD-19 | `ON_ERROR_STOP=1` and non-empty-Selection are real; **`${VAR:?}` appears in no AD** (M3) |
| NFR-6 | AD-19, AD-4 | Sound |
| NFR-7 | AD-7 | AD-7's Rule says nothing about documenting footprint (L6) |
| NFR-8 | **Paradigm** | **The Design Paradigm section contains nothing about security posture** (C1) |

**Dangling references:** "Module metadata" (FR-12), `${VAR:?}` (NFR-5), "CI matrix" (FR-19), "Paradigm" as NFR-8's governor, `backups/` (FR-15, absent from the seed).

---

## CRITICAL

### C1 — The licensing / host-access / security-posture invariant is still missing, and NFR-8's map row points at a section that does not contain it

The PRD carries three converging constraints:

- §5 Non-Goals, **user-stated**: "**No HashiCorp Vault**, or any BUSL-licensed component, anywhere in the Catalog."
- FR-10: "using only components under an OSI-approved license with **no authentication token** and **no privileged host access**" — consequences add "No component requires mounting the host Docker socket."
- NFR-8: "Security posture is fixed, not improved," backed by §5's "Requests to 'harden it a bit' are rejected on principle: a stack that is *almost* safe to deploy is more dangerous than one that obviously is not."

The memlog records it as binding on the first page — "(constraint) User-stated: OpenBao not HashiCorp Vault; no BUSL-licensed components anywhere in the catalog" — and then it does not survive distillation. The revision added a map row, but the row reads `NFR-8 Security posture fixed | no AD may relax it | **Paradigm**`, and the Design Paradigm section is about the Core/Module split. It says nothing about licences, tokens, the Docker socket, or hardening. "No AD may relax it" is a meta-rule about the document, not a constraint on the system, and it is not checkable.

Run the skill's test. Could two independently-built Modules choose incompatibly? Trivially yes. Is the call non-obvious? Yes — the addendum shows three *different* ways a plausible component fails it: Vault on BUSL, LocalStack on non-commercial licensing **plus** an auth token **plus** a Docker-socket mount, and `amazon/dynamodb-local` on free-to-use-but-not-open-source. Is it a real trade-off? Yes — the addendum's FR-8 note weighs OpenSearch's production fidelity against lighter options, and the FR-10 table trades service breadth for licence purity. It binds every future Module, and it is exactly what a builder cannot read off compliant code: a working `compose.yaml` never tells you why an image was *rejected*.

The "don't harden it" half is equally load-bearing and equally absent. A contributor adding TLS to Keycloak, or turning off `GF_AUTH_ANONYMOUS_ENABLED`, would be violating a stated product principle with nothing in the build substrate to stop them.

This is blocking because catalog expansion (FR-7…FR-10) is the next MVP phase, and it is the phase where the constraint bites.

**Fix — one AD, roughly:**
*Binds:* FR-7, FR-8, FR-9, FR-10, NFR-8, every Module.
*Prevents:* a Module arriving on a licence or host-access footing the project has already rejected, and incremental hardening drift.
*Rule:* every image is OSI-licensed — no BUSL, no source-available, no free-tier-licensed component; no Service requires an account, auth token or licence key to start; no Service mounts the host Docker socket or requires privileged host access; credentials stay trivial and TLS stays off, and a change that improves security posture is rejected rather than merged. CI asserts the licence of every image in the Stack table against a committed allowlist.

### C2 — Fast-path spine with zero `[ASSUMPTION]` tags

`grep -c ASSUMPTION` on the spine returns **0**. On the Fast path, tagging unconfirmed inferences *is* the job — it is the mechanism the author corrects in review, and Finalize step 4's triage ("blockers resolved one at a time; the rest deferred with a revisit condition") has nothing to operate on without it.

The memlog landing since the earlier draft fixes half of this — the rationale now has a home and the Update path has an authority to resume from. But the memlog also makes the gap *visible*: it records calls as decided that the PRD explicitly marks unconfirmed, and the spine states them flatly.

- **AD-9's** "The `Makefile` is retained only as a deprecation shim forwarding to pixi." Addendum §E.4 ends on this as an *open sub-question for the architect*; PRD Assumptions Index #10 marks the whole recommended pixi shape "my proposal, not a decision."
- **AD-19's** "A full run completes within 15 minutes." PRD Assumption #5 and Q6 both flag the number as invented, with Q6 asking directly whether 13 Services fit a hosted runner and warning "if not, CI needs a tiering strategy." Q6 is neither answered nor deferred.
- **Three major image upgrades** in the Stack table (M2). The memlog flags them "(MAJOR)"; the spine does not.
- **Bundle names and membership** — `minimal` / `core` / `admin` / `observability` and their contents are invented here.
- **`seed.none`** as the marker filename. PRD FR-6 says `seed: none`.
- **The breaking change to bare `docker compose up`.** The memlog is explicit — "giving every service a profile is a BREAKING change … Must be called out in the migration plan." AD-18 mitigates it, but nothing marks the mitigation as unconfirmed with the maintainer.

**Fix:** six `[ASSUMPTION]` tags. Ten minutes.

---

## HIGH

### H1 — AD-8 still does not enforce FR-6, the Endpoint Contract has no artifact, and AD-19's CI list omits three FR-16 consequences

**FR-6** requires five things per Module: **a Healthcheck, a smoke check exercising real function, a documented Endpoint Contract, a Seed Data declaration, and a Gotchas file.** The map governs FR-6 with AD-8 + AD-14.

AD-8's Rule lists `compose.yaml`, `smoke.sh`, `gotchas.md`, `seed/`-or-`seed.none`, `conf/` when needed. That is three of the five. **Neither the Healthcheck nor the Endpoint Contract has an artifact in the contract directory**, so `check-modules.sh` as specified passes a Module missing two of the things FR-6 says make a Module done. The healthcheck at least survives as a conventions row; the Endpoint Contract does not appear anywhere in AD-8, the conventions, or the seed.

Compounding: the map's FR-12 row names its source as "`.env` + **Module metadata**" — an artifact **no AD, convention, or seed entry defines**. This is a live divergence point: two builders would reasonably choose a `module.yaml` sidecar, front-matter in `gotchas.md`, Compose service `labels:`, or a hand-maintained central table — all incompatible, all defensible, and the spine's whole purpose is to pick one. FR-12's hard consequence is that "a port or credential change in configuration causes the documentation to change or CI to fail — the two cannot silently diverge," and **AD-19's CI list contains no such check.**

AD-19's enumerated CI contract also omits two further FR-16 consequences:

- "CI exercises the worked example (FR-14)" — absent.
- "CI validates Compose configuration, shell scripts, YAML and JSON — the checks `make lint` performs today" — AD-19 lists `config -q` and five structural checks, but no shellcheck / yamllint / JSON validation. AD-9 provisions the tools and a conventions row says "shellcheck-clean under CI," so it is half-covered across two ADs — but AD-19 reads as *the* CI contract and a builder implementing it literally would ship a CI that lints nothing.

**Fix:** name the Endpoint Contract artifact in AD-8's directory list, add healthcheck-presence to its bidirectional check, and add the doc-divergence check, the worked-example run and the lint suite to AD-19's list.

### H2 — AD-16 forecloses the consumption model that AD-6 and `Deferred` exist to keep open

AD-6 is explicitly justified by PRD Q1: closure-validity "is the mechanism keeping the reusable-base path reachable," and `Deferred` item 2 says "nothing here commits to it." Addendum §A.2 describes that path concretely: a Consumer Project pulls devinfra in via Compose `include` pointing at a vendored path or submodule, and overlays its own services.

AD-16 then makes `scripts/select.sh` mandatory for every Compose invocation: "Every task that invokes Compose goes through it. **A raw `docker compose --profile <module>` invocation that skips the resolver is expected to fail loudly, and that is correct behaviour, not a defect.**"

A Consumer Project that `include`s devinfra's Module files has no `select.sh`, no `x-bundles` registry, and no pixi task surface — it drives Compose directly. Under AD-16 that is precisely the invocation declared broken-by-design. So the spine keeps Module *files* liftable (AD-6) while making the Selection mechanism unliftable, which is the half that matters: the addendum's §A.2 costs-to-solve list is versioning, port allocation and seed overrides, all of which now route through Core-owned scripts.

Secondary: raw `docker compose up` no longer works at all, in a project whose pitch is minimal friction, and PRD FR-2 requires "no existing invocation breaks." AD-18 covers the *default* case; ad-hoc `--profile` use is now a documented failure.

**Fix:** either state in AD-16 that Selection resolution is Core-only and a Consumer Project pins a resolved `COMPOSE_PROFILES` value rather than the resolver, or move Q1 out of `Deferred` and accept that the consumption model is now decided as A.1.

### H3 — The `object-storage` rename collides with AD-5 and with the volume-name convention

`Deferred` says: "The Module is named `object-storage` rather than `minio` so the decision does not require a rename against AD-5."

It does not avoid the problem; it relocates it. The volume today is **`minio-data`**. AD-5 freezes it: "Named volumes keep their current identifiers (`postgres-data`, `keycloak-data`, …) permanently." The conventions table says: "Volume name | `<service-name>-data` (existing pattern, **frozen** per AD-5)." With the service named `object-storage`, those two demand `minio-data` and `object-storage-data` respectively. **A convention row and the AD it cites now require different names for the same volume.**

The rename cascades further and is unaddressed anywhere:

- Seven `MINIO_*` variables (`MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKETS`, `MINIO_VERSION`, `MINIO_MC_VERSION`) are Module variables under AD-4 and would become `OBJECT_STORAGE_*` — while AD-11 requires the version variable to carry a `# renovate:` annotation naming the repo, which is the very thing still undecided.
- The container name convention `devinfra-<service-name>` gives `devinfra-object-storage` against today's `devinfra-minio`.
- `scripts/smoke-test.sh` addresses the service by name (`dc minio mc alias set …`), as do `make mc` and the Docker-network DNS name other Modules use.
- The helper is `minio-init`; under AD-8's `<module>-<role>` rule it becomes `object-storage-init`.

**Fix:** state explicitly in AD-5 or `Deferred` that the volume identifier stays `minio-data` regardless of the Module name — and add a carve-out to the volume convention, since the frozen identifier now diverges from the pattern by design.

### H4 — AD-19's CI coverage is Bundle-indexed, so a Module in no Bundle is never validated

> **AD-19:** "CI runs, **for every Bundle in the `x-bundles` registry**: `docker compose config -q`, the AD-8 bidirectional module check, the AD-17 port check, the AD-5 volume-stanza check, the AD-14 `x-requires` reconciliation, and the Smoke Test."

Nothing requires a Module to belong to a Bundle. AD-7 checks one direction only — "CI fails on a Bundle profile that appears in no registry entry, or a registry entry no Module joins" — not "a Module that joins no Bundle." AD-8 enforces a Module↔Service bijection, not Bundle membership.

So a Module in zero Bundles is invisible to every check in AD-19. And that is exactly how the deferred catalog arrives: OpenBao, the message broker and the search engine are all single Modules with no natural home in `minimal` / `core` / `admin` / `observability`. The first Module added after this spine ships would land uncovered, in the phase whose entire purpose was to make correctness continuous.

**Fix:** either require every Module to join at least one Bundle (AD-7, checkable against `x-bundles`), or have AD-19 iterate Modules rather than Bundles for the structural checks and Bundles only for the Smoke Test.

### H5 — FR-9 (OpenBao) and FR-10 are half-decided: present in Stack, absent everywhere else

`openbao/openbao 2.6.2` sits in the Stack table. It has **no** Module in the Structural Seed (which lists 13 dirs, none of them openbao), **no** Bundle membership in the Bundle diagram, **no** AD, and — critically — **no entry in `Deferred`**. `Deferred` item 4 defers "which product fills each Catalog category (message broker, search)," deliberately excluding secrets, because the PRD already named OpenBao as a user-stated constraint.

So FR-9 is neither decided nor deferred; it is pinned and orphaned. The addendum has it fully verified against a running image and calls it "ready to build," with four non-obvious facts a Module builder needs and would otherwise rediscover: do not re-pass `-dev-listen-address` (the entrypoint supplies it); `BAO_ADDR` **must** be set for exec-based use or the CLI defaults to `https://` and fails with "server gave HTTP response to HTTPS client"; KV v2 is pre-mounted at `secret/` so no enable step is needed; `cap_add: ["IPC_LOCK"]` is conventional. None of that is carried anywhere.

FR-10 is worse: the addendum's substitute set (motoserver + ElasticMQ, which PRD Q3 asks the architect to *confirm*) appears nowhere in the spine — not in Stack, not in an AD, not in `Deferred`. And with C1 unfixed, the constraints that ruled LocalStack out have no governing rule to stop it coming back.

**Fix:** add `services/openbao/` to the seed with a Bundle row, or move FR-9 into `Deferred` with the addendum's four facts as the revisit note. Add FR-10's remaining confirm to `Deferred`.

### H6 — Breadth gap: versioning and release of the repository itself is silent

Nothing in the spine says whether devinfra tags releases, keeps a CHANGELOG, or uses conventional commits. Three things in the inputs depend on it:

- **FR-19's** consequence: portability is verified "in CI, or by a documented manual run **recorded per release**" — presupposing a release concept the spine never establishes.
- **Addendum §A.2:** a Consumer Project on the reusable-base path pins "a tag, a commit, a submodule ref." AD-6 exists to keep that path reachable, but what a consumer would *pin* is undefined — and H2 makes this sharper, not softer.
- **FR-16:** CI "gates merge." No AD makes CI a required check or mentions branch protection; AD-19 stops at "fails loudly." FR-17's Renovate PRs (AD-11) depend on that gate existing.

Not decided, not deferred, not an open question. At feature altitude the CI epic and the Endpoint-Contract epic would each invent an answer.

### H7 — Breadth gap: secrets and credential posture is silent

AD-4 fixes variable *naming* in two tiers. Nothing fixes the *posture*: that credentials are deliberately trivial dev-only defaults committed in `.env.example` on purpose, that `.env` is never committed, that no Module file may inline a credential outside the `.env` namespace, or how a Module's admin password / root token is named and surfaced.

FR-9 requires the OpenBao root token to be "documented in the Endpoint Contract and configurable via environment variable" — with no Endpoint Contract artifact (H1) and no secrets convention, that has nowhere to land. `MINIO_ROOT_PASSWORD=devinfra123` and `GRAFANA_ADMIN_PASSWORD=admin` are checked into `.env.example` today, correctly and deliberately, and nothing in the spine says so — which is precisely how a well-meaning contributor "fixes" it. This is where the NFR-8 half of C1 naturally lives.

### H8 — Breadth gap: nothing decides how `scripts/` is verified, and `select.sh` is now load-bearing

The Structural Seed now lists **eight** scripts, and `scripts/select.sh` has become the single source of truth for Selection — AD-6 computes closure with it, AD-10 drives the smoke runner from it, AD-16 routes every Compose invocation through it, AD-18 requires it to fail on an empty Selection. A bug in `select.sh` produces a green CI that started the wrong Services, which is the exact failure class AD-19 exists to prevent.

Nothing decides whether these scripts get tests (bats? shellspec? none), whether `pixi.toml` carries a `test` task, or what `check-modules.sh`, `check-ports.sh` and `check-volumes.sh` are verified against. AD-19's CI list validates the *stack*; nothing validates the *validators*. Shellcheck is asserted in a conventions row, but a linter is not a test.

This was AD-9's own justification, now dropped: addendum §E.4 argues extraction to `scripts/` is worth doing because logic in Makefile recipes "cannot be unit-tested, cannot be run directly for debugging." The spine takes the extraction and leaves the testability claim unredeemed. It also sits against the maintainer's standing convention that every project carries `test` and `cov` pixi tasks.

---

## MEDIUM

### M1 — AD-15's exception list still contradicts AD-4 and AD-11

> "A Module never edits another Module's file, and never edits Core Substrate files except to add its own entry to the include registry, its own identifier-only volume declaration, its own port-table row, and its Bundle membership."

The revision widened this usefully, but it is still short. **AD-4** requires every Module variable to live in the root `.env`, and contract variables to be added to a Core-owned registry. **AD-11** requires the version variable *and* its `# renovate: datasource=docker depName=…` annotation in the root `.env`. Adding a Module therefore necessarily edits root `.env` and `.env.example` beyond the port-table row. The three Rules cannot all hold.

Also ambiguous: "its Bundle membership" is Module-side under AD-7 (a `profiles:` list in the Module's own file), so as a *Core* exception it either means adding a name to `x-bundles` — a genuinely new Bundle, not membership — or is redundant.

**Fix:** enumerate the Core edits as: the include-registry entry, the identifier-only volume stanza, the port-table row, and the Module's own `<MODULE>_*` block including its Renovate annotation. Then AD-15 becomes checkable by diff, which is what makes it worth having.

### M2 — The Stack table presents three major upgrades as "the cold-start pin"

The memlog is precise — "(event) Stale pins found (11): … redisinsight 2.70→3.8.0 (MAJOR), tempo 2.9.0→3.0.3 (MAJOR), grafana 12.2.0→13.2.1 (MAJOR)" — but none of that reaches the spine, which introduces the table as "Verified current as of 2026-09-06. The code owns these once they exist; this is the cold-start pin."

Against the repo: pgvector `0.8.1-pg17`→`0.8.6-pg17`, redis `8-alpine`→`8.10.1` (a genuine improvement — today's tag is floating, violating NFR-4/AD-11), grafana `12.2.0`→**`13.2.1`**, loki `3.5.7`→`3.7.7`, tempo `2.9.0`→**`3.0.3`**.

Two consequences. First, the spine defines FR-17's mechanism — Renovate proposes, CI validates before merge — and then routes three majors around it, in the document that is the substrate. Second and more concretely, **AD-13's entire argument rests on two version-sensitive configs**: it weighs `otel-lgtm` against "the two solved gotchas — Loki's `allow_structured_metadata`, Tempo span metrics needing Prometheus remote-write." A Tempo 2→3 major is the single likeliest thing to invalidate them, and AD-13 does not notice that its own Stack table proposes one.

**Fix:** either restate today's pins and let Renovate do its job, or mark the deltas as upgrades and add a note that AD-13's gotchas must be re-verified against Tempo 3.x.

### M3 — `${VAR:?}` is asserted in the map and decided in no AD

Map row: `NFR-5 Fail loud | ${VAR:?}, ON_ERROR_STOP=1, non-empty Selection | AD-12, AD-18, AD-19`.

Two of the three mechanisms are real: AD-12 states `ON_ERROR_STOP=1`, AD-18 states the non-empty-Selection failure. **`${VAR:?}` appears nowhere else in the spine.** No AD names which variables carry it.

PRD Assumption #7 hands exactly this to the architect — "the architect should decide which variables are genuinely required, and by what mechanism" — and addendum §D warns that "applying it indiscriminately would break the current 'works with no `.env`' defaults," which is a real tension with the low-friction pitch and with SM-C2. The decision the PRD specifically asked for was neither made nor deferred.

### M4 — NFR-1 has no verification anywhere

The PRD calls NFR-1 "the Stack's single most load-bearing property"; the memlog's third entry repeats it; AD-5's Prevents calls its violation "the unrecoverable class." AD-19 runs six checks per Bundle — `config -q`, module bijection, ports, volume stanzas, `x-requires`, Smoke Test — and **not one of them is a `down` / `up` durability test.**

The addendum names the exact test and its highest-value target: the Postgres `PGDATA` path is "the highest-severity Gotcha in the register and the strongest candidate for FR-18's 'verifiable Gotchas get a CI assertion' clause — a test that writes a marker, cycles the Stack, and reads it back would catch it." FR-18 requires that class of Gotcha to carry a CI assertion. Not decided, not deferred.

Given the emphasis the spine itself places on NFR-1, this is the most conspicuous absence in AD-19's list.

### M5 — Missing invariant: destructive operations require explicit confirmation

NFR-1: "Only an **explicit, confirmed** destroy removes volumes." The repo has `make destroy` and `make keycloak-reimport`, both guarded by `read -p`. AD-9 moves that shell into `scripts/`, and addendum §E.3 flags the confirmation prompts as one of three things that "do not translate directly" to pixi tasks.

AD-12 governs *ordering* for cross-Module writes (stop → write → restart), which is excellent and general. Nothing governs *authorization*: that no task removes a volume or drops a database without an explicit interactive confirmation, and that no CI path may invoke one. That binds every future task, every Module's teardown, and AD-19's CI — which starts and stops Stacks for every Bundle.

### M6 — The healthcheck convention silently contradicts the running stack in three places

> "Healthcheck | Every long-running Service declares one with a `start_period`; dependents use `condition: service_healthy`"

Against `compose.yaml` today: **`otel-collector`, `prometheus` and `grafana` have no healthcheck at all**; **`keycloak` depends on `mailpit` with `condition: service_started`**; and **`grafana`'s `depends_on` uses the bare-list form** with no conditions. The memlog's code sweep captured the last two accurately ("otel-collector->loki,tempo (plain depends_on, NOT healthy)"), so this is a distillation loss, not a research gap.

The rubric asks whether the spine ratifies the codebase. This row changes it in three places, presents itself as an existing convention, and scopes no work.

### M7 — AD-13 is a scope decision, not an invariant

Apply the skill's test to "The observability Bundle stays five containers." Could two independently-built units choose incompatibly? No — it is a single localized product call about one Bundle, answering PRD Q5. It is worth *recording*, and the reasoning is good (the pin comparison against `otel-lgtm:0.32.1` is genuinely useful), but as an `AD` it is design-doc content promoted into a consistency contract. The memlog already holds it in full.

Note the contrast with the revision's own successes: AD-12 was generalized from "how `keycloak-reimport` behaves" into "cross-Module state mutation follows stop → write → restart," and AD-14 from a Keycloak/Postgres coupling into `x-requires`. AD-13 is the one that did not get that treatment.

### M8 — Rationale is still inlined in thirteen ADs, now duplicating the memlog

AD-1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14 and 17 each carry a trailing italic rationale block. The skill is explicit: "Record decisions, not rationale (rationale lives in the memlog)."

This mattered less when no memlog existed. Now that `.memlog.md` holds all of it — and holds it more precisely, with the adversarial-gate evidence — the inline copies are pure duplication, and they will drift on the first Update. Roughly 15% of the spine is rationale.

Some of it is genuinely load-bearing and should be *promoted into the Rule* rather than deleted — AD-5's "reordering the include registry can change a volume's driver" is the reason the Rule exists and belongs in the Prevents line.

### M9 — Five Rules that cannot be checked

> **AD-19:** "A full run completes within 15 minutes."

A budget, not a rule: no timeout is specified, no CI step enforces it, no tiering fallback is defined — and PRD Q6 asks precisely whether 13 Services fit a hosted runner. An unchecked aspiration in the AD whose entire subject is checks that do not silently pass. Note the revision *increased* the CI workload (six checks per Bundle across four Bundles) without revisiting the number.

> **AD-5:** "Named volumes keep their current identifiers … **permanently**."

The second half of AD-5 gained a real check (`check-volumes.sh` diffs stanzas against Core). The freeze itself still has none. "The set of top-level volume identifiers is asserted against a committed manifest" would be checkable — and H3 shows the freeze is already under pressure.

> **AD-3:** "A dependency not expressed as `depends_on` is unenforceable and therefore does not exist."

Rhetoric appended to an otherwise excellent Rule.

> **AD-10:** "A check must exercise real function — mint a token, round-trip an object, publish and consume — never liveness."

Reviewable by a human, invisible to `check-modules.sh`. Fine as a convention; it should not sit in a Rule that AD-8's CI check is presented as enforcing.

> **AD-15's diagram** still draws the forbidden edge `C -.->|never| B` as a real arrow.

The Rule no longer says "per the diagram below" — a good fix — but a reader skimming the picture still sees Core depending on Modules.

### M10 — AD-14's `x-requires:` placement is unspecified and its mechanics unverified

AD-14 is one of the best ADs in the spine, and it has a hole at exactly the point it is meant to close. "A Module … declares it in **its own** file as an `x-requires:` block (`x-requires: {database: keycloak}`)" does not say *where*: top-level extension field, or under `services.<name>`. Two Modules would choose differently, and the CI reconciler can only parse one shape.

Unverified mechanic: whether a **top-level** `x-` field in an *included* file survives into `docker compose config` output at all. Service-level `x-` keys are preserved; top-level extension fields from included files are the kind of thing this spine has (rightly) tested empirically everywhere else. Given the memlog shows eight such mechanics were tested against Compose v5.3.0, this one standing untested is conspicuous.

### M11 — The Compose floor contradicts the addendum's CVE note, and the row carries two versions

> "Docker Compose (floor) | 2.20 (for `include`); current release 5.5.1"

Addendum §D: "**Compose CVE-2025-62725** — fixed in 2.40.2. A documented minimum Compose version is warranted regardless, since `include` requires 2.20+." The point was that the *security* floor is higher than the *feature* floor; a 2.20 floor knowingly admits the vulnerable range. The memlog adds a third constraint the spine drops: "`!reset`/`!override` require >=2.24.4."

Separately, the template asks for name + version and this row gives two, leaving ambiguous which CI provisions and which a contributor must have. Nothing asserts the floor anywhere — no preflight check, no AD, no CI step — so it is documentation, not a constraint. The "current release 5.5.1" figure should carry its verification date (the memlog has it: 2026-09-03) or be dropped.

### M12 — The Renovate Stack row is a mechanism, not a version

> "Renovate | `customManagers` regex over `.env`"

The version column holds a configuration strategy. It duplicates AD-11 (which states the mechanism properly) and leaves the row unpinned — `lint_spine.py` flags unpinned Stack versions.

---

## LOW

- **L1 — `status: draft`.** Expected pre-Finalize; step 7 flips it to `final` with `updated:`. Noted so it is not missed.
- **L2 — AD-2's evidence claim overstates.** "*The restart-loop **was observed**: the base's `unless-stopped` is inherited by init containers that exit 0 by design.*" Today's `minio-init` extends `<<: *logging` only — not `*defaults` — and already sets `restart: "no"` explicitly, so it does not inherit `unless-stopped`. The risk is real *after* the AD-1 migration to `extends: common/base.yaml`, and the Rule is correct; the claim that it was observed is not. Elsewhere the spine's empirical claims check out, so this one is worth correcting rather than leaving to erode trust in the rest.
- **L3 — AD-10 and AD-18 both state the empty-Selection rule.** "The runner fails if the resolved Selection is empty" (AD-10) and "`scripts/select.sh` fails loudly on an empty resolved Selection" (AD-18). Harmless duplication; they will drift.
- **L4 — AD-2 and AD-15 appear in no map row.** AD-15 binds `all`; AD-2 survives only in a seed comment and a conventions row.
- **L5 — FR-19's "CI matrix" is not in AD-19.** AD-19 binds FR-19 and is the right governor, but its Rule says nothing about Podman, Colima or OrbStack, and PRD Assumption #6 (which runtimes are worth supporting) is unaddressed.
- **L6 — NFR-7's footprint documentation is not in AD-7's Rule.** AD-7 binds NFR-7 and governs profile mechanics; "every Bundle documents its approximate memory footprint" is stated nowhere.
- **L7 — NFR-3's real invariant is unstated.** "The start command blocks until healthy and does not return optimistically" appears in no AD or convention. `scripts/wait-healthy.sh` is in the seed; nothing says tasks must go through it.
- **L8 — SM-C2 is unacknowledged.** "Configuration surface … If `.env.example` doubles in length, modularity was implemented wrong." The revision added a contract-variable registry, a port-allocation table and an `x-bundles` registry — all of which grow `.env.example` / `compose.yaml` config surface. AD-13 cites SM-C1 by name; SM-C2 has no counterpart, and it now has more to say than it did.
- **L9 — FR-18's aggregate "register" is undecided.** AD-8 puts `gotchas.md` in each Module. FR-18 asks for a register with a consistent shape and requires "every Gotcha currently in the README appears in the register." Nothing decides whether the register is a union view, a generated aggregate, or an index, or how migration completeness is checked.
- **L10 — `docs/adr/` now has a source but no rule.** The memlog explains it — "(direction) Deliverables: ARCHITECTURE-SPINE.md + visual walkthrough (HTML) + migration plan + **ADR set in the repo (docs/adr/)**". The spine still gives no rule for when a decision becomes an ADR versus an AD amendment, which risks two decision records diverging.
- **L11 — `backups/` is absent from the Structural Seed** though `backup.sh` / `restore.sh` are listed and `backups/` is gitignored. FR-15's map row cites it implicitly.
- **L12 — PRD Q7 (worked-example language) is neither decided nor deferred.** `examples/` is now in the seed (good), governed only by "AD-4" — nothing decides the language, how it stays in sync with the Endpoint Contracts, or that it must carry its own README saying it is not a production template (an explicit FR-14 requirement).

---

## What is working

Recorded so the revision does not sand these off:

- **The adversarial gate was the right move and it landed.** SC-1 (AD-6 satisfiable only vacuously — `config` on one Module file returns `services: {}` and exits 0), SC-2 (AD-12 requiring "every Bundle" while AD-7 forbade a central list), H-1 through H-8 — all empirically tested, all closed. AD-6's rewrite to closure-validity and AD-7's `x-bundles` registry are both strictly better than what they replaced.
- **AD-4's two-tier namespace** is the standout. The flat `<MODULE>_<CONCERN>` rule was elegant and *wrong* — it cannot express `AWS_ENDPOINT_URL`, and it was already violated by `BIND_ADDRESS` and `COMPOSE_PROFILES`. The contract-variable tier with a single owning Module per name is a real invariant with a real CI check, and it makes FR-10 implementable where the previous rule made it impossible.
- **AD-5's identifier-only stanza** rests on a genuinely surprising finding: root wins only on keys root sets, so a Module adding `driver_opts` silently wins and *reordering the include registry can change a volume's driver*. That is exactly what an invariant is for.
- **AD-14 (`x-requires`) and AD-17 (port table)** both close divergences invisible to `docker compose config`. AD-17's evidence is concrete and verifiable — `KEYCLOAK_MGMT_PORT` defaults to 9000 and MinIO's natural port is 9000, dodged today only by an undocumented shift to 9100/9101; SeaweedFS, a named replacement candidate, defaults its volume server to 8080, which is `KEYCLOAK_PORT`. Both confirmed against `.env.example` and `compose.yaml`.
- **AD-12's generalization** from one script's behavior into "cross-Module state mutation follows stop → write → restart" is the single best structural improvement in the revision, and the `ON_ERROR_STOP=1` finding (today's restore exits 0 on a failed `DROP DATABASE`) is a live NFR-5 violation caught by reading the code.
- **AD-8's bidirectional check and helper-Service rule** absorb `minio-init` cleanly and name the next two instances (a Kafka topic-initialiser, an OpenSearch Dashboards sidecar) — anticipating rather than reacting.
- **AD-1, AD-9, AD-11** remain excellent: each rests on a verified mechanic, and "**No task may branch on `command -v`**" is a rule a grep can enforce that prevents exactly the defect that motivated PRD Q8.
- **`Deferred` is doing real work.** Six items, each with a reason and most with a revisit condition. The gate's failures are about what sits *outside* it.

---

## Recommended disposition

**Autofix (cheap, no user input needed):** C2 (six `[ASSUMPTION]` tags), H1 (name the Endpoint Contract artifact; extend AD-19's CI list), H3 (state the `minio-data` carve-out), H4 (require Bundle membership, or index AD-19 by Module), M1 (complete AD-15's exception list), M8 (drop the duplicated rationale; promote the load-bearing lines into Prevents), M9 (rewrite or demote the five Rules), M10 (say where `x-requires` sits), M11–M12, L2, L3, L4, L11.

**Needs the user:** C1 (confirm the licence / host-access / no-hardening AD's wording — the one true blocker), H2 (does AD-16 settle PRD Q1 as A.1, or does the resolver stay Core-only?), H5 (is FR-9 decided or deferred?), H6 (does devinfra release?), H7 (secrets posture), M2 (are the three major upgrades intended?), M3 (which variables are genuinely required — PRD Assumption #7), M5 (promote destructive-operation confirmation), M7 (demote AD-13 to the memlog).

**Defer with a revisit condition:** H8 (verification of `scripts/` — though `select.sh`'s new centrality argues for doing it now), M4 (the NFR-1 durability check — likewise, given the weight the spine places on NFR-1), L12 (worked-example language, PRD Q7), L9 (the Gotcha Register's aggregate shape).

---
title: 'Bundles, and a safe path through the breaking change'
type: 'feature'
created: '2026-09-07'
baseline_revision: '6c183456f4f5bfa34d52d36940760caa022e5d19'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** A Selection can only be spelled as Modules or the two ad-hoc group names `admin` and `observability`, so asking for "the usual five" means typing `postgres,redis,keycloak,minio,mailpit` and `scripts/up-core.sh` hard-codes exactly that list. Nothing declares which group names are legal, so a typo'd profile on a service invents a new one silently (the gap story 2-5 recorded as residual); nothing states what a group costs to run; and neither `admin` nor a hypothetical `core` is dependency-closed — `admin`'s three services do not carry Postgres or Redis, so the group only works because the resolver expands it at runtime. Separately, the epic's one user-visible break — a `.env` predating Selection now starts nothing — is documented halfway down the README and in no release note at all.

**Approach:** Add the Core-owned `x-bundles` registry to the root `compose.yaml` (AD-7): the four legal Bundle names, each with a one-line description and an approximate memory footprint. Membership stays where AD-7 puts it — per service in `profiles:` — so `core` and `minimal` are declared by the services that join them, and `admin` gains Postgres and Redis so that every registered Bundle is already dependency-closed rather than closed by the resolver at runtime. `lint-config` gains a Bundle leg in both directions: a registered name no Module joins fails, a profile no registry entry names fails, and a Bundle whose members reach outside themselves fails. Then the breaking change gets its safe path: a `CHANGELOG.md` that leads with it, a README that leads with it, and a behavioural pin that a legacy `.env` carrying no `COMPOSE_PROFILES` line at all is refused at the task surface, naming the variable and the exact line to add.

## Boundaries & Constraints

**Always:**
- **The registry names Bundles; it never lists their members.** AD-7 puts membership in each service's `profiles:` precisely so it cannot drift from what starts. A `modules:` key in a registry entry would be a second, hand-maintained membership list — forbidden.
- **Every registered Bundle is dependency-closed by declaration, not by expansion** (AD-16, epics AC 2.6-1). For every Bundle `B`, the set of Modules whose services declare `B` is closed under `depends_on`. `admin` is not closed today; closing it means Postgres and Redis join `admin`.
- **Profiles are added to, never replaced.** `postgres` goes from `[postgres]` to `[postgres, minimal, core, admin]`; the observability five keep exactly what they carry. Every existing Selection resolves to exactly what it resolves to today — `select.sh admin` still prints `flower,pgadmin,postgres,redis,redisinsight`.
- **The Bundle vocabulary is closed.** After this change a service may declare only its own Module name and registered Bundle names. That closes the residual gap story 2-5 recorded: a bogus profile alongside the correct ones currently passes every check and leaks a request name into the resolver's vocabulary.
- **Every Bundle states an approximate memory footprint** (NFR-7), and it lives in the registry where CI can require it — not only in prose that can rot. The README repeats it, and the self-test proves the two agree.
- **The shipped `.env.example` Selection still resolves to every Module** (AD-18). It becomes `core,admin,observability`; that it resolves to all thirteen is asserted, not asserted-by-reading.
- **Data durability is the overriding constraint.** No volume is renamed, removed or re-driven; no `down -v`, no `pixi run destroy`, and the running stack — which belongs to the main checkout — is not recreated from this worktree. Capture `docker volume ls --filter name=devinfra` before and after and prove it identical.
- Every new check is proved load-bearing: a fixture that makes it fail by name, and for the registry an enumeration that fails when it names nothing.
- The security posture is fixed, not improved: no credential, TLS, auth or exposure setting changes.

**Never:**
- Never change what any existing Selection resolves to. Adding `core` to Keycloak must not change `select.sh keycloak`; adding `admin` to Postgres must not change `select.sh postgres`. Both are properties of the closure and both are asserted.
- Never rename or remove a profile, volume, container, service, environment variable or port; never touch `scripts/urls.sh` (story 3-3 owns it) or reconcile host ports across their three declaration sites (DW-34).
- Never teach `scripts/resolve_selection.py` to read the root `compose.yaml`. The resolver's vocabulary is the profile index it already builds from the module files; the registry is a *validation* input, read by `scripts/assert_config.py`. Coupling the resolver to the root file would make every lifecycle script depend on it.
- Never make `pixi run smoke` or `scripts/lint-compose.sh` consult the resolver — they stay the two documented exceptions, and `lint-compose.sh` picks up the two new Bundles automatically because it enumerates from `select.sh --selections`.
- Never write `_bmad-output/implementation-artifacts/sprint-status.yaml` or `deferred-work.md`; both are orchestrator artifacts.
- Never edit a decided ADR in place — a new ADR (0014), or a dated amendment.
- Never generate endpoint or connection documentation from the registry; story 3-3 owns generated docs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| The core Bundle | `select.sh core` | `keycloak,mailpit,minio,postgres,redis` — the same five `up-core` starts today | — |
| The minimal Bundle | `select.sh minimal` | `postgres,redis` | — |
| An existing group is unchanged | `select.sh admin` | `flower,pgadmin,postgres,redis,redisinsight` — byte-identical to today | — |
| A Module request is unchanged | `select.sh keycloak`, `select.sh postgres` | `keycloak,mailpit,postgres`; `postgres` | — |
| Bundles compose | `select.sh core,observability` | the core five plus the observability five, ten Modules | — |
| Shipped default | `.env.example`'s `core,admin,observability` | resolves to all thirteen Module names | — |
| A registered Bundle no Module joins | `x-bundles` gains `zz-unjoined:` | `pixi run lint-config` exits 1 naming `zz-unjoined`, signs off on nothing | Non-zero |
| A profile no registry entry names | a service declaring `profiles: [<module>, zz-nosuch]` | `pixi run lint-config` exits 1 naming the Module, the service and `zz-nosuch` | Non-zero |
| A Bundle that is not dependency-closed | a Module joining `minimal` while `depends_on` a Module outside it | `pixi run lint-config` exits 1 naming the Bundle and the Module reached but not joined | Non-zero |
| A registry entry missing its footprint | an entry with `description:` and no `memory:` | `pixi run lint-config` exits 1 naming the entry and the missing key | Non-zero |
| A malformed or absent registry | `x-bundles` is a list, or gone entirely | `pixi run lint-config` exits 1 saying so — never read as "no Bundles" | Non-zero |
| A legacy `.env` with no `COMPOSE_PROFILES` line | `pixi run start` against it | exit 1 naming `COMPOSE_PROFILES` and the line to add; the container runtime is never invoked | Non-zero, nothing started |
| A legacy `.env` with an empty `COMPOSE_PROFILES=` | `pixi run start` against it | the same refusal — an empty value is not a Selection | Non-zero |
| README and registry disagree | a Bundle's `memory:` changed in one place only | `pixi run test` fails naming the Bundle | Non-zero |

</intent-contract>

## Code Map

- `compose.yaml:1-27` -- the header prose that describes the two groups (`:12-13`) and the Selection model; it must describe Bundles instead. `:29` `name:`, `:34-47` `include:`, `:52-64` `volumes:`, `:66-69` `networks:` — **the file's top-level key set is pinned by equality** in `scripts/lint_selftest.py:653-660` (`{"name","include","volumes","networks"}`), deliberately, so adding `x-bundles` reds that assertion until it is extended. Put the registry after `name:` and before `include:`.
- `services/*/compose.yaml` × 13 -- the only files whose `profiles:` change. Current values: `postgres:45 [postgres]`, `redis:39 [redis]`, `keycloak:55 [keycloak]`, `minio:51 [minio]`, `minio-init:80 [minio]`, `mailpit:42 [mailpit]`, `pgadmin:28 [pgadmin, admin]`, `redisinsight:30 [redisinsight, admin]`, `flower:30 [flower, admin]`, and `prometheus:23`, `loki:23`, `tempo:24`, `otel-collector:37`, `grafana:37` each `[<module>, observability]`. Target: the core five gain `core`; `postgres` and `redis` additionally gain `minimal` and `admin`; `minio-init` takes `[minio, core]`, identical to its primary (the AD-8 leg checks that); the observability five are untouched. `depends_on` edges, for the closure argument: `keycloak:56` → postgres, mailpit; `minio-init:81` → minio; `pgadmin:29` → postgres; `redisinsight:31` → redis; `flower:31` → redis; `grafana:38` and `otel-collector:38` → observability Modules only (bare list form). Nothing else in any file changes.
- `scripts/assert_config.py` -- **where every new check lands.** `:2-78` module docstring: `:37` says "Three properties are read from the module files' own text"; the registry is a fourth, and root-sourced. `:271-287` `root_declarations(stanza)` is the *only* read of the root file (`:286` `read_model(REPO / "compose.yaml").get(stanza)`); it coerces a non-mapping to `{}`, so it must **not** be reused for the registry — a malformed `x-bundles` would read as absent. Write a `bundle_registry()` reader that distinguishes absent / malformed / present. `:481-713` `module_contract(paths)`: `:516-524` the pre-pass building `parsed_by_module` (which doubles as the "is this a Module name?" set), `:622-672` the profile-membership leg, and inside it `:648-656` the "another Module's name" sub-leg — the exact block the new vocabulary check extends. `:684-687` leg 7's `edges` computation is the pattern for the closure check. `:862-951` `main()`: `:884-889` the static checks, `:893-896` the pre-seeded `passed` OK lines (add one), `:904-905` `build_graph` / `selections`, `:916-922` the `declared_profiles()` vs `Graph.names()` reconciliation, `:948-951` the final `assert-config: OK — {len(wanted)} Selection(s)` line. `:90-98` the `resolve_selection` imports; `:102-124` the constants block where `BUNDLE_KEY = "x-bundles"` belongs.
- `scripts/resolve_selection.py:201-264` `build_graph()` -- builds `Graph.profiles` (profile name → the Modules declaring it), which **is** the membership index the registry checks read; `:330-353` `selections()` derives groups as `set(profiles) - set(modules)`, so `core` and `minimal` become Selections automatically and the validated count goes 16 → 18. `:282-327` `closure()` and `:298-306` the empty-request refusal — the message a legacy `.env` sees; it names `COMPOSE_PROFILES`, points at `pixi run init`, and lists valid names. It should also name the concrete line to add. **No structural change here.**
- `scripts/up-core.sh:15` `select_profiles postgres redis keycloak minio mailpit` -- becomes `select_profiles core`; the header at `:2-9` and `pixi.toml:56-57`'s description change with it. The self-test pins the exported value, not the argv, so the pin stays true.
- `scripts/lint-compose.sh:75-88` -- enumerates from `./scripts/select.sh --selections`; **no change**, it picks up the two new Bundles by construction. `:111` prints the count.
- `scripts/lib/common.sh:87-99` `select_profiles`/`select_ambient` -- the seam every script resolves through; unchanged.
- `.env.example:44-62` -- the `COMPOSE_PROFILES` comment block and `:62` the value. Becomes `core,admin,observability`; the comment gains the Bundle table and drops the hand-listed Module enumeration. `scripts/lint_selftest.py:1141,1157` pins that `init-env.sh` copies this file byte-identically, so edits carry automatically.
- `.github/workflows/ci.yml:68-70,105-109` -- both stack jobs' `COMPOSE_PROFILES`; only their value need change (to `core,admin,observability`), and only the *resolution* is pinned (`lint_selftest.py:3902-3924`).
- `scripts/lint_selftest.py` -- everything that must move: `:641-666` the root-key equality (add `x-bundles`); `:1273-1281` the fixture `dotenv` and its literal closures at `:1297-1298,1327-1328,1351-1352,1503-1505`; `:1361-1379` the up-core pin (`COMPOSE_PROFILES={core_modules}`); `:1380-1392` the empty-Selection refusal — **extend, do not replace**, with a legacy `.env` that has no `COMPOSE_PROFILES` line at all; `:1443-1479` `seam_tasks` and `recorded_env()`; `:1734-1826` the lint-compose block, whose `:1743` `groups = ["admin","observability"]` and `:1784,1789` literals must become the four Bundles; `:2015-2262` `contract_fixture`/`without`/`contract_cases` (`:2042` `own_profile`, `:2067` `complete_body`, `:2247-2262` the three-assertion driver) — the home for the new module-side cases; `:2332-2345` the module-count OK line; `:3520-3626` the real-runtime Selection block and its `:3558-3575` `expected_members` table (15 entries → 17); `:3627-3706` the shipped-default and refusal group; `:3754-3794` the AD-16 resolver scan and its `:3766` exemptions. Harness API: `expect(name, condition, detail)` at `:512-516` is the only assertion primitive, `failures` at `:510`, report at `:4448-4450`; `planted()` `:378-401` refuses to overwrite an existing path, `moved_aside()` `:402-423` renames — **the pair is how a root-file defect case is staged safely** (read the text, move aside, plant the mutation).
- `pixi.toml:37-41` the header rule that no task body names a profile; `:56-59` `up-core`; `:169-172` the `select` task; `:186-188` `lint-config`'s description (it enumerates what the check asserts and gains the Bundle leg); `:218-224` `lint`/`ci`.
- `README.md:1-27` the title, one-paragraph summary and Contents table — **the breaking change has to lead here**; `:44-59` Quick start; `:61-123` the Selection section, which already carries the migration paragraph at `:112-123` and the "observability is the expensive one" line at `:109-110` (the only footprint stated anywhere today); `:92-96` the group table that becomes the Bundle table; `:219-319` the repository layout; `:320-367` the task table (`:350` `pixi run select`).
- `AGENTS.md:29-35` -- the Selection paragraph inside the `bmad:context` managed block; it says "Module and group names" and needs Bundles.
- `docs/adr/README.md:11-24` -- the ADR index table, `:24` the last row; ADR **0014** is next. `docs/adr/0013-*.md` -- the decision this one builds on; read-only.
- `CHANGELOG.md` -- **does not exist.** No release-note artifact exists in this repository at all, and `pixi.toml` has no changelog task; creating it is part of this story.
- **Measured, do not re-derive** (read-only `docker stats --no-stream` against the running stack, 2026-09-07): postgres 43 MiB, redis 19 MiB, keycloak 587 MiB, minio 133 MiB, mailpit 20 MiB, flower 40 MiB, redisinsight 92 MiB, prometheus 126 MiB, loki 90 MiB, tempo 168 MiB, grafana 119 MiB. `pgadmin` and `otel-collector` were not running and must be measured separately (see Design Notes).
- **Verified against the live runtime (Compose v5.3.0), do not re-derive:** a profiled service whose `depends_on` target is unselected fails `config -q` with `depends on undefined service`, exit 1; an empty `COMPOSE_PROFILES` renders zero services at exit 0; `compose ps`, `exec` and `logs <service>` ignore active profiles while `config --services` and bare `logs` honour them.

## Tasks & Acceptance

**Execution:**
- `<scratch>/before-*.txt` -- **outside the repository tree, before touching anything**: capture `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort`, `docker ps --format '{{.Names}}' | sort`, and `docker compose config --format json` under `COMPOSE_PROFILES="$(./scripts/select.sh --all)"`. The rendered capture is the evidence that adding Bundle names to `profiles:` changed nothing else about any service body.
- `pgadmin` and `otel-collector` footprints -- measure them without touching the running project: `docker run --rm -d --name devinfra-memcheck-<x>` with no volume, no `devinfra` network and no published port, read `docker stats --no-stream`, then `docker rm -f` it. If either cannot be started cleanly, state the figure as an upstream-documented approximation in the run result rather than inventing a measurement.
- `compose.yaml` -- add the `x-bundles` registry between `name:` and `include:`: `minimal`, `core`, `admin`, `observability`, each a mapping with exactly `description` and `memory`, both non-empty strings. Rewrite the header's Selection paragraph to describe Bundles, name the four, and state that membership is declared per service, not here.
- `services/{postgres,redis,keycloak,minio,mailpit}/compose.yaml` -- add `core` to each service's `profiles:` (`minio-init` included, keeping it identical to `minio`'s); add `minimal` and `admin` to `postgres` and `redis` only. Nothing else changes in any file.
- `scripts/assert_config.py` -- add `BUNDLE_KEY`, a registry reader that fails distinctly on absent / non-mapping / non-mapping-entry / missing-or-empty `description` / missing-or-empty `memory` / unexpected keys, and a `bundle_registry()` check taking the registry and the `Graph`: every registered Bundle is joined by at least one Module, and every registered Bundle is closed under `depends_on`. Pass the registry names into `module_contract()` and extend the `:648-656` sub-leg so a profile that is neither the Module's own name nor a registered Bundle name fails, naming all three of Module, service and profile. Add the third pre-seeded `OK` line, extend the module docstring's "Three properties" block, and update `pixi.toml:187`'s description to match.
- `scripts/resolve_selection.py:298-306` -- extend the empty-request refusal with the concrete line to add (`COMPOSE_PROFILES=core,admin,observability`) so a legacy `.env` is told the fix, not only the variable. No other change; the resolver still never reads the root file.
- `scripts/up-core.sh` + `pixi.toml` -- `select_profiles core`; correct the header and the task description to name the Bundle rather than the five services.
- `.env.example` -- value `core,admin,observability`; comment rewritten around Bundles, with the four names, what each is for, and its footprint.
- `.github/workflows/ci.yml` -- the same value in both stack jobs; comments restated in Bundle terms.
- `scripts/lint_selftest.py` -- (a) extend the root-key equality to `{"name","include","volumes","networks","x-bundles"}` and add positive assertions that the registry is a non-empty mapping and that its four names are exactly the non-Module profiles the model declares; (b) the four registry-defect cases from the I/O matrix, each staged with `moved_aside` + `planted` over the root file, asserting non-zero exit, the diagnostic naming the entry, and `"OK" not in stdout`; (c) `contract_cases` entries for a profile no registry entry names, and for a Bundle broken open by a fixture Module that joins `minimal` while depending on a Module outside it; a positive case that a fixture joining a registered Bundle is accepted; (d) a real-runtime membership expectation for `core` and `minimal` in `expected_members`, plus assertions that `admin`, `keycloak` and `postgres` resolve byte-identically to their pre-change closures; (e) the legacy-`.env` case: a planted `.env` with no `COMPOSE_PROFILES` line driven through the task surface, asserting exit 1, that stderr names both the variable and the replacement line, and that the runtime was never invoked; (f) an assertion that every registered Bundle's name and `memory` value appear in `README.md`, so registry and prose cannot drift; (g) update every literal the new profiles move: the lint-compose group list and its expected exports, the `dotenv` fixture closures, and the Selection count.
- `docs/adr/0014-bundles-are-a-core-owned-registry.md` + `docs/adr/README.md` -- record the decision: names in Core, membership per service, closed-by-declaration rather than by expansion, the closed profile vocabulary that follows, the footprint living in the registry, and why the resolver stays ignorant of the root file. Add the index row.
- `CHANGELOG.md` (new) -- Keep-a-Changelog shape, `## [Unreleased]`, and the **breaking change first**: what changed, what breaks, the exact `.env` line to add, and how to check (`pixi run select`). Bundles follow it as a Feature entry.
- `README.md` -- lead with the breaking change: a short "Upgrading from a pre-Selection checkout" callout immediately after the opening paragraph, naming the variable and the line. Replace the group table with a Bundle table carrying description and memory, describe Bundles in the Selection section, and point at `CHANGELOG.md`.
- `AGENTS.md` -- update the Selection paragraph inside the managed block: Bundles are Core-registered, membership is per service, and a service may declare only its own Module name and registered Bundle names.

**Acceptance Criteria:**
- Given the registry in the root `compose.yaml`, when `pixi run lint-config` runs, then it reports four Bundles — a data-only `minimal`, today's core five as `core`, plus `admin` and `observability` — each joined by at least one Module and each already dependency-closed, and adding a fifth entry no Module joins makes it exit 1 naming that entry.
- Given a Module service that declares a profile which is neither its own Module name nor a registered Bundle name, when `pixi run lint-config` runs, then it exits 1 naming the Module, the service and the profile, and signs off on nothing — the vocabulary gap story 2-5 left open is closed.
- Given a checkout whose `.env` predates Selection and carries no `COMPOSE_PROFILES` line, when the stack is started through the task surface, then it exits non-zero with a message naming the variable and the exact line to add, the container runtime is never invoked, and nothing is started or reported as a pass.
- Given the shipped `.env.example` and both CI stack jobs' `COMPOSE_PROFILES`, when each is resolved, then each yields exactly the thirteen Module names, so a fresh checkout and both CI jobs start the same services as before this change.
- Given every Selection that resolved before this change, when it is resolved after, then it is byte-identical — `admin` still resolves to five Modules, `keycloak` to three, `postgres` to one — and `core` resolves to exactly the five Modules `up-core` started by name.
- Given `pixi run lint` and `pixi run ci`, when they run, then they exit 0, `lint-compose` and `lint-config` each validate eighteen Selections rather than sixteen, and the run takes the time it takes today.
- Given the volume inventory and the rendered all-Modules document captured before the change, when both are captured again afterwards, then the volumes are identical and the document differs only by the added Bundle names in `profiles:` lists.
- Given `CHANGELOG.md` and `README.md`, when a maintainer reads either from the top, then the breaking change is the first thing stated, with the fix, and every registered Bundle's memory footprint is stated in both the registry and the README.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass

- verdicts: 27 findings — high 0, medium 2, low 15, false 5, maybe-false 0
- findings:
  - `[low]` `[patch]` blind: `pixi.toml:183` still describes `lint-compose` as validating "each group" while the README task table was rewritten to "each Bundle" in the same diff — verified in the file; patched, the task description now says Bundle.
  - `[low]` `[patch]` blind: `scripts/lint-compose.sh` header still says "group's closure" and quotes the stale "fifteen declared profiles is 32 768 renders" the same change corrected elsewhere to seventeen/131 072 — verified; patched, header and both inline comments corrected.
  - `[low]` `[patch]` blind: `scripts/select.sh`'s usage block still speaks the pre-Bundle vocabulary, and it is the first surface a migrating developer is told to run — verified at `:6,:11,:15`; patched.
  - `[low]` `[patch]` blind: `resolve_selection.py`, `assert_config.py` and `lint_selftest.py` keep "group" and the stale power-set arithmetic where the same files were rewritten to say Bundle — verified; patched, `grep -n '\bgroup\b'` over those files now returns nothing.
  - `[low]` `[reject]` blind: `lint-config` cannot catch a Bundle name that shadows a leaf Module name — real but already red at the gate: `lint_selftest.py`'s `set(registry_entries) == non_module_profiles` assertion fails on it, so `pixi run ci` catches it; the proposed fix adds a guard for a state nothing showed reachable.
  - `[medium]` `[patch]` blind: the registry↔README footprint pin is two unanchored substring searches, so swapping two Bundles' figures still passes — verified by running the assertion's own expression against a mutated registry; patched, both the README and `.env.example` tables are now parsed into name→footprint mappings and compared to the registry, proved load-bearing three ways.
  - `[low]` `[reject]` blind: `description:` is write-only and there is no `pixi run bundles` listing — the fix adds a public command surface, which the patch route excludes, and NFR-7 is satisfied by the registry plus the README table.
  - `[low]` `[patch]` blind: `.env.example` is a fourth unpinned restatement of the four footprints — verified; patched under the same anchored-mapping pin. The companion claim that dropping the thirteen literal Module names from the comment was a regression is rejected: the comment now points at `services/`, which cannot go stale.
  - `[low]` `[patch]` blind: `compose.yaml` states the figures are "measured" while pgAdmin's and the collector's were taken standalone rather than under this stack's configuration — verified against the implementation report; patched, the comment now records the weaker provenance.
  - `[low]` `[patch]` blind: the footprints overlap (`minimal` ⊂ `core`; `core` and `admin` both count the data layer) and nothing warns against summing them — verified arithmetically; patched in `compose.yaml`, README and `.env.example`, with the shipped default's real figure stated.
  - `[low]` `[patch]` blind: "thirteen services" in the README callout and CHANGELOG conflicts with the repository's Module/service distinction — verified, there are thirteen Modules and fourteen services; patched to Modules.
  - `[low]` `[patch]` blind: `up-core.sh:2` re-introduces the five-service list the comment ten lines below claims the file does not carry — verified; patched, the enumeration is gone.
  - `[low]` `[reject]` blind: nothing asserts `minimal ⊆ core` at the `lint-config` gate — real of `lint-config` alone, but `lint_selftest.py`'s `expected_members` pins both memberships and runs in `ci`, and the only fix that would move it to `lint-config` is a membership list in the registry, which AD-7 and the intent-contract forbid.
  - `[low]` `[patch]` blind: `CHANGELOG.md` claims Keep a Changelog conformance it does not hold — `## [Unreleased]` has no link definition and the breaking section is an invented type; verified; patched. The companion ask for a changelog task and a CI freshness check is rejected as new capability, not a correction.
  - `[medium]` `[patch]` blind: the CHANGELOG omits two user-visible consequences this story ships — the closed vocabulary now fails `lint-config` for a fork carrying a custom profile, and raw `docker compose --profile admin` now succeeds where it used to fail — both verified live; patched, both stated under the breaking section with the observed error text.
  - `[low]` `[reject]` edge: a Bundle name shadowing a Module name — same finding as the blind layer's; rejected on the same refutation.
  - `[low]` `[reject]` edge: a module file declaring its own top-level `x-bundles:` would be silently ignored — true, but nothing showed a developer reaching for it, and the fix adds a check for an unreached state.
  - `[low]` `[reject]` edge: a Module with no outward `depends_on` could join a Bundle and go unnoticed by `lint-config` — same root cause as the `minimal ⊆ core` finding and rejected on the same evidence: `expected_members` reds it in `ci`.
  - `[low]` `[patch]` edge: the `.env.example` footprints are unpinned — same defect as the blind layer's, patched with it.
  - `[low]` `[patch]` edge: `root_parsed["x-bundles"]` raises `KeyError` and aborts the whole self-test when the registry is absent, after an earlier `expect` already recorded the failure — verified by reading the block; patched to `.get(...) or {}`.
  - `[low]` `[patch]` edge: the OK-line assertion's `entry['memory']` crashes the same way for an entry with no `memory` key — verified; patched to read the already-guarded footprint mapping.
  - `[medium]` `[patch]` verification-gap: the registry↔README cross-check matches any footprint anywhere in the file, with a demonstration that a mutated registry passes — pre-verified by that layer; patched as above (grouped with the blind layer's same finding).
  - `[low]` `[patch]` verification-gap: `Makefile:36` still names the five services in the `up-core` help text while both sibling descriptions were updated — verified; patched.
  - `[low]` `[patch]` verification-gap: `pixi.toml:183` stale — same finding as the blind layer's, patched with it.
  - `[low]` `[patch]` verification-gap: three stale prose counts survive in `assert_config.py:1069`, `resolve_selection.py:353` and `lint-compose.sh:14` — verified; patched with the vocabulary sweep.
  - `[low]` `[patch]` verification-gap: `CHANGELOG.md` is missing from the README's repository-layout block and `compose.yaml`'s entry there omits the registry — verified; patched.
  - `[false]` `[reject]` intent-alignment ×5: the layer's five divergences all concern the run protocol rather than the change — no test reads spec frontmatter or `sprint-status.yaml` (correct: those are orchestrator surfaces, not repository behaviour); the `awaiting-operator` branch was silently not taken (it correctly does not fire — every acceptance criterion is checkable by a `pixi run` task, and the two footprints a live daemon was needed for were measured by the agent in throwaway containers, so nothing is owed to a human outside the repo); footprint accuracy is unpinned (true and inherent — NFR-7 asks for an approximation, and the provenance is now recorded in the file); "commit it" unmet and `status: in-review` (both mid-run states observed by a reviewer running before finalization).

### 2026-09-07 — Review pass (follow-up)

- verdicts: 29 findings — high 0, medium 1, low 23, false 5, maybe-false 0
- findings:
  - `[medium]` `[patch]` blind: the upgrade line `COMPOSE_PROFILES=core,admin,observability` is pinned against `.env.example` only through `resolve_selection.py`'s literal, while `README.md`'s callout and `CHANGELOG.md`'s fix block — the two copies a migrating developer actually pastes — restate it free-floating — verified, neither string was read by any check; patched, `lint_selftest.py` now requires both documents to carry `COMPOSE_PROFILES={shipped}`, proved load-bearing by narrowing the README line to `core,observability` and watching the pin red.
  - `[low]` `[patch]` blind: `CHANGELOG.md` carries a fourth Bundle/footprint table that nothing parses, created by the same pass that patched `.env.example` for being the third — verified against the anchored parse; patched, the table parser was generalized over indented rows and `CHANGELOG.md` joined the pinned loop, proved load-bearing by mutating `observability` to `~999 MB` there alone.
  - `[low]` `[patch]` blind: the word "four" is stated in `README.md`, `.env.example`, `compose.yaml` and `CHANGELOG.md` and asserted nowhere, so a fifth Bundle leaves four count words stale at exit 0 — verified; patched, all four now describe the registry without counting it.
  - `[low]` `[patch]` blind: ADR 0014's Consequences calls adding a Bundle "a three-part change, and the checker names each part", omitting the footprint restatement that `pixi run test` — not `lint-config` — enforces in three documents — verified in the file; patched to four parts, naming which check owns which. `AGENTS.md` enumerates no parts, so no agent-context file needed editing.
  - `[low]` `[patch]` blind: `README.md`'s upgrade callout says the refusal gives "an exit 1 naming the variable", understating this story's own improvement — the refusal now prints the line too — verified against `resolve_selection.py`; patched.
  - `[low]` `[patch]` blind: `README.md` and `CHANGELOG.md` both tell a migrating developer that `pixi run select` "prints what your current `.env` actually starts", which is false for the `.env` the callout addresses — verified: with no `COMPOSE_PROFILES` line it exits 1; patched, both now state both outcomes.
  - `[low]` `[patch]` blind: the Bundle tables' Services column uses display names ("object storage"), so a developer grepping for their own `minio` profile finds nothing — verified; patched, both tables spell the Module names.
  - `[low]` `[patch]` blind: `CHANGELOG.md` claims Keep a Changelog conformance it does not hold — bare `## Unreleased`, invented `⚠ BREAKING` section types — and the previous pass logged this as patched while the file still deviated — verified in the shipped file; patched, `## [Unreleased]` and the deviation now stated as deliberate, since leading with the break is the story's own requirement.
  - `[low]` `[patch]` blind: the aggregate footprints are hand-computed arithmetic restated in four files, and the "1.975 GB" sum claims four significant figures over values the same paragraph calls rounded — verified; patched, the false-precision sum is gone and the overlap is stated as the reason not to sum.
  - `[low]` `[reject]` blind: `lint-config` cannot catch a Bundle name that shadows a leaf Module name — carried from the 2026-09-07 pass; re-verified that `lint_selftest.py`'s `set(registry_entries) == non_module_profiles` still reds it in `pixi run ci`.
  - `[low]` `[reject]` blind: the rendered-document acceptance criterion is unsatisfiable because `docker compose config` emits the `x-bundles` block — the claim is true, and `compose.yaml`'s own comment says so, but the only fix is to edit this build's spec.
  - `[low]` `[reject]` blind: `memory:` is required but unconstrained, so `memory: "quite a lot"` would pass — real, but nothing showed that state reachable and NFR-7 asks for an approximation; the fix adds a format guard.
  - `[low]` `[reject]` blind: a module file declaring its own top-level `x-bundles:` is silently discarded and undocumented — carried from the 2026-09-07 pass on the same evidence.
  - `[low]` `[patch]` edge: `CHANGELOG.md:106-111`'s footprint table is unpinned — same defect as the blind layer's, patched with it.
  - `[low]` `[patch]` edge: `README.md:160` restates `~725 MB` in prose outside the anchored table parse — verified against the parser; patched, the sentence now points at the table and names no figure.
  - `[low]` `[patch]` edge: the derived totals are recomputed nowhere — same defect as the blind layer's aggregate finding, patched with it.
  - `[low]` `[patch]` edge: `compose.yaml:19`'s own header enumerates the four Bundles the block below registers — same defect as the count finding, patched with it.
  - `[low]` `[reject]` edge: `docker compose config` renders a top-level `x-bundles` key, so the "differs only by profiles" claim is wrong — same as the blind layer's; rejected on the same ground, the fix edits this build's spec.
  - `[low]` `[patch]` verification-gap: no verification gaps found; the layer's one Other finding — `README.md:160`'s prose footprint and `CHANGELOG.md`'s table both sitting outside the pin — is pre-verified and patched with the entries above.
  - `[low]` `[reject]` intent-alignment (3.1): `CHANGELOG.md`'s raw `docker compose --profile admin` claim is published at a surface no check exercises — true; the statement was observed live during the previous pass, and the fix adds a runtime test for a surface the intent's matrix never names.
  - `[false]` `[reject]` intent-alignment (3.2): closure is proved at Module granularity while the property is service-granular — refuted at `scripts/assert_config.py:820-836`, the helper-equals-primary leg, which requires every service a Module owns to carry an identical profile set, so the two granularities coincide.
  - `[low]` `[patch]` intent-alignment (3.3): the registry's `description:` values enumerate membership in prose, so a Module joining or leaving leaves four prose statements stale — verified; patched into ADR 0014's Consequences alongside the footprint surfaces, since a check here would be the hand-maintained membership list AD-7 forbids.
  - `[low]` `[reject]` intent-alignment (3.4): the footprint pin is anchored on table formatting, so a reformat reds the gate — verified, and correct behavior: the paired "still carries a Bundle table this check can read" assertion exists precisely so the failure blames the parse rather than the content.
  - `[false]` `[reject]` intent-alignment (3.5): footprint accuracy is pinned to no observation — carried from the 2026-09-07 pass, where it was rejected on the same ground.
  - `[false]` `[reject]` intent-alignment (3.6): the upgrade-line drift guard binds `resolve_selection.py`'s source text rather than the emitted message — refuted at `scripts/lint_selftest.py:1531-1553`, which asserts the line in `pixi run start`'s actual stderr, so a message that stopped using the constant reds there.
  - `[false]` `[reject]` intent-alignment (3.7): the registry-defect cases run against a YAML-normalized root file — refuted: the shipped file is asserted separately and positively at `scripts/lint_selftest.py:672-712`, the block asserts byte-for-byte restoration afterwards, and the property under test is registry shape, which comment stripping cannot affect.
  - `[low]` `[patch]` intent-alignment (3.8): `bundle_registry()`'s empty-mapping branch is unexercised, and the "Bundles compose" matrix row is asserted only through the shipped default — both verified; patched, an `("an empty registry", {}, ["x-bundles"])` case and a `core,observability` render equal to the union of the two Bundles' own memberships.
  - `[false]` `[reject]` intent-alignment (3.9): the diff writes `sprint-status.yaml`, which the intent forbids — refuted: that file is the orchestrator's, changed outside this build, and this run is instructed never to write or revert it.
  - `[low]` `[reject]` intent-alignment (3.10): data durability is evidenced only in prose — not a defect in the change; the inventory was re-captured this pass (12 volumes, 11 containers, identical before and after).

## Design Notes

**Why `admin` gains Postgres and Redis.** Epics AC 2.6-1 requires every Bundle to be dependency-closed *by declaration*, "so no Bundle relies on runtime expansion". `admin` today is `{flower, pgadmin, redisinsight}`, and all three depend outward, so it is closed only because `select.sh` expands it. Adding `admin` to Postgres's and Redis's `profiles:` closes it with no change to any resolved Selection — `select.sh admin` printed those five before and prints them after. That equality is the check that proves the change is inert, and it is asserted rather than argued.

**Why the registry carries description and memory but not members.** AD-7 is explicit that names are Core-owned and membership is Module-declared, and the footgun it records is real: a `profiles:` key on an `include` entry is silently ignored. A `modules:` list in the registry would be a hand-maintained second answer to "what starts", exactly the drift AD-7 exists to prevent. What the registry *can* own is what no service knows: the name is legal, what the Bundle is for, and what it costs (NFR-7). Putting the footprint there rather than only in the README makes it a CI-checkable declaration, and the self-test's README cross-check keeps the prose honest.

**Why the resolver stays ignorant of the registry.** Every lifecycle script now resolves through `select.sh`, so anything the resolver reads becomes a runtime dependency of `ps`, `psql` and `logs`. The resolver's vocabulary is the profile index it builds from the module files, which after this change contains exactly the Module names and the registered Bundle names — because `lint-config` refuses any other. Validation belongs in the checker; the runtime path stays as small as it is.

**Golden shape for the registry:**

```yaml
x-bundles:
  minimal:
    description: Postgres and Redis only — the data layer, nothing else.
    memory: ~60 MB
  core:
    description: Postgres, Redis, Keycloak, object storage and Mailpit.
    memory: ~800 MB
```

**Why the breaking-change work is not a second story.** The two mitigations AD-18 names (a non-empty shipped default, a loud refusal) landed in story 2-5; what remains is the third — leading the README and the release notes with it — and the concrete fix that documentation offers is `COMPOSE_PROFILES=core,admin,observability`, a line that does not exist until this story's registry does. They ship together or the advice is wrong.

**Why the running stack is not recreated.** Unchanged from story 2-5: the live stack belongs to the main checkout and every Module pins a fixed `container_name`, so a second project cannot start alongside it. Container-count criteria are settled at the rendered-model surface — `config --services` under a resolved Selection — and the live `up`/`down` cycle is left to CI's `ci-stack` job. The throwaway containers used to measure pgAdmin and the collector are a deliberate exception: `--rm`, no volume, no `devinfra` network, a name that cannot collide.

## Verification

**Commands:**
- `./scripts/select.sh core`, `minimal`, `admin`, `keycloak`, `postgres`, `core,observability`, `--all` -- expected: exactly the I/O matrix rows; `admin`, `keycloak` and `postgres` byte-identical to their pre-change output.
- `./scripts/select.sh --selections | wc -l` -- expected: 18.
- `COMPOSE_PROFILES="$(./scripts/select.sh core)" docker compose config --services | sort` -- expected: `keycloak`, `mailpit`, `minio`, `minio-init`, `postgres`, `redis`.
- `./scripts/select.sh "$(grep '^COMPOSE_PROFILES=' .env.example | cut -d= -f2-)"` -- expected: all thirteen Module names.
- `pixi run lint-config` -- expected: OK, four Bundles reported, eighteen Selections rendered.
- `pixi run lint-compose` -- expected: `OK — 18 Selection(s) validated`.
- `pixi run test` -- expected: every case passes, including the registry-defect, vocabulary, Bundle-closure, legacy-`.env` and README-footprint cases.
- `pixi run ci` -- expected: exit 0.
- Load-bearing mutations, each reverted immediately: an unjoined registry entry; a service profile absent from the registry; a fixture Module joining `minimal` and depending outside it; a registry entry with no `memory:`; a README with one footprint changed. Each must red a named case.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.
- `docker compose config --format json` under the all-Modules Selection, diffed against the pre-change capture -- expected: added Bundle names inside `profiles:` lists and nothing else; no image, port, environment value, command, volume identifier, restart policy, logging option, network attachment or `depends_on` edge differs.

**Manual checks (if no CLI):**
- Confirm no `pixi run up`, `down`, `restart` or `destroy` was executed against the live stack from this worktree, and that the eleven containers running at the start are the eleven running at the end.
- Read `CHANGELOG.md` and `README.md` from the top and confirm the breaking change is the first substantive thing each says, with the exact `.env` line to add.

## Auto Run Result

Status: done

**Summary.** Follow-up review pass over the story as committed at `e951d28`. No code
behaviour changed: every patch this pass applied closes a drift gap between the
`x-bundles` registry and the prose that restates it, or adds an assertion for a
declared behaviour that had none. The registry, the membership, the closed vocabulary,
the resolver and the refusal are untouched.

**Files changed this pass.**
- `scripts/lint_selftest.py` — the Bundle-table parser generalized over indented rows so
  `CHANGELOG.md` joins `README.md` and `.env.example` in the footprint pin; a new pin that
  both `README.md` and `CHANGELOG.md` print the Selection `.env.example` ships; an
  `("an empty registry", {}, ["x-bundles"])` defect case for `bundle_registry()`'s third
  unreadable shape; and a `core,observability` composition case asserting the union of the
  two Bundles' own memberships.
- `README.md` — the callout now says the refusal prints the line as well as naming the
  variable, and states what `pixi run select` does in both directions; the Bundle table's
  Services column spells Module names; the prose restatement of `~725 MB` and the
  four-significant-figure sum are gone.
- `CHANGELOG.md` — `## [Unreleased]`, the Keep a Changelog deviation stated as deliberate,
  the same table and "how to check" corrections.
- `compose.yaml`, `.env.example` — the Bundle count is no longer asserted in prose, the
  footprint's restatement sites are named accurately, and the false-precision sum is gone.
- `docs/adr/0014-bundles-are-a-core-owned-registry.md` — adding or changing a Bundle is a
  four-part change; which check owns which part, and which prose a membership change drags.

**Review findings breakdown.** 29 findings across four layers — 0 high, 1 medium, 23 low,
5 false, 0 maybe-false. Patched: 18 rows in 8 grouped entries (1 medium, 17 low). Deferred:
none. Rejected: 11 — two carried unchanged from the first pass (a Bundle name shadowing a
Module name, and a module file's own `x-bundles:`, both red at `pixi run ci` or unreachable);
two whose only fix edits this build's spec (the rendered-document acceptance criterion, which
`docker compose config` really does contradict by emitting the `x-bundles` key); an
unconstrained `memory:` format (a guard for an unreached state); the `CHANGELOG`'s raw
`--profile admin` claim (a surface the intent's matrix never names); the formatting-anchored
pin (loud failure is the design); and four `false` verdicts refuted at named lines —
Module-vs-service closure granularity (`assert_config.py:820-836`), the upgrade-line drift
guard (`lint_selftest.py:1531-1553`), the re-serialized root file (`lint_selftest.py:672-712`),
and `sprint-status.yaml` (orchestrator-owned, not written by this build).

**Follow-up review recommendation: false.** This was a follow-up pass and it patched no
`high`; the named risk the first pass carried — the rewritten footprint pin, which no
reviewer had read — was read by three layers this pass, and the two gaps they found in it
(`CHANGELOG.md` outside the loop, `README.md:160` outside the anchor) are closed and proved
load-bearing. Patched counts this pass: medium 1, low 17.

**Verification performed.**
- `pixi run ci` — exit 0. `lint-selftest` 1064 PASS, 0 FAIL (1058 before this pass; the six
  new assertions are the difference).
- `pixi run lint-config` — `OK — 18 Selection(s), 4 Bundle(s), bind address 127.0.0.1`, and
  `OK 4 registered Bundle(s): admin (~450 MB), core (~800 MB), minimal (~60 MB),
  observability (~725 MB) — each joined by at least one Module and each dependency-closed`.
- `pixi run select` — `core` → `keycloak,mailpit,minio,postgres,redis`; `minimal` →
  `postgres,redis`; `admin` → `flower,pgadmin,postgres,redis,redisinsight`; `keycloak` →
  `keycloak,mailpit,postgres`; `postgres` → `postgres`; `core,observability` → ten Modules;
  `.env.example`'s default → all thirteen. `--selections` → 18.
- Load-bearing mutations, each reverted and the files diffed back byte-identical: mutating
  `observability` to `~999 MB` in `CHANGELOG.md` alone reds the footprint mapping; narrowing
  the README callout's line to `core,observability` reds the upgrade-advice pin. The
  empty-registry case is itself the proof for that branch — `lint-config` exits non-zero,
  names `x-bundles`, and signs off on nothing.
- Durability: `docker volume ls --filter name=devinfra` — 12 volumes, identical before and
  after; `docker ps` — the same 11 containers. No `up`, `down`, `restart` or `destroy` ran
  from this worktree, and no throwaway container was needed this pass.

**Residual risks.**
- The registry's `description:` values and both Bundle tables' Services columns state
  membership in prose that no check reconciles — deliberately, since a machine-readable
  membership list in the registry is what AD-7 forbids. ADR 0014 now names them as the part
  of a Bundle change no checker will catch.
- `CHANGELOG.md`'s third breaking-change section describes raw `docker compose --profile
  admin` behaviour observed live during the previous pass; it is accurate but exercised by
  no test, because the resolver-bypassing surface is outside the intent's matrix.
- The two aggregate figures (~1.9 GB) remain prose arithmetic over the registry. The
  per-Bundle figures they derive from are pinned; the sum is not.

---
title: 'Selecting a Module brings what it needs'
type: 'feature'
created: '2026-09-07'
baseline_revision: 'e5ff7ad79308d81ff39da703f878be399bd370dc'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** Thirteen Modules exist but only two Selections do. The core five carry no `profiles:` key at all, so they start whether or not anyone asked for them: a developer who wants Postgres and Redis still boots Keycloak, MinIO and Mailpit, and `COMPOSE_PROFILES=keycloak` cannot work because Postgres does not carry the `keycloak` profile and Keycloak may not edit Postgres's file (AD-15). Nothing computes a dependency closure; the two profile enumerations that do exist (`lint-compose.sh` and `assert_config.py`) each walk the power set of declared profiles, which at fifteen profiles is 32 768 renders and would never finish.

**Approach:** Give every service its own Module-name profile alongside the profiles it already carries, and add `scripts/select.sh` — the Selection resolver — which expands a requested set of profile names into its transitive `depends_on` closure and emits the resulting `COMPOSE_PROFILES` value before Compose sees it. Every script that reaches the runtime resolves through it. Both power-set enumerations are replaced by the resolver's own list of Selections that actually exist: every Module's closure, every non-Module profile's closure, and the full Selection.

## Boundaries & Constraints

**Always:**
- The resolver's **output is always a set of Module names** — never a Bundle name. A request may name a Module or any profile a Module service declares; the emitted value names exactly the Modules in the closure, sorted and comma-joined on one line of stdout. Resolving an already-resolved Selection returns the same set.
- **The closure is over `depends_on`, computed from the Module files, never hand-maintained** (AD-6, AD-16). A service's `depends_on` targets map to their owning Module by the AD-8 rule `<dir>` or `<dir>-<role>`; both the mapping form with conditions and the bare list form are read.
- **The resolver fails loudly, and prints nothing on stdout when it does** (AD-18, NFR-5). Three refusals, each exit 1 with a diagnostic on stderr: an empty request (naming `COMPOSE_PROFILES` and the fix), an unknown name (naming it and listing the valid ones), and an empty resolved Selection.
- **Data durability is the overriding constraint.** No volume is renamed, removed or re-driven; no `down -v`, no `pixi run destroy`, and the running stack is not recreated from this worktree (see Design Notes). Capture `docker volume ls --filter name=devinfra` before and after and prove it identical.
- The shipped `.env.example` default and both CI jobs' `COMPOSE_PROFILES` must **resolve to every Module** — a fresh copy starts exactly the services it starts today (AD-18). This is asserted, not stated.
- `down`, `stop`, `pull`, `dump-logs`, `config` and `destroy` act on **every** Module, as they do today; they reach that through the resolver's all-Modules request rather than through hard-coded `--profile` flags. Only `start`/`up`/`up-core`/`wait` act on the requested Selection.
- The security posture is fixed, not improved: no credential, TLS, auth or exposure setting changes.
- Core gains no list of Module names. The catalog stays `services/*/compose.yaml` on the filesystem, as `module_composes()` already reads it.
- **Every script that calls `compose` resolves first**, with exactly two named exceptions: `lint-compose.sh`, which drives `--profile` itself, and `smoke-test.sh`, whose oracle is observational by contract and whose every Compose subcommand ignores profiles. A consequence to accept rather than work around: a script like `psql.sh` now refuses to run when the Selection is empty. That is AD-18 doing its job — `pixi run init` ships a non-empty default, and a `.env` that lost the variable should fail rather than half-work.
- Every new check is proved load-bearing: a fixture that makes it fail, and — for a glob or an enumeration — a case that makes it fail when the term matches nothing.

**Never:**
- Never add `x-bundles`, a Bundle registry, the `core`/`minimal` Bundles, or Bundle memory-footprint documentation — those are story 2.6. The existing `admin` and `observability` profiles stay exactly as they are; 2.6 promotes them into the registry.
- Never remove or rename a profile, volume, container, service, environment variable or port. `profiles:` keys are **added to**, never replaced.
- Never make `scripts/smoke-test.sh` consult the resolver — not for its `running` oracle and not for its own Compose calls. Verified on Compose v5.3.0: `compose ps`, `exec` and `version` ignore active profiles entirely, so the observational oracle is already correct under any Selection, and `scripts/lint_selftest.py:2636-2709` pins it.
- Never touch `scripts/urls.sh` (DW-40 and story 3-3 own it) or reconcile host ports across their three declaration sites (DW-34).
- Never name a new Python module `select.py` — it shadows the stdlib `select` that `subprocess` depends on.
- Never write `_bmad-output/implementation-artifacts/sprint-status.yaml` or `deferred-work.md`; both are orchestrator artifacts. (This change does close DW-1, DW-2 and DW-32; recording that is the orchestrator's, not this story's.)
- Never edit a decided ADR in place — a new ADR, or a dated amendment.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Single Module, no dependencies | `select.sh postgres` | `postgres` on stdout, exit 0 | — |
| Two Modules, no dependencies | `select.sh postgres redis` | `postgres,redis` — exactly two Modules, no Keycloak, MinIO, Mailpit or observability | — |
| Transitive closure | `select.sh keycloak` | `keycloak,mailpit,postgres` — Keycloak `depends_on` both | — |
| Two-hop closure | `select.sh grafana` | `grafana,loki,prometheus,tempo` | — |
| Non-Module profile expands to Modules | `select.sh admin` | `flower,pgadmin,postgres,redis,redisinsight` — the closure, not the three admin Modules | — |
| All Modules | `select.sh --all` | Every Module directory name, sorted | — |
| Idempotence | `select.sh "$(select.sh keycloak)"` | Byte-identical to `select.sh keycloak` | — |
| Request from the environment | No arguments, `COMPOSE_PROFILES=keycloak` | Same as `select.sh keycloak` | — |
| Empty request | No arguments, `COMPOSE_PROFILES` unset or empty | Exit 1, stderr names `COMPOSE_PROFILES` and how to fix it, **stdout empty** | Non-zero, nothing started |
| Unknown name | `select.sh nosuch` | Exit 1, stderr names `nosuch` and lists every valid name, stdout empty | Non-zero |
| `depends_on` on a service no Module owns | A module file `depends_on: [zz-orphan]` | Exit 1 naming the edge — never silently dropped from the closure | Non-zero |
| Unresolved Selection reaching Compose | `COMPOSE_PROFILES=keycloak docker compose config -q`, bypassing the resolver | Exit 1: `service "keycloak" depends on undefined service "postgres": invalid compose project`; no container or volume created | Non-zero — correct behaviour per AD-16, not a defect |
| Resolved Selection reaching Compose | `COMPOSE_PROFILES=$(select.sh keycloak) docker compose config -q` | Exit 0; `config --services` lists exactly `keycloak`, `mailpit`, `postgres` | — |
| Service missing its own Module profile | A module file whose service omits its `<dir>` profile | `pixi run lint-config` exits 1 naming the Module and the service, and signs off on nothing | Non-zero |
| Helper with a profile set unlike its primary | `minio-init` carrying `profiles: [admin]` | `pixi run lint-config` exits 1 naming both services | Non-zero |
| Shipped defaults under-select | `.env.example` or a CI job whose `COMPOSE_PROFILES` resolves to fewer than every Module | `pixi run test` fails naming the file and the missing Modules | Non-zero |
| A stack script bypasses the resolver | A script calling `compose` without `select_profiles` | `pixi run test` fails naming the script | Non-zero |

</intent-contract>

## Code Map

- `scripts/lib/common.sh:49-56` -- the `compose()` seam every shell path already funnels through; `:19` the `cd` to repo root; `:24-40` the `.env` load whose `export -p` replay makes an exported `COMPOSE_PROFILES` beat `.env` (`:26-30` states this is load-bearing for `up-core.sh` → `wait-healthy.sh`, and it is what lets a resolved Selection survive a re-source). **Add `select_profiles()` here**, beside `compose()`; do not resolve at source time — `init-env.sh` and `bootstrap.sh` source this file before a `.env` exists.
- `scripts/compose.sh:12-17` -- the whole file; the seam a pixi task reaches. Resolve here, honouring an all-Modules request, then delegate. Its header at `:2-11` explains why no task body may name a runtime; the same argument now applies to profiles.
- `scripts/up-core.sh:13` -- `export COMPOSE_PROFILES=` (start the unprofiled core). **This is the single line the breaking change invalidates**: with a profile on every service, an empty value starts nothing. Becomes an explicit request for the five core Modules; `:2` and `:6-7` header claims change with it.
- `scripts/destroy.sh:16` -- `compose --profile admin --profile observability down -v`; must remove **every** Module, so it takes the all-Modules request.
- `scripts/wait-healthy.sh:27-31` -- `compose config --services` is the expected set and an empty one already exits 1. `config --services` **does** honour profiles (verified), so this script must resolve. `:39` `compose ps` does not honour them (verified) and needs nothing.
- `scripts/logs.sh:20`, `scripts/keycloak-reimport.sh:18-22` -- `logs` with no service argument and `up -d keycloak` both read the model; they resolve. `scripts/ps.sh:13`, `psql.sh:13`, `redis-cli.sh:20`, `mc.sh:13`, `backup.sh:18`, `restore.sh:33`, `keycloak-export.sh:10-12`, `assert-podman.sh:39`, `smoke-test.sh:58,115,128` reach Compose only through `ps`/`exec`/`cp`, which ignore profiles — they resolve anyway, so the rule stays "every script that calls `compose` resolves first" with exactly one documented exception.
- `scripts/lint-compose.sh:21-25` -- the deliberate `export COMPOSE_PROFILES=""`; **the one exception**, and the only script that keeps driving `--profile` itself. `:33-44` the `config --profiles` read and its "an enumeration that could not be read is never no profiles" guard (keep; `lint_selftest.py:3226-3250` pins it). `:46-68` **the power-set loop — delete**; 2^15 combinations. Replaced by the resolver's Selection list. `:70-76` the failure roll-up and OK line: keep the shape, change the noun from "combination" to "Selection".
- `scripts/assert_config.py:210-222` `combinations()` -- **the Python half of the same power set; delete.** `:195-208` `declared_profiles()` — keep, it is how non-Module profiles are found. `:225-235` `label()`; `:237-263` `render()`; `:170-193` `run_compose()` (clears `COMPOSE_PROFILES` at `:184`, drives `--profile`) — all keep. `:731-850` `check()`, whose `:748` "rendered no services — a pass over an empty set verifies nothing" guard is why the empty combination must leave the enumeration rather than be rendered. `:852-919` `main()`: `:888` is where `combinations(declared_profiles())` is called and `:918` prints the count.
- `scripts/assert_config.py:265-272` `module_composes()`, `:274-299` `read_model()`, `:511-530` `depends_on_names()` -- the three graph primitives the resolver needs. **Move them into the new resolver module and re-import them here**, so the arrow points `assert_config` → resolver and there is exactly one closure implementation (the duplication DW-1 records). Every existing call site in `assert_config.py` keeps its current spelling.
- `scripts/assert_config.py:532-705` `module_contract()` -- add the profile-membership leg here. `:566-597` is the ownership block that already computes each Module's owned service names — reuse it. `:599-612` (healthcheck leg) is the shape to copy for the new leg's diagnostic. `:101` `MODULE_FILES`, `:421-444` `justified()`, `:446-474` `healthcheck_declared()` unchanged.
- `services/*/compose.yaml` × 13 -- 14 services. Seven declare `profiles:` today: `pgadmin:28`, `redisinsight:30`, `flower:30` (`[admin]`); `prometheus:23`, `loki:23`, `tempo:24`, `otel-collector:37`, `grafana:37` (`[observability]`). Six declare none: `postgres:39`, `redis:33`, `keycloak:49`, `minio:45`, `minio-init:71`, `mailpit:36`. `depends_on` edges: `keycloak:55` (mapping, postgres+mailpit), `minio-init:79` (mapping, minio), `pgadmin:29`, `redisinsight:31`, `flower:31` (mapping), `otel-collector:38` and `grafana:38` (**bare list form**). `minio-init` is the only helper; it takes `[minio]`, identical to its primary.
- `pixi.toml:44` `start`, `:57` `down`, `:61` `stop`, `:73` `pull`, `:93` `dump-logs`, `:156` `config` -- five of the six carry a hard-coded `--profile admin --profile observability`; all six route through `compose.sh`. `:99-102` `smoke-strict` is the precedent for a task-level `env` table. `:31-35` the header rule that no task body names a runtime. `:197` `lint`, `:203` `ci`, `:210` `ci-stack`, `:227` `precommit` (deliberately excludes the two runtime-bound lint tasks).
- `scripts/lint_selftest.py:1293-1310` `seam_tasks` -- six task argvs pinned **literally including the `--profile` flags**; the flags leave, and the assertion surface moves to `recorded_env()`. `:1004` destroy's pinned argv, same. `:1281-1287` up-core's "every runtime call sees `COMPOSE_PROFILES=` empty". `:1266` `pixi run ps` observing the planted `.env`'s value. `:1543-1600` the `lint-compose` expected-argv sequence and the `STUB_PROFILES_EXIT=1` unreadable-enumeration path. `:2168-2177` `lint-config` clearing `COMPOSE_PROFILES`.
- `scripts/lint_selftest.py:3171-3225` -- the real-runtime profile block. `:3177-3190` proves an empty `COMPOSE_PROFILES` beats `.env` and that `core_services < all_services`; **`core_services` becomes empty after this change**, which is the breaking change itself and must be re-expressed as: the empty Selection renders nothing, and that is exactly why the resolver refuses it. `:3202-3204` `expected_core`/`expected_admin`/`expected_observability` literal membership — re-express as resolved-closure membership per Module and per profile.
- `scripts/lint_selftest.py:3379-3394` -- both CI jobs' `COMPOSE_PROFILES` must equal `docker compose config --profiles` exactly. **That invariant dies here** (15 declared profiles, and a job must not name every Module by hand); replace with: each job's value *resolves to* every Module.
- `scripts/lint_selftest.py:102-127` `pixi()`, `:130-164` `run_script()`, `:166-207` `RECORDER` (answers `config --profiles`/`--format`/`ps --all` from stubs and records argv plus one `COMPOSE_PROFILES=` line per call), `:235-259` `write_recorder()`, `:261-302` `stub_env()`, `:304-331` `recorded()`/`recorded_env()`, `:372-394` `planted()` (raises `FileExistsError` rather than overwrite), `:396-416` `moved_aside()`, `:479-495` `tool()` (the real runtime), `:505-509` `expect()`, `:914` `fresh()`, `:1606-1625` `rendered()`/`port()`/`clean_doc`, `:1830-1855` `contract_fixture()`/`without()`, `:1856+` `contract_cases`. The resolver needs no stub: it parses the real `services/*/compose.yaml`, so every recorded `COMPOSE_PROFILES` value is deterministic.
- `scripts/lint_selftest.py:770-850` `defects` and `:852-870` `empties` -- the load-bearing-glob pattern. `lint-shell` already covers `scripts/**/*.sh`, so `select.sh` needs no new term; `lint-python` (`pixi.toml:191`) already covers `scripts` for ruff and mypy.
- `.env.example:46-49` -- the `COMPOSE_PROFILES` comment block and `COMPOSE_PROFILES=admin,observability`. `:48` "Core services (postgres, redis, keycloak, minio, mailpit) always start" becomes false the moment core services carry profiles.
- `.github/workflows/ci.yml:68-70` and `:105-109` -- both stack jobs' `COMPOSE_PROFILES: admin,observability` and the comments claiming it equals `config --profiles`.
- `compose.yaml:1-20` -- the header prose describing the three profile groups ("core = none"); `:27-40` the include registry; `:45-57` bare volumes; `:59-62` the keyed `devinfra` network. No structural change; the header's core-has-no-profile claim does.
- `docs/adr/0002-dependency-validation-is-compose-native.md`, `0012-every-module-carries-its-own-contract.md`, `0001`, `0005` -- read-only context; the new decision (resolver, its name space, the all-Modules request, and the retired power set) gets its own ADR.
- `README.md:64,73` profile documentation; `:174-252` the repository-layout block (add `scripts/select.sh`); `:216` the `assert_config.py` description. `AGENTS.md` -- layout claims inside the `bmad:context` managed block.
- **Verified against the live runtime (Compose v5.3.0), do not re-derive:** a profiled service whose `depends_on` target is unselected fails `config -q` with `service "a" depends on undefined service "b": invalid compose project`, exit 1; an empty `COMPOSE_PROFILES` renders zero services at exit 0; `compose ps`, `exec` and `logs <service>` ignore active profiles; `config --services` and `logs` with no service argument honour them.

## Tasks & Acceptance

**Execution:**
- `<scratch>/before-*.txt` -- **outside the repository tree, before touching anything**: capture `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort`, and `docker compose config --format json` for the current `(none)`, `admin`, `observability` and `admin,observability` combinations. The rendered captures are the evidence that adding `profiles:` keys changed nothing else about any service body.
- `scripts/resolve_selection.py` (new) -- the Module graph and the closure. Home for `module_composes()`, `read_model()` and `depends_on_names()` moved down from `assert_config.py`, plus: an owner map from service name to Module directory (`<dir>` or `<dir>-<role>`), a profile index from profile name to the Modules whose services declare it, `closure()` over `depends_on`, and `selections()` returning the list this repository actually validates — every Module, then every non-Module profile, then the full set. A `main()` with a `--all` flag and positional request names, printing the sorted comma-joined Module names to stdout and every refusal to stderr. Google-style docstrings, full type hints, `mypy --strict` clean. **Not named `select.py`.**
- `scripts/select.sh` (new) -- the entry point AD-16 names. Sources `lib/common.sh` (for the repo-root `cd` and the `.env` load), then execs the resolver with the given names, or with the value of `COMPOSE_PROFILES` when none are given. `set -euo pipefail`, shellcheck-clean, header explaining that it is the supported way to reach Compose and that a raw `--profile` invocation bypassing it is expected to fail.
- `scripts/lib/common.sh` -- add `select_profiles()`: run `select.sh` with the arguments given, and on success `export COMPOSE_PROFILES` to the resolved value; on failure let the diagnostic through and exit non-zero. Resolve nothing at source time.
- `scripts/compose.sh` -- call `select_profiles --all` when `DEVINFRA_SELECT_ALL=1`, otherwise `select_profiles`, then delegate as it does now. Update the header.
- `scripts/{up-core,destroy,wait-healthy,logs,ps,psql,redis-cli,mc,backup,restore,keycloak-export,keycloak-reimport,assert-podman}.sh` -- add the `select_profiles` call each one needs: `up-core.sh` requests the five core Modules by name (`postgres redis keycloak minio mailpit`) instead of clearing the variable; `destroy.sh` requests `--all`; every other one takes the ambient Selection. `smoke-test.sh` and `lint-compose.sh` are the two exceptions and stay as they are. Correct each header that describes the old behaviour.
- `services/*/compose.yaml` × 13 -- give every service a `profiles:` list containing its own Module name, **added to** whatever it already carries: the six unprofiled services gain `profiles: [<module>]`, the three admin services become `[<module>, admin]`, the five observability services become `[<module>, observability]`, and `minio-init` takes `[minio]` — identical to its primary's. Nothing else in any file changes.
- `.env.example:46-49` -- set `COMPOSE_PROFILES=postgres,redis,keycloak,minio,mailpit,admin,observability`, which resolves to every Module and so starts exactly what it starts today. Rewrite the comment: the value is a Selection of Module and group names, it is expanded to its dependency closure before Compose sees it, and it is no longer optional — core services now carry profiles too.
- `.github/workflows/ci.yml:68-70,105-109` -- the same value in both stack jobs, with the comment restated as "resolves to every Module, which the self-test pins".
- `scripts/lint-compose.sh` -- replace the power-set loop with the resolver's Selection list: for each Module directory name, each declared profile that is not a Module name, and the all-Modules request, `export COMPOSE_PROFILES="$(./scripts/select.sh …)"` and run `compose config -q`. Keep the "an enumeration that could not be read is a failure" guard, keep running every Selection after one fails, and close DW-32 by refusing a successfully-read but empty profile list. Do not export a bare assignment prefix onto the `compose` function — in bash that persists past the call.
- `scripts/assert_config.py` -- delete `combinations()`; import the three moved primitives and the Selection list from `resolve_selection`; render one document per Selection instead of per subset. Add the profile-membership leg to `module_contract()`: every service a Module owns declares its own Module name in `profiles:`, and a helper's profile set equals its primary's. Extend the module docstring at `:1-70` and the final OK line's noun.
- `pixi.toml` -- drop `--profile admin --profile observability` from `down`, `stop`, `pull`, `dump-logs` and `config`, giving each `env = { DEVINFRA_SELECT_ALL = "1" }`; leave `start` as it is. Add a `select` task (`./scripts/select.sh` with a `names` arg) so a developer can print a closure without reading a script. No `lint-*` task is added or removed.
- `scripts/lint_selftest.py` -- (a) resolver cases proving AD-21's three contracts: closure correctness over every row of the I/O matrix, refusal of an empty Selection, refusal of an unknown name, plus idempotence and the stdout-stays-empty-on-refusal rule; (b) rewrite `seam_tasks`, the destroy pin and the up-core pin to assert `recorded_env()` — the resolved Selection each task exports — now that no argv carries `--profile`; (c) rewrite the real-runtime block so the empty Selection renders nothing (the breaking change, asserted rather than assumed) and each Module's and each group's resolved closure renders exactly its expected services; (d) replace the CI-jobs pin with "each job's `COMPOSE_PROFILES` resolves to every Module", and add the same assertion for `.env.example`; (e) a static case that every script calling `compose` also calls `select_profiles`, with `lint-compose.sh` and `smoke-test.sh` the two named exceptions; (f) a `contract_cases` entry per new contract leg, each asserting a non-zero exit, that the diagnostic names the Module and the service, and that `"OK" not in stdout`; (g) prove the new enumeration load-bearing — a Selection list that missed a Module must fail a case by name.
- `docs/adr/0013-*.md` + `docs/adr/README.md` -- record the resolver: `depends_on` as the sole closure source, Module names as the output vocabulary, the all-Modules request for lifecycle tasks, the retirement of the profile power set, and the breaking change with the two mitigations that land here (a non-empty shipped default, and loud refusal of an empty Selection).
- `README.md`, `AGENTS.md` -- document `COMPOSE_PROFILES` as a Selection and `pixi run select`, add `scripts/select.sh` to the layout, and correct the "core services always start" claim. Leave the migration notes and release-note lead to story 2.6.

**Acceptance Criteria:**
- Given a request for `postgres` and `redis`, when the stack starts, then exactly two containers run — no Keycloak, no MinIO, no `minio-init`, no Mailpit, no admin service and no observability service. Evidenced here by `docker compose config --services` under the resolved Selection, since the live stack must not be recreated from this worktree (see Design Notes); state that in the run result rather than claiming a start that did not happen.
- Given a request for `keycloak` alone, when the resolver runs, then it emits `keycloak,mailpit,postgres`, that Selection validates at exit 0, and all three services — and only those three — are what the model renders for it.
- Given the pull request, when CI's `stack` and `stack-podman` jobs run, then both start the stack through the resolver and `smoke-strict` reports its full pass count with no skip — the live proof that the resolved default Selection starts every service it started before.
- Given a Selection that reaches Compose without passing through the resolver, when `docker compose config -q` runs against it, then it exits non-zero naming the undefined service, and no container or volume is created.
- Given `.env.example`'s shipped `COMPOSE_PROFILES`, and each of the two CI stack jobs' `COMPOSE_PROFILES`, when each is resolved, then each yields exactly the thirteen Module names — a fresh checkout and both CI jobs start the same services as before this change.
- Given each of the fourteen services, when `pixi run lint-config` runs, then it reports every Module carrying its own profile on every service it owns, and removing that profile from any one service makes it exit 1 naming the Module and the service while signing off on nothing.
- Given `pixi run lint` and `pixi run ci`, when they run, then they exit 0 and complete in the time they take today — `lint-compose` and `lint-config` each validate sixteen Selections, not 32 768 combinations.
- Given the volume inventory captured before the change, when it is captured again afterwards, then it is identical, and the rendered document for every pre-existing profile combination differs only by the added `profiles:` entries.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass

- verdicts: 33 findings — high 0, medium 6, low 24, false 3, maybe-false 0
- findings:
  - `[medium]` `[patch]` `select.sh` defaults `DEVINFRA_PYTHON` to bare `python3`, which cannot import PyYAML, so every lifecycle script now dies on an interpreter traceback outside pixi — Reproduced: `./scripts/select.sh keycloak` printed `ModuleNotFoundError: No module named 'yaml'`. Real regression: `ps.sh`, `psql.sh`, `up-core.sh` and the rest needed no Python before. Patched — the ImportError is caught and reported as a `select:` diagnostic naming PyYAML, `pixi run`, and the `DEVINFRA_PYTHON` seam.
  - `[low]` `[reject]` `--all` and `--selections` are matched against raw argv, so extra names are silently swallowed — Confirmed: `resolve_selection.py --all zz-nosuch` exits 0 printing every Module. Rejected: no call site passes a flag with names (`--all` comes only from `DEVINFRA_SELECT_ALL` in five task bodies and `destroy.sh`, `--selections` only from `lint-compose.sh`), and the fix adds a guard rather than correcting a wrong result.
  - `[low]` `[patch]` The advertised "empty resolved Selection" refusal is unreachable — Confirmed by reading `build_graph`, which seeds `profiles[module].add(module)`, so every accepted name maps to at least one Module and `closure()`'s `if not resolved:` cannot fire. Patched — the dead branch is deleted and all three documents now say three refusals.
  - `[low]` `[reject]` The helper profile-set leg is skipped for a Module with no `<dir>`-named primary — Rejected: `module_contract` already appends "declares no primary service named '<module>'" for that case at `assert_config.py:553`, so such a Module fails by name; the skipped leg adds nothing to an already-red run.
  - `[medium]` `[patch]` The Selection-membership leg is one-directional, so a service may declare another Module's profile — Verified live: `pgadmin` written as `profiles: [pgadmin, postgres, admin]` made `select.sh postgres` emit `pgadmin,postgres` with `lint-config` green, breaking the headline two-names-two-containers property. Patched — the leg now also refuses any profile that is another Module's directory name, with a `contract_cases` entry proved load-bearing.
  - `[low]` `[patch]` `--selections` is a real flag `lint-compose.sh` depends on but is documented nowhere — Confirmed absent from `select.sh`'s usage header and ADR 0013. Patched — documented in both.
  - `[low]` `[patch]` The new `pixi run select` task has no self-test coverage — Confirmed: no `pixi("select"` existed, so the empty-argument filter written for the bare-task case was never executed. Patched — four task cases added; removing the filter now reds one by name.
  - `[low]` `[patch]` `lint-compose.sh`'s header still claims the `config --profiles` read is what stops a new group profile going unvalidated — Confirmed stale: the enumeration now comes from `select.sh --selections`, and the reconciliation lives at `assert_config.py:893`. Patched — comment corrected and cross-referenced in both directions.
  - `[low]` `[patch]` A `resolved` → `resolve_names` rename garbled four self-test strings — Confirmed at `lint_selftest.py:3457,3462,3500-3501,3506`; these are the messages a contributor reads when the idempotence case fails. Patched — wording restored.
  - `[low]` `[reject]` The orphan fixture's `mkdir(exist_ok=True)` plus unconditional `rmtree` could remove a real directory — Rejected: `services/zz-selftest-orphan/` follows the repository's established `zz-selftest-*` fixture convention, and `contract_fixture` (added by story 2-4 and accepted at review) uses exactly the same `mkdir(exist_ok=True)` / `rmtree(ignore_errors=True)` pair.
  - `[false]` `[reject]` `build_graph` lets a duplicate service name across two Modules overwrite `owners` silently — Disproved: `module_contract` requires every service key in `services/<dir>/compose.yaml` to be `<dir>` or `<dir>-<role>`, so two Modules cannot declare the same service name; the overwrite is unreachable.
  - `[low]` `[reject]` `.env.example` and both CI jobs carry a hand-maintained list that only happens to resolve to every Module — Rejected: the self-test asserts all three resolve to every Module, so drift is caught by name; the proposed fix (a reserved `all` request name) is new public request-vocabulary surface, not a direct correction.
  - `[low]` `[patch]` The spec says three refusals, ADR 0013 and `select.sh` say four — Confirmed inconsistent. Patched with the unreachable-branch entry above; all three now say three.
  - `[low]` `[patch]` `pixi run select postgres redis`, documented in README, errors — Reproduced: pixi exits non-zero with "task 'select' received more arguments than expected". Patched — README shows the comma-joined form pixi accepts and names `./scripts/select.sh postgres redis` for separate arguments.
  - `[medium]` `[patch]` `select.sh` dies on a raw PyYAML traceback rather than a named refusal — Duplicate root cause of the first row; patched with it.
  - `[low]` `[reject]` `DEVINFRA_SELECT_ALL` set to anything but the exact string `1` silently falls through to the ambient Selection — Rejected: the variable is set only by five pixi task bodies and read nowhere else; no user path sets it, and the fix adds a branch rather than correcting a wrong result.
  - `[low]` `[reject]` `--all`/`--selections` combined with names — Duplicate of the second row; rejected on the same reasoning.
  - `[low]` `[patch]` The "empty resolved Selection" refusal is dead code — Duplicate of the third row; patched with it.
  - `[false]` `[reject]` A Module whose services declare no `profiles:` key lets the resolver emit a name Compose cannot gate on — Disproved: the Selection-membership leg added by this change fails such a Module by name, with three `contract_cases` entries covering it; the resolver's seeding keeps the request resolvable only so the defect is reported rather than crashing the report.
  - `[low]` `[reject]` An explicit but empty request argument falls back to the ambient Selection instead of refusing — Rejected: every call site passes either explicit non-empty names or `${COMPOSE_PROFILES:-}`, and an empty ambient value still reaches the refusal; no path reaches a different outcome.
  - `[low]` `[reject]` `epic-2-context.md` lost four clauses when reworded — Rejected: that file is a workflow-generated planning cache, regenerated by this run's own step-01 compile because the planning artifacts were newer; it is not repository content and its wording is not a contract.
  - `[low]` `[patch]` README's `pixi run select postgres redis` claim is false — Duplicate of the fourteenth row; patched with it.
  - `[low]` `[patch]` Three documents claim a refusal that cannot be triggered — Duplicate of the third row; patched with it.
  - `[medium]` `[patch]` The smoke driver's `running()` oracle swallows a failed `compose ps`, so an unresolved narrow ambient Selection makes every Module skip and the suite exit 0 having verified nothing — Verified live: `COMPOSE_PROFILES=keycloak docker compose ps --services --filter status=running` exits 1 with `depends on undefined service "mailpit"`, and `2>/dev/null` turned that into "nothing running". Patched — a non-zero `ps` is now fatal with a diagnostic naming `COMPOSE_PROFILES`; the file still consults no resolver, and a self-test case proves the suite no longer reports a clean zero-check pass.
  - `[low]` `[patch]` The `pixi run select` task and its empty-argument path are never executed by any test — Duplicate of the seventh row; patched with it.
  - `[medium]` `[patch]` Ambient resolution in the rewritten scripts is pinned only by a source-text scan, because every behavioural case uses a request whose closure is itself — Confirmed: `fresh()` uses `postgres,redis`, and the planted-`.env` `psql` case asserted argv only, so deleting `select_ambient` from `psql.sh` reddened nothing but the grep. Patched — the `psql` case now asserts `recorded_env` equals the ten-Module closure of `admin,observability`.
  - `[medium]` `[patch]` `select.sh` tracebacks outside pixi — Duplicate of the first row; patched with it.
  - `[low]` `[reject]` AC 1 and AC 2 are evidenced at the rendered-model surface, never at the container surface — Rejected: the spec states and argues this (Design Notes, "Why the running stack is not recreated"), so the finding's fix would edit this build's spec; the live proof is CI's `ci-stack` job, and the rendered surface was independently verified here.
  - `[low]` `[reject]` AC 3's "no container or volume is created" clause is asserted nowhere — Rejected: no bad outcome is demonstrated — `config -q` fails before Compose creates anything, and the volume inventory was captured before and after and is identical; the proposed fix requires starting containers, which the spec forbids from this worktree.
  - `[low]` `[reject]` AC 6's volume preservation across a *changed* Selection has no counterpart at any surface — Rejected: no volume-affecting behaviour is added — the root `volumes:` stanza is untouched, `down` carries no `-v`, and the `--all` request that keeps `down` from orphaning containers is pinned by `recorded_env`; the proposed fix is a new CI start/stop job, not a direct correction.
  - `[medium]` `[patch]` An existing `.env` holding the old `COMPOSE_PROFILES=admin,observability` stays legal but now resolves to ten Modules, silently dropping Keycloak, MinIO and Mailpit at exit 0 — Confirmed by resolving that value. Story 2.6 covers the absent-variable case only. Patched — README's migration sentence and ADR 0013's consequences now cover the stale-value case and tell the reader to check it with `pixi run select`.
  - `[low]` `[reject]` The AD-21 resolver cases sit inside the real-runtime block although the resolver needs no runtime — Rejected: they run in `pixi run test`, which CI runs on every push; placement changes nothing about whether they execute.
  - `[false]` `[reject]` The diff's surface is substantially wider than the six acceptance criteria — Disproved as a defect: every addition is forced by putting a profile on every service. The power-set retirement is not optional (fifteen profiles is 32 768 renders), the `declare -x` filter is forced by the new task-level `env` tables, and the contract leg is what makes the resolver's name space trustworthy.

### 2026-09-07 — Review pass (follow-up)

- verdicts: 30 findings — high 0, medium 2, low 21, false 5, maybe-false 0
- findings:
  - `[low]` `[patch]` `pixi run smoke` now hard-fails on a narrow-but-legal ambient Selection, and no document says so — Verified live: `COMPOSE_PROFILES=keycloak docker compose ps --services --filter status=running` exits 1 with `depends on undefined service "postgres"`, and README's own `COMPOSE_PROFILES=keycloak pixi run up` example leaves exactly that value exported. The refusal itself is AD-18 doing its job (loud, with the remedy in the diagnostic); the defect is that it was undocumented. Patched — README now says `smoke` reads the runtime rather than the resolver and needs an already-closed Selection, and shows the export that gives one.
  - `[low]` `[patch]` The new smoke diagnostic prints the Selection twice with literal backslashes — Reproduced under bash: `${V:+\'${V}\'}${V:-unset or empty}` fires *both* arms for a set value, printing `COMPOSE_PROFILES is \'postgres,redis\'postgres,redis.`. The covering case asserted only `"COMPOSE_PROFILES" in stderr`, so the mangled form was what it accepted. Patched — built in a `shown` variable, and the case now reads the line and pins it exactly; restoring the old expansion reds it by name.
  - `[low]` `[patch]` `.env.example` and README both claim an empty value "starts nothing at all", which is the pre-mitigation behaviour — Confirmed: the scripts refuse it (exit 1 naming the variable), and README says so eleven lines later. The shipped template is the first thing a migrating reader sees. Patched — both now say it is refused rather than started as nothing.
  - `[low]` `[reject]` The Selection-membership leg rejects only another Module's name, not an unknown profile — Confirmed the gate is `profile in parsed_by_module`. But a typo that *replaces* a group name reds `expected_members` by name (the group's literal service set is pinned at `lint_selftest.py:3493-3510`); a typo that merely *adds* one only leaks an extra request name. Rejected: marginal, and the fix needs a registered group-name vocabulary, which story 2.6 owns and this story's Never list forbids.
  - `[low]` `[patch]` The PyYAML prerequisite the resolver puts on every lifecycle script is documented only inside `select.sh`'s header — Confirmed: `./scripts/select.sh keycloak` outside pixi exits 1 on the PyYAML diagnostic, and README recommends the direct form. Patched — README's Selection section now states it.
  - `[low]` `[patch]` `DEVINFRA_PYTHON` appears nowhere outside script headers although the refusal tells the reader to set it — Confirmed absent from README and AGENTS.md. Patched with the row above, naming it beside the `DEVINFRA_COMPOSE` convention.
  - `[low]` `[reject]` "Every script that calls compose resolves first" is enforced by a `scripts/*.sh` glob and a text grep — Confirmed narrow: it cannot see `scripts/lib/*.sh`, a Python entry point, or call ordering. Rejected: `assert_config.py` is the only Python that reaches Compose and is a linter driving `--profile` itself, exactly like the named `lint-compose.sh` exemption; widening the scan would add a third and fourth exemption rather than catch anything real, and the check already fails if either named exemption stops reaching Compose.
  - `[false]` `[reject]` The `declared_profiles()` ↔ `graph.names()` reconciliation cannot fire — Disproved: the static parse reads each module file's literal `profiles:` key, while `config --profiles` reports the *rendered* model, so a profile arriving through the `extends: common/base.yaml` every service carries would appear in one and not the other. That is reachable and is what the check guards.
  - `[low]` `[reject]` Nothing pins `depends_on` as the only cross-Module edge the closure must follow — Confirmed no guard forbids `extends` at another Module's file, `network_mode: service:`, `links` or `volumes_from`. Rejected: no such edge exists, and one would fail loudly anyway — `lint-compose` renders every Module's own closure, so the undefined service surfaces there; the fix is a new check for a defect nobody has shown reachable.
  - `[low]` `[reject]` The resolver is re-executed once per request, re-parsing the catalog each time — Confirmed 17 spawns. Rejected on measurement: `pixi run lint-compose` completes in 2.04s for all 16 Selections, and the proposed fix changes `--selections` into a two-column format, which is new output surface.
  - `[low]` `[reject]` The board says `done` while the spec says `in-review`, and the triage log says "six acceptance criteria" where the spec lists eight — Rejected: `sprint-status.yaml` is the orchestrator's, which this session must not write, and the spec status was mid-flight for this very pass. The count phrase is inside this build's spec, so its fix would edit the spec.
  - `[low]` `[patch]` Two paragraphs left unwrapped by the previous pass's patches — ADR 0013 line 31 confirmed at 160 characters in a file whose next-longest line is under 100. The README half is refuted: that file already carries ten lines over 100 characters, so 115 is not an outlier. Patched — the ADR line is re-wrapped.
  - `[low]` `[reject]` The core five and the request vocabulary are restated without a single source, and `select.sh` has no `--help` — Rejected: the self-test's list is a *deliberately* independent oracle (the file says so where `expected_members` is defined) — deriving it from the thing under test is the failure mode it exists to avoid; `--help` is new public surface, and the argv-matching half carries the previous pass's reject.
  - `[low]` `[patch]` The smoke diagnostic mangles the value it reports — Duplicate root cause of the second row; patched with it.
  - `[false]` `[reject]` An empty `COMPOSE_PROFILES` makes `ps` answer over zero services, so the suite reports 0 checks and exits 0 — Disproved live: `COMPOSE_PROFILES=` and the full Selection both make `docker compose ps --services --filter status=running` print the same eleven running services. `ps` genuinely ignores active profiles; the oracle observes containers, not the Selection.
  - `[low]` `[reject]` The `declare -x` replay filter drops a continuation line of a multi-line value that itself begins `declare -x ` — Confirmed possible in principle; requires an exported value containing a newline followed by that literal text. Rejected: not met in everyday use, and the fix is quote-balance tracking across lines, not a direct correction. Ordinary multi-line values are unaffected — their continuation lines do not match the prefix.
  - `[low]` `[reject]` No `python3` on PATH gives bash's `exec: python3: not found` rather than a named diagnostic — Confirmed there is no pre-flight. Rejected: the message already names the missing binary, every documented path is `pixi run`, and the fix adds a guard branch.
  - `[low]` `[reject]` A mid-loop `select.sh` failure aborts `lint-compose.sh` under `set -e` before the roll-up — Rejected: the requests come from `select.sh --selections`, so every one of them is resolvable by construction; and the failure would not be silent — the resolver's own diagnostic names it on stderr and the script exits non-zero.
  - `[low]` `[reject]` The spec's third refusal (an empty resolved Selection) is not the code's third (an unownable `depends_on` edge) — Rejected: the fix edits this build's spec. The previous pass deleted the unreachable branch and reconciled `select.sh`, ADR 0013 and the code on three refusals; the matrix row that replaced it is tested.
  - `[low]` `[reject]` "Completes in the time they take today" is doubtful when each enumeration went from 4 renders to 16 — Rejected on measurement: `pixi run lint-compose` is 2.04s and `pixi run ci` 66s end to end.
  - `[medium]` `[patch]` `select_profiles`'s fatal refusal — one of the two mitigations ADR 0013 leans on — has no behavioural test at any consuming script — Pre-verified gap: every `request=None` case runs inside a planted `.env` supplying `admin,observability`, and every refusal case drives `select.sh` standalone, so the shell function is never exercised empty. Patched — `pixi run ps` with no request now asserts non-zero exit, the variable named on stderr, and `recorded(record) == []`. Proved load-bearing: rewriting `select_profiles` as `local x="$(...)"` reds both new cases by name (`exited 0 with nothing selected`; `recorded ['ps', '--format', ...]`).
  - `[medium]` `[patch]` The `DEVINFRA_PYTHON` seam and the PyYAML refusal added by the previous pass have no test — Pre-verified gap: neither name occurs in `lint_selftest.py`, and the `except ModuleNotFoundError` branch carries `# pragma: no cover`. Patched — a stub interpreter proves the seam is honoured, and a `yaml.py` shim ahead of the real package on `PYTHONPATH` proves the named diagnostic. Proved load-bearing: hard-coding `python3` reds the seam case, and restoring a bare `import yaml` reds all three diagnostic cases with the traceback in the failure text.
  - `[low]` `[patch]` The smoke diagnostic is unreadable for a set value — Duplicate root cause of the second row; patched with it.
  - `[low]` `[reject]` The real-runtime block never exercises `ps` under an unresolved Selection, so the premise behind the fatal branch is pinned only by a stub — Rejected: the stub case pins the branch behaviourally, and the premise itself was verified against the real runtime in this pass (unresolved `keycloak` → exit 1; empty and full → the same eleven services).
  - `[low]` `[reject]` The spec's third refusal is discharged at the reachability surface rather than the contract surface — Duplicate of the nineteenth row; rejected on the same reasoning.
  - `[low]` `[reject]` The I/O matrix's literal stdout strings are never compared as strings; the tests compare rendered service sets — Rejected: the service-set assertions are strictly stronger evidence for what a Selection means, and the sorted comma-joined format *is* pinned as a string by the `.env.example` and both CI jobs' `== all_modules` comparisons and by the idempotence and from-environment cases.
  - `[low]` `[reject]` Nine of the rewritten scripts are pinned only by the source-text scan — Rejected: five scripts and six seam tasks carry behavioural `recorded_env` pins, and this pass added the empty-Selection behavioural case that the scan could never catch; per-script behavioural pins for the remaining nine would restate the same one property nine times.
  - `[false]` `[reject]` The diff changes surfaces the intent never names — Disproved as a defect: each is forced by the change (the `declare -x` filter by the new task-level `env` tables, `--selections` by the retirement of the power set, the contract's third leg by the resolver's name space), and the reviewer filed it descriptively rather than as a bad outcome.
  - `[false]` `[reject]` `sprint-status.yaml` and `epic-2-context.md` sit outside the story's boundary — Disproved as this story's defect: both are orchestrator/workflow artefacts, the first explicitly excluded by the intent and not writable by this session, the second a planning cache this run's own step-01 regenerates.
  - `[false]` `[reject]` The README migration paragraph and ADR consequence go further than the spec's task line — Disproved as a defect by the reviewer's own reading: the intent's Never list forbids only Bundle material, and the epic asks this change to lead the README.

## Auto Run Result

Status: done (follow-up review pass)

**Summary.** The story's implementation was already complete and committed at `6e8613a`.
This run was a follow-up review pass over the same diff: four review layers, 30 findings,
7 patched entries, 0 deferred. No code path of the resolver changed; the patches correct
one user-facing diagnostic, three documentation inaccuracies, and close the two
verification gaps that let the story's own safety mitigations regress unnoticed.

**Files changed in this pass:**
- `scripts/smoke-test.sh` — the "project did not load" diagnostic built in a `shown`
  variable; the nested `${V:+…}${V:-…}` form printed the Selection twice with literal
  backslashes.
- `scripts/lint_selftest.py` — three new check groups: the empty-Selection refusal
  asserted behaviourally at a consuming script (exit non-zero, variable named, runtime
  never reached), the `DEVINFRA_PYTHON` seam and the PyYAML refusal, and the smoke
  diagnostic read line-by-line instead of substring-matched.
- `scripts/resolve_selection.py` — the `# pragma: no cover` note corrected; the branch is
  now driven by the self-test in a subprocess.
- `README.md` — the empty Selection is refused, not "starts nothing"; the PyYAML
  prerequisite and `DEVINFRA_PYTHON` named beside the `DEVINFRA_COMPOSE` convention; a
  note that `pixi run smoke` reads the runtime rather than the resolver and so needs an
  already-closed Selection.
- `.env.example` — the same empty-Selection correction, re-wrapped.
- `docs/adr/0013-selection-is-resolved-to-its-dependency-closure.md` — line 31 re-wrapped
  (160 characters, spliced in by the previous pass).

**Review findings breakdown.** 30 findings across four layers — 0 high, 2 medium, 21 low,
5 false, 0 maybe-false. Patched: 7 entries (2 medium, 5 low). Deferred: none. Rejected: 18,
each with its reason recorded in the triage log above — the substantive ones being the
membership leg's unknown-profile gap (needs the group registry story 2.6 owns), the
resolver-coverage scan's narrowness (widening it would add exemptions, not catch defects),
the reconciliation "unreachable" claim (disproved — `extends` carries profiles the static
parse cannot see), the empty-`COMPOSE_PROFILES` smoke claim (disproved live — `ps` ignores
active profiles), and two performance claims (disproved by measurement).

**Follow-up review recommendation: false.** This was a follow-up pass and it patched no
`high`; the two medium entries were both verification gaps, and each new check was proved
load-bearing by mutation rather than assumed. The work has converged.

**Verification performed.**
- `pixi run ci` — exit 0. 986 self-test PASS, 0 FAIL; `lint-compose: OK — 16 Selection(s)
  validated`; `assert-config: OK — 16 Selection(s), bind address 127.0.0.1`. 66s end to end.
- Every I/O-matrix row re-run through pixi: `postgres redis` → `postgres,redis`;
  `keycloak` → `keycloak,mailpit,postgres`; `grafana` → `grafana,loki,prometheus,tempo`;
  `admin` → `flower,pgadmin,postgres,redis,redisinsight`; `--all` → all thirteen;
  idempotence byte-identical. Both refusals exit 1 with an explanatory first line and zero
  bytes on stdout.
- `COMPOSE_PROFILES="$(./scripts/select.sh keycloak)" docker compose config --services` →
  exactly `keycloak mailpit postgres`; the `postgres redis` request → exactly those two.
- `COMPOSE_PROFILES=keycloak docker compose config -q` → exit 1,
  `depends on undefined service "postgres"` (correct per AD-16).
- Load-bearing mutations, each reverted immediately: rewriting `select_profiles` as
  `local x="$(...)"` reds the two new refusal cases by name; hard-coding `python3` in
  `select.sh` reds the seam case; restoring a bare `import yaml` reds all three PyYAML
  cases with the traceback quoted in the failure text; restoring the old nested expansion
  reds the unmangled-diagnostic case showing `\'postgres,redis\'postgres,redis`.
- Premise checks against the real runtime, read-only: `docker compose ps --services
  --filter status=running` returns the same eleven services under `COMPOSE_PROFILES=` and
  under the full Selection (so the observational oracle is genuinely profile-independent),
  and exits 1 under the unresolved `keycloak` (so the fatal branch is genuinely reachable).
- Data durability: `docker volume ls --filter name=devinfra` captured before and after this
  pass and diffed — identical, twelve volumes. Eleven containers running at start and at
  end. No `up`, `down`, `restart`, `destroy` or `-v` was executed against the live stack
  from this worktree; the container-count acceptance criteria remain evidenced at the
  rendered-model surface, as the Design Notes require, with CI's `ci-stack` job the live
  proof.
- The `services/*/compose.yaml` files were not touched in this pass, so the rendered model
  is byte-identical to the one the implementation pass captured and diffed.

**Residual risks.**
- The live `up`/`down` cycle under a narrow Selection is still only proved by CI's
  `ci-stack` and `ci-stack-podman` jobs, never from this worktree. That is the deliberate
  data-durability trade the spec makes.
- A service that adds a *bogus* profile name alongside its correct ones (a typo that adds
  rather than replaces) still passes every check and leaks one extra request name into the
  resolver's vocabulary. Closing it needs the registered group vocabulary story 2.6 owns.
- The "every script resolves first" rule is enforced over `scripts/*.sh` by text scan plus
  five behavioural pins and one new empty-Selection pin; a future Python entry point that
  reaches the runtime would not be seen by it.

## Design Notes

**Why the resolver is Python behind a shell entry point.** AD-16 and AD-21 name `scripts/select.sh`, and shell is what `common.sh` and the pixi task surface can call, so the entry point stays a shell script. The closure itself reads YAML — two `depends_on` forms, service-to-Module ownership, a profile index — which `assert_config.py` already parses correctly and which bash would parse badly. Splitting them keeps `select.sh` trivially shellcheck-clean and puts the logic under `mypy --strict` and the self-test. The graph primitives move *down* into the resolver rather than the resolver importing upward, because `assert_config.py` must get its Selection list from the resolver (AD-16: computed by the resolver, never hand-maintained) and the import arrow can only point one way.

**Why the output vocabulary is Module names only.** A request may name a group (`admin`) but the emitted value never does. Emitting `admin` would re-enter Compose's own profile semantics and select the three admin services without their Postgres and Redis — the exact failure AD-16 exists to prevent. Emitting the closure as Module names makes "exactly two containers" a property of the string, checkable without starting anything, and it is what makes story 2.6's "every Bundle is already dependency-closed" a real assertion rather than a tautology.

**Why lifecycle tasks request every Module.** `down`, `stop`, `pull` and `config` honour active profiles, which is why they carry `--profile admin --profile observability` today. Routing them through the resolver's Selection instead would mean that narrowing your Selection and then running `down` silently orphans the containers you just stopped asking for. The all-Modules request keeps today's semantics, removes the hard-coded flags, and still satisfies "every task that invokes Compose goes through the resolver".

**Why the power set has to go, not just shrink.** Fifteen declared profiles is 32 768 renders; a cap would be arbitrary and would leave the choice of *which* combinations unexplained. The Selections that exist are the ones the resolver can name: one per Module (AD-6's closure-validity, which is what makes a Module liftable), one per group, and the full set. Every duplicate-published-port collision is still caught, because the full Selection contains every Module. This closes DW-1 (two enumerations, one now), DW-2 (the power set named as unsurvivable) and DW-32 (an empty profile list read as success).

**Why the running stack is not recreated.** The stack currently running belongs to the main checkout, and every Module pins a fixed `container_name`, so a second project cannot start alongside it and a recreate from this worktree would repoint bind mounts at a directory that disappears when the run ends. Container-count criteria are therefore settled at the rendered-model surface — `config --services` under a resolved Selection, which is exactly what `lint_selftest.py:3171-3225` already does against the real runtime — and the live `up`/`down` cycle is left to CI's `ci-stack` job. Say so explicitly in the run result rather than reporting a live start that did not happen.

## Verification

**Commands:**
- `./scripts/select.sh postgres redis`, `keycloak`, `grafana`, `admin`, `--all` -- expected: exactly the I/O matrix rows, sorted and comma-joined.
- `./scripts/select.sh` with `COMPOSE_PROFILES=` and with `COMPOSE_PROFILES=nosuch` -- expected: exit 1, an explanatory diagnostic on stderr naming the variable or the bad name, and **nothing on stdout**.
- `COMPOSE_PROFILES="$(./scripts/select.sh keycloak)" docker compose config --services | sort` -- expected: `keycloak`, `mailpit`, `postgres`. Then the same for `postgres redis` -- expected: exactly two names.
- `COMPOSE_PROFILES=keycloak docker compose config -q` -- expected: exit 1, `depends on undefined service "postgres"`.
- `pixi run lint-compose` -- expected: OK over sixteen Selections, seconds not hours.
- `pixi run lint-config` -- expected: OK, thirteen module files carrying the contract, sixteen Selections rendered.
- `pixi run test` -- expected: every case passes, including the resolver cases and the rewritten seam, real-runtime and CI-workflow pins.
- `pixi run ci` -- expected: exit 0.
- `docker compose config --format json` for `(none)`, `admin`, `observability` and both, diffed against the pre-change captures -- expected: the added `profiles:` entries and nothing else; no image, port, environment value, command, volume identifier, restart policy, logging option, network attachment or `depends_on` edge differs. The `(none)` capture is the exception and the point: it goes from six services to zero, which is the breaking change.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.

**Manual checks (if no CLI):**
- Confirm no `pixi run up`, `down`, `restart` or `destroy` was executed against the live stack from this worktree, and that the container list is the same thirteen at the end of the run as at the start.
- Read back every `services/*/compose.yaml` diff and confirm each hunk adds only a `profiles:` entry.



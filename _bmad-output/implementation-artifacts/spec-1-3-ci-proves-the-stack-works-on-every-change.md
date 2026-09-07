---
title: 'CI proves the stack works on every change'
type: 'feature'
created: '2026-09-07'
status: 'awaiting-operator'
baseline_revision: '4adad78c695c11112accc2fabb9a365685734721'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/implementation-artifacts/spec-1-2-lifecycle-logic-extracted-into-testable-scripts.md', '{project-root}/docs/adr/0002-dependency-validation-is-compose-native.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
operator_actions:
  - "Merge this branch to `main`, then watch the first hosted CI run to completion: both the `validate` and the `stack` job must go green before the merge gate below is attached, because a required check named before it has ever reported blocks every pull request permanently."
  - "Read the `stack` job's wall-clock duration from that first run. If the job was cancelled at its 15-minute bound, split it into a core-profiles job and a full-profiles job running in parallel — never drop a check to fit the bound."
  - "Add a `required_status_checks` rule naming the contexts `validate` and `stack` to repository ruleset `protect-default-branch` (id 22412252) at https://github.com/millsks/devinfra/rules/22412252, or by PUT to `repos/millsks/devinfra/rulesets/22412252`. The ruleset carries `deletion`, `non_fast_forward`, `creation`, `update` and `pull_request` rules today and no status-check rule at all, so until this is added a red CI run does not block merge and story 1.3's \"the result gates merge\" is unsatisfied."
deferred:
  - summary: >-
      The profile power set is implemented twice, in bash and in Python, with nothing asserting
      the two enumerations agree.
    evidence: |-
      scripts/lint-compose.sh builds it with `for ((mask = 0; mask < combinations; mask++))` and
      scripts/assert_config.py with `for mask in range(1 << len(profiles))`, each carrying its own
      `(none)` label convention and its own COMPOSE_PROFILES clearing. Both are tested, but only
      independently: a future rule (canonical ordering, skipping the empty combination) can land in
      one and not the other with nothing going red. Merging them means either shelling from bash to
      the Python enumerator or folding `config -q` into assert_config.py, which is a restructure
      rather than a correction.
    location: >-
      scripts/lint-compose.sh and scripts/assert_config.py
    severity: low
  - summary: >-
      Enumerating 2^N profile combinations will not survive Epic 2 giving every service a profile.
    evidence: |-
      Two profiles today, so four compose invocations taking about two seconds — nobody meets this
      now. The epics file states that Stage 5 gives every Service a profile; at fourteen services
      that is 16384 invocations and `pixi run lint` would never finish. The remedy (a cap, or
      enumerating only the selections that exist rather than the power set) is a design decision
      belonging to the story that introduces per-service profiles, not a guard to add here.
    location: >-
      scripts/lint-compose.sh, scripts/assert_config.py
    severity: low
  - summary: >-
      `pixi run ci` crashes with an uncaught FileExistsError if the developer already has a
      compose.override.yaml.
    evidence: |-
      scripts/lint_selftest.py:551 (pre-existing, not introduced by this story) and the new block
      both use `planted(REPO / "compose.override.yaml", ...)`, which deliberately raises rather
      than overwrite tracked content. compose.override.yaml is not gitignored, so a developer using
      one gets a traceback instead of a gate result. The fix is `moved_aside()` around an existing
      file, or planting under a unique name passed with `-f`.
    location: >-
      scripts/lint_selftest.py:551
    severity: low
---

<intent-contract>

## Intent

**Problem:** Nothing proves this stack works except a human running `pixi run smoke` by hand. `.github/` holds agent definitions and no workflows, so a pull request that breaks Keycloak, deletes a `depends_on` target, or exposes a port on `0.0.0.0` merges green. `scripts/smoke-test.sh` additionally reports `SKIP` for any service that is not running and still exits 0 — in CI that is the same silently-passing defect Story 1-1 removed from the lint surface.

**Approach:** Add a GitHub Actions workflow that runs the existing pixi task surface — never its own tool versions or inline shell. One job runs the static gate; a second starts the stack with every profile, waits for health, and runs the smoke suite in a new strict mode where a skip is a failure. `lint-compose` is widened from one profile pair to every combination the file declares, and a new assertion pass reads the *resolved* `docker compose config --format json` to hold NFR-2 (bind address), NFR-4 (explicit tags) and AD-17 (duplicate host ports).

## Boundaries & Constraints

**Always:** CI invokes `pixi run <task>`; the workflow file contains no tool version, no `apt-get install`, and no logic a task could hold. Every job is time-bounded by `timeout-minutes`, so breaching the 15-minute rule fails the run rather than hiding. Assertions read `docker compose config` output, never `compose.yaml` or `.env` as text (NFR-6). Profile combinations are enumerated from `docker compose config --profiles`, never hard-coded, so a profile added later cannot go unvalidated. Every new script ships with a case in `scripts/lint_selftest.py` that runs in `pixi run ci`.

**Never:** No `continue-on-error`, no `|| true`, no `if: always()` softening, no `command -v` guard, no step that reports success having checked nothing. Do not change what the smoke test asserts about any service — strict mode changes only how an absent service is scored. Do not weaken the security posture (NFR-8): trivial credentials, no TLS and dev-mode Keycloak are correct here. Do not mutate the `protect-default-branch` ruleset from this run — see `operator_actions`. Do not add Podman (Story 1-4), Renovate (1-6) or commit hooks (1-7).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Every profile combination validates | `compose.yaml` declares `admin` and `observability` | `lint-compose` runs `config -q` four times: none, admin, observability, both | No error expected |
| Undefined `depends_on` target | A service depends on a service no profile combination defines | Non-zero; compose's own message names the undefined service; the failing combination is named | Exit non-zero |
| Port published on all interfaces | A `ports:` entry rendered with `host_ip` `0.0.0.0` or empty | Non-zero, naming the service, the port and the address found | Exit non-zero |
| Two services share a host port | Rendered config publishes `127.0.0.1:5432` twice | Non-zero, naming both services and the port | Exit non-zero |
| Floating or absent image tag | A service resolves to `redis` or `redis:latest` | Non-zero, naming the service and the image | Exit non-zero |
| Smoke suite, strict, service absent | `SMOKE_STRICT=1`, keycloak not running | Non-zero; the line reads FAIL, not SKIP, and names keycloak | Exit non-zero |
| Smoke suite, default, service absent | `SMOKE_STRICT` unset, keycloak not running | SKIP as today; exit status unchanged (FR-5) | No error expected |
| Smoke suite, strict, `curl` missing | `SMOKE_STRICT=1` and no `curl` on PATH | Fails at preflight naming `curl`, before any check runs | Exit non-zero |
| CI job overruns the bound | Stack job exceeds its `timeout-minutes` | GitHub cancels the job and the run is red | Exit non-zero |

</intent-contract>

## Code Map

- `.github/` -- holds `agents/` only. **No `workflows/` directory exists**; this story creates the first one. Nothing to preserve or migrate.
- `pixi.toml:180-215` -- the validation block. `lint-compose` is today a single `docker compose --profile admin --profile observability config -q`, which is one combination of four. `lint` fans out to the five `lint-*` tasks; `ci` is `lint` + `test`. New tasks belong here, and CI must call these names rather than restating their bodies (AD-9).
- `pixi.toml:30-75` -- lifecycle tasks. `start` is `docker compose up -d` (profiles come from the environment), `wait` is `./scripts/wait-healthy.sh`, `smoke` is `./scripts/smoke-test.sh`, `init` copies `.env.example`. The stack job composes these; no new lifecycle logic is needed.
- `scripts/smoke-test.sh:12-19` -- `set -uo pipefail` then its own `[[ -f .env ]] && set -a && source .env && set +a`. **The change point.** Replace with `source "$(dirname "$0")/lib/common.sh"` so the suite runs through the `compose` seam and can be driven by the selftest's recording stub. Note common.sh also supplies the four `?=` defaults and makes exported values beat `.env` — required so the CI job's `COMPOSE_PROFILES` wins.
- `scripts/smoke-test.sh:36-39,42` -- `skip()` and `running()`. `skip()` increments `SKIP` and never touches `FAIL`; `running()` shells to `docker compose ps` directly. Strict mode routes `skip()` through `fail()`; `running()` must go through `compose`.
- `scripts/smoke-test.sh:60,91,119,160,180,218,277,285,293,301,317,337` -- the twelve `if running <svc>` guards and the `check_http` helper. Behaviour of the checks themselves is frozen; only the scoring of their `else` branches changes.
- `scripts/smoke-test.sh:352-359` -- the summary and `((FAIL == 0)) || exit 1`. Strict mode needs no new exit path: turning a skip into a failure reuses this one.
- `scripts/lib/common.sh:52-56` -- `DEVINFRA_COMPOSE` word-split into `DEVINFRA_COMPOSE_ARGV`, wrapped by `compose()`. This is the established test seam; `scripts/token.sh:17` shows the same pattern for `DEVINFRA_CURL`. Reuse both, do not invent a third.
- `scripts/lint_json.py` -- precedent for a Python helper under `scripts/`: module docstring, Google-style docstrings, `from __future__ import annotations`, exits non-zero naming the offending file. `lint-python` already runs `ruff format --check`, `ruff check` and `mypy` over `scripts/`, so a new `.py` must satisfy all three.
- `scripts/lint_selftest.py:143-243` -- `write_recorder()`, `stub_env()`, `recorded()`, `recorded_env()`. The recorder answers `config` with `STUB_SERVICES`, `ps --all` with `STUB_ALL`, everything else with `STUB_STDOUT`, and exits `STUB_EXIT`. **A `config --format json` case needs `STUB_STDOUT`/`STUB_SERVICES` to carry JSON, and `--profiles` needs its own answer — extend the recorder's `case` rather than writing a second stub.**
- `scripts/lint_selftest.py:282-327` -- `planted()` (refuses to overwrite, removes in `finally`) and `moved_aside()`. The only sanctioned way to put a broken fixture inside the repository; a planted `compose.override.yaml` is how the undefined-`depends_on` case reaches `pixi run lint-compose`.
- `scripts/lint_selftest.py:44-83` -- `FORBIDDEN = ("command -v", "which ", "|| true", "skipping")` is asserted against every task body, and `MAKE_FORWARDS` is a fixed dict of the pre-pixi targets. Adding tasks does not disturb `MAKE_FORWARDS`; a new task body must not contain a `FORBIDDEN` string.
- `compose.yaml:37-413` -- fourteen services. Unprofiled: postgres, redis, keycloak, minio, minio-init, mailpit. `profiles: [admin]`: pgadmin, redisinsight, flower. `profiles: [observability]`: otel-collector, prometheus, loki, tempo, grafana. Every port is `"${BIND_ADDRESS:-127.0.0.1}:${X_PORT:-N}:N"` and every image `<repo>:${X_VERSION:-tag}` — verified: `config --format json` renders these as `ports[].host_ip` / `ports[].published` and a fully resolved `image` string. Read-only in this story.
- `.env.example:14,17` -- `COMPOSE_PROFILES=admin,observability` and `BIND_ADDRESS=127.0.0.1`. `pixi run init` copies this, so a CI job that runs `init` gets every profile by default; the job still sets `COMPOSE_PROFILES` explicitly so the intent is visible in the workflow.
- `docs/adr/0002-dependency-validation-is-compose-native.md` -- `config -q` is the dependency gate and no bespoke resolver is written; its Consequences section states that `config -q` is blind to host-port collisions and that CI must check rendered config for duplicates (AD-17). Read-only; it is the authority for two decisions here.
- `README.md:206-239` (`## Common tasks`) and `README.md:164-205` (`## Repository layout`) -- the tables to extend with the new tasks, the workflow and the two new scripts.
- `AGENTS.md:24-27` -- "`pixi run lint` is the validation surface … `pixi run ci` is the done-gate". Read-only: agent-context edits were deferred in Stories 1-1 and 1-2 and stay deferred.
- `Makefile` -- read-only. `MAKE_FORWARDS` covers the pre-pixi targets only; the new tasks are CI-facing and get no shim.
- Live GitHub state, verified with `gh api repos/millsks/devinfra/rulesets/22412252`: ruleset `protect-default-branch` is `active` on `~DEFAULT_BRANCH` with `deletion`, `non_fast_forward`, `creation`, `update` and a `pull_request` rule (1 approval, code-owner review). **It carries no `required_status_checks` rule**, so "the result gates merge" is not satisfied by the repository alone — see `operator_actions`.

## Tasks & Acceptance

**Execution:**
- `scripts/lint-compose.sh` -- new. Read the declared profiles from `compose config --profiles`, enumerate the power set, and run `compose <flags> config -q` once per combination, echoing which combination is under test and continuing to the end so one broken combination does not hide another. Exit non-zero if any failed, listing every failing combination -- "every profile combination" is the acceptance criterion, and a loop that stops at the first failure reports one defect where there may be four.
- `scripts/assert_config.py` -- new. For each profile combination, parse `compose <flags> config --format json` and assert: every `ports[]` entry's `host_ip` equals `BIND_ADDRESS` (default `127.0.0.1`) and is never empty or `0.0.0.0`; no two `(host_ip, published)` pairs collide across services; every `image` carries an explicit tag that is not `latest`. Fail naming the service, the value found and the combination. Refuse an empty service set -- reading the resolved output rather than the source file is NFR-6 itself, and a pass over zero services is the silent skip this epic exists to remove.
- `scripts/smoke-test.sh` -- source `scripts/lib/common.sh` in place of its private `.env` chain, route `running()` and `dc()` through `compose`, add a preflight that fails naming any missing required tool (`curl`), and add `SMOKE_STRICT`: when set to `1`, `skip()` records a failure naming the absent service instead of incrementing `SKIP`. Default behaviour is unchanged -- FR-5 requires an out-of-selection check to report skipped, so strict is the CI opt-in, not a new default.
- `pixi.toml` -- point `lint-compose` at the new script; add `lint-config` (`python scripts/assert_config.py`) to the `lint` chain; add `smoke-strict` (`smoke-test.sh` with `env = { SMOKE_STRICT = "1" }`) and `ci-stack` (`depends-on = ["init", "start", "wait", "smoke-strict"]`) -- CI must invoke one named task per job so the workflow never becomes a second definition of what CI runs (AD-9).
- `.github/workflows/ci.yml` -- new. Triggers on `push` to `main` and on `pull_request`; a `concurrency` group cancels superseded runs. Job `validate`: checkout, `prefix-dev/setup-pixi` pinned by version, `pixi run ci`, `timeout-minutes: 10`. Job `stack`: same setup, `COMPOSE_PROFILES: admin,observability` in the job environment, `pixi run ci-stack`, then `pixi run ps` and `docker compose logs` on failure only, `timeout-minutes: 15`. The two jobs run in parallel so wall-clock stays inside the bound; if the stack job ever breaches it, tier it into core and full profile jobs rather than dropping a check.
- `scripts/lint_selftest.py` -- extend the recorder to answer `--profiles` and to return JSON for `config --format json`, then add contract cases: `lint-compose.sh` invokes `config -q` once per combination with the right `--profile` flags; it exits non-zero and names every failing combination when the stub fails; a planted `compose.override.yaml` with an undefined `depends_on` makes `pixi run lint-compose` fail and name that service; `assert_config.py` rejects a `0.0.0.0` port, a duplicated host port, a `latest` tag and an empty service set, and accepts a clean fixture; `smoke-test.sh` with nothing running exits 0 under default scoring and non-zero under `SMOKE_STRICT=1` naming an absent service; the strict run fails at preflight when `curl` is shadowed. Every script in this repository ships with a test that runs in the gate.
- `README.md` -- add the CI section (what runs, the two jobs, the time bound), the new tasks to `## Common tasks`, and the two new scripts to `## Repository layout` -- the documented command must be the one the project supports.

**Acceptance Criteria:**
- Given a pull request, when CI runs, then `docker compose config -q` is validated for every profile combination, the stack starts, health is waited for, and the full smoke suite runs -- with no step marked `continue-on-error` and no conditional that lets a failure pass.
- Given a service whose `depends_on` names a service no combination defines, when CI runs, then it exits non-zero and the output names that undefined service.
- Given a check whose tool, service or fixture is unavailable, when CI runs, then the run fails naming what was missing -- no `SKIP` line may appear in the CI smoke output.
- Given the `validate` and `stack` jobs, when the workflow is read, then each declares `timeout-minutes` no greater than 15, so exceeding the bound turns the run red instead of extending it.
- Given `pixi run ci`, when it runs on a clean checkout, then it exits 0 and the selftest covers every new script's contract.
- Given the workflow file, when it is read, then it names no tool version, installs nothing with a package manager, and every job's work is a `pixi run <task>` invocation.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 39 findings — high 0, medium 17, low 15, false 4, maybe-false 0
- findings:
  - `[low]` `[reject]` blind-hunter: `operator_actions` referenced twice in the spec body but absent from frontmatter — real, but the fix edits this build's spec; the Finalize step adds `operator_actions` and `status: awaiting-operator` per the invocation protocol.
  - `[low]` `[patch]` blind-hunter: `ci.yml` comment claims the `validate` job "does not touch a container runtime", but `pixi run ci` runs `lint-compose`/`lint-config`, both of which shell to `docker compose` — comment corrected and README Requirements now say `pixi run lint` needs Docker too.
  - `[medium]` `[patch]` blind-hunter: the selftest checks the workflow only for what must not be there; deleting the whole `stack` job, or the `on:` triggers, left every assertion passing — added positive assertions pinning `validate`'s run list to `pixi run ci`, `stack`'s to `pixi run ci-stack`, and the trigger map to `push` + `pull_request`.
  - `[medium]` `[patch]` blind-hunter: `assert_config.py:dotenv_value` hand-parses `.env` and diverges from `common.sh`'s `source` on `export ` and inline `#` comments — now strips both and honours quotes; a `.env` case with `export BIND_ADDRESS=10.1.2.3   # lab` covers it.
  - `[low]` `[reject]` blind-hunter: the `if: failure()` logs step repeats `--profile admin --profile observability` — a diagnostic that runs only after the job already failed; a missing profile there costs some log output, nothing more. `scripts/logs.sh` hard-codes `-f` and would hang the job, so it cannot be reused.
  - `[medium]` `[patch]` blind-hunter: no `permissions:` block, so both jobs inherited the repository's default token scopes — added top-level `permissions: contents: read` and `persist-credentials: false` on both checkouts. The SHA-pinning half is `low` and rejected: `@v4`/`@v0.8.1` is this repository's idiom and the fix is more than a direct correction.
  - `[medium]` `[patch]` blind-hunter: `cancel-in-progress: true` also cancels `push` runs on `main`, leaving a merged commit permanently unverified — now `${{ github.event_name == 'pull_request' }}`.
  - `[medium]` `[patch]` blind-hunter: the preflight named only `curl`, though the suite shells to `openssl rand -hex` (smoke-test.sh:253-254) and `base64 -d` (:181) on the host — verified both are used; `REQUIRED_TOOLS` is now `(curl openssl base64)`.
  - `[low]` `[reject]` blind-hunter: `type -P` passes the `FORBIDDEN` ban because that ban is a string match — true, but the preflight is the correct polarity (it fails, never skips), and broadening `FORBIDDEN` would need an allow-list for it: more than a direct correction.
  - `[low]` `[defer]` blind-hunter: the profile power set is implemented twice, in bash and Python, with nothing asserting the two agree.
  - `[medium]` `[patch]` blind-hunter: the `COMPOSE_PROFILES=""`-beats-`.env` precedence the whole enumeration rests on was proven only against a stub, which has no precedence rules — confirmed against the real runtime (14 services with `.env`, 6 with the variable cleared) and added a real-runtime selftest case.
  - `[low]` `[patch]` blind-hunter: `assert_config.py`'s wildcard-`BIND_ADDRESS` refusal and `tag_problem`'s digest branch had no cases — one case added for each.
  - `[low]` `[reject]` blind-hunter: three duplications — (a) `compose.override.yaml` planted in two places: the blocks are sequential, not nested, so they cannot collide (the underlying crash risk is deferred separately); (b) `smoke-strict` restates `cmd` instead of depending on `smoke`: pixi's propagation of a task `env` to a `depends-on` child is unverified, and the patched task-level assertion removes the actual risk; (c) `init` listed in `ci-stack`'s `depends-on` though `start` already depends on it: explicit and harmless.
  - `[medium]` `[patch]` edge-case: the `stack` job hard-codes `COMPOSE_PROFILES: admin,observability`, so a third profile would be linted but never started or smoke-tested — the selftest now pins that value to the set `docker compose config --profiles` declares, so adding a profile reds the gate until the workflow follows.
  - `[medium]` `[patch]` edge-case: `cancel-in-progress` on `main` — same root cause as the blind-hunter row above; same fix.
  - `[medium]` `[patch]` edge-case: the softener scan is a substring match, so `if: ${{ always() }}` slips through — now asserted on parsed YAML that every step's `if` is absent or exactly `failure()`; the substring scan is kept as well.
  - `[medium]` `[patch]` edge-case: only `ci.yml` was scanned, so a second workflow added later would be unchecked — the block now iterates every `*.y*ml` under `.github/workflows` and fails if the directory holds none.
  - `[low]` `[reject]` edge-case: a step using `uses:` bypasses the "every step is a pixi task" rule — true, but an allow-list of actions adds surface, and a new `uses:` is visible in review.
  - `[false]` `[reject]` edge-case: `DEVINFRA_COMPOSE` could be pointed at a no-op to make `lint-compose` report OK having validated nothing — that is the documented test seam (`common.sh:52`, `token.sh:17`); a developer disabling their own gate is not a defect the gate can prevent.
  - `[low]` `[reject]` edge-case: 2^N compose invocations could overrun the bound — 2 profiles today, four invocations, ~2 seconds. Deferred separately as an Epic 2 concern rather than guarded now.
  - `[low]` `[patch]` edge-case: an exported-but-empty `DEVINFRA_COMPOSE` made `shlex.split` return `[]` so `argv[0]` became `config` — reproduced as an uncaught `FileNotFoundError`; now `os.environ.get(...) or "docker compose"`, matching `common.sh`'s `:-`.
  - `[false]` `[reject]` edge-case: a routable LAN `BIND_ADDRESS` passes the NFR-2 check — NFR-2 requires ports to bind to *the configured* `BIND_ADDRESS` and says no service is reachable "by default"; a deliberately configured LAN address is not the default, and the wildcard guard already covers "every interface".
  - `[medium]` `[patch]` edge-case: `.env` inline comments and `export ` broke `dotenv_value` — same root cause as the blind-hunter row; same fix.
  - `[medium]` `[patch]` edge-case: `openssl`/`base64` missing from the preflight — same root cause as the blind-hunter row; same fix.
  - `[medium]` `[patch]` edge-case: an unreachable runtime made `running()` report every service absent, so default mode exited 0 having checked nothing — reproduced (`DEVINFRA_COMPOSE=/usr/bin/false` exited 0 with 10 SKIPs); a `compose version` preflight now fails naming the runtime in both modes.
  - `[low]` `[reject]` edge-case: `ci-stack` with a narrower `.env` fails strict on deliberately excluded services — `smoke-strict` is documented as "what CI runs" and CI selects every profile; asserting the selection would add a guard for a case the task does not claim to serve.
  - `[low]` `[defer]` edge-case: `planted(compose.override.yaml)` raises `FileExistsError` if a developer already has one, crashing the gate with a traceback — pre-existing at `lint_selftest.py:551`, not introduced here.
  - `[false]` `[reject]` edge-case: routing smoke-test through `common.sh` makes an exported value beat `.env` — deliberate, recorded in the Code Map, and required so the CI job's `COMPOSE_PROFILES` wins; it is the precedence every other script in this repository already uses.
  - `[low]` `[reject]` edge-case: the matrix row for an undefined `depends_on` says "the failing combination is named", but `config --profiles` fails first so no combination is reached — the substantive half (compose names the undefined service) holds and is tested against the real runtime; the smallest fix edits this build's spec.
  - `[medium]` `[patch]` edge-case: "profile combinations enumerated, never hard-coded" is true of the lint tasks but not of the `stack` job — same root cause as the `COMPOSE_PROFILES` row; same fix.
  - `[medium]` `[patch]` edge-case: "fails naming what was missing" held only for `curl` — same root cause as the preflight rows; the tool list and the runtime preflight together settle it.
  - `[medium]` `[patch]` verification-gap: the `smoke-strict` *task* was never exercised; deleting its `env` table left every test green while CI would run the skip-tolerant suite — added a `pixi("smoke-strict", ...)` case; mutation-confirmed it now fails.
  - `[medium]` `[patch]` verification-gap: `ci-stack`'s composition and the workflow's binding to it were asserted only as "the name exists" — now pins each job's run list and asserts `ci-stack`'s `depends-on` contains start, wait and smoke-strict.
  - `[medium]` `[patch]` verification-gap: nothing proved `lint-config` was reachable from `pixi run ci` — now asserts every declared `lint-*` task appears in `lint`'s `depends-on`, so no future lint task can be orphaned either.
  - `[medium]` `[patch]` verification-gap: `assert_config.py` clearing `COMPOSE_PROFILES` had no assertion, unlike its shell twin — added a `recorded_env` case and a `STUB_PROFILES_EXIT=1` enumeration-failure case.
  - `[medium]` `[patch]` verification-gap: nothing pinned *when* the workflow runs; `workflow_dispatch` alone would have passed — the trigger map is now asserted to contain `push` and `pull_request`.
  - `[medium]` `[patch]` verification-gap (other): `dotenv_value` vs `common.sh` divergence — same root cause as above; same fix.
  - `[low]` `[patch]` verification-gap (other): the wildcard-`BIND_ADDRESS` guard had no case — same root cause as the blind-hunter coverage row; same fix.
  - `[low]` `[reject]` intent-alignment: the frontmatter is `in-review` with no `operator_actions`, though the intent prescribes `awaiting-operator` plus a non-empty list for AC clauses only a human can satisfy — correct observation; the fix is the Finalize step of this same pass, not a code change.

## Design Notes

**Strict mode is the whole point of the story, not a flag.** `skip()` at `scripts/smoke-test.sh:36` is FR-5's contract — an out-of-selection service must report skipped, not failed — and FR-16's prohibition on silent skipping is the opposite. They are reconciled by *who is asking*: a developer running a partial selection wants skips; CI, which starts every profile, must treat a skip as evidence the stack did not come up. Hence one branch inside `skip()` rather than a second suite:

```sh
skip() {
    if [[ "${SMOKE_STRICT:-}" == "1" ]]; then
        fail "$1" "strict mode: nothing may be skipped"
        return
    fi
    printf '  %s  %s\n' "$(dim SKIP)" "$(dim "$1")"
    SKIP=$((SKIP + 1))
}
```

**Assertions read rendered config, never the file.** NFR-6 exists because `env_file` values do not participate in Compose interpolation, so a port that looks bound to `127.0.0.1` in `compose.yaml` can render as `0.0.0.0`. Verified shape from `docker compose config --format json`: `services.<name>.ports[]` carries `host_ip`, `published`, `target`, `protocol`, and `image` is fully resolved (`pgvector/pgvector:0.8.1-pg17`). Assert against those keys.

**The 15-minute bound is enforced, not measured.** No local run predicts a hosted runner, so `timeout-minutes` is the mechanism: a breach fails the run and forces the tiering the criterion calls for. The two jobs are parallel and independent, so the run's wall clock is the slower job, not their sum.

**Story 1-2 left the real `docker compose` path unexercised** (deferred: every wait case runs against a stub). The `stack` job is the first thing in this repository to drive `wait-healthy.sh` and the argument-free lifecycle tasks against a real runtime, which is the deferred item's stated remedy.

## Verification

**Commands:**
- `pixi run ci` -- expected: exits 0; new selftest cases appear as `lint-selftest: PASS`.
- `pixi run lint-compose` -- expected: exits 0 and reports four combinations (none, admin, observability, both) against `docker compose config --profiles` output of `admin` and `observability`.
- `pixi run lint-config` -- expected: exits 0; every rendered port shows `host_ip` `127.0.0.1` and every image an explicit tag.
- `SMOKE_STRICT=1 DEVINFRA_COMPOSE=/bin/echo ./scripts/smoke-test.sh` -- expected: non-zero, output contains `FAIL` and no `SKIP`. (A *reachable* stub: the runtime preflight added at review rejects `/bin/true` and `/usr/bin/false` before any check runs, which is its point.)
- `./scripts/smoke-test.sh` with the same stub -- expected: exits 0 with `SKIP` lines, proving default scoring is untouched.
- `DEVINFRA_COMPOSE=/usr/bin/false ./scripts/smoke-test.sh` -- expected: non-zero, naming the unreachable runtime, in default mode as well as strict.
- `pixi run smoke-strict` against the stack already running locally (all profiles up) -- expected: exits 0 with zero skips, proving strict mode passes on a genuinely complete stack.
- `grep -nE 'continue-on-error|\|\| true|command -v|if: always' .github/workflows/ci.yml` -- expected: no match.
- `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` and `pixi run lint-yaml` -- expected: the workflow parses and satisfies `.yamllint.yaml`.

**Manual checks (if no CLI):**
- The workflow's first real run on GitHub must be observed green before the ruleset gate is added; the check context names come from the job names in `ci.yml`.

## Auto Run Result

Status: awaiting-operator
Blocking condition: none — every part an agent can perform is complete and committed; three operator actions remain, enumerated under `operator_actions` in the frontmatter.

### Implemented change

This repository had no CI. `.github/` held agent definitions and nothing else, so a pull request that broke a service, deleted a `depends_on` target or published a port on every interface merged unchallenged. The change adds a GitHub Actions workflow whose every step is a `pixi run <task>` invocation — no tool version, no package-manager install — so the checks a hosted runner performs are the ones a contributor runs locally, at the versions `pixi.lock` pins. Two jobs run in parallel: `validate` (`pixi run ci`) and `stack` (`pixi run ci-stack`, which starts every profile, blocks on health, and runs the smoke suite in strict mode). Around it, three gaps in the existing surface are closed: `lint-compose` grew from one profile pair to every combination the model declares; a new `lint-config` asserts the *rendered* configuration's bind addresses, host-port collisions and image pinning; and the smoke suite gained a strict mode in which a skipped service is a failure, plus a preflight that fails naming a missing tool or an unreachable container runtime.

### Files changed

- `.github/workflows/ci.yml` -- new. Two parallel, time-bounded jobs on push to `main` and every pull request; read-only token; concurrency cancels superseded pull-request runs only.
- `scripts/lint-compose.sh` -- new. Reads the declared profiles from the model, walks the power set, runs `config -q` per combination, continues past a failure and lists every failing one.
- `scripts/assert_config.py` -- new. Per combination, parses `config --format json` and asserts bind address (NFR-2), host-port uniqueness (AD-17) and explicit non-`latest` tags (NFR-4); refuses an empty service set.
- `scripts/smoke-test.sh` -- sources `lib/common.sh` so the runtime goes through the `compose` seam; preflights `curl`, `openssl`, `base64` and the runtime itself; `SMOKE_STRICT=1` scores a skip as a failure. Default scoring is untouched.
- `pixi.toml` -- `lint-compose` repointed; new `lint-config`, `smoke-strict`, `ci-stack`; `lint` gained `lint-config`; `lint-yaml` now covers the workflow; dev dependencies gained `pyyaml`/`types-pyyaml` for the workflow self-test.
- `scripts/lint_selftest.py` -- recorder extended for `--profiles` and `config --format json`; 362 assertions total, covering every new contract and the workflow's own shape.
- `README.md` -- a `## Continuous integration` section, the new tasks and scripts, and Docker corrected as a requirement of `pixi run lint`, not only of running the stack.
- `pixi.lock` -- regenerated for the two added dev dependencies.

### Review findings

Four layers reported 39 findings. Verdicts: 0 high, 17 medium, 15 low, 4 false, 0 maybe-false. No `intent_gap` and no `bad_spec`, so no loopback.

**Patched — 11 entries by root cause (9 medium, 2 low):** the `smoke-strict` task's `env` was never exercised (deleting it left every test green while CI would have run the skip-tolerant suite); the workflow self-test asserted only negatives, so deleting the `stack` job, rewiring it to `pixi run ps`, dropping `lint-config` from the `lint` chain, or replacing the triggers with `workflow_dispatch` all passed; `if: ${{ always() }}` slipped past a substring scan; only `ci.yml` was scanned rather than every workflow; the `stack` job's hard-coded `COMPOSE_PROFILES` is now pinned to the declared profile set; `assert_config.py`'s `COMPOSE_PROFILES` clearing had no assertion and its `.env` reader diverged from `source` on `export ` and inline comments; an exported-but-empty `DEVINFRA_COMPOSE` raised an uncaught traceback; the preflight named only `curl` though the suite also shells to `openssl` and `base64`; an unreachable runtime made the default suite exit 0 having checked nothing; `cancel-in-progress` cancelled `push` runs on `main`; the workflow inherited default token scopes. Every added assertion was mutation-confirmed to fail when the property it guards is removed.

**Deferred — 3 items** (see frontmatter `deferred`): the power set implemented twice with nothing asserting agreement; 2^N enumeration will not survive Epic 2 giving every service a profile; and the pre-existing `planted(compose.override.yaml)` crash if a developer already has that file.

**Rejected findings and reasons:** `operator_actions` missing from the frontmatter and the status not yet `awaiting-operator` (two findings, blind-hunter and intent-alignment) -- correct, but the fix is this Finalize step, not a code change. The `if: failure()` logs step repeating the profile list -- a diagnostic that runs only after the job has failed; `scripts/logs.sh` hard-codes `-f` and would hang the job, so it cannot be reused. SHA-pinning the two actions -- `@v4`/`@v0.8.1` is this repository's idiom and the change is more than a direct correction. `type -P` passing the `FORBIDDEN` string ban -- the preflight has the right polarity (it fails, never skips); broadening the ban would need an allow-list for it. An allow-list for `uses:` steps -- adds surface for something visible in review. `DEVINFRA_COMPOSE` pointed at a no-op defeating `lint-compose` (`false`) -- that is the documented test seam; a developer disabling their own gate is not preventable by the gate. A routable LAN `BIND_ADDRESS` passing NFR-2 (`false`) -- NFR-2 requires ports to match *the configured* address and says no service is reachable "by default"; the wildcard guard already covers every-interface. Smoke-test's new environment-beats-`.env` precedence (`false`) -- deliberate, recorded in the Code Map, and the precedence every other script here already uses. `ci-stack` failing strict on a narrower local `.env` -- `smoke-strict` is documented as what CI runs, and CI selects every profile. The I/O matrix row claiming a failing combination is named when `config --profiles` itself fails -- the substantive half (compose names the undefined service) holds and is tested against the real runtime; the smallest fix would edit this build's spec. 2^N invocations overrunning the bound -- four invocations today, deferred rather than guarded. `smoke-strict` restating `cmd` instead of depending on `smoke`, and `init` listed redundantly in `ci-stack` -- cosmetic; pixi's `env` propagation to a `depends-on` child is unverified and the patched task-level assertion removes the real risk.

### Follow-up review

`followup_review_recommended: true`. Nine `medium` entries were patched on a first pass. The specific unverified risk: **no hosted run has ever executed.** Every verification here ran on macOS/arm64 against a stack that was already warm — the 15-minute bound, whether `ubuntu-latest` pulls and starts fourteen containers inside it, and whether `wait-healthy.sh`'s real-runtime path holds on x86 Linux with a cold image cache are all unobserved. The first hosted run is the evidence that settles them, and it is the first `operator_actions` item.

### Verification performed

- `pixi run ci` -- exit 0; 362 self-test assertions pass, 0 failures.
- `pixi run lint-compose` -- exit 0, four combinations: `(none)`, `admin`, `observability`, `admin,observability`.
- `pixi run lint-config` -- exit 0 over the same four; bind address `127.0.0.1` throughout.
- `SMOKE_STRICT=1 DEVINFRA_COMPOSE=/bin/echo ./scripts/smoke-test.sh` -- exit 1, 10 FAIL, 0 SKIP. Same stub without the flag -- exit 0, 0 FAIL, 10 SKIP: default scoring is untouched.
- `DEVINFRA_COMPOSE=/usr/bin/false ./scripts/smoke-test.sh` -- exit 1 in both modes, naming the unreachable runtime. Before the review patch this exited 0 with ten skips.
- `pixi run smoke-strict` against the live full stack (all fourteen services up) -- **45 passed, 0 failed, 0 skipped**. This is also the first time `wait-healthy.sh` and the argument-free lifecycle tasks have been driven against a real runtime, which is Story 1-2's deferred "real `docker compose` path unexercised" item.
- `grep -nE 'continue-on-error|\|\| true|command -v|if: always' .github/workflows/ci.yml` -- no match. `yaml.safe_load` parses it; triggers are `push` and `pull_request`; `pixi run lint-yaml` exit 0.
- Empirically confirmed the load-bearing assumption behind the whole enumeration: with `.env` selecting both profiles, `docker compose config --services` renders 14 services and `COMPOSE_PROFILES= docker compose config --services` renders 6. A real-runtime self-test case now pins it.
- Matrix test audit: all nine I/O matrix rows are covered by named self-test cases that ran and passed in the output above.
- Confirmed `pixi run ci` leaves a pre-existing `.env` byte-identical (canary file survived a full gate run).

### Residual risks

- **The hosted run is unobserved** — the follow-up risk named above. The workflow's correctness on `ubuntu-latest` rests on that runner shipping the Compose plugin, which nothing here asserts because nothing here can.
- **The merge gate is not attached.** Ruleset `protect-default-branch` carries no `required_status_checks` rule, verified live. Until an operator adds one, CI reports but does not gate, and story 1.3's "the result gates merge" is unsatisfied. Deliberately not done from this unattended run: it mutates live repository settings, it would immediately apply to every other in-flight branch, and a check context named before it has ever reported blocks all pull requests permanently.
- **The 15-minute bound is enforced, not measured.** `timeout-minutes` turns a breach red rather than letting it run long; whether the real duration is 4 minutes or 14 is unknown until the first hosted run.
- Three deferred items are recorded in the frontmatter, all `low`.

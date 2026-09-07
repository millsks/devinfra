---
title: 'Lifecycle logic extracted into testable scripts'
type: 'refactor'
created: '2026-09-06'
status: 'done'
baseline_revision: '9caff822495995d99283c16a62f093b6bfb2ae94'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/implementation-artifacts/spec-1-1-validation-tooling-that-cannot-silently-skip.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
deferred:
  - summary: >-
      urls.sh omits Loki, Tempo and the Keycloak management port, all published in compose.yaml.
    evidence: |-
      compose.yaml publishes LOKI_PORT:-3100, TEMPO_PORT:-3200 and KEYCLOAK_MGMT_PORT:-9000, and
      all three are in .env.example, but urls.sh prints none of them and the self-test now codifies
      the incomplete list as expected output. Inherited from the Makefile; adding them is a
      behaviour change this extraction story's contract forbids.
    location: >-
      scripts/urls.sh
    severity: medium
  - summary: >-
      `make logs S="postgres redis"` used to tail both services and now fails.
    evidence: |-
      The deleted recipe passed $(S) straight to `docker compose logs`; the pixi logs task declares
      a single `service` argument, so two positional arguments are rejected. The single-service form
      documented in README is unaffected. Fixing it means a variadic task argument, a surface change.
    location: >-
      pixi.toml [tasks.logs]
    severity: medium
  - summary: >-
      restore.sh does not verify the archive is valid gzip before applying it.
    evidence: |-
      A corrupt or truncated .gz that exists passes the -f check; psql then applies a partial dump.
      The backup.sh partial-file fix removes the most likely source of such a file. A `gzip -t`
      precheck is a behaviour addition beyond this extraction story.
    location: >-
      scripts/restore.sh
    severity: medium
  - summary: >-
      The 60x5s health-wait bound and real-runtime behaviour are asserted nowhere.
    evidence: |-
      Every wait case runs with WAIT_ATTEMPTS=2, WAIT_INTERVAL=0 and DEVINFRA_COMPOSE pointed at a
      stub, so the shipped configuration and the real `docker compose` path are unexercised.
      Settling it needs a running stack, which is Story 1-3's remit.
    location: >-
      scripts/wait-healthy.sh
    severity: medium (unverified)
  - summary: >-
      `pixi run wait` and 16 other declared tasks are never invoked in the gate.
    evidence: |-
      The lifecycle scripts are driven directly and the argument-taking tasks through `pixi run`,
      but tasks like wait, up, down and destroy are not executed. No mutation produced a case where
      the task and its script disagreed, so the added signal is unproven rather than absent.
    location: >-
      pixi.toml
    severity: medium (unverified)
  - summary: >-
      `pixi run wait` now exits non-zero when nothing is running; the old loop exited 0.
    evidence: |-
      A deliberate improvement demanded by NFR-3's "never returns optimistically", but a change to
      what a target does, which this story's contract otherwise forbids, and not covered by the
      epic text. Recorded so it is visible rather than shipped silently.
    location: >-
      scripts/wait-healthy.sh
    severity: low
---

<intent-contract>

## Intent

**Problem:** The stack's lifecycle logic lives inside Makefile recipes — a 60-iteration health-wait loop (`Makefile:78-87`), a 14-line endpoint printf block (`Makefile:106-119`), and two `read -p` confirmations — where it cannot be run, tested, or reused except through `make`. Story 1-1 made the task surface honest but left every target except `lint` on the old surface.

**Approach:** Move each non-trivial recipe into its own `scripts/*.sh` file that runs standalone, give every current Makefile target a pixi task equivalent — with the five argument-taking targets translated to pixi task arguments rather than dropped — and reduce the Makefile to forwarding shims that print a deprecation notice.

## Boundaries & Constraints

**Always:** Every script is `set -euo pipefail`, shellcheck-clean under `pixi run lint`, runnable directly as `./scripts/<name>.sh`, and carries at least one test exercising its contract that runs inside `pixi run ci`. Every target the Makefile exposes today keeps working through the Makefile and gains a pixi equivalent. `logs S=`, `psql DB=`, `redis-cli N=`, `restore F=` and `token U= P=` keep their behaviour through pixi task arguments. The health-wait script blocks until every container is healthy and exits non-zero on timeout, listing what was not ready. Scripts that read configuration load `.env` themselves — pixi, unlike make, does not.

**Never:** Do not change what any target does, only where its logic lives and how it is invoked; a behaviour change is a separate story. Do not touch `compose.yaml` service definitions or any pinned image tag. Do not add a CI workflow (Story 1-3) or commit-time hooks (Story 1-7). Do not weaken the destroy or realm-reimport confirmations into something a non-interactive run can satisfy by accident. Do not reintroduce any construct that lets a check or a wait report success having done nothing — no `command -v` branches, no `|| true`, no swallowed failures.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Health wait, all healthy | Every container running, every healthcheck healthy | Exits 0 reporting all containers ready | No error expected |
| Health wait, timeout | A container stays unhealthy past the deadline | Exits non-zero and lists each container that was not ready | Names the containers; never exits 0 optimistically |
| Health wait, nothing running | No containers for the project | Exits non-zero rather than treating an empty set as success | Says nothing is running |
| Destroy confirmation, agreed | Exactly `destroy` on stdin | Proceeds to remove containers and volumes | No error expected |
| Destroy confirmation, refused | Any other input, including empty | Aborts without touching volumes, exits non-zero | Prints that it aborted |
| Endpoint listing without `.env` | `.env` absent | Prints every endpoint using the same defaults `compose.yaml` interpolates | No error; defaults are the documented fallback |
| Restore, missing argument | No file given | Exits non-zero with usage | Never invokes psql |
| Restore, nonexistent file | A path that does not exist | Exits non-zero naming the path | Never invokes psql |
| Task argument passthrough | `pixi run psql keycloak`, `pixi run redis-cli 1`, `pixi run logs keycloak` | The argument reaches the underlying command | A required argument omitted fails loudly |
| Task argument default | `pixi run psql` with no argument | Falls back to the same default the Makefile used | No error expected |

</intent-contract>

## Code Map

- `Makefile:75-87` -- `wait`: 60 iterations of `compose ps` parsed by awk for `restarting|dead|paused` state or a non-`healthy` health value; 5s sleep; on timeout prints a `compose ps` table and exits 1. The extraction target for `scripts/wait-healthy.sh`; NFR-3 lives here.
- `Makefile:104-119` -- `urls`: 14 printf lines over `POSTGRES_USER/PORT/DB`, `REDIS_PASSWORD/PORT`, `KEYCLOAK_PORT/REALM`, `MINIO_CONSOLE_PORT/API_PORT`, `MAILPIT_UI_PORT/SMTP_PORT`, `PGADMIN_PORT`, `REDISINSIGHT_PORT`, `FLOWER_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `OTEL_GRPC_PORT/HTTP_PORT`. Target for `scripts/urls.sh`.
- `Makefile:65-69` -- `destroy`: `read -p "Type 'destroy' to confirm: "` then `down -v`. Target for `scripts/destroy.sh`.
- `Makefile:152-162` -- `keycloak-reimport`: `read -p "Type 'reimport' to confirm: "`, stops keycloak, drops and recreates the database, restarts, waits. Target for `scripts/keycloak-reimport.sh`.
- `Makefile:139-150` -- `backup` (mkdir, timestamped filename, `pg_dumpall | gzip`, size report) and `restore` (argument check, `gunzip | psql`). Targets for `scripts/backup.sh` and `scripts/restore.sh`.
- `Makefile:12-15` -- `include $(ENV_FILE)` + `export`. **This is the load-bearing difference.** Verified: `pixi run sh -c 'echo $COMPOSE_PROFILES'` prints nothing, so every `.env` value the recipes rely on at shell level disappears under pixi. `docker compose` still interpolates `.env` on its own, so compose-only targets are unaffected; the shell-level users are `urls`, `psql`, `redis-cli`, `token`, `backup`, `restore` and `keycloak-reimport`.
- `scripts/smoke-test.sh:16-17` -- the established `.env` loading pattern: `# shellcheck disable=SC1091` then `[[ -f .env ]] && set -a && source .env && set +a`. Reuse the intent, but as an `if` block — the `&&` chain returns 1 under `set -e` when `.env` is absent.
- `Makefile:17-20` -- the four defaults the Makefile supplies when `.env` omits them: `POSTGRES_USER`, `POSTGRES_DB`, `REDIS_PASSWORD`, `KEYCLOAK_REALM` all `?= devinfra`. Port defaults live only in `compose.yaml`'s `${VAR:-N}` forms; `.env.example` carries the rest.
- `Makefile:22-31` -- `help` greps `$(firstword $(MAKEFILE_LIST))` for `## ` comments. The pixi equivalent is `pixi task list`, which already renders the `description` fields Story 1-1 added.
- `pixi.toml` -- Story 1-1's surface: `lint-compose`, `lint-shell`, `lint-yaml`, `lint-json`, `lint-python`, `lint`, `test`, `ci`. Task arguments verified working on pixi 0.70.2 with `args = [{ arg = "x", default = "y" }]` and `{{ x }}` interpolation; a missing required argument fails loudly.
- `scripts/lint_selftest.py` -- 44 cases, the `test` task, and the place new script tests belong. Its `planted()` and `moved_aside()` context managers are the pattern for fixtures that must not leave the tree dirty.
- `scripts/lint_json.py` -- the precedent for a Python helper in `scripts/`; `lint-python` (ruff + mypy) already covers `scripts/`, so any new `.py` must satisfy it.
- `README.md:170-200` -- the command table, still written in `make` verbs apart from the lint entries. `README.md:161-175` -- the structure listing.
- `AGENTS.md:24` -- "`make smoke` is the verification, not `make ps`". Read-only here: agent-context edits were deferred in Story 1-1 for the same reason and stay deferred.
- `docker/postgres/initdb/20-extra-databases.sh` -- read-only; the existing example of the house style (`set -euo pipefail`, comment header explaining the contract).

## Tasks & Acceptance

**Execution:**
- `scripts/lib/common.sh` -- add a tiny sourced helper providing `.env` loading and the four `?=` defaults, so seven scripts do not each re-derive it -- one source of truth for configuration resolution, and the only way the pixi surface can match the Makefile's behaviour.
- `scripts/wait-healthy.sh` -- extract `Makefile:75-87`, preserving the 60-iteration/5-second bound, the state and health parsing, and the non-zero timeout that lists what was not ready. Read the compose command from an overridable variable (defaulting to `docker compose`) so the timeout path is testable without an unhealthy stack -- NFR-3 is the contract this story must prove, not merely relocate.
- `scripts/urls.sh` -- extract `Makefile:104-119` verbatim in output, sourcing `.env` and falling back to the same defaults `compose.yaml` interpolates -- the endpoint list must not silently lose a service.
- `scripts/destroy.sh`, `scripts/keycloak-reimport.sh` -- extract the two `read -p` confirmations, each requiring its exact word and exiting non-zero on anything else -- these are the only irreversible operations in the repository.
- `scripts/backup.sh`, `scripts/restore.sh` -- extract `Makefile:139-150`; `restore.sh` refuses a missing or nonexistent file before invoking psql -- a restore that starts and fails halfway is worse than one that refuses.
- `pixi.toml` -- add a task for every current Makefile target, with `args` for `logs`, `psql`, `redis-cli`, `restore` and `token`, and `depends-on` chains where the Makefile used `$(MAKE)` (`up` → start, wait, urls; `restart` → down, up) -- the acceptance criterion is parity, target for target.
- `scripts/lint_selftest.py` -- add contract cases for each new script: the health-wait timeout path lists what was not ready and exits non-zero, the empty-container case fails rather than passing, both confirmations refuse every input but their exact word, `restore.sh` refuses a missing and a nonexistent file, `urls.sh` prints every service with `.env` absent, and each argument-taking task passes its argument through and applies its default -- every script must ship with a test that runs in the gate.
- `Makefile` -- reduce every target to a forwarding shim that prints a deprecation notice on stderr and calls its pixi task, keeping the target names, the `##` help text and the argument variables working -- existing muscle memory must keep working for one release.
- `README.md` -- rewrite the command table in `pixi run` verbs with the `make` equivalents noted as deprecated, and add the new scripts to the structure listing -- the documented command must be the one the project supports.

**Acceptance Criteria:**
- Given the health-wait, endpoint and confirmation logic, when the extraction is complete, then each lives in its own `scripts/*.sh`, runs standalone, and `pixi run lint` reports it shellcheck-clean.
- Given the Makefile, when it is read, then no recipe contains a loop, a conditional, a `read`, or more than a single forwarding command plus its notice.
- Given every target the Makefile exposed before this story, when `pixi task list` is read, then each has an equivalent, and `pixi run logs keycloak`, `pixi run psql keycloak`, `pixi run redis-cli 1`, `pixi run restore <file>` and `pixi run token dev dev` all reach the same command the Makefile built.
- Given a developer with existing muscle memory, when they run a former `make` target, then it still works and prints a deprecation notice on stderr, leaving stdout clean.
- Given a stack where one container never becomes healthy, when the health-wait script runs, then it exits non-zero and its output names that container.
- Given `.env` is absent, when `pixi run urls` runs, then every service endpoint still prints using the defaults `compose.yaml` interpolates.
- Given `pixi run ci`, when it runs, then it exits 0 and the self-test covers every new script's contract.

## Spec Change Log

## Review Triage Log

### 2026-09-06 — Review pass
- verdicts: 50 findings — high 4, medium 24, low 15, false 4, maybe-false 3
- findings:
  - `[medium]` `[patch]` blind: common.sh cds to the repo root before restore.sh resolves `$1`, so a relative path from another directory misresolves — verified; the argument is now resolved against `$PWD` before the cd, with a case covering it.
  - `[high]` `[patch]` blind: `set -a; source .env` clobbers variables the caller exported, and up-core.sh's cleared `COMPOSE_PROFILES` is restored when wait-healthy re-sources — real; common.sh now snapshots `export -p` and replays it, so environment beats dotenv.
  - `[high]` `[patch]` blind: the FORBIDDEN scan reads only pixi task cmds, and this story moved the logic into scripts, so `|| true` in a script passed the gate — verified; the scan now covers `scripts/**/*.sh`.
  - `[high]` `[patch]` blind: no test asserts a make target forwards to its own task — verified directly, rewiring `make destroy` to `pixi run down` passed; replaced with per-target equality against an explicit table.
  - `[medium]` `[patch]` blind: nothing verified the deprecation notice goes to stderr, an explicit acceptance criterion — verified; `NOTICE` is now checked for `>&2` and mutation-tested.
  - `[high]` `[patch]` blind: six new scripts shipped with no test, `init-env.sh`'s never-overwrite guard among them, and it runs on every `pixi run up` — verified by inverting the guard; cases added for all six.
  - `[medium]` `[defer]` blind: urls.sh omits Loki, Tempo and the Keycloak management port — real and confirmed against compose.yaml, but the omission is inherited and this story's contract forbids behaviour changes; deferred.
  - `[low]` `[reject]` blind: port defaults are duplicated across urls.sh, token.sh and the self-test — real, but deriving them by parsing compose.yaml is more machinery than the drift risk warrants while the values live in one `.env.example`.
  - `[medium]` `[patch]` blind: lint-shell's non-recursive globs leave a script in a new subdirectory unlinted while the self-test uses rglob — verified; the glob is now recursive and the two are asserted to agree.
  - `[medium]` `[patch]` blind: backup.sh's redirection creates the archive before the pipeline, so a failed dump leaves a truncated file restore.sh would accept — verified; writes to `.partial` and moves on success.
  - `[medium]` `[patch]` blind: pixi's `{{ }}` interpolation is unquoted, so a path containing a space word-splits — verified; the five tasks quote their interpolation and a space-containing argument is now a case.
  - `[low]` `[defer]` blind: smoke-test.sh keeps its own cd and `.env` loader rather than sourcing common.sh — real duplication, but rewriting the smoke test is outside an extraction story.
  - `[low]` `[reject]` blind: token.sh re-derives the `DEVINFRA_CURL` seam locally rather than in common.sh — one other user would justify the move; with one, centralising it adds indirection for no reduction in duplication.
  - `[medium]` `[patch]` blind: unguarded `split(...)[1].splitlines()[1]` raises IndexError and aborts the whole suite rather than failing one case — verified; replaced with guarded helpers asserting the exact not-ready set.
  - `[low]` `[reject]` blind: the spec artifact carries in-progress status and an oversized warning — rejected; the fix edits this build's spec.
  - `[low]` `[defer]` blind: destroy.sh prints its irreversibility warning to stdout while "Aborted." goes to stderr — cosmetic stream inconsistency, no behavioural harm.
  - `[high]` `[patch]` edge: `.env` overrides the caller's environment — same defect as the common.sh row above; one fix.
  - `[high]` `[patch]` edge: up-core's profile clearing is undone by wait-healthy re-sourcing — same root cause; the export snapshot fixes it and a recorded-environment case now proves it.
  - `[low]` `[reject]` edge: WAIT_ATTEMPTS is not validated as a positive integer — it is a test seam, not a user-facing input; the fix adds a branch guarding state never shown reachable.
  - `[medium]` `[patch]` edge: an exited or crashed container is absent from `compose ps`, so the loop reports all-running and exits 0 — verified; readiness is now judged against `compose config --services` with `ps --all`.
  - `[medium]` `[patch]` edge: a failed dump leaves a truncated archive — same as the backup.sh row; one fix.
  - `[medium]` `[patch]` edge: a relative restore path misresolves — same as the restore.sh row; one fix.
  - `[medium]` `[defer]` edge: restore.sh does not `gzip -t` the archive before applying it — real, and the partial-archive fix removes the most likely source; validating arbitrary corrupt input is a behaviour addition beyond this story.
  - `[false]` `[reject]` edge: quoting `POSTGRES_USER` in the CREATE DATABASE identifier would break case-folding — the script carries the identifier exactly as the Makefile did, unquoted; the claimed change is not present.
  - `[medium]` `[patch]` edge: unquoted task interpolation word-splits an argument — same as the pixi.toml row; one fix.
  - `[false]` `[reject]` edge: `depends-on` has no edges so wait can run before start — refuted by direct test: pixi runs a depends-on list sequentially in order; a deliberately slow first task still completed before the second began.
  - `[medium]` `[patch]` edge: an unguarded IndexError aborts the suite — same as the guards row; one fix.
  - `[low]` `[patch]` edge: a whitespace-only recipe line raises IndexError in the Makefile check — same root cause; the guarded `first_word()` helper covers it.
  - `[medium]` `[defer]` edge: `make logs S="a b"` used to tail two services and now hands pixi two positional arguments — real regression for an undocumented multi-service form; recorded rather than fixed, since a variadic task argument is a surface change.
  - `[false]` `[reject]` edge: the removed `help` recipe's "Profiles:" hint is gone from the CLI — refuted: `pixi task list` renders the descriptions, and the profiles hint survives in README's profile section.
  - `[high]` `[patch]` edge: six scripts have no contract test — same as the blind row; cases added for all six.
  - `[high]` `[patch]` edge: up-core's header claims it clears profiles for both phases when it does not — same root cause as the export snapshot; the header is now accurate and asserted.
  - `[medium]` `[patch]` edge: wait-healthy's header claims it never returns optimistically while a crashed service yields exit 0 — same as the readiness row; one fix.
  - `[high]` `[patch]` verification-gap: `.env` loading, the entire reason common.sh exists, is never exercised because the whole block runs with `.env` moved aside — verified: flipping `set -a` to `set +a` left the suite green; a planted-`.env` block plus a child-process environment assertion now catch both.
  - `[high]` `[patch]` verification-gap: init-env.sh's never-overwrite guard has no test and runs on every `pixi run up` — verified by inverting it; two cases added.
  - `[high]` `[patch]` verification-gap: the shim check compares forwarded names to a set, never to the target — same as the blind row; per-target equality.
  - `[medium]` `[patch]` verification-gap: up-core's profile clearing is untested — same root cause; a recorded-environment case now covers it.
  - `[medium]` `[patch]` verification-gap: backup.sh and keycloak-export.sh build compose commands nothing asserts — verified by swapping the `cp` operands; argv cases added for both.
  - `[medium]` `[patch]` verification-gap: the `.env` re-source undoes up-core's clearing — same as above; one fix.
  - `[medium]` `[defer]` verification-gap: `make logs S="a b"` regression — same as the edge row; deferred.
  - `[medium]` `[reject]` intent: the make surface is parsed, never executed — real as stated, but running `make` in the gate would require Make on every runner; the per-target equality table plus the NOTICE check close the failure modes that motivated it, and both are mutation-tested.
  - `[high]` `[patch]` intent: make-side argument forwarding is guarded only by a first-word comparison, the exact thing flagged as easiest to leave half-done — verified by dropping `$(S)`; the table now pins the full forward line.
  - `[medium]` `[reject]` intent: the parity check is anchored on the rewritten Makefile, so a target deleted in this commit would vanish from both sides — addressed in substance by the explicit expected-target table, which is now the pinned inventory rather than a scan of the current file.
  - `[maybe-false]` `[defer]` intent: 17 declared tasks are never invoked in the gate, so NFR-3 is proven for the script and not for `pixi run wait` — the argument-taking tasks and the lifecycle scripts are both covered; whether task-level invocation adds signal beyond that needs a case where the task and script disagree, which none of the mutations produced.
  - `[false]` `[reject]` intent: sequencing moved to an unexercised declarative construct that may not preserve order — refuted by the same ordering test as above; pixi executes a depends-on list in order.
  - `[maybe-false]` `[defer]` intent: the tested configuration is not the shipped one — the 60x5s bound is asserted nowhere and the scripts are exercised only against a stub runtime; settling it needs a real stack, which is Story 1-3's job.
  - `[medium]` `[patch]` intent: the notice is asserted as a token, not as output — same as the NOTICE row; one fix.
  - `[low]` `[defer]` intent: wait now exits non-zero when nothing is running, where the old loop exited 0 — a deliberate behaviour change the epic text does not cover; recorded so it is visible rather than silently shipped.
  - `[maybe-false]` `[defer]` intent: a target added without a `## ` description is invisible to the AD-9 checks — real for a hypothetical undocumented target; the expected-target table bounds it for every target that exists today.
  - `[low]` `[reject]` intent: the diff's Code Map cites Makefile line ranges this diff deletes — rejected; the fix edits this build's spec.

## Design Notes

**pixi does not load `.env`; make did.** This is the one thing that can silently break the migration. `include .env` + `export` at `Makefile:12-15` put every tunable into each recipe's environment; pixi tasks inherit no such thing — verified directly, `COMPOSE_PROFILES` and `BIND_ADDRESS` are both unset inside `pixi run`. `docker compose` reads `.env` itself, so compose-only targets survive untouched, but every recipe that interpolated a value at shell level must now load it. Hence `scripts/lib/common.sh`: sourced by the scripts that need configuration, carrying the loader and the four `?=` defaults.

```sh
# scripts/lib/common.sh — the pattern, not the whole file
if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi
: "${POSTGRES_USER:=devinfra}" "${POSTGRES_DB:=devinfra}"
```

Note the `if` rather than smoke-test.sh's `[[ -f .env ]] && ...` chain: under `set -e` that chain exits the script when `.env` is absent, which is exactly the "no `.env`" row of the matrix.

The health-wait script takes its compose command from an overridable variable so the timeout path can be driven by a stub that reports a permanently-unhealthy container. Without that seam the only way to test NFR-3 is to break a real stack and wait a minute, which means in practice it would never be tested — and an untested wait loop that returns optimistically is the same class of defect as Story 1-1's skipping lint.

Story 1-1's lesson applies directly: test the surface that ships, not a re-implementation of it. Argument passthrough cases must invoke `pixi run psql …` and assert on the command that would run, not re-derive the argument handling in the test.

## Verification

**Commands:**
- `pixi run ci` -- expected: exits 0; the self-test covers each new script's contract.
- `pixi run lint-shell` -- expected: every new `scripts/*.sh` is shellcheck-clean.
- `for t in init up up-core down stop restart destroy pull wait ps logs smoke urls psql redis-cli mc backup restore keycloak-reimport keycloak-export token config help; do pixi task list | grep -q "$t" || echo "MISSING $t"; done` -- expected: no output.
- `grep -nE '^\t.*(if |for |while |read |&&|\|\|)' Makefile` -- expected: no match outside the forwarding notice.
- `printf 'no\n' | ./scripts/destroy.sh` -- expected: non-zero, aborts, no volume touched.
- `./scripts/restore.sh` and `./scripts/restore.sh /nonexistent.gz` -- expected: non-zero with usage and with the path named, psql never invoked.
- `env -u POSTGRES_USER sh -c 'cd $(mktemp -d) && :'` then `./scripts/urls.sh` with `.env` moved aside -- expected: every endpoint still printed.
- `make wait 2>/dev/null` -- expected: forwards, stdout carries no deprecation notice.

## Auto Run Result

Status: done
Blocking condition: none

### Implemented change

Every non-trivial Makefile recipe now lives in its own `scripts/*.sh`, runnable standalone and shellcheck-clean. `scripts/lib/common.sh` carries the one thing the migration turns on: pixi, unlike make, does not load `.env`, so scripts load it themselves — and the caller's environment beats it. Every former Makefile target has a pixi task, with `logs`, `psql`, `redis-cli`, `restore` and `token` translated to quoted pixi task arguments. The Makefile is reduced to per-target forwarding shims that print a deprecation notice on stderr.

### Files changed

- `scripts/lib/common.sh` — `.env` loading with an `export -p` snapshot so the environment wins, the four `?=` defaults, and the `compose` seam.
- `scripts/wait-healthy.sh` — the health loop; readiness judged against `compose config --services` with `ps --all`, so an exited or absent container is not ready. NFR-3.
- `scripts/urls.sh`, `destroy.sh`, `keycloak-reimport.sh`, `backup.sh`, `restore.sh` — the extractions the story names. `backup.sh` writes to `.partial` and moves on success; `restore.sh` resolves a relative path before the cd.
- `scripts/init-env.sh`, `up-core.sh`, `ps.sh`, `logs.sh`, `psql.sh`, `redis-cli.sh`, `mc.sh`, `token.sh`, `keycloak-export.sh` — needed for parity because pixi does not load `.env`.
- `pixi.toml` — a task per former target; quoted argument interpolation; recursive `lint-shell` glob.
- `Makefile` — every recipe is a notice plus one forward. No loop, conditional or `read` remains.
- `scripts/lint_selftest.py` — 277 cases, up from 225.
- `.gitignore` — `!scripts/lib/` unignores `common.sh`, which the stock Python template's `lib/` rule was excluding.
- `README.md` — command table in pixi verbs with the make equivalents marked deprecated.

### Review findings

Four layers reported 50 findings: 4 high, 24 medium, 15 low, 4 false, 3 maybe-false.

- **Patched (12 fixes across 9 grouped entries; 4 high, 4 medium, 1 low at entry verdict).** The high entries were all the same shape — the gate asserted structure where it needed to assert behaviour. Verified directly before fixing: flipping `set -a` to `set +a` in `common.sh` left the whole suite green, so the helper's reason for existing was unverified; rewiring `make destroy` to `@pixi run down` passed, so a data-preserving command could stand in for one that deletes every volume; `|| true` inside an extracted script passed, because the forbidden-construct scan only read pixi task bodies; and `init-env.sh`'s never-overwrite guard — which runs on every `pixi run up` — had no test at all. All four now fail the gate when broken, along with 12 further mutations covering the notice's stderr redirection, dropped `$(S)`-style argument forwarding, narrowed lint globs, swapped `cp` operands, and unquoted task interpolation.
- **Deferred (6).** `urls.sh` omitting Loki, Tempo and the Keycloak management port; the `make logs S="a b"` multi-service regression; `restore.sh` not validating gzip; the shipped 60×5s bound and the real `docker compose` path being unexercised; 17 declared tasks never invoked in the gate; and `wait` now failing when nothing is running where the old loop passed.
- **Rejected (9).** Four refuted outright: pixi runs a `depends-on` list sequentially in order (tested directly, so the sequencing and ordering concerns do not hold), the `CREATE DATABASE` identifier is unquoted exactly as the Makefile had it, and the profiles hint survives in the README. The rest were low-value or spec edits.

### Follow-up review

Recommended: **true**. Four `high` entries were patched on this first pass. The named unverified risk: `scripts/lint_selftest.py` now moves `.env` aside, plants a substitute `.env`, renames tracked scripts, and prepends stub directories to `PATH`, all restored in `finally` blocks. `.env` holds the developer's own credentials, so an interruption between the rename and the restore has a materially higher blast radius than Story 1-1's config renames. Verified to round-trip on the passing path, on the deliberately sabotaged path, and across 16 mutation runs — but not under a kill.

### Verification performed

- `pixi run ci` — exits 0; 277 self-test cases pass.
- Independent mutation spot-checks, each reverted, each making `pixi run test` exit non-zero: `set -a` → `set +a` in common.sh, `make destroy` → `pixi run down`, `NOTICE` losing `>&2`, `make logs` dropping `$(S)`.
- Target parity loop over all 24 former Makefile targets — no output.
- `grep -nE '^\t.*(if |for |while |read )' Makefile` — no match.
- `.env` verified byte-identical (4386 bytes) after the full suite; working tree clean.

### Residual risks

- The self-test's `.env` handling, as above.
- Everything is exercised against a stub runtime. Nothing in this story has been run against a live stack; Story 1-3 is where that becomes possible.
- `win-64` is now further from working: the lifecycle tasks invoke `./scripts/*.sh`, which the deno task shell cannot execute on Windows. The lock still solves there, and the lint and test tasks are unaffected.

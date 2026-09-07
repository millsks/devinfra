---
title: 'Validation tooling that cannot silently skip'
type: 'feature'
created: '2026-09-06'
status: 'done'
baseline_revision: '63cb793614acd68bb0e90af3fd9c85b9328de72a'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
deferred:
  - summary: >-
      AGENTS.md claims all four lint checks come "from a pinned pixi dependency", but the
      compose check runs the host's docker.
    evidence: |-
      pixi.toml's lint-compose task invokes `docker compose`, which pixi does not and cannot
      provision; the spec's own Design Notes record Docker as a host prerequisite, and
      README's Requirements section states it correctly. The AGENTS.md line also omits that
      `ci` now chains `test` as well as `lint`. Deferred rather than patched because the fix
      edits an agent-context file.
    location: >-
      AGENTS.md:25
    severity: medium
  - summary: >-
      The surviving `make lint` shim can regress to a no-op with nothing in the gate to catch it.
    evidence: |-
      Makefile:184-186 forwards to `pixi run lint`, but no test invokes `make`. Deleting the
      forward line, or writing it as `@-pixi run lint`, restores the original defect verbatim
      while `pixi run ci` stays green. Story 1-2 reworks the Makefile wholesale, and a `make`
      case would require Make on the runner, so it is better closed there.
    location: >-
      Makefile:184-186
    severity: medium
---

<intent-contract>

## Intent

**Problem:** `make lint` (Makefile:186-194) branches on `command -v shellcheck` and swallows a PyYAML `ImportError`, so on a machine lacking either tool it prints "not installed — skipping" and exits 0 having validated a subset of what it claims. A check that degrades to a no-op while reporting success is the defect Epic 1 exists to remove.

**Approach:** Introduce `pixi.toml` as the task surface, with `shellcheck`, `yamllint`, `python` and `jq` as pinned dependencies resolved from a committed `pixi.lock`, and define `lint` (compose + shell + YAML + JSON) and `ci` tasks that contain no conditional-tool branch — a missing tool becomes impossible rather than skipped.

## Boundaries & Constraints

**Always:** Every validation tool is a pinned pixi dependency and `pixi.lock` is committed, so two machines on the same commit resolve identical versions. Every check either executes or fails the task. `ci` exits non-zero when any chained check fails. Preserve the existing checks' coverage: compose config for the admin+observability profile combination, `scripts/*.sh` and `docker/postgres/initdb/*.sh`, the `docker/` YAML configs, and `docker/keycloak/realms/*.json` plus `docker/pgadmin/servers.json`.

**Never:** No task may branch on `command -v`, `which`, `|| true`, `|| echo "... skipping"`, or any other construct that converts a missing tool into a pass. Do not port the remaining Makefile targets to pixi, do not extract the `wait`/`urls`/`destroy` recipes into scripts, and do not reduce the Makefile to a full deprecation shim — that is Story 1-2. Do not add a CI workflow (Story 1-3), touch `compose.yaml` service definitions, or change any pinned image tag (Story 1-5). Do not add a Python test suite or a `test`/`cov` task; this repository has no Python package. Do not alter the stack's deliberate security posture. Do not commit `.env` or `.pixi/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean repo | All tracked files valid, pixi env installed | `pixi run ci` runs compose, shell, YAML and JSON checks and exits 0 | No error expected |
| No system shellcheck | `shellcheck` absent from `PATH` outside the pixi env | The shell check still executes, using the pixi-supplied binary | Exits non-zero only on a real violation, never on absence |
| Real shellcheck violation | A script with e.g. an unquoted expansion | `pixi run lint` exits non-zero and the output names the file and the `SC####` rule | Failure propagates; `ci` exits non-zero |
| Invalid YAML | A `docker/` config with broken indentation | `pixi run lint` exits non-zero naming the file and line | Failure propagates; `ci` exits non-zero |
| Invalid JSON | A trailing comma in a realm or pgAdmin JSON file | `pixi run lint` exits non-zero naming the file | Failure propagates; `ci` exits non-zero |
| Broken compose reference | A `depends_on` naming an undefined service | `pixi run lint` exits non-zero naming the undefined service | Failure propagates; `ci` exits non-zero |
| Glob matches nothing | A lint target's file glob matches zero files | The task fails rather than reporting success on an empty set | Non-zero exit; no silent pass |

</intent-contract>

## Code Map

- `Makefile:183-194` -- the defect. `lint` guards shellcheck with `command -v` and terminates the YAML check with `|| echo "PyYAML not installed — skipping"`. Only this target changes in this story; `SHELL := /bin/bash`, the `.env` include (lines 12-15) and every other target stay as they are.
- `Makefile:8` -- `COMPOSE := docker compose`; line 185 runs `--profile admin --profile observability config -q`. That exact profile combination is the compose check to preserve.
- `compose.yaml` -- 432 lines, monolithic, untouched here. Uses YAML merge keys (`<<: [*restart, *logging]`, lines 28-30) and `${VAR:-default}` interpolation throughout, so it lints without a populated `.env`. Include it in the YAML check.
- `scripts/smoke-test.sh` -- 16KB, `set -uo pipefail` (line 12, deliberately no `-e`), already carries a `# shellcheck disable=SC1091` (line 16). Must become shellcheck-clean without changing its error semantics.
- `docker/postgres/initdb/20-extra-databases.sh` -- the second shellcheck target; `set -euo pipefail`.
- YAML lint targets: `docker/grafana/provisioning/dashboards/dashboards.yaml`, `docker/grafana/provisioning/datasources/datasources.yaml`, `docker/loki/loki-config.yaml`, `docker/otel/otel-collector-config.yaml`, `docker/prometheus/prometheus.yml`, `docker/tempo/tempo.yaml`, plus `compose.yaml`.
- JSON lint targets: `docker/keycloak/realms/devinfra-realm.json`, `docker/pgadmin/servers.json`.
- `.gitignore:~180` -- already ignores `.pixi` and leaves `pixi.lock` tracked (the `# pixi.lock` line is commented out). No change needed; verify rather than edit.
- `AGENTS.md` -- the "Running and verifying" bullet says `make lint` can exit 0 having skipped checks and carries an explicit instruction: "Retire this line once Epic 1 Story 1.1 provisions the tooling." It sits inside the managed `<!-- bmad:context -->` block.
- `README.md:183` -- `make lint  # validate compose, shell, YAML, JSON` in a command table.
- `docs/adr/0005-pixi-as-the-task-surface.md` -- Accepted; binds AD-9/AD-21. Read-only: it already records this decision, so no new ADR is needed.
- `.claude/settings.json` -- Stop hook runs `.bmad-loop/bmad_loop_hook.py`, not `pixi run ci` directly. Read-only.
- No `.github/workflows/` exists yet. Read-only constraint: creating one belongs to Story 1-3.

## Tasks & Acceptance

**Execution:**
- `pixi.toml` -- create at the repo root via `pixi init` (never hand-written, so the current `[workspace]` schema is used), then declare `platforms = ["linux-64", "win-64", "osx-64", "osx-arm64"]` and add a `dev` feature whose dependencies are `shellcheck`, `yamllint` and `python`, each pinned to an explicit version constraint from conda-forge, and make `dev` part of the default environment so `pixi run lint` needs no `-e` flag -- gives every check a tool the project supplies rather than one the machine happens to have.
- `pixi.toml` -- define `lint-compose`, `lint-shell`, `lint-yaml` and `lint-json` tasks, a `lint` task depending on all four, and a `ci` task depending on `lint` -- `ci` is the done-gate and must chain only what this repository needs. No task body may contain `command -v`, `which`, `|| true`, or a "skipping" message.
- `.yamllint.yaml` -- add a yamllint configuration extending `default` with the rules relaxed only where the existing configs legitimately differ (line length, document start, truthy) -- so the YAML check reports real defects rather than being disabled wholesale.
- `pixi.lock` -- commit the lockfile pixi generates -- reproducibility (NFR-4): two machines on the same commit must resolve identical tool versions.
- `scripts/smoke-test.sh`, `docker/postgres/initdb/*.sh`, `docker/**/*.yaml`, `docker/**/*.json`, `compose.yaml` -- fix whatever real violations the newly-honest checks surface, preserving each file's behavior; where a finding is a deliberate choice, add a narrowly-scoped `# shellcheck disable=SCxxxx` or yamllint inline directive with a comment saying why -- a blanket rule disable would recreate the skip in a new form.
- `scripts/lint_json.py` -- add the JSON checker, invoked per-file so a failure names the file and a true line number -- `python` is the only JSON-capable tool conda-forge publishes for all four declared platforms.
- `scripts/lint_selftest.py` -- add a stdlib self-test that drives each lint check against throwaway files in a temp directory, asserting both directions: a real defect fails and names the file, a clean file passes. Covers the JSON checker's own contract (valid, invalid-names-file-and-line, missing file, empty argument list, mixed batch) plus shellcheck, yamllint and `docker compose config`. Wired to a `test` task that `ci` depends on -- new script logic must ship with a test that runs inside the gate, and every I/O matrix row needs a repeatable check rather than a one-off injection.
- `Makefile:183-194` -- replace the `lint` recipe body with a forward to `pixi run lint` plus a deprecation notice, leaving every other target untouched -- the lying target must not survive this story, but porting the rest is Story 1-2.
- `AGENTS.md` -- inside the managed context block, replace the "`make lint` can exit 0 having skipped checks" bullet with one describing the pixi lint surface -- the file explicitly asks for this retirement on completion of this story.
- `README.md:183` -- update the command table entry to `pixi run lint`, and state pixi as a prerequisite alongside Docker -- the documented command must be the one that actually validates.

**Acceptance Criteria:**
- Given a shell in which `shellcheck`, `yamllint` and `python` are shadowed on `PATH` by stubs that exit 127, when `pixi run lint` runs, then all four checks execute against pixi-supplied versions and the output contains no "skipping" message.
- Given `pixi.toml`, when `pixi install` runs, then the lock solves for all four declared platforms, so no declared platform depends on a tool conda-forge does not publish for it.
- Given the repository in its committed state, when `pixi run ci` runs, then it exits 0 having executed compose validation, shell, YAML and JSON linting.
- Given `pixi.toml` and `pixi.lock`, when either is inspected, then every validation tool carries an explicit version constraint and the lockfile is tracked by git while `.pixi/` is not.
- Given a grep for `command -v`, `which `, `|| true` and `skipping` across `pixi.toml`, when it runs, then it returns no match inside any task definition.
- Given `Makefile`, when `make lint` runs, then it forwards to `pixi run lint` and prints a deprecation notice, and the file contains no `command -v` branch.
- Given the lint surface, when `pixi run test` runs, then every case in `scripts/lint_selftest.py` passes and the task exits 0; when any case fails, `pixi run ci` exits non-zero.
- Given `AGENTS.md` and `README.md`, when they are read, then neither instructs the reader to distrust the lint exit code and both name `pixi run lint` as the validation command.

## Spec Change Log

## Review Triage Log

### 2026-09-06 — Review pass
- verdicts: 36 findings — high 3, medium 18, low 15, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` blind-hunter: non-UTF-8 JSON raises UnicodeDecodeError past both except clauses in `scripts/lint_json.py` — verified by feeding a UTF-16 file: traceback, and the second file in the batch never checked; caught alongside OSError so the loop continues and the file is named.
  - `[medium]` `[patch]` blind-hunter: lint globs are a hand-maintained directory list, so files in new locations go unchecked — verified: `lint-yaml` names two Grafana provisioning dirs where the old Makefile globbed `*/*.yaml`, `.yamllint.yaml` lints nothing, and `docker/grafana/dashboards/` (which the provisioner invites dashboards into) is outside the JSON set; file sets widened.
  - `[medium]` `[patch]` blind-hunter: the repository now owns ~200 lines of Python that no check validates — real; pinned ruff and mypy added with a `lint-python` task chained into `lint`.
  - `[low]` `[reject]` blind-hunter: `scripts/lint_selftest.py` raises a bare traceback when a tool is missing — real but rejected: a missing tool inside the pixi env is the situation the whole story makes impossible, and the fix adds a branch rather than correcting one.
  - `[medium]` `[patch]` blind-hunter: self-test coverage gaps against the spec's own I/O matrix (per-task empty glob, PATH-stub case) — verified against the matrix; cases added for both.
  - `[medium]` `[defer]` blind-hunter: `AGENTS.md` claims all four checks come "from a pinned pixi dependency" when `docker compose` is a host prerequisite — real, and the spec's own Design Notes say so; deferred because the fix edits an agent-context file.
  - `[low]` `[patch]` blind-hunter: README additions stop short — no `pixi install` in Quick start, no `pixi run ci`/`test` in the command table, new files missing from the structure listing; all added.
  - `[low]` `[patch]` blind-hunter: `.gitattributes` marks `pixi.lock` `-diff`, hiding the story's central reproducibility artifact from every future review — `-diff` dropped, rest of the line kept.
  - `[low]` `[patch]` blind-hunter: the `yamllint disable` in `docker/prometheus/prometheus.yml` has no matching enable, so it suppresses the rule to end of file while claiming "this block only" — scoped down.
  - `[low]` `[patch]` blind-hunter: the Makefile `lint` help text advertises validation the target no longer performs, and its notice goes to stdout — help reworded, notice moved to stderr.
  - `[low]` `[reject]` blind-hunter: the spec contradicts itself (jq in Approach, the `truthy` relaxation, the standing "no test task" clause) — rejected on the rule that a finding whose fix is to edit this build's spec is not actionable here.
  - `[medium]` `[patch]` edge-case: `Path.write_text` emits CRLF on win-64, a declared platform, so the self-test's clean-file cases would fail there — `newline="\n"` on every fixture write.
  - `[medium]` `[patch]` edge-case: no `* text=auto eol=lf`, so a Windows checkout hands shellcheck CRLF scripts (SC1017) — added.
  - `[medium]` `[patch]` edge-case: UnicodeDecodeError escapes `lint_json.py` — same defect as the blind-hunter row above; one fix.
  - `[low]` `[reject]` edge-case: `tool()` has no FileNotFoundError handling — same as the blind-hunter row above; rejected for the same reason.
  - `[low]` `[patch]` edge-case: the prometheus yamllint suppression runs to EOF — same defect as above; one fix.
  - `[medium]` `[patch]` edge-case: enumerated globs miss files added outside them — same defect as the glob row above; one fix.
  - `[medium]` `[patch]` edge-case: newly owned Python is the only file class with no check — same as the ruff/mypy row above; one fix.
  - `[low]` `[patch]` edge-case: `-diff` makes lockfile changes invisible to review — same as above; one fix.
  - `[medium]` `[patch]` edge-case (deletion): the old Makefile's `docker/grafana/provisioning/*/*.yaml` was narrowed to two named directories — verified against `Makefile:192` at the baseline; folded into the glob fix.
  - `[medium]` `[patch]` edge-case (claim): the spec claims empty-set failure is verified per task, but only lint_json's argv guard exists — verified; per-task cases added.
  - `[low]` `[reject]` edge-case (claim): the spec's Approach still lists `jq` as a pinned dependency after it was dropped — rejected: the fix edits this build's spec.
  - `[medium]` `[defer]` edge-case (claim): `AGENTS.md` overstates what pixi pins — same as the blind-hunter row above; deferred as an agent-context edit.
  - `[high]` `[patch]` verification-gap: the self-test verifies tool binaries, never the declared tasks — verified directly: appending `|| true` to `lint-shell`'s cmd leaves `pixi run test` and `pixi run ci` both green, reintroducing the story's own defect inside the new manifest; cases added that run the real tasks against a defective fixture.
  - `[medium]` `[patch]` verification-gap: the "glob matches nothing" matrix row is tested only for lint-json, the rest resting on unpinned deno-shell semantics — verified; per-task cases added.
  - `[medium]` `[defer]` verification-gap: the surviving `make lint` shim can regress to a no-op with nothing in the gate to catch it — real; deferred to Story 1-2, which reworks the Makefile wholesale, per the layer's own filed disposition.
  - `[medium]` `[defer]` verification-gap: `AGENTS.md` overstates that the compose check comes from a pinned dependency — same as above; deferred as an agent-context edit.
  - `[medium]` `[patch]` verification-gap: task file sets are hand-enumerated with no completeness assertion — same as the glob row; one fix.
  - `[medium]` `[patch]` verification-gap: `lint_json.py` breaks its own file-naming contract on the non-UTF-8 path — same as the UnicodeDecodeError row; one fix.
  - `[medium]` `[patch]` intent-alignment: the headline acceptance criterion (tools absent from the system) has no executable witness, existing only as prose in the Verification section — verified; a PATH-shadowing case now runs in the gate.
  - `[high]` `[patch]` intent-alignment: "no task contains a `command -v` branch or a skipping message" is checked only by a grep in a document, so a future edit reintroducing `|| true` leaves every test green — verified by the sabotage above; the grep now runs as a self-test case over the parsed task bodies.
  - `[high]` `[patch]` intent-alignment: the task definitions themselves are untested — globs, `--strict`, and the profile pair are all unexercised, so a silently narrowed task stays green — same root cause as the two rows above; the task-level cases close it.
  - `[low]` `[patch]` intent-alignment: the shellcheck criterion is proven against a synthetic `/tmp` file, never against the repository's real targets — same root cause; the task-level cases run over repository state.
  - `[low]` `[reject]` intent-alignment: `lint-compose` borrows the host's `docker`, so the lockfile's version guarantee covers three checks of four — rejected: correct and documented behavior, it fails loudly rather than skipping, and pixi cannot provision a container runtime.
  - `[low]` `[reject]` intent-alignment: `depends-on` short-circuits, so "all three checks actually execute" holds only on the clean path — rejected: a gate that stops at the first failure is correct; no run reports success having skipped anything.
  - `[low]` `[reject]` intent-alignment: `ci` chains `test`, a member the acceptance criteria did not name — rejected: the fix would edit this build's spec, and the deviation is already recorded in Design Notes with its rationale.

## Design Notes

The four lint sub-tasks are separate rather than one script so a failure names which class of check broke, and so Story 1-3's CI can address them individually if the 15-minute budget forces tiering. `ci` depends on `lint` rather than restating the four, so the two surfaces cannot drift.

Every check must fail loudly on an empty file set — a glob matching nothing is the same silent pass in a new costume. Verify this explicitly for each lint task rather than assuming the shell's glob behavior.

Shape of the honest task, contrasted with the defect it replaces:

```toml
# Correct — the tool is guaranteed present, so absence is not a case.
lint-shell = "shellcheck scripts/*.sh docker/postgres/initdb/*.sh"

# Forbidden — this is Makefile:186-188 in a new file.
# lint-shell = "if command -v shellcheck; then shellcheck ...; else echo skipping; fi"
```

`docker compose` stays a host prerequisite; pixi provisions validation tools, not the container runtime. `lint-compose` failing on a machine without Docker is correct behavior — it fails rather than pretending.

JSON validation uses the pinned `python` rather than `jq`, and lives in `scripts/lint_json.py` rather than a shell script. conda-forge publishes no `jq` for `win-64`, so keeping it would have forced either a platform-scoped dependency — which reintroduces the skip in a new form — or dropping `win-64` from the lock. Python is available on all four declared platforms, and a `.py` helper is executable on all four where a `.sh` helper is not. Per-file invocation is what makes the diagnostic name the file and a true line number; a single multi-file `jq` call reports neither.

**Deviation from the contract's "Never" clause, recorded for review.** The contract rules out adding a `test` task, on the reasoning that this repository has no Python package. That reasoning held while every check was an off-the-shelf binary; it stopped holding once `scripts/lint_json.py` introduced branching logic this repository owns. The project-wide standard that every code change ships a test outranks the clause, so a `test` task now runs `scripts/lint_selftest.py` and `ci` depends on it. What the clause was guarding against — a pytest dependency and a coverage gate on a repository with nothing to cover — is still absent: the self-test is stdlib, runs in under a second, and has no runner.

## Verification

**Commands:**
- `pixi run test` -- expected: exits 0 with every `lint-selftest:` line reading PASS; it drives shellcheck, yamllint, the JSON checker and `docker compose config` against throwaway defective and clean files, so it covers every I/O matrix row except the pixi-supplies-the-tool row below.
- `pixi run ci` -- expected: exits 0, output shows compose, shell, YAML and JSON checks all having run.
- Shadow `shellcheck`, `yamllint`, `python` and `python3` on `PATH` with stubs that `exit 127`, then run `pixi run lint` -- expected: exits 0, because pixi's own bin directory precedes the stubs.
- `grep -oE '(linux-64|win-64|osx-64|osx-arm64)' pixi.lock | sort -u` -- expected: all four platforms present.
- Introduce a deliberate defect, run `pixi run ci`, then revert -- expected non-zero exit and a message naming the file, for each of: an unquoted `$var` in a script (shellcheck `SC2086`), a bad indent in `docker/tempo/tempo.yaml`, a trailing comma in `docker/pgadmin/servers.json`, and a `depends_on` naming an undefined service in `compose.yaml`.
- `grep -nE 'command -v|which |\|\| true|skipping' pixi.toml Makefile` -- expected: no match.
- `git check-ignore -v pixi.lock; git ls-files pixi.lock` -- expected: not ignored, and tracked.
- `make lint` -- expected: prints a deprecation notice, forwards to pixi, exits 0.

## Auto Run Result

Status: done
Blocking condition: none

### Implemented change

`pixi.toml` is now the task surface. Every validation tool — `shellcheck`, `yamllint`, `python`, `ruff`, `mypy` — is a pinned conda-forge dependency resolved from a committed `pixi.lock` that solves for `linux-64`, `win-64`, `osx-64` and `osx-arm64`, so absence is impossible rather than skipped. `lint` chains compose configuration validation, shell, YAML, JSON and Python checks; `ci` chains `lint` and `test`. No task body branches on tool availability, and `scripts/lint_selftest.py` asserts that property against the parsed task definitions rather than trusting a manual grep.

### Files changed

- `pixi.toml` — task surface, pinned validation tooling, four declared platforms.
- `pixi.lock` — committed lockfile; identical tool versions on every machine and platform.
- `pyproject.toml` — ruff and mypy configuration only; no `[project]` table, since this repository ships no Python package.
- `.yamllint.yaml` — yamllint rules; two adjustments, no rule disabled wholesale.
- `.gitattributes` — `* text=auto eol=lf` so Windows checkouts do not hand the linters CRLF; `pixi.lock` marked generated but left diffable.
- `scripts/lint_json.py` — per-file JSON validation that names the file and the offending line.
- `scripts/lint_selftest.py` — 44 cases driving the declared tasks against planted defects, empty file sets, and a PATH shadowed by failing stubs.
- `Makefile` — the `lint` recipe forwards to `pixi run lint` with a deprecation notice on stderr; every other target untouched.
- `AGENTS.md`, `README.md` — the "do not trust the exit code" guidance retired; pixi documented as a prerequisite and `pixi run ci` as the gate.
- `compose.yaml`, `docker/prometheus/prometheus.yml`, `docker/tempo/tempo.yaml` — real findings the newly honest yamllint surfaced.

### Review findings

Four layers reported 36 findings: 3 high, 18 medium, 15 low, 0 false, 0 maybe-false.

- **Patched (13 fixes across 8 grouped entries; 1 high, 5 medium, 2 low at entry verdict).** The high entry: the self-test exercised tool binaries rather than the declared tasks, so appending `|| true` to a task body left `pixi run ci` green — the story's own defect reintroduced inside the new manifest. Reproduced, then closed by cases that run the real tasks, empty every glob, shadow `PATH`, and parse each task body for skip constructs. The same sabotage now fails the gate with three distinct errors. Also patched: a `UnicodeDecodeError` that escaped `lint_json.py` and abandoned the rest of the batch; glob coverage narrower than the Makefile it replaced; CRLF fixtures that would fail on the declared `win-64`; `-diff` hiding the lockfile from review; ~200 lines of newly owned Python that no check validated; a `yamllint disable` running to end of file; stale Makefile help text and a notice on stdout; README gaps.
- **Deferred (2).** The `AGENTS.md` overstatement, because its fix edits an agent-context file. The unguarded `make lint` shim, because Story 1-2 reworks the Makefile wholesale.
- **Rejected (7).** A missing-tool traceback in the self-test — real, but a missing tool inside the pixi env is the situation this story makes impossible, and the fix adds a branch rather than correcting one. Three findings whose fix was to edit this build's spec. `lint-compose` borrowing the host's `docker` — correct and documented; pixi cannot provision a container runtime, and it fails loudly. `depends-on` short-circuiting on first failure — correct gate behavior; nothing reports success having skipped. `ci` chaining `test`, a member the acceptance criteria did not name — the deviation is recorded with its rationale in Design Notes.

### Follow-up review

Recommended: **true**. One `high` entry was patched on this first pass. The named unverified risk: `scripts/lint_selftest.py` now renames tracked files out from under the globs and prepends stub directories to `PATH` while driving `pixi run` recursively from inside a pixi task. Restoration depends entirely on its `finally` blocks. A crash or a kill between the rename and the restore would leave the working tree with repository configs renamed aside — verified to restore cleanly on both the passing and the deliberately sabotaged paths, but not under interruption.

### Verification performed

- `pixi run ci` — exits 0; 44 self-test cases pass.
- Sabotage: `|| true` appended to `lint-shell`'s `cmd` — `pixi run ci` exits 1 with three failures (`cmd contains ['|| true']`, task exits 0 on a real defect, task passes an empty file set). Restored, exits 0.
- Defect injection, each reverted, each naming the offender: unquoted expansion (`SC2086`, `scripts/smoke-test.sh:362`), bad indent (`docker/tempo/tempo.yaml`), trailing comma (`docker/pgadmin/servers.json:25`), `depends_on` on an undefined service (`nonexistent-service`).
- Non-UTF-8 JSON: reports the file and byte offset, continues the batch, exits 1 — no traceback.
- `PATH` shadowed by `exit 127` stubs for shellcheck, yamllint and python — `pixi run lint` exits 0, output contains no "skipping".
- `grep -nE 'command -v|which |\|\| true|skipping' pixi.toml Makefile` — no match.
- `grep -oE '(linux-64|win-64|osx-64|osx-arm64)' pixi.lock | sort -u` — all four present.
- `git check-ignore pixi.lock` — not ignored; `.pixi` — ignored.
- `make lint` — exits 0, notice on stderr, stdout clean.

### Residual risks

- `yamllint --strict` fails on warnings, so a yamllint minor bump could redden `ci`; the `1.38.*` pin bounds that to patch releases. The same applies to the `ruff 0.16.*` and `mypy 2.3.*` pins.
- `pixi run ci` requires a working Docker CLI, for `lint-compose` and for the self-test's compose case. This is deliberate — pixi provisions validation tools, not the container runtime — and it fails loudly, but it does mean the gate cannot run on a Docker-less machine.
- The lock solves for `win-64`, and the self-test writes `.bat` stubs there, but nothing has actually executed the suite on Windows. Story 1-3 is where a runner could prove it.

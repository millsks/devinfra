---
title: 'Commit-time checks run the same tasks CI does'
type: 'feature'
created: '2026-09-07'
status: 'awaiting-operator'
baseline_revision: '9a38ccc47b61a97aa8ce1c688cc9f7845993f80d'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/implementation-artifacts/spec-1-4-the-stack-runs-under-podman.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
deferred:
  - summary: >-
      AGENTS.md still presents `pixi run lint` as the whole validation surface and never
      mentions `pixi run bootstrap`, the `precommit` task or the commit-message contract.
    evidence: |-
      The `bmad:context` block in AGENTS.md is managed by bmad-project-context and edits
      inside it are replaced on refresh, so the correction belongs either in a section
      outside the markers or in the next context refresh. As shipped, an agent's first
      commit in a bootstrapped clone is rejected by a contract nothing in its instructions
      described. Routed to defer because the fix edits an agent-context file.
    location: >-
      AGENTS.md (Running and verifying)
    severity: low
operator_actions:
  - "Run `pixi run bootstrap` once in every clone of this repository you commit from. `core.hooksPath` lives in `.git/config`, which is not part of the tree, so no commit can install the hooks and no clone arrives with them — until you run it, this story's checks do not exist on your machine and CI is the only gate."
  - "Before running it in a clone the bmad-loop orchestrator drives, decide what happens to its commit subjects. `story <id>: implemented and reviewed via bmad-loop` is not a Conventional Commit and the `commit-msg` hook rejects it (verified against the shipped checker); its `Merge ...` commits are accepted. Either have the loop commit with `--no-verify`, or change its subject convention to `chore(story): ...`."
  - "After bootstrapping, make one real commit and confirm the hooks fire: `pixi run precommit` should run before the commit is written, and a subject such as `updated the readme` should be refused. That is the story's actual acceptance evidence for the first criterion — everything in this branch was proved in throwaway clones."
  - "Merge this branch to `main`. Nothing here changes CI, so the hosted run is unaffected, but the hooks are only useful once they are on `main` for every clone to pick up."
---

<intent-contract>

## Intent

**Problem:** A shellcheck violation, a malformed YAML file or a non-conventional commit message
is only discovered after the commit has landed and a hosted CI run has finished. The feedback
that costs seconds to produce currently costs minutes to receive, and the repository has no
commit-message contract at all.

**Approach:** Ship two tracked git hooks in `.githooks/` — `pre-commit` and `commit-msg` — that do
nothing but `exec` an existing pixi task, installed per clone by `pixi run bootstrap` setting
`core.hooksPath`. The hooks name no tool and pin no version: every tool they reach comes from
`pixi.lock`, the same one CI reads. The commit-message contract is a new checker,
`scripts/check_commit_msg.py`, driven by the `commit-msg` task and proved by the self-test the
same way every other check in this repository is.

## Boundaries & Constraints

**Always:**
- A hook's entire body is a `cd` to the work tree root and an `exec pixi run <task>`. No tool name,
  no version, no check logic. One source of truth for what version of a tool runs, locally or in CI.
- The set of checks the pre-commit hook runs is a strict subset of what `pixi run lint` runs, and
  the self-test asserts that containment. A hook can never run a check the gate does not.
- Nothing may silently skip. A hook whose `pixi` is missing, whose task is undefined or whose check
  fails must exit non-zero and reject the commit. A hook file that is not tracked mode `100755` is
  silently ignored by git, so the tracked mode is asserted, not assumed.
- `git merge` runs both `pre-commit` and `commit-msg` (verified on git 2.49), so the message check
  must accept git's own generated messages — `Merge ...`, `Revert "..."`, and the
  `fixup!`/`squash!`/`amend!` prefixes — or every merge in this repository breaks.
- `.githooks/` joins every universal loop that today walks only `scripts/`: the `FORBIDDEN`
  substring scan, `lint-shell`'s coverage assertion, and the fresh-clone tracking check.
- The pre-commit checks stay offline and runtime-free, so a commit is possible on a machine with no
  container runtime running.
- Conventional Commits is enforced structurally: `type(optional-scope)!: description`, type drawn
  from a declared set, description non-empty.

**Never:**
- Do not adopt the `pre-commit` framework. Its `rev:`-pinned remote repositories are a second
  declaration of tool versions — exactly the drift this story's own acceptance forbids — and its
  `repo: local` escape hatch buys nothing that `core.hooksPath` does not already provide.
- Do not weaken, reorder or remove any existing check to make a hook fast. `lint-compose` and
  `lint-config` stay in `lint`; they are excluded from the hook because they need a container
  runtime, not because they are inconvenient.
- Do not make the hooks the gate. CI (story 1.3) stays authoritative; `--no-verify` remains a
  supported escape and is documented as one.
- Do not run `pixi run bootstrap` against this repository's own `.git`. `core.hooksPath` is written
  to the shared config that every linked worktree reads, so an install here changes the environment
  of the loop that is running. Prove the installer against a throwaway clone instead.
- Do not add a CI job, a workflow, or a `make` target. This story adds no hosted work.
- Do not edit `AGENTS.md` inside its managed `bmad:context` block.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean commit | Tree passes every fast check, message `feat: x` | Hooks exit 0, commit lands | No error expected |
| Shell defect | A script with an unquoted expansion is staged | `pre-commit` exits non-zero; output names the file and `SC2086` | Commit rejected |
| YAML defect | A malformed `docker/**/*.yaml` | `pre-commit` exits non-zero; output names the file and the yamllint rule | Commit rejected |
| Bad message type | `updated the readme` | `commit-msg` exits non-zero naming the offending line and the allowed types | Commit rejected |
| Missing colon | `feat add hooks` | Exit non-zero naming the expected `type(scope): description` shape | Commit rejected |
| Unknown type | `feature: add hooks` | Exit non-zero naming `feature` and listing the allowed types | Commit rejected |
| Empty description | `fix:` or `fix:   ` | Exit non-zero naming the empty description | Commit rejected |
| Breaking marker | `feat(api)!: drop X` | Accepted | No error expected |
| Merge message | `Merge branch 'x' into main` | Accepted — git generates it and runs the hook on it | No error expected |
| Revert message | `Revert "feat: x"` | Accepted | No error expected |
| Fixup message | `fixup! feat: x` | Accepted | No error expected |
| Comments only | A message whose non-comment lines are all blank | Exit non-zero naming the empty message | Commit rejected |
| Verbose commit | Message followed by the `# ---- >8 ----` scissors and a diff | Only the text above the scissors is judged | No error expected |
| No message path | `pixi run commit-msg` with no argument | Exit non-zero printing usage | Diagnostic, no traceback |
| Missing file | Argument names a path that does not exist | Exit non-zero naming the path | Diagnostic, no traceback |
| Non-UTF-8 message | Message file holds invalid UTF-8 | Exit non-zero naming the file | Diagnostic, no traceback |
| Installer, no repo | `bootstrap` run outside a git work tree | Exit non-zero naming the condition | Nothing written |
| Installer, bad mode | A hook file is not executable | Exit non-zero naming the file | `core.hooksPath` left unchanged |
| Installer, rerun | `core.hooksPath` already `.githooks` | Exit 0, idempotent, states it was already set | No error expected |
| Installer, other value | `core.hooksPath` set elsewhere | Exit 0, overwrites, prints the old and new values | No error expected |

</intent-contract>

## Code Map

- `pixi.toml:197-199` -- `[tasks.lint] depends-on = ["lint-compose", "lint-config", "lint-pins",
  "lint-renovate", "lint-shell", "lint-yaml", "lint-json", "lint-python"]`. **The change point is
  narrow: this list must keep naming all eight directly.** `chain()` in the self-test
  (`scripts/lint_selftest.py:2329-2332`) reads only `depends-on` and is **not transitive**, so a
  grouping task inserted between `lint` and its members orphans every one of them at
  `:2335`. The fast subset is therefore a *sibling* task with a non-`lint-` name, whose members are
  independently asserted to be a subset of `lint`'s.
- `pixi.toml:177-179` -- `[tasks.lint-shell] cmd = "shellcheck scripts/**/*.sh
  docker/postgres/initdb/*.sh"`. Gains `.githooks/*`. Verified: pixi's shell expands `.githooks/*`
  (a literal dotted directory, so no hidden-file rule applies), and shellcheck reads the shebang of
  an extensionless file — `shellcheck <dir>/pre-commit` reports SC2086 on a planted defect.
- `pixi.toml:104-118` -- `[tasks.logs]` / `[tasks.psql]`: `args = [{ arg = "x", default = "" }]` with
  `cmd = "./scripts/x.sh '{{ x }}'"`. The house shape for an argument-taking task; `commit-msg`
  copies it, and `scripts/restore.sh:16-25` is the precedent for a script that refuses an empty
  argument with a usage line rather than proceeding.
- `scripts/lint_selftest.py:551-570` -- the `FORBIDDEN` scan over every `pixi.toml` task body, and
  the "names no container runtime directly" scan beside it. `FORBIDDEN` is
  `("command -v", "which ", "|| true", "skipping")` at `:51`. Both new task bodies must be clean of
  all of it, and so must every new shell line.
- `scripts/lint_selftest.py:572-586` -- `shell_sources = sorted((REPO / "scripts").rglob("*.sh"))`,
  the per-script `FORBIDDEN` scan, and `expect("lint-shell covers every script at any depth", ...)`
  which expands `lint-shell`'s own patterns and diffs them against `shell_sources`. **Widen
  `shell_sources` to include `.githooks/*`** — otherwise a hook script is unlinted and
  unscanned, which is the silent-skip class this file exists to remove.
- `scripts/lint_selftest.py:2098-2117` -- the fresh-clone tracking loop over
  `scripts/**/*.sh` + `scripts/**/*.py`, run through `git check-ignore --stdin`. Widen the same way.
  Verified `git check-ignore .githooks/pre-commit` exits 1 today, so nothing ignores the directory.
- `scripts/lint_selftest.py:465` -- `expect(name, condition, detail)`, a closure over `failures`;
  PASS streams to stdout, FAIL batches to stderr at `:2415-2417`. `:91` `pixi(task, *args, env=,
  stdin=)` shells out to `pixi run`. `:361` `planted(path, body)` refuses to clobber an existing
  path and unlinks in `finally`; `:385` `moved_aside(paths)` renames and restores. `:439`
  `tool(args, cwd=, env=)` runs an arbitrary command line. **There is no git-repository fixture in
  this file** — the only `git` call is `check-ignore` at `:2104`. This story introduces the first
  one; it must be a module-level `@contextlib.contextmanager` shaped like `planted`, building its
  repository under `tempfile.TemporaryDirectory()`, and it must never run `git commit` against
  `REPO`.
- `scripts/lint_selftest.py:589-614` -- the `defects` table (`task, fixture, body`) driving "fails
  on a real defect" plus "names the defect". The two hook-visible defects (a shell one and a YAML
  one) are already in it; the new cases reuse the same fixtures through the hook rather than
  restating them.
- `scripts/lint_selftest.py:2326-2357` -- `chain()` and the gate-reachability block. `{"lint",
  "test"} <= set(chain("ci"))` is a **subset** check, so `ci`'s `depends-on` may be appended to;
  `orphans = lint_tasks - set(chain("lint"))` is **not**.
- `scripts/lint_selftest.py:2200-2214` -- `expect("ci.yml declares exactly the three CI jobs", ...)`
  is a set equality. Read-only: this story adds no job and no workflow.
- `scripts/assert_pins.py` -- the checker skeleton to copy exactly: module docstring stating why the
  check exists, `REPO` constant, `#:` comments on module constants, `check()` returning
  `(count, problems)`, an argv-parameterised `main(argv)` so the self-test drives fixtures instead of
  editing tracked files, a zero-match refusal, diagnostics prefixed `"<task>: "` on stderr, one `OK`
  line with a count on stdout, `raise SystemExit(main(sys.argv[1:]))`.
- `scripts/lib/common.sh:18` -- `cd "$(dirname "${BASH_SOURCE[0]}")/../.."`. Read-only, and
  deliberately **not** sourced by the hooks: it loads `.env` and defines `compose()`, neither of
  which a hook needs, and DW-5 records that its `set -a; source .env` mangles values. A hook is two
  lines; it needs no shared helper.
- `pyproject.toml` -- `[tool.ruff] line-length = 120`, `select = ["E","F","I","UP","B","RUF","D"]`,
  google pydocstyle, `[tool.mypy] strict = true, python_version = "3.12"`. `lint-python` runs
  `ruff format --check scripts && ruff check scripts && mypy scripts`, so new Python must arrive
  already formatted and fully annotated with Google docstrings.
- `README.md:225` (Common tasks), `:269` (Continuous integration), `:307` (Keeping images current) --
  the section shapes to follow; the new section belongs after Continuous integration, because it is
  defined by its relationship to the gate.
- `docs/adr/README.md:10-22` -- the index table; `0010` is the highest, so `0011` is next.
  `docs/adr/0009-...md` is the house style for an ADR.
- Verified behaviour, git 2.49.0, throwaway repository: `core.hooksPath = .githooks` (relative)
  resolves against the work-tree root and fires correctly when `git commit` is run from a
  subdirectory; **`git merge --no-ff` runs `pre-commit` and then `commit-msg`**, the latter with
  `.git/MERGE_MSG` as `$1`; a hook exiting non-zero blocks the commit.

## Tasks & Acceptance

**Execution:**
- `.githooks/pre-commit` (new, mode 100755) -- `#!/usr/bin/env bash`, `set -euo pipefail`,
  `cd "$(git rev-parse --show-toplevel)"`, `exec pixi run precommit` -- the hook carries no logic, so
  there is nothing in it to drift from what CI runs.
- `.githooks/commit-msg` (new, mode 100755) -- same preamble, `exec pixi run commit-msg "$1"`,
  resolving `$1` to an absolute path before the `cd` (the argument is relative to the work-tree
  root, and `restore.sh:12-19` is the precedent for that ordering) -- keeps the message contract in
  Python, where it can be tested.
- `scripts/check_commit_msg.py` (new) -- a Conventional Commits validator following the
  `assert_pins.py` skeleton: strip comment lines and everything from the `# ---- >8 ----` scissors,
  accept the git-generated `Merge`/`Revert`/`fixup!`/`squash!`/`amend!` forms, otherwise require
  `type(optional-scope)!: description` with the type in a declared, documented set and a non-empty
  description. `main(argv)` takes the message path so the self-test can drive fixtures.
- `scripts/bootstrap.sh` (new) -- refuse outside a git work tree; refuse if any `.githooks/*` file
  is not executable; set `core.hooksPath` to `.githooks`; read it back and refuse if it did not
  take; print the previous value when overwriting -- an installer that appears to work and does not
  is the same silent skip in a different place.
- `pixi.toml` -- add `[tasks.bootstrap]` (`./scripts/bootstrap.sh`), `[tasks.commit-msg]` (argument
  `file`, defaulting to empty, forwarded to `scripts/check_commit_msg.py`), and `[tasks.precommit]`
  as a `depends-on` grouping over the six offline checks (`lint-shell`, `lint-yaml`, `lint-json`,
  `lint-python`, `lint-pins`, `lint-renovate`); extend `lint-shell`'s glob with `.githooks/*`; leave
  `[tasks.lint]`'s eight direct dependencies exactly as they are.
- `scripts/lint_selftest.py` -- widen `shell_sources` and the fresh-clone `sources` list to
  `.githooks/*`; add a temp-git-repository fixture; add a block proving: the tracked mode of every
  `.githooks/*` file is `100755`; each hook's body is one `exec pixi run` line naming a declared
  task; `precommit`'s members are a subset of `lint`'s; `precommit` reaches no task that needs a
  container runtime; `bootstrap` sets and re-reads `core.hooksPath` in a throwaway clone and refuses
  outside one; a real commit in that clone is rejected for a planted shell defect and for a planted
  YAML defect, each naming the file; every row of the I/O matrix for `check_commit_msg.py` fails or
  passes as stated; and a hook whose `pixi` is absent from `PATH` rejects the commit rather than
  passing it.
- `docs/adr/0011-commit-time-checks-are-git-hooks-invoking-pixi-tasks.md` (new) + a row in
  `docs/adr/README.md` -- record why `core.hooksPath` over the `pre-commit` framework, why the hook
  set is a subset of `lint`, and what this leaves unguarded.
- `README.md` -- a "Commit-time checks" section after "Continuous integration": what
  `pixi run bootstrap` does, that it is per clone and cannot be committed, which checks run, the
  commit-message contract with examples, that `--no-verify` bypasses them by design, and that the
  hook judges the working tree rather than the index.

**Acceptance Criteria:**
- Given a clone with `pixi run bootstrap` run and a staged shellcheck violation or malformed YAML,
  when `git commit` is run, then the commit is rejected and the output names the offending file and
  the rule that caught it.
- Given the tool versions pinned in `pixi.lock`, when the hooks are inspected, then neither hook
  names a tool or a version, and the checks they reach are exactly tasks already declared in
  `pixi.toml`.
- Given a commit message that does not follow Conventional Commits and is not one of git's own
  generated forms, when `git commit` is run, then it is rejected with a diagnostic naming what was
  wrong.
- Given the hooks are installed, when a developer runs `git commit --no-verify`, then the commit
  lands — the hooks are fast feedback and CI (story 1.3) remains the authoritative gate.
- Given `pixi run ci`, when it is run on this branch, then it exits 0 with zero FAIL lines, and the
  set of checks `pixi run lint` performs is unchanged from the baseline revision.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 29 findings — high 0, medium 9, low 20, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` blind-hunter: `GENERATED`'s `Merge\b` accepts ordinary prose, not only git's generated forms — reproduced: `Merge the two configs into one` exited 0. Narrowed to `Merge (remote-tracking )?(branch|branches|tag|tags|commit|commits|pull request) `; the subject now exits 1.
  - `[low]` `[patch]` blind-hunter: the docstring claims comments are stripped "exactly as git strips it", but `-m` uses whitespace cleanup — verified `git commit -m "#142 fix the pin drift"` records the `#` line verbatim. Docstring corrected to say the checker is stricter than git, not identical to it.
  - `[medium]` `[patch]` blind-hunter: `bootstrap.sh` read back `git config --local`, which proves only that the value it wrote is still there — verified with `extensions.worktreeConfig` that `--local` reads `.githooks` while the effective value is `.other-hooks`. Read-back moved to plain `git config --get`; the installer now exits 1 naming the outranking value.
  - `[low]` `[patch]` blind-hunter: nothing warned that `core.hooksPath` lands in the config every linked worktree shares — a sentence added to both the README and the ADR.
  - `[low]` `[defer]` blind-hunter: `AGENTS.md` still presents `pixi run lint` as the whole validation surface and never mentions `bootstrap`, `precommit` or the message contract. Routed to defer: the fix edits an agent-context file.
  - `[low]` `[patch]` blind-hunter: README and ADR claim a containment that `chain()` cannot see — it returns only immediate `depends-on`, so a member that later grew its own would slip a runtime-bound check into the hook with the gate green. A `closure()` helper now computes the transitive set for all three containment assertions.
  - `[medium]` `[patch]` blind-hunter: the `.githooks/*` coverage assertion compares `REPO.glob` against `REPO.glob`, so nothing proved pixi's shell expands the dotted directory. A `defects` row now plants `.githooks/zz_selftest_defect` and requires `lint-shell` to fail naming it.
  - `[low]` `[patch]` blind-hunter: the per-hook tool-name and version scans read comments as code, so an accurate comment naming a tool or a version would fail the gate. Both scans now read non-comment lines only, as the `FORBIDDEN` walk above already does.
  - `[low]` `[patch]` blind-hunter: the pixi-free-PATH case asserted only the exit code, so an unrelated failure satisfied it — it now also asserts the output names `pixi`.
  - `[low]` `[patch]` blind-hunter: `(?P<breaking>!)?` was captured and never read, and the ADR's Context implied the checker validates breaking-change announcements. Group made non-capturing, ADR sentence softened, and the subject-only scope recorded beside the `core.commentChar` limitation.
  - `[low]` `[reject]` blind-hunter: `'{{ file }}'` breaks on a message path containing a single quote. Verified it fails closed — pixi refuses to parse and the commit is rejected — and the quoting matches the pre-existing `restore`/`psql`/`logs` tasks; the fix (env-var indirection) is more than a direct correction for a path shape a developer is unlikely to meet. The `$PWD` half of the same finding was patched, below.
  - `[low]` `[patch]` edge-case: `.githooks/pre-commit`'s `cd "$(git rev-parse --show-toplevel)"` swallows a failure — verified `cd ""` exits 0 under `set -euo pipefail`. The root is now captured into a variable first.
  - `[low]` `[patch]` edge-case: same in `.githooks/commit-msg` — same fix, and in the new `pre-merge-commit`.
  - `[medium]` `[patch]` edge-case: no `pre-merge-commit` hook, so every merge commit landed with the offline lint never run. **Verified on git 2.49.0**: `git merge --no-ff` fires `pre-merge-commit`, `prepare-commit-msg` and `commit-msg`, and never `pre-commit`. `.githooks/pre-merge-commit` added, execing the same `precommit` task; a merge over a planted defect is now rejected naming the file.
  - `[low]` `[patch]` edge-case: `Reapply "..."` — what git writes for a revert of a revert (verified: `Reapply "base"`) — was rejected, blocking the conflicted path. Added beside `Revert "` in `GENERATED`.
  - `[low]` `[reject]` edge-case: single-quote message path — same finding as the blind hunter's, rejected on the same evidence.
  - `[low]` `[reject]` edge-case: a subject with leading whitespace is rejected though git records it. Not a defect: `  feat: x` is not `type: description`, so rejecting it is the contract working; accepting it would let a malformed subject through.
  - `[low]` `[reject]` edge-case: a developer's pre-existing `.git/hooks` are silently shadowed by `core.hooksPath`. That is what redirection means and what the installer is asked to do; the fix adds a warning branch, which is more than a direct correction.
  - `[low]` `[patch]` edge-case: an untracked stray in `.githooks/` failed the mode loop with `mode None` and an unactionable diagnostic. The loop now iterates the entries `git ls-files --stage` reports, with a non-empty assertion beside it.
  - `[medium]` `[patch]` edge-case: the claim "git merge runs both `pre-commit` and `commit-msg`" is false — confirmed by the hook-probe run above. Corrected in the ADR, the README and the self-test case name; the tree half of the property is now true because `pre-merge-commit` exists.
  - `[low]` `[patch]` edge-case: the docstring claimed `git revert` and `git commit --fixup` run `commit-msg`. Verified a clean `git revert --no-edit` runs only `prepare-commit-msg`; the claim is now narrowed to the conflicted path completed by hand.
  - `[low]` `[reject]` edge-case: the end-to-end case plants its defect in the outer repository rather than in the commit, so it proves the pipeline rather than "a defect in the commit". Structural — the throwaway repository must sit inside this one for pixi to find any task — and the real behaviour was verified by hand in a real clone, where the defect and the commit share a tree and the commit is rejected naming the file. The fix is a restructure, not a correction.
  - `[medium]` `[patch]` verification-gap: `lint-shell`'s `.githooks/*` pattern was proved only by a Python glob comparison — same root cause as the blind hunter's, patched by the same `defects` row.
  - `[medium]` `[patch]` verification-gap: the `Merge\b` narrowing was pinned by no test, so deleting `\b` would leave every row green. Rejected rows added for `Merged the two configs`, `Merge the two configs into one` and `Reapplying the change`, plus an accepted row for `Reapply "feat: x"`.
  - `[medium]` `[patch]` verification-gap: "the pre-commit checks stay offline" was only a substring scan of task bodies. `docker`, `docker-compose` and `podman` are now shadowed by failing stubs on `PATH` and `pixi run precommit` is asserted to still exit 0.
  - `[medium]` `[patch]` verification-gap (other): `GENERATED` is wider than git's generated forms — duplicate of the first row, patched with it.
  - `[low]` `[patch]` verification-gap (other): `.githooks/commit-msg` carried four lines absolutising `$1`, contradicting the documented two-statement shape, and the logic is inert — githooks(5) chdirs to the work-tree root first, verified. Deleted, and a commit-from-a-subdirectory case added so the assumption is pinned rather than assumed.
  - `[low]` `[patch]` verification-gap (other): `bootstrap.sh`'s missing-directory and empty-directory refusals had no case — one of each added in the throwaway clone.
  - `[low]` `[reject]` intent-alignment: the audit enumerates readings R1-R6, places the diff at R2+R4 with R1/R3 rejected on the record, and prescribes nothing — descriptive by its own instruction. Its substantive points duplicate rows already patched or rejected above; its R6 point (whether the per-clone install is owed to an operator) is acted on at Finalize under the invocation instruction, not as a review finding.

## Design Notes

Two decisions carry the design.

**`core.hooksPath` over the `pre-commit` framework.** The framework's normal mode declares a
`rev:` per hook repository — a second pinning of the same tools that `pixi.lock` already pins, which
is the drift the story's second acceptance criterion exists to forbid. Driving it entirely from
`repo: local` entries avoids that, but then it contributes nothing except an extra tool to install
and its own environment cache to keep. A tracked `.githooks/` directory plus one `git config` line
is the whole mechanism, and it is inspectable in two files.

**The hook runs a subset, and the subset is asserted.** `lint-compose` and `lint-config` resolve the
compose model through a container runtime. Including them would mean no commit is possible while
Docker or Podman is down, which trains the exact `--no-verify` reflex that makes the hook worthless.
They stay in `lint` and out of `precommit`. The containment is not a comment: the self-test asserts
`chain("precommit") ⊆ chain("lint")`, so the hook can never grow a check the gate does not run.

The hook shape, in full:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec pixi run precommit
```

The known limitation, stated rather than papered over: the checks read the working tree, not the
index, so a partial `git add` is judged against files the commit does not contain. Stashing to
correct that is unsafe in a repository with linked worktrees sharing one stash stack. CI reads the
merged tree and is the answer to it.

## Verification

**Commands:**
- `pixi run ci` -- expected: exit 0, zero `lint-selftest: FAIL` lines.
- `pixi run precommit` -- expected: exit 0, and visibly faster than `pixi run lint`; no container
  runtime contacted.
- `pixi run commit-msg <fixture>` -- expected: exit 0 on `feat(scope)!: x`, non-zero with a named
  diagnostic on `updated the readme`.
- `pixi run lint-shell` -- expected: exit 0 and, with a planted unquoted expansion in
  `.githooks/`, non-zero naming that file.
- `git clone <this worktree> <tmp> && cd <tmp> && pixi run bootstrap` then a real commit in that
  clone -- expected: `core.hooksPath` reads back as `.githooks`; a commit carrying a planted shell
  defect is rejected naming the file; a commit with message `nope` is rejected; the same commit with
  `feat: x` and a clean tree lands. Run against a throwaway clone only — never against this
  repository's own `.git`, whose config every linked worktree shares.
- `git diff 9a38ccc47b61a97aa8ce1c688cc9f7845993f80d -- pixi.toml | grep 'tasks.lint\]' -A2` --
  expected: `[tasks.lint]`'s `depends-on` list is unchanged.

## Auto Run Result

Status: awaiting-operator

**Implemented.** Commit-time checks that run the same task definitions CI runs: three tracked
hooks in `.githooks/`, installed per clone by `pixi run bootstrap` pointing `core.hooksPath`
at them, each hook nothing but a `cd` to the work-tree root and an `exec pixi run <task>`. No
hook names a tool and none pins a version — everything they reach resolves from `pixi.lock`,
the lockfile the hosted runner reads. `precommit` is a strict, asserted subset of `lint`; the
message contract is a new checker with a case for every row of the I/O matrix.

**Files changed**
- `.githooks/pre-commit` (new, tracked 100755) — execs `pixi run precommit`.
- `.githooks/pre-merge-commit` (new, tracked 100755) — the same task again. git fires this,
  not `pre-commit`, when `git merge` writes a commit; without it every merge commit landed
  with the offline checks never run.
- `.githooks/commit-msg` (new, tracked 100755) — execs `pixi run commit-msg "$1"`.
- `scripts/check_commit_msg.py` (new) — the Conventional Commits contract on the
  `assert_pins.py` skeleton: comments and the `--verbose` scissors block stripped, git's own
  `Merge <branch|tag|commit|pull request> `, `Revert "`, `Reapply "` and `fixup!`/`squash!`/
  `amend!` subjects accepted as given, everything else required to be
  `type(optional-scope)!: description` with the type in a declared set.
- `scripts/bootstrap.sh` (new) — refuses outside a work tree, refuses a missing, empty or
  non-executable hook set before writing anything, then sets `core.hooksPath` and reads back
  the *effective* value, so a worktree-scoped setting that outranks it is a refusal rather
  than a false success.
- `pixi.toml` — `bootstrap`, `precommit` (six offline checks) and `commit-msg` tasks;
  `lint-shell`'s glob extended with `.githooks/*`. `[tasks.lint]`'s eight direct dependencies
  are byte-identical to the baseline.
- `scripts/lint_selftest.py` — `.githooks/` joined to the `FORBIDDEN` scan, the `lint-shell`
  coverage diff and the fresh-clone tracking check; a `throwaway_repo()` fixture; a
  `closure()` helper for transitive task containment; ~120 new cases covering hook wiring,
  tracked mode, the whole message contract, the installer's five states, and real commits and
  merges driven through the hooks.
- `docs/adr/0011-…` (new) + index row — why `core.hooksPath` over the `pre-commit` framework,
  why the hook set is a subset, and what it leaves unguarded.
- `README.md` — a "Commit-time checks" section, the three hooks in Repository layout and in
  Common tasks, and `pixi run bootstrap` in Quick start.

**Review findings.** 29 findings across four layers — 0 high, 9 medium, 20 low, 0 false.
22 patched in one round; the substantive ones were the missing `pre-merge-commit` hook (every
merge commit was landing unchecked), `bootstrap` reading back the wrong config scope, the
`Merge\b` escape hatch swallowing ordinary prose, and three properties that were asserted by
comparing Python against Python rather than by running anything. 1 deferred: `AGENTS.md` is
not updated, which routes to defer because the fix edits an agent-context file. 6 rejected:
the single-quote message path (fails closed, matches the pre-existing task quoting), a
leading-whitespace subject (rejecting it is the contract working), pre-existing `.git/hooks`
being shadowed (that is what redirection means), the end-to-end fixture planting its defect
one repository out (structural, and the real path was verified by hand), and the
intent-alignment audit (descriptive, its points already covered).

**Follow-up review recommended: true.** No high entry was patched, but nine medium ones were,
in a single round that added a third hook, a transitive-closure helper, an index-read for
tracked modes and comment-stripped scans. The specific unverified risk: to make an end-to-end
hook case possible at all, `throwaway_repo()` builds its repository *inside* this one so pixi
can find a manifest, and now sets `GIT_DIR`/`GIT_WORK_TREE` absolutely — so the relative
`.git/COMMIT_EDITMSG` path git actually hands the hook is exercised only by the by-hand clone
run recorded below, never by the gate. Patched by verdict: high 0, medium 9, low 13.

**Verification performed**
- `pixi run ci` — exit 0, 704 self-test PASS lines, zero FAIL.
- `pixi run precommit` — exit 0 in 1.3s against `pixi run ci`'s 43s; no container runtime
  contacted.
- `git diff <baseline> -- pixi.toml` — no `[tasks.lint]` hunk; the gate runs what it ran.
- `pixi run lint-shell` with an unquoted expansion planted in `.githooks/` — exit 1 naming the
  file and `SC2086`, so pixi's shell really does expand the dotted directory.
- Hook-probe repository, git 2.49.0: `git commit` fires `pre-commit`/`prepare-commit-msg`/
  `commit-msg`; `git merge --no-ff` fires `pre-merge-commit`/`prepare-commit-msg`/`commit-msg`
  and never `pre-commit`; a clean `git revert --no-edit` fires only `prepare-commit-msg` and
  writes `Reapply "…"` for a revert of a revert. Three shipped claims were corrected against
  this run rather than left as prose.
- A real throwaway clone with `pixi run bootstrap` run, after the patches: a clean `feat:`
  commit lands; a commit issued from a subdirectory lands; `updated the readme` and
  `Merge the two collector configs into one` are rejected with a named diagnostic;
  `Reapply "feat: clean"` is accepted; a merge over a planted `scripts/zz_defect.sh` is
  rejected naming the file and `SC2086` while a clean merge passes; `--no-verify` lands the
  commit anyway; and with `extensions.worktreeConfig` pointing elsewhere the installer refuses
  with `core.hooksPath is '.other-hooks', not '.githooks'`.
- Every row of the I/O & Edge-Case Matrix has a case in `scripts/lint_selftest.py`, and every
  one of them ran and passed in the `pixi run ci` above.
- `pixi run bootstrap` was never run against this repository's own `.git`; `git config --get
  core.hooksPath` in this worktree still returns nothing.

**Residual risks**
- Nothing in this branch has installed the hooks anywhere that survives it. `core.hooksPath`
  is per clone and cannot be committed, so the first acceptance criterion — "given a clone
  with `pixi run bootstrap` run" — is verified only in throwaway clones. The operator action
  below is what makes it true of a real one.
- The bmad-loop orchestrator's own commit subjects (`story <id>: implemented and reviewed via
  bmad-loop`) do not follow Conventional Commits and are rejected by this contract; its merge
  commits are accepted. Verified against the shipped checker. Installing the hooks in a clone
  the loop drives will block those commits unless the loop uses `--no-verify` or its subjects
  change.
- The checks read the working tree, not the index, so a partial `git add` is judged against
  files the commit does not contain, in either direction. Documented in the hook, the ADR and
  the README; CI reads the merged tree and is the answer to it.
- The hooks assume the work-tree root and the pixi manifest root are the same directory, which
  the spec's standalone-checkout constraint guarantees but nothing enforces. In a layout where
  they differ, the relative message path git passes does not resolve from the directory pixi
  runs the task in, and the commit is refused with `no such file` rather than passing.
- `check_commit_msg.py` judges the subject only. A `feat!:` subject carries no obligation to
  have a `BREAKING CHANGE:` footer, and a footer is neither required nor validated. Recorded
  in the checker's docstring and in ADR 0011.

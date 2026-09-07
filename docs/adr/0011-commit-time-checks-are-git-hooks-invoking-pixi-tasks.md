# 11. Commit-time checks are git hooks invoking pixi tasks

Date: 2026-09-07 · Status: Accepted · Spine: AD-9, AD-21

## Context

A shellcheck violation, a malformed YAML file or a commit message nobody can group was
discovered only after the commit had landed and a hosted run had finished. The feedback
costs seconds to produce and was costing minutes to receive, and the repository had no
commit-message contract at all — `updated the readme` and `feat(hooks)!: ...` were equally
acceptable, so the history could not be read by tooling and nothing made a change's kind or
scope machine-readable.

The constraint that shapes the answer is AD-9: pixi is the single task surface, and every
validation tool is a pinned dependency resolved from `pixi.lock`. Anything that declares a
tool version a second time reintroduces exactly the drift the lockfile exists to remove —
and a commit-time check that ran a *different* shellcheck from CI's would be worse than no
check, because it would be trusted.

## Decision

Three tracked hooks in `.githooks/`, installed per clone by `pixi run bootstrap` setting
`core.hooksPath`. Each hook's whole body is a `cd` to the work tree root and an
`exec pixi run <task>`: `precommit` for both `pre-commit` and `pre-merge-commit`, and
`commit-msg` for `commit-msg`. `pre-merge-commit` is not redundant — git runs it, and never
`pre-commit`, when `git merge` creates a commit, so without it every merge commit would land
with the offline checks never run. No hook names a tool and no hook pins a version, so every
check a commit gets is the same task definition, at the same pinned version, that the hosted
run uses.

`precommit` is a **strict subset** of `lint`, and `scripts/lint_selftest.py` asserts the
containment rather than documenting it — a hook can never grow a check the gate does not
run. `lint-compose` and `lint-config` are the two excluded: they resolve the compose model
through a container runtime, and a hook that cannot run while Docker is down trains the
`--no-verify` reflex that makes hooks worthless.

The message contract is `type(optional-scope)!: description` with the type drawn from a
declared set, enforced by `scripts/check_commit_msg.py` — Python, because a hook body cannot
be tested and that can. It judges the subject line only: a `BREAKING CHANGE:` footer is
neither required alongside the `!` marker nor validated when one appears.

## Rejected

**The `pre-commit` framework.** Its normal mode declares a `rev:` per hook repository, which
is a second pinning of the same tools `pixi.lock` already pins — the drift this decision
exists to prevent. Driving it entirely from `repo: local` entries avoids that but then
contributes nothing except one more tool to install and one more environment cache to keep
current. A tracked directory plus one line of `git config` is the whole mechanism here, and
it is inspectable in two files.

**Copying hooks into `.git/hooks` at bootstrap.** An installed copy silently goes stale the
moment the tracked hook changes, and nothing would report it. `core.hooksPath` points at the
tracked files themselves, so there is no copy to drift.

**Making the hooks the gate.** CI (ADR 0009's workflow, story 1.3) reads the merged tree and
stays authoritative. `--no-verify` remains supported and is documented as supported: a check
a developer cannot get past is one they disable permanently.

## Consequences

The hooks are per clone and cannot be committed — `.git/hooks` is not part of the tree — so
`pixi run bootstrap` is a step every contributor must take, and a clone that skipped it is
checked by CI alone. That is the accepted cost of not shipping a bootstrap that runs itself.
`core.hooksPath` is written to the repository config that every linked worktree of a clone
shares, so installing from one worktree installs for all of them.

The checks read the **working tree, not the index**. A partial `git add` is judged against
files the commit does not contain, so a defect can be committed while an unstaged fix hides
it, or a clean commit can be rejected for an unstaged defect. Stashing to correct that is
unsafe in a repository with linked worktrees sharing one stash stack. CI reads the merged
tree and is the answer to it.

`git merge` runs `pre-merge-commit` and then `commit-msg` — never `pre-commit` — and the
message it hands the second is one git generated itself, so the contract has to accept git's
own `Merge branch ...`, `Revert "..."`, `Reapply "..."`, `fixup!`, `squash!` and `amend!`
forms or every merge here becomes impossible. Both halves are rows of the self-test, proved
with a real merge rather than with fixtures that look like one.

`.githooks/` joins every universal walk in `scripts/lint_selftest.py`: the `FORBIDDEN`
substring scan, `lint-shell`'s coverage assertion and the fresh-clone tracking check. A hook
is shell that runs on every commit; an unlinted one is the silent skip one directory over.
Because the files are extensionless, the walks name the directory rather than a suffix.

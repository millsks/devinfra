# 5. pixi as the single task surface

Date: 2026-09-06 · Status: Accepted · Spine: AD-9, AD-21

## Context

`make lint` reports success for checks it never ran:

```make
@if command -v shellcheck >/dev/null 2>&1; then \
    shellcheck scripts/*.sh ... ; \
else echo "shellcheck not installed — skipping"; fi
@python3 -c "import sys,yaml; ..." || echo "PyYAML not installed — skipping"
```

On a machine without those tools the target exits 0 having validated a subset of what it
claims. A check that degrades to a no-op and still passes is the same silent-failure class
the README's Gotchas section exists to prevent — and it undermines CI at the root, since
"run the same checks locally" is meaningless when the local checks are conditional on
ambient tooling.

## Decision

pixi is the single task surface and provisions every validation tool as a pinned dependency,
so a missing tool is impossible rather than skipped. **No task may branch on `command -v`.**
Non-trivial shell moves from Makefile recipes into `scripts/*.sh`. The `Makefile` is retained
only as a deprecation shim.

## Rejected

**Keeping Make and installing tools separately.** Restates the problem: nothing enforces the
install, so the skip branch stays reachable.

**Keeping both surfaces permanently.** Two task definitions drift.

## Consequences

pixi becomes a prerequisite for a repository whose pitch is low friction — the strongest
argument against, accepted because the alternative is a lint that lies.

Three things do not translate directly and move to `scripts/`: the health-wait polling loop,
the `read -p` destroy confirmation, and the `urls` printf block. This is an improvement
regardless of runner — logic in a Makefile recipe cannot be tested.

`scripts/select.sh` becomes load-bearing for four separate invariants, so scripts are
verified like code: shellcheck-clean, with tests for closure correctness, empty-selection
refusal, and unknown-name refusal.

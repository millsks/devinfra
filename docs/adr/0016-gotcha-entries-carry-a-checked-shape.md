# 16. Gotcha entries carry a checked shape

Date: 2026-09-07 · Status: Accepted

## Context

ADR 0012 made a `gotchas.md` one of the five things every Module carries, and made the
check presence-based: `lint-config` asks whether the file exists and never what is in it.
That was the right bar for four of the five items, and it left the fifth as 77 free-prose
bullets across thirteen files in no fixed shape.

The shape mattered more than it looked. Of those 77, seven named an affected version;
roughly half described a symptom; about three in five said what to do. A bullet that names
a cause and no symptom is a bullet nobody recognises when the thing actually happens, and
one that names no version is a bullet that outlives the release it was true of. Eight of
them were also duplicated verbatim into `README.md`, where the two copies were free to
drift, and one had already gone false: `--import-realm only creates realms that do not
already exist` was true, but the sentence that followed it — that dropping the `keycloak`
database is therefore the only way an edited realm file takes effect — was not, and
`scripts/keycloak-reimport.sh` dropped the database because of it (FR-18, AD-12).

## Decision

**Every entry in a `gotchas.md` carries four fields, populated, in fixed order.** One
`###` heading per entry — the heading is the claim, so the file still skims like the prose
it replaces — followed by `- **Symptom:**`, `- **Cause:**`, `- **Fix:**` and
`- **Affected versions:**`. `Affected versions:` is a version expression or the exact
phrase `Not version-specific`; `TBD`, `TODO`, `unknown`, `n/a` and `?` are refused.

**An entry may add a fifth field, `Verified by:`,** naming the check that catches a
regression as a backticked repo-relative path followed by prose for a reader.

**The check is a separate offline task, `scripts/check_gotchas.py`.** It globs
`services/*/gotchas.md` and refuses an empty set, exactly as `assert_config.py` and
`smoke-test.sh` refuse one. It reads Markdown and needs no container runtime, so it joins
both `[tasks.lint]` and `[tasks.precommit]` — `lint-config`, which resolves the compose
model through a runtime, is deliberately kept out of the hook and could not carry this.

**ADR 0012's presence rule stands for the other four contract items**, and stands for this
one too: `lint-config` still asks only whether the file is there. The shape is a second,
narrower question asked by a second check.

**The README carries no copies.** Its `## Gotchas worth knowing` section describes the
register and points at it.

## Rejected

**A central register in Core.** A `docs/gotchas.md`, or an index Core maintains, is the
registry ADR 0012 removed wearing a different name: a Module would enter the register by
being named in Core, and the file nobody editing `services/<name>/` opens is exactly the
file that goes stale.

**A YAML sidecar beside each `gotchas.md`.** Machine-readable, and unreadable at the moment
it is needed — a developer meets a gotcha while reading the Module's directory, not while
parsing it. Markdown with a fixed field shape is checkable enough for the properties worth
checking, and it stays the thing a person reads.

**A minimum entry count above one.** ADR 0012 rejected "a `gotchas.md` above some length"
as unfalsifiable and an invitation to pad, and that stays rejected. One entry is the bar,
and it is the same bar `justified()` already sets on a `seed.none` marker.

**Folding the shape check into `assert_config.py`.** It is `RUNTIME_BOUND`, so a commit-time
check of the register would have been impossible for no reason at all.

**Checking what a `Verified by:` check asserts.** Not machine-decidable. See below.

## Consequences

**A fixed field shape is falsifiable where a minimum length is not.** "Is there a `Fix:`
line, and is it non-empty and not `TBD`?" has one answer, the same one for every reader,
and a defect is repaired by writing the missing sentence. "Is this file long enough?" has
no such answer: the number is arbitrary, and the cheapest way to satisfy it is to pad,
which makes the register worse while turning the check green. The rule ADR 0012 rejected
judged content; this one judges structure, and whether the sentence after `**Fix:**` is any
good remains a review's job.

**`Verified by:` is checked for path existence and nothing more.** A field naming a check
that no longer exists is worse than no field at all, so the path is resolved against the
repository root and a dead one fails the build. Whether the named check actually asserts
the fix is not machine-decidable and is not attempted — the value of the field is that the
link rots loudly rather than quietly.

**The register is now a place a check can point back at.** Most `Verified by:` lines name a
check that already existed. Three entries gained one only because two assertions were added
to earn them: the Postgres data-directory mount, in that Module's own `smoke.sh`, and a
self-test pin on Prometheus's `--web.enable-remote-write-receiver`, which the Prometheus and
Tempo entries both stand on. A gotcha whose fix is a specific configuration fact should be
asserted somewhere, and the field is what makes it obvious when it is not.

**A gotcha that is genuinely not recoverable from the code cannot be written.** Every field
must be populated from what the repository or a verified planning artifact already
evidences, so an entry whose symptom nobody recorded is rewritten to state what is actually
known rather than invented to fill the field. That is a constraint on the author, not
something the check can see.

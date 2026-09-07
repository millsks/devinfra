# 10. Image updates are proposed by regex over the dotenv template

Date: 2026-09-07 · Status: Accepted · Spine: AD-9, CAP-20

## Context

"Image currency": every pinned tag either matches the current upstream release or carries a
dated written reason for lagging. Story 1.5 ended with four pins knowingly behind, because
a human had to notice the release, edit two files, run the suite and open the pull request.
Nothing watched upstream, so currency decayed by default and was restored only by a chore
somebody remembered.

The CI suite from story 1.3 would catch a bad bump, but only if something opens a pull
request for it to run against. So the missing piece is a proposer, not a checker.

Every tag lives in two places that must move together — the `<NAME>_VERSION=` declaration
in `.env.example`, which `pixi run init` copies into a contributor's `.env`, and the
`${<NAME>_VERSION:-<tag>}` fallback in `compose.yaml`, which is what a clone with no `.env`
actually runs. `pixi run lint-pins` fails any change that moves one without the other.

## Decision

Renovate, configured with a single `customManagers` regex entry that reads **both** files,
run by `.github/workflows/renovate.yml` on a schedule, and gated locally by
`pixi run lint-renovate`.

The annotation lives in `.env.example`, immediately above each declaration:

```
# renovate: datasource=docker depName=redis
REDIS_VERSION=8.10.1-alpine
```

One manager, not two, reads both files. Renovate resolves a branch from the dependency and
its new value, so the same `depName` at the same `newValue` extracted from `.env.example`
and from `compose.yaml` lands on one branch — one pull request carrying both edits. That is
the only shape `lint-pins` accepts, so the bot's proposals are green by construction rather
than by review.

That pairing is the load-bearing claim here, and it is verified rather than reasoned about.
An `--dry-run=extract` cannot show it: extraction computes no branches at all. A full
`--dry-run=full` against Renovate 44.66.1 does, and reports
`"dependencyStatus": {"outdated": 4, "total": 13}`, then `12 flattened updates found` — each
of the four outdated images appearing twice, once per file — collapsing to
`Returning 6 branch(es)`, with both `"packageFile": ".env.example"` and
`"packageFile": "compose.yaml"` present in the update set. Twelve file-level updates
resolving to a handful of branches is the pairing, observed.

The bot authenticates with `RENOVATE_TOKEN`, a repository secret, never the workflow's own
`GITHUB_TOKEN`. A pull request opened with `GITHUB_TOKEN` triggers no `pull_request`
workflow: the proposals would arrive looking validated, with nothing having run.

`scripts/assert_renovate.py` applies the configuration's own `matchStrings` to the files its
own `managerFilePatterns` select, and fails on zero detections. It runs offline from
pixi-provisioned tooling, so `pixi run ci` reaches it on every change.

## Rejected

**Renovate's `docker-compose` manager.** It parses `image:` values and skips the
`repo:${VAR:-tag}` form — the interpolation is the whole point of the two-tier
configuration, and the manager treats the value as unpinnable. It detects zero dependencies
here and reports a clean run doing so, which is the silent skip this epic exists to remove.

**Dependabot.** It cannot read a dotenv file at all, and its `docker` ecosystem has the same
blind spot as the Compose manager. It also offers no equivalent of `customManagers`.

**Putting the annotation in `compose.yaml` instead.** The `image:` line already names the
repository, so an annotation there would restate it — and the fallback tag is the copy, not
the source. `.env.example` is where a contributor reads what version they will get and where
`pixi run init` seeds it from, so that is where the pin is declared and where its metadata
belongs.

**A pull request that edits only `.env.example`,** as the epic's acceptance originally said.
Written before `lint-pins` existed. Such a pull request is red on arrival and proves nothing;
the resolution keeps both rules — exactly one image per pull request, in both places that
image's version is written down.

**Trusting the configuration without a local check.** A regex manager that matches nothing
opens no pull requests and reports success. There would be no signal at all, indefinitely,
and the repository would look current because nothing was proposing changes.

**A check with its own copy of the regexes.** It would keep passing after the configuration's
regexes broke, which is precisely the failure it exists to catch. `assert_renovate.py` reads
`renovate.json`'s patterns and runs those.

## Consequences

`renovate.yml` is the first workflow here that is not `ci.yml`, so the self-test's
per-workflow rule splits: `ci.yml` must run on `push` and `pull_request`; any other workflow
must be scheduled or hand-started and must **not** be reachable by either. A second workflow
on those events would be a second, weaker definition of what a change is checked by. Every
other rule — bounded jobs, no softeners, no self-installed tools, `run:` steps are `pixi run`
tasks — stays universal.

The operator must supply `RENOVATE_TOKEN` before the bot does anything. Until then the
scheduled run fails loudly, which is the correct signal: a bot that silently does nothing is
what this ADR is about.

The README service table's Version column records abbreviated versions (`8.10`, `12.2`), so
no regex can maintain it. `prBodyNotes` tells the reviewer to update the row by hand; the
column stays unguarded, as story 1.5 already recorded.

`SILO_VERSION` needs a `regex:` versioning to order `RELEASE.<date>T<time>Z`. The annotation
carries it, and a `packageRules` entry restates it for the `compose.yaml` half of the pair,
where there is no annotation to read — without that restatement the two extractions would
disagree about `newValue` and the bot would edit one file alone.

`enabledManagers: ["custom.regex"]` is deliberately absolute, and it disables the
`github-actions` manager along with everything else. So `actions/checkout@v4`,
`prefix-dev/setup-pixi@v0.8.1` and the Renovate action itself now have no proposer and will
decay exactly the way the image pins did before this story. That is a known, accepted gap,
not an oversight: scoping this story to image pins is what keeps the detected-dependency
count attributable to these two regexes, and widening it here would have meant asserting a
count that mixes two unrelated sources. Watching action refs is a later story's work.

Renovate is not a pixi dependency and is never run by `pixi run ci`: it needs Node, network
and a platform token. `npx --yes renovate --platform=local --dry-run=extract` is the real
extraction and is documented in the README as evidence a maintainer gathers by hand;
`--dry-run=full` is what shows the branch resolution above.

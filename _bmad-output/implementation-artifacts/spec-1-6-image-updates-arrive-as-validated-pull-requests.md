---
title: 'Image updates arrive as validated pull requests'
type: 'feature'
created: '2026-09-07'
status: 'awaiting-operator'
baseline_revision: '46be76136d951bcbf6d4082a47dbd44263d91a13'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/implementation-artifacts/spec-1-5-every-image-brought-current.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
deferred:
  - summary: >-
      Nothing inside the repository can tell whether the Renovate App is still installed, so
      image currency can decay silently.
    evidence: |-
      The self-hosted workflow was a tracked file, so its absence or misconfiguration reddened
      the gate. The hosted App is repository state on GitHub's side, invisible to `pixi run ci`.
      If it were uninstalled tomorrow, `lint-renovate` stays green — it proves renovate.json
      would detect the pins, not that anything is reading it — and no proposals would arrive.
      That is the failure mode ADR 0010 exists to prevent, moved outside the gate's reach. The
      Dependency Dashboard issue is the only signal, and the README now says to check it.
      Closing it honestly needs a network call to the GitHub API for the installation, which no
      current task can make; checking pin currency against upstream is the other half and would
      catch decay regardless of cause.
    location: >-
      renovate.json, scripts/assert_renovate.py
    severity: medium
operator_actions:
  - "Mint a credential for the bot and add it as the repository secret `RENOVATE_TOKEN`: a fine-grained personal access token, or a GitHub App installation token, scoped to this repository with `contents: write`, `pull-requests: write` and `issues: write`. It must not be the workflow's own `GITHUB_TOKEN` — a pull request opened with that token triggers no `pull_request` workflow, so the bot's proposals would arrive with no checks having run, which is the one outcome this story exists to prevent."
  - "Merge this branch to `main`, then start the Renovate workflow by hand from the Actions tab (`workflow_dispatch`) rather than waiting for Monday's schedule. Nothing in this repository has ever authenticated as the bot; that run is the story's actual acceptance evidence for the first criterion."
  - "On that first run, confirm the bot opens one pull request per outdated image and no batched one. A local `--dry-run=full` predicts six branches from four outdated images — RedisInsight, Loki, and Tempo and Grafana at both their current-major and next-major lines — so anything fewer means grouping was re-enabled somewhere the local check cannot see."
  - "Open the first bot pull request and confirm all three CI jobs (`validate`, `stack`, `stack-podman`) actually ran on it. If the checks tab is empty, the token is a `GITHUB_TOKEN` equivalent and must be replaced — every in-repository gate stays green in that case."
  - "Before merging any bot pull request, update its service's row in the README's Version column by hand. The column records abbreviated versions (`8.10`, `12.2`), so no regex maintains it and no check enforces it; the pull request body carries the same reminder."
---

<intent-contract>

## Intent

**Problem:** Upgrading an image is a manual chore the maintainer postpones. Nothing watches
upstream, nothing proposes the bump, and story 1.5 ended with four images knowingly behind
because a human had to drive every step. The CI suite from story 1.3 exists and would catch a
bad bump, but only if something opens a pull request for it to run against.

**Approach:** Annotate every `*_VERSION` declaration in `.env.example` with a
`# renovate: datasource=docker depName=<repo>` line, commit a Renovate configuration whose
`customManagers` regex reads those annotations, and add a scheduled workflow that runs the bot.
Prove the regex actually matches with a new offline check, `pixi run lint-renovate`, wired into
`lint` and therefore into `ci` — a bot whose regex silently matches nothing is exactly the
silent skip this epic exists to remove.

## Boundaries & Constraints

**Always:**
- Every `*_VERSION` declaration in `.env.example` is immediately preceded by its
  `# renovate: datasource=docker depName=<repo>` annotation, and that `depName` is the image
  repository `compose.yaml` actually names for the same variable.
- A bot pull request changes exactly one image's version, in both places that version lives:
  the `.env.example` declaration and the matching `${VAR:-tag}` fallback in `compose.yaml`.
  `pixi run lint-pins` (story 1.5) fails any pull request that moves only one of the two, so a
  one-file update would be red by construction and would prove nothing.
- The bot's pull requests must run the full CI suite from story 1.3. That means the token the
  bot authenticates with is not the workflow's own `GITHUB_TOKEN` — a pull request opened with
  `GITHUB_TOKEN` triggers no `pull_request` workflow — and `ci.yml`'s `pull_request` trigger
  carries no `paths`/`paths-ignore` filter that a `.env.example`-only diff could fall through.
- `lint-renovate` runs offline, from pixi-provisioned tooling only, and fails when it detects
  zero dependencies.
- The security posture stays fixed: this story changes no credential, port binding or service
  configuration.

**Never:**
- Do not apply any image bump here. The four knowingly-behind pins (DW-9) stay where story 1.5
  left them; this story builds the machine that proposes them, and the machine's first run is
  the operator's, not this branch's.
- Do not relax `lint-pins`, `assert_config.py`, or any existing gate to make bot pull requests
  pass. If a bot pull request would be red, the configuration is wrong, not the gate.
- Do not enable Renovate's other managers (`github-actions`, `docker-compose`, `dockerfile`).
  Scope is image pins; anything else makes the detected-dependency count unattributable.
- Do not require network, Node, or a platform token in any task `pixi run ci` reaches.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Config detects pins | `renovate.json` + tracked `.env.example`/`compose.yaml` | `lint-renovate` prints the detected-dependency count and exits 0 | No error expected |
| Regex matches nothing | A `matchStrings` entry that matches no file | Exit non-zero naming the manager and the pattern | `detected no dependencies` diagnostic |
| Missing annotation | A `*_VERSION=` line with no `# renovate:` line above it | Exit non-zero naming the variable and its line | Diagnostic per offending variable |
| Wrong depName | Annotation says `redis`, compose says `redis/redisinsight` | Exit non-zero naming the variable and both repositories | Diagnostic per disagreement |
| CI made skippable | `ci.yml` gains `paths:` under `pull_request` | Exit non-zero — a filtered gate cannot promise to run on a bot PR | Diagnostic naming the filter |
| Malformed config | `renovate.json` is not valid JSON | Exit non-zero naming the file and the parse position | Diagnostic, no traceback |

</intent-contract>

## Code Map

- `.env.example:69,82,97,117,129,136,143,146,152,156,159,162,165` -- the thirteen `*_VERSION`
  declarations, each of which gains one annotation line immediately above it. Line numbers shift
  as annotations are inserted; the variable names are the stable anchor.
- `compose.yaml:39,76,100,168,194,222,246,269,292,314,329,349,367,385` -- fourteen `image:` lines,
  the source of truth for each variable's `depName`. `SILO_VERSION` appears twice (`minio`,
  `minio-init`), so a variable-to-repository mapping is many-to-one and must be built by variable,
  not by line. Read-only in this story.
- `scripts/assert_pins.py` -- the shape to copy exactly: `REPO` constant, module docstring stating
  why the check exists and why an adjacent check cannot see it, `check()` returning
  `(count, problems)`, a zero-match refusal, `main(argv)` accepting optional path overrides so the
  self-test drives fixtures instead of editing tracked files, `raise SystemExit(main(...))`. Its
  `DECLARATION` regex and `dotenv_declarations()` are the dotenv-reading precedent. Read-only.
- `scripts/lint_selftest.py:1456-1595` -- the `lint-pins` block: `pins()` fixture helper, one
  `expect()` per defect proved to fail, then the task run over the tracked pair, then a coverage
  assertion tying the reported count to the number of `image:` keys. `lint-renovate` needs the
  same treatment, including the coverage tie.
- `scripts/lint_selftest.py:1730-1795` -- the universal per-workflow loop. It currently requires
  **every** workflow to run on `push` and `pull_request`; a scheduled bot workflow cannot. Split
  it: `ci.yml` (the gate) keeps that requirement; any other workflow must instead declare
  `schedule` or `workflow_dispatch` and must **not** be reachable by `push` or `pull_request`, so
  a second workflow can never become a weaker second definition of what a change is checked by.
  Every other assertion in that loop (softeners, no self-installed tools, parses, jobs declared,
  `timeout-minutes` ≤ 15, step guards `failure()`-only, `run:` steps are `pixi run` tasks) stays
  universal and must keep passing unchanged.
- `scripts/lint_selftest.py:1799-1806` -- `expect("ci.yml declares exactly the three CI jobs", ...)`
  is keyed on `ci.yml` already, so a second workflow file does not disturb it.
- `pixi.toml:[tasks.lint]` -- the `depends-on` list `lint-renovate` joins; the self-test already
  asserts every `lint-*` task is reachable from `lint`, so adding the task without wiring it fails.
- `pixi.toml:[feature.dev.dependencies]` -- `python 3.14`, `pyyaml`, `ruff`, `mypy` are present.
  `lint-renovate` needs nothing new: `json` is stdlib and `yaml` is already a dependency.
- `.github/workflows/ci.yml:23-27` -- the `pull_request: branches: [main]` trigger the bot's pull
  requests must land on, with no `paths` filter. Read-only; the new check asserts it stays so.
- `.yamllint.yaml` -- `lint-yaml` globs `.github/workflows/*.y*ml`, so the new workflow must be
  yamllint-strict-clean under this config (quote the `on:` key, respect line length).
- `docs/adr/0009-podman-is-verified-through-the-docker-compatible-socket.md` -- the ADR house
  style and the numbering to continue from (0010 is next).
- `README.md:11-23` -- the service table's Version column. Read-only evidence that a bot pull
  request leaves it stale: the column is abbreviated (`8.10`, `12.2`), so no regex can maintain
  it. The configuration carries a `prBodyNotes` line telling the reviewer to update it.
- Read-only evidence, verified 2026-09-07 against the live schema and registry: Renovate's current
  release is 44.66.1; `renovatebot/github-action`'s is v46.2.6; the schema at
  `https://docs.renovatebot.com/renovate-schema.json` declares `customManagers[].managerFilePatterns`
  (not the removed `fileMatch`), `matchStringsStrategy` ∈ {any, recursive, combination}, and
  `groupName` as nullable.

## Tasks & Acceptance

**Execution:**
- `.env.example` -- insert one `# renovate: datasource=docker depName=<repo>` line immediately
  above each of the thirteen `*_VERSION=` declarations, taking `<repo>` from the `image:` line in
  `compose.yaml` that references it. Append ` versioning=<v>` only where the default `docker`
  versioning cannot order the tag: `SILO_VERSION`'s `RELEASE.<date>T<time>Z` form needs a
  `regex:` versioning. Change no value. -- the annotation is AC 2, and it is also the input the
  `customManagers` regex reads.
- `renovate.json` (new) -- strict JSON (comments live in `description` fields, which Renovate
  accepts at any level, so stdlib `json` can parse the file). `enabledManagers: ["custom.regex"]`;
  one `customManagers` entry whose `managerFilePatterns` cover `.env.example` and `compose.yaml`
  and whose two `matchStrings` read the annotated declaration and the `${VAR:-tag}` fallback
  respectively; a `packageRules` entry setting `groupName: null` so each image gets its own pull
  request; semantic commit settings matching this repository's Conventional Commits; a
  `prBodyNotes` entry naming the README row a bump leaves stale. -- one dependency, matched in
  both files, produces one branch and therefore one pull request that moves both.
- `scripts/assert_renovate.py` (new) -- parse `renovate.json`, apply each `customManagers` entry's
  own `matchStrings` to the files its `managerFilePatterns` select, and assert: the config parses;
  every enabled manager is `custom.regex`; every `matchStrings` pattern matches at least once;
  every `*_VERSION` declared in `.env.example` is detected with a `depName` equal to the
  repository `compose.yaml` names for it; every `*_VERSION` declaration is immediately preceded by
  its annotation; `ci.yml`'s `pull_request` trigger targets `main` and declares no
  `paths`/`paths-ignore`; the total detected count is non-zero. Print the count on success.
  Accept optional path overrides like `assert_pins.py` does. -- this is the dry run AC 3 asks for,
  made reproducible: no network, no Node, no platform token.
- `pixi.toml` -- add `[tasks.lint-renovate]` running `python scripts/assert_renovate.py`, and add
  `lint-renovate` to `[tasks.lint]`'s `depends-on`. -- an unreachable check is one CI never runs.
- `.github/workflows/renovate.yml` (new) -- `schedule` plus `workflow_dispatch`; `permissions:
  contents: read`; `timeout-minutes: 15`; checkout then `renovatebot/github-action` pinned to its
  current major, authenticating with `secrets.RENOVATE_TOKEN`. No `run:` step, no installer, no
  `continue-on-error`. -- the bot has to be something the repository runs, not a service someone
  remembers to configure.
- `scripts/lint_selftest.py` -- split the workflow-trigger assertion as the Code Map describes,
  add the bot-workflow assertions (uses the pinned Renovate action; the token is a repository
  secret and is not `GITHUB_TOKEN`; declares `schedule` or `workflow_dispatch`), and add a
  `lint-renovate` block that proves each rejection path fires on a planted defect and that the
  task passes on the tracked files, with the detected count tied to the number of `*_VERSION`
  declarations. -- every check in this repository is proved to fail on a real defect.
- `docs/adr/0010-image-updates-are-proposed-by-regex-over-the-dotenv-template.md` (new) -- record
  why the Compose and Dependabot managers are not options, why the annotation lives in
  `.env.example`, and why a bot pull request touches `compose.yaml` too. -- the next maintainer
  will otherwise "simplify" this back to the Compose manager, which silently matches nothing.
- `README.md` -- add a "Keeping images current" section: what the bot does, the annotation
  contract, `pixi run lint-renovate`, the optional real dry run
  (`npx --yes renovate --platform=local --dry-run=extract`), and the token the operator must
  supply. Add the `lint-renovate` row to the task table. -- the operator action must be written
  down where an operator looks.

**Acceptance Criteria:**
- Given the tracked tree, when `pixi run ci` runs, then it exits 0 and `lint-renovate` reports a
  non-zero detected-dependency count naming every `*_VERSION` variable.
- Given `renovate.json`, when a `*_VERSION` declaration's annotation is removed, renamed, or given
  a `depName` that disagrees with `compose.yaml`, then `pixi run lint-renovate` exits non-zero and
  names the variable.
- Given `.github/workflows/`, when `pixi run test` runs, then every workflow is still bounded,
  softener-free and installer-free, `ci.yml` still runs on `push` and `pull_request`, and
  `renovate.yml` is asserted to be scheduled-only and to authenticate with a non-`GITHUB_TOKEN`
  secret.
- Given `ci.yml`, when a `paths:` or `paths-ignore:` filter is added under `pull_request`, then
  `pixi run lint-renovate` exits non-zero — the promise that a bot pull request runs the full
  suite is mechanical, not editorial.
- Given no image bump is applied in this branch, when the diff is reviewed, then no `*_VERSION`
  value and no `${VAR:-tag}` fallback has changed.

## Spec Change Log

**2026-09-07, implementation — the real dry run was reachable, and it confirmed both claims.**

`npx --yes renovate@44 --platform=local --dry-run=extract` ran on this workstation. Two runs,
because the local platform lists files through git and `renovate.json` was untracked on the
first: unstaged it fell back to onboarding and ran the stock managers; with the new files
staged it reported `{"regex": {"fileCount": 2, "depCount": 27}}` and nothing else — 27
references across `.env.example` and `compose.yaml`, zero `skipReason` entries, which is the
same 27 `pixi run lint-renovate` counts.

The unstaged run is the ADR's evidence rather than a mistake: the `docker-compose` manager
found all fourteen `image:` lines and marked every one `skipReason: "contains-variable"`. The
Compose manager genuinely detects nothing here, confirmed against the running bot rather than
asserted from documentation.

**One addition to the plan.** The extract shows the `.env.example` half of `SILO_VERSION`
carrying `versioning: "regex:…"` from its annotation while the two `compose.yaml` halves carry
`versioning: "docker"` — the compose line has no annotation for the `versioningTemplate` to
read. Left there, the two halves would compute different `newValue`s and the bot would edit one
file alone, which `lint-pins` rejects. `renovate.json` therefore also carries a `packageRules`
entry restating that versioning for `pgsty/silo`, so both extractions agree. This is additive to
the spec's plan, not a departure from it, and the reason is recorded in ADR 0010.

**2026-09-07, review round 1 — the pairing claim was upgraded from extract to a full run.**

`--dry-run=extract` computes no branches, so it could never have evidenced "one branch, one
pull request carrying both edits". A `--dry-run=full` against Renovate 44.66.1 does:
`"dependencyStatus": {"outdated": 4, "total": 13}`, then `12 flattened updates found` — each
of the four outdated images appearing twice, once per file — collapsing to
`Returning 6 branch(es)`, with `renovate/redis-redisinsight-3.x` appearing once under
`"packageFile": ".env.example"` and once under `"packageFile": "compose.yaml"`. ADR 0010
now cites those figures instead of the extract.

The same round hardened `assert_renovate.py` against ten ways the configuration could break
while the check stayed green — an empty capture, a deleted `datasourceTemplate` or
`versioningTemplate`, a `matchStringsStrategy` other than `any`, an annotation `versioning=`
no `packageRules` entry restates, `types`/`branches-ignore` on the gate's `pull_request`
trigger, a commented-out compose `image:` line, a non-UTF-8 input, an RE2-incompatible
pattern, and a restored `extends` or `groupName` — each now proved to fail in the self-test.
The reported count also changed from distinct repositories to distinct variables, so the
self-test's coverage tie to the `*_VERSION` declaration count holds by construction.

**Not done, by design.** No image bump is applied. The four knowingly-behind pins from story 1.5
are untouched; `git diff main -- .env.example` shows no `*_VERSION` line changed, and
`compose.yaml` is unmodified.

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 34 findings — high 0, medium 20, low 11, false 3, maybe-false 0
- findings:
  - `[medium]` `[patch]` blind-hunter: `dependencyDashboard: true` needs `issues: write`, which the documented token scope omits — confirmed against the published schema (`dependencyDashboard` default `false`; it creates a repository issue). `issues: write` added to the scopes in `README.md` and the workflow header.
  - `[medium]` `[patch]` blind-hunter: Renovate's default `prHourlyLimit` of 2 silently caps a weekly run at two pull requests — confirmed from `renovate-schema.json` (`prHourlyLimit` default 2, `prConcurrentLimit` default 10); story 1.5 alone left four pins behind. `renovate.json` now sets both to 0.
  - `[medium]` `[patch]` blind-hunter: the `pgsty/silo` `packageRules` versioning and the `.env.example` annotation can drift apart unchecked — `check()` compared only `depName`. `assert_renovate.py` now requires a byte-identical `packageRules` restatement for every annotated `versioning=`.
  - `[medium]` `[patch]` blind-hunter: the `datasource` capture group is extracted and discarded, so `datasource=github-releases` would pass — `Detection` kept only three fields. The check now asserts every reference resolves a `docker` datasource.
  - `[low]` `[patch]` blind-hunter: `selects()` matched `managerFilePatterns` against the basename while Renovate matches the repository-relative path — true, latent while both files sit at the repository root. `selects()` now matches both forms.
  - `[medium]` `[patch]` blind-hunter: `translate()` compiles under Python `re` while Renovate uses RE2, so a lookaround or backreference would be green here and dead there — `translate()` now refuses both construct families, and the docstring states the boundary.
  - `[medium]` `[patch]` blind-hunter: the "one branch, one pull request carrying both edits" claim rested on `--dry-run=extract`, which computes no branches — correct. A `--dry-run=full` was run: `12 flattened updates found` → `Returning 6 branch(es)`, both `packageFile` values present. ADR 0010 now cites those figures.
  - `[low]` `[patch]` blind-hunter: `renovatebot/github-action@v46` is itself a mutable major while the comment claims drift is closed — true. Comment corrected; a SHA was rejected because nothing in this repository watches action SHAs.
  - `[low]` `[patch]` blind-hunter: disabling the `github-actions` manager leaves `actions/checkout` and the Renovate action with no proposer, undocumented — true. Recorded as a deliberate gap in ADR 0010's Consequences.
  - `[medium]` `[patch]` blind-hunter: several `assert_renovate.py` rejection paths were never proved to fire, against this repository's own rule — true, notably the compose-side-missing branch. The self-test's `lint-renovate` block grew from 25 to 67 cases.
  - `[low]` `[patch]` blind-hunter: `packageRules[0]`'s `groupName: null` restates the default and nothing asserts grouping stays off — true. The check now rejects any `extends` and any non-null `groupName`.
  - `[low]` `[patch]` blind-hunter: `renovate.json` sat outside `lint-json`'s glob while the task claims "every JSON config" — true. Glob widened; `lint-json: OK renovate.json`.
  - `[low]` `[patch]` blind-hunter: `LOG_LEVEL: info` sets Renovate's own default, so its comment's justification does not hold — true. Variable and comment removed.
  - `[false]` `[reject]` blind-hunter: `concurrency: group: renovate-${{ github.workflow }}` interpolates a constant — the interpolation is constant, but the finding names no bad outcome, and `ci.yml` already uses the identical construction as house style.
  - `[low]` `[patch]` blind-hunter: "the scheduled run fails loudly without the secret" is asserted twice and verified nowhere — true. Both statements softened to what is known.
  - `[false]` `[reject]` blind-hunter: a bot pull request could propose a `-pg18` pgvector tag and pass CI on an empty volume — refuted by the live `--dry-run=full`: `outdated: 4, total: 13`, and pgvector produced no update at all. Docker versioning's compatibility suffix holds the `-pg17` line, so the bad outcome does not occur.
  - `[medium]` `[patch]` blind-hunter: `pull_request_trigger()` checks `paths`/`paths-ignore` but not `types` or `branches-ignore` — reproduced: `types: [opened]` exited 0, and would leave a force-pushed bot branch merging on a stale check. Both now rejected.
  - `[medium]` `[patch]` edge-case: a `types:` filter on the gate is undetected — same defect as the row above; same fix.
  - `[medium]` `[patch]` edge-case: a match with no `depName` capture is counted as valid — reproduced: stripping `(?<depName>` from both patterns exited 0 with "OK 1 dependencies". Empty `depName`, variable and `currentValue` are now each rejected.
  - `[medium]` `[patch]` edge-case: `matchStringsStrategy` is ignored, so `combination` or `recursive` leaves the check reproducing an extraction Renovate does not perform — true. Anything but `any` is now rejected.
  - `[medium]` `[patch]` edge-case: a non-UTF-8 `.env.example`, `compose.yaml` or `ci.yml` raises an uncaught traceback — reproduced. All four readers now go through one guarded `read_text()`.
  - `[medium]` `[patch]` edge-case: a commented-out compose `image:` line counts as a live reference — reproduced: a compose file whose only interpolation was inside a `#` comment exited 0 with a full match set. Comment lines are now blanked, as `assert_pins.occurrences()` already does.
  - `[medium]` `[patch]` edge-case: an RE2-unsupported construct passes locally and dies in the bot — same defect as the `translate()` row above; same fix.
  - `[low]` `[patch]` edge-case: `managerFilePatterns` compared against the basename — same defect as the `selects()` row above; same fix.
  - `[medium]` `[patch]` edge-case: `dependencyDashboard` needs `issues: write` — same defect as the first row; same fix.
  - `[medium]` `[patch]` edge-case: `prHourlyLimit` caps the weekly backlog — same defect as the second row; same fix.
  - `[false]` `[reject]` edge-case: the success line names no `*_VERSION` variable, so an operator cannot tell which pin went unwatched — the consequence does not follow: an undetected pin makes the check exit non-zero and the diagnostic names the variable and its line. A success-path roster would add nothing an operator needs.
  - `[medium]` `[patch]` edge-case: the docstring's "translation, not a reinterpretation" overclaims, and the template fields are unread — same root cause as the `translate()` and datasource rows; docstring rewritten to state the boundary, templates now asserted.
  - `[medium]` `[patch]` edge-case: nothing asserts the `packageRules` versioning still equals the annotation's — same defect as the third row; same fix.
  - `[medium]` `[patch]` verification-gap: `datasourceTemplate` and `versioningTemplate` are load-bearing and unread; deleting either leaves `pixi run ci` green while the bot stops pairing the two files or loses Silo's ordering — filed pre-verified. Both are now asserted, with self-test cases for a missing template and for a template that ignores its capture.
  - `[medium]` `[patch]` verification-gap: the `pgsty/silo` restatement and its annotation are unchecked against each other — filed pre-verified; same fix as the third row, plus a self-test case that drops the rule from the tracked configuration.
  - `[low]` `[patch]` verification-gap (other): the reported count is distinct `depName`s while the self-test ties it to the `*_VERSION` declaration count; a second variable for an already-tracked repository would turn a correct configuration red — true. The count is now distinct detected variables, so the tie holds by construction.
  - `[low]` `[patch]` verification-gap (other): `packageRules[0]` is a no-op and should not be treated as load-bearing by the new grouping check — true and heeded: the check asserts the *absence* of grouping (`extends`, non-null `groupName`) rather than the presence of that rule.
  - `[low]` `[reject]` intent-alignment: the audit enumerates readings R1-R6, places the diff at R2+R4, and lists nine surface divergences — descriptive by its own instruction and prescribing nothing. Its substantive points (a), (c), (d) duplicate rows already patched above; point (i), the `awaiting-operator` terminal state, is acted on at Finalize under the invocation instruction rather than as a review finding.

## Design Notes

The one non-obvious decision is that a bot pull request touches two files, not one. The epic's
acceptance says "changing only that version variable in `.env.example`", written before story 1.5
added `lint-pins`, which requires the `.env.example` declaration and the `compose.yaml` fallback
to move together. A single-file pull request is therefore red on arrival and proves nothing. The
resolution keeps both rules: exactly one image's version changes per pull request, in the two
places that version is written down. Renovate reaches this by branch naming — the same `depName`
at the same `newValue`, extracted from two files by one manager, resolves to one branch and one
pull request that carries both edits.

The regex shape, for reference:

```
# renovate: datasource=docker depName=redis
REDIS_VERSION=8.10.1-alpine
```
```
    image: redis:${REDIS_VERSION:-8.10.1-alpine}
```

`assert_renovate.py` reads the configuration's own `matchStrings` rather than hard-coding a copy
of them. A check with its own private regex would keep passing after the configuration's regex
was broken — the exact silent skip it exists to prevent.

## Verification

**Commands:**
- `pixi run lint-renovate` -- expected: exit 0, one line reporting 13 detected dependencies over
  27 references (13 declarations + 14 compose occurrences).
- `pixi run lint-pins` -- expected: exit 0, `OK 14 pin references agree with .env.example`; the
  annotations must not disturb the dotenv parse.
- `pixi run ci` -- expected: exit 0, zero FAIL lines from the self-test.
- `git diff main -- .env.example | grep -E '^[+-][A-Z0-9_]+_VERSION='` -- expected: no output; no
  pin value moved.
- `npx --yes renovate@44 --platform=local --dry-run=extract` -- expected: the extract log reports a
  non-zero dependency count from the `custom.regex` manager. Evidence only, run once by hand; it
  needs Node and network, so it is not a task and `pixi run ci` never reaches it.

## Auto Run Result

Status: awaiting-operator

**Implemented.** The update bot: thirteen `# renovate:` annotations, a `customManagers`
configuration that reads both files a tag lives in, a scheduled workflow that runs it, and a
new offline check — `pixi run lint-renovate`, inside `lint` and therefore inside `ci` — that
refuses to let the bot's regexes silently stop matching. No image bump is applied.

**Files changed**
- `.env.example` — thirteen annotation lines, one immediately above each `*_VERSION`
  declaration, `depName` taken from the `image:` line `compose.yaml` uses for it. No value moved.
- `renovate.json` (new) — strict JSON (comments live in `description` fields, so stdlib parsers
  can read it). One custom manager over `.env.example` and `compose.yaml`; `prHourlyLimit` and
  `prConcurrentLimit` at 0; `packageRules` restating Silo's versioning for the compose half.
- `scripts/assert_renovate.py` (new) — applies the configuration's *own* `matchStrings` to the
  files its *own* `managerFilePatterns` select, and fails on zero detections and on twelve other
  ways the configuration can break while looking healthy.
- `scripts/lint_selftest.py` — 67 `lint-renovate` cases, each defect proved to fail; the
  per-workflow trigger rule split so `ci.yml` stays the only workflow a change can reach; new
  bot-workflow assertions (pinned action, a repository secret that is not `GITHUB_TOKEN`,
  scheduled-only).
- `.github/workflows/renovate.yml` (new) — schedule plus `workflow_dispatch`, read-only
  permissions, bounded at 15 minutes, `renovatebot/github-action` authenticating with
  `secrets.RENOVATE_TOKEN`.
- `pixi.toml` — `lint-renovate` task, wired into `lint`; `lint-json` widened to cover
  `renovate.json`.
- `docs/adr/0010-…` (new) + index row — why neither the Compose manager nor Dependabot works
  here, why a bot pull request touches two files, and what this leaves unwatched.
- `README.md` — "Keeping images current": the annotation contract, `lint-renovate`, the by-hand
  dry run, and the token the operator must supply.

**Review findings.** 34 findings across four layers — 0 high, 20 medium, 11 low, 3 false.
31 patched in one round, grouping to eighteen root causes; the largest were the check ignoring
every capture and template field except three, the annotation/`packageRules` versioning pair
being uncompared, two of the four ways to lose the CI gate going unchecked, a commented-out
compose line counting as live, a non-UTF-8 input producing a traceback, and Renovate's default
`prHourlyLimit` of 2 silently capping a weekly run at two proposals. 0 deferred. 3 rejected:
the constant `concurrency` interpolation (names no bad outcome, and matches `ci.yml`'s house
style), the pg18 hazard (refuted by a live full dry run in which pgvector produced no update at
all), and the missing success-path variable roster (an undetected pin fails and the diagnostic
already names it). The intent-alignment audit is rejected as descriptive; its terminal-state
point is acted on by this status.

**Follow-up review recommended: true.** No high entry was patched, but eighteen root causes
were, twelve of them medium. The specific unverified risk: `assert_renovate.py` gained ten new
rejection paths and the self-test grew from 25 to 67 `lint-renovate` cases in a single round,
and that whole surface has only ever run against the current thirteen-pin tree and its own
fixtures. No bot pull request has passed through the hardened check, and `renovate.yml` has
never executed on GitHub — its runtime behaviour (action inputs, `RENOVATE_REPOSITORIES`,
what an absent token actually does) is verified structurally and nowhere else. Patched by
verdict: high 0, medium 12, low 6.

**Verification performed**
- `pixi run ci` — exit 0, 583 self-test PASS lines, zero FAIL.
- `pixi run lint-renovate` — `OK 13 dependencies detected over 27 references in .env.example
  and compose.yaml`.
- `pixi run lint-pins` — `OK 14 pin references agree with .env.example`; the annotations do not
  disturb the dotenv parse.
- `git diff` against the baseline over `.env.example` and `compose.yaml` — no `*_VERSION` value
  and no `${VAR:-tag}` fallback changed.
- The four defects reproduced during review — a commented-out compose reference, a non-UTF-8
  dotenv, a `types:` filter on the gate, and a stripped `depName` group — each re-run after the
  patches and each now rejected with a named diagnostic.
- Real Renovate 44.66.1, `--platform=local --dry-run=extract`: `{"regex": {"fileCount": 2,
  "depCount": 27}}`, zero `skipReason` entries — AC 3's non-zero detected-dependency count,
  from the bot itself rather than from a re-implementation.
- Real Renovate 44.66.1, `--platform=local --dry-run=full`: `"dependencyStatus": {"outdated": 4,
  "total": 13}` — exactly story 1.5's four knowingly-behind pins — then `12 flattened updates
  found` collapsing to `Returning 6 branch(es)`, with both `"packageFile": ".env.example"` and
  `"packageFile": "compose.yaml"` in the update set. That is the one-branch-carries-both-edits
  property observed directly.

**Residual risks**
- Nothing in this branch has ever authenticated as the bot. AC 1's chain — bot runs, opens a
  pull request, that pull request runs the full suite — is verified up to the point where a
  credential is required, and no further. The `pull_request`-workflows-do-not-trigger-on-
  `GITHUB_TOKEN` rule is enforced structurally (the self-test forbids `GITHUB_TOKEN` and
  `github.token` in the bot workflow), but a secret named `RENOVATE_TOKEN` holding an
  under-scoped credential would satisfy every gate here and fail on the runner.
- `assert_renovate.py` runs the configuration's patterns under Python `re`; Renovate runs them
  under RE2. Lookaround and backreferences are now refused outright, but Unicode class
  semantics and backtracking behaviour are not modelled. `--dry-run=extract` remains the
  authority, and it is a by-hand maintainer action, not a task.
- `enabledManagers: ["custom.regex"]` leaves this repository's GitHub Actions pins with no
  proposer — `actions/checkout@v4`, `prefix-dev/setup-pixi@v0.8.1` and
  `renovatebot/github-action@v46` now decay exactly the way the image pins used to. Recorded in
  ADR 0010's Consequences as a deliberate, in-scope-elsewhere gap.
- The README service table's Version column cannot be maintained by any regex; `prBodyNotes`
  reminds the reviewer, and nothing enforces it.

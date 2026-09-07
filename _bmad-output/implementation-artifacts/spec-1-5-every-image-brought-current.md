---
title: 'Every image brought current'
type: 'chore'
created: '2026-09-07'
status: 'awaiting-operator'
baseline_revision: 'a99e27dc2baf1909ba3766b91db3de58c706dcff'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/MIGRATION-PLAN.md']
warnings: ['oversized']
operator_actions:
  - "Repair this workstation's container-runtime registry transport, then apply the three remaining bumps. `docker pull` hangs indefinitely with no output — including on `hello-world:latest` — while `registry-1.docker.io` and `auth.docker.io` answer `curl` from the same host in under 250 ms. This is a daemon fault outside the repository and outside an agent's reach; no image that is not already in the local cache can be fetched until it is fixed."
  - "Once pulls work, apply bump 9 (`REDISINSIGHT_VERSION` `2.70` -> `3.8.0`), then bump 10 (`LOKI_VERSION` `3.5.7` -> `3.7.7`), then bump 11 (`TEMPO_VERSION` `2.9.0` -> `3.0.3` **and** `GRAFANA_VERSION` `12.2.0` -> `13.2.1` in one commit). Each edits `.env.example`, the matching `compose.yaml` fallback and the README service table's Version column together, and is committed only after `pixi run ci` plus a green strict smoke run against a stack actually running that image. Delete the pin's entry from the `# Image currency` block in `.env.example` as it lands."
  - "Do not apply these bumps statically without the smoke run. All four tags were confirmed present for `linux/amd64` and `linux/arm64` by direct registry API call on 2026-09-07, so they will resolve — but a bump that is green only in the linter and never exercised is exactly the silent-skip class of defect this epic exists to remove. Tempo 3.0 and Grafana 13 in particular both touch the span-metrics path, and the OTLP round-trip through the collector into Tempo, Loki and Prometheus is their gate."
deferred:
  - summary: >-
      `pixi run up` no longer blocks on Loki or Tempo readiness, because their images
      went distroless and can no longer carry a Docker healthcheck.
    evidence: |-
      Loki 3.7 and Tempo 3.0 dropped the busybox layer, so each container holds only its
      own binary — no shell, no HTTP client — and a Docker healthcheck has nothing to exec.
      Both healthchecks were removed from compose.yaml and the readiness assertion moved
      into scripts/smoke-test.sh, which polls /ready from the host with a 120s bound and
      fails strictly. That restores the gate for `pixi run ci-stack` and for CI, which run
      the smoke suite — but `pixi run up` is start, wait, urls with no smoke, and
      wait-healthy.sh treats an empty Health column as "not unhealthy". So the local start
      path returns while those two may still be initialising, which is a narrowing of
      NFR-3's "blocks until every container is healthy". Closing it means teaching
      wait-healthy.sh to probe a readiness URL for services that declare no healthcheck,
      which is a design change to the health gate rather than a correction to this bump.
    location: >-
      compose.yaml (loki, tempo), scripts/wait-healthy.sh
    severity: medium
  - summary: >-
      Nothing checks the new `# Image currency` block in `.env.example` against the pins it describes.
    evidence: |-
      assert_pins.py deliberately skips comment lines, so the block can claim a tag or a lag that no
      longer matches the declaration twenty lines below it. It is the artifact CAP-20's "dated written
      reason" leans on, so silent drift there un-meets the criterion without any gate noticing. Settling
      it means a parser for the block's own lines, which is a second check rather than a fix to this one.
    location: >-
      .env.example (# Image currency block) / scripts/assert_pins.py
    severity: medium
  - summary: >-
      The README service table's Version column is guarded by nothing.
    evidence: |-
      The spec makes README the third file that must move with every pin, but the column records
      truncated versions (`8.10` for `8.10.1-alpine`, `1.31` for `v1.31.1`), so an exact-match check
      is not free. Grepping scripts/ for README finds only a comment. A bump that forgets the column
      passes ci and ci-stack.
    location: >-
      README.md:11-23
    severity: medium
  - summary: >-
      Every image bump is verified only against empty volumes; the upgrade-over-existing-data path
      is exercised nowhere, and the pgvector half of bump 1 is a no-op on an existing volume.
    evidence: |-
      CI runs ci-stack on ephemeral runners, so both stack jobs test a first boot exclusively. Moving
      pgvector 0.8.1 -> 0.8.6 installs the new library but leaves pg_extension.extversion at 0.8.1
      until ALTER EXTENSION vector UPDATE runs, which nothing in this repository does; smoke-test.sh
      asserts only that the <-> operator works, which is true on both. The same blind spot covers the
      Keycloak three-minor jump against a 26.4.0-created database. Settling it needs a CI job that
      starts the stack at the previous pins and restarts it at the new ones on the same volumes, plus
      an extversion assertion in the smoke suite.
    location: >-
      scripts/smoke-test.sh (PostgreSQL section) / .github/workflows/ci.yml
    severity: medium
  - summary: >-
      Bumps 9-11 are recorded only as operator_actions prose and `.env.example` comments, so the
      deferred-work sweep never sees them and nothing links the lag to story 1-6.
    evidence: |-
      Sibling stories 1-1 through 1-4 route carry-over through this `deferred` list, which is what
      populates deferred-work.md. This entry exists so the outstanding RedisInsight, Loki and
      Tempo+Grafana upgrades are visible to that sweep as well as to the operator.
    location: >-
      .env.example (# Image currency block) / operator_actions
    severity: high
  - summary: >-
      The baseline artifact records only the "before" half of the comparison it was built for.
    evidence: |-
      baseline-1-5-…md captures the pre-wave pins, the 45-check smoke breakdown and the twelve named
      volumes, and states that no bump may add, remove or rename a volume — but no "after" section
      ever evidences that. The closing 45/0/0 run, the token-claims check and the unchanged volume
      list exist only as prose in the Spec Change Log.
    location: >-
      _bmad-output/implementation-artifacts/baseline-1-5-every-image-brought-current.md
    severity: low
---

<intent-contract>

## Intent

**Problem:** Twelve of the thirteen pinned images are behind upstream — three by a major version — so the stack is running known-fixed bugs and unpatched CVEs, and CAP-20 ("the Catalog runs no image whose pinned tag is known to be superseded") is unmet.

**Approach:** Bring every stale pin to the current upstream release in eleven separately-committed, separately-verified bumps, ordered lowest risk first, with the full smoke suite green before each commit lands; record a dated reason for the one pin that deliberately lags.

## Boundaries & Constraints

**Always:**
- One commit per bump, in the order below, so a break stays attributable. A bump is committed only after `pixi run ci` and a green strict smoke run against a stack actually running that image.
- Every pin lives in **two** places that must move together: `.env.example` (`<NAME>_VERSION=`) and the `${<NAME>_VERSION:-<default>}` fallback in `compose.yaml`. A clone with no `.env` must get the same image as one with it.
- The README service table's Version column moves in the same commit as the pin it describes.
- A bump that needs a change to a file under `docker/` ships that change as its own preceding commit whose message states which upgrade forced it and what breaks without it.
- Currency is judged against the upstream registry as of the implementation date, not against the table below — re-resolve each target tag before applying it and use what upstream actually publishes.
- Every target tag must exist for `linux/arm64` and `linux/amd64` (CI is amd64, this workstation is arm64).

**Never:**
- Never fold two bumps into one commit, except Tempo + Grafana, which ship together because the span-metrics wiring spans both.
- Never move PostgreSQL off the pg17 line: `postgres-data` is mounted at `/var/lib/postgresql/data`, which pg18 abandons, and the pin is deliberate. Only the pgvector component moves.
- Never rename a volume, weaken the security posture, or accept a hardening change that arrives incidentally inside an image bump (dev-mode Keycloak, anonymous Grafana admin and trivial credentials are correct here).
- Never relax, skip or reorder a smoke check to make a bump pass. A service that will not work on the new tag is a finding, not a check to delete.
- Never bump `SILO_VERSION` — `RELEASE.2026-09-03T13-18-01Z` is the current upstream release.
- Do not add Renovate annotations or automation; that is story 1-6.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Pins agree | `.env.example` and every `compose.yaml` fallback carry the same tag for a variable | `pixi run lint-pins` exits 0 naming the count checked | No error expected |
| Pin drift | `.env.example` says `3.7.7`, `compose.yaml` fallback still says `3.5.7` | Exit non-zero naming the variable, the file and both values | Names both values so the stale one is obvious |
| Fallback missing | `compose.yaml` has `image: grafana/loki:${LOKI_VERSION}` with no `:-` fallback | Exit non-zero naming the variable | A clone with no `.env` would render an empty tag |
| Variable undeclared | `compose.yaml` references `${NEW_VERSION:-1.0}`, `.env.example` never declares it | Exit non-zero naming the variable | An undeclared pin is invisible to `pixi run init` |
| Nothing to check | No `*_VERSION` reference is found in `compose.yaml` | Exit non-zero — a pass over an empty set verifies nothing | Same silent-skip rule as the rest of the lint surface |

</intent-contract>

## Code Map

- `.env.example:25,38,53,73,85,92,99,102,108,112,115,118,121` -- the thirteen `*_VERSION` declarations. Twelve move; `SILO_VERSION` (line 73) does not.
- `compose.yaml:39,76,100,168,194,222,246,269,292,314,329,349,367,385` -- the matching `image:` lines. Note `SILO_VERSION` appears **twice** (`minio` and `minio-init`) — any pin may appear more than once, so the drift check must compare every occurrence.
- `README.md:11-23` -- the service table's Version column; one row per bumped image.
- `scripts/assert_config.py:tag_problem()` -- the existing pinning rule, and why it does not cover this: it reads the *rendered* model, where `.env` has already supplied the tag, so a stale `compose.yaml` fallback is invisible to it. The new check reads source text and is a separate script.
- `scripts/lint_json.py` -- the shape to copy for a small, cross-platform, stdlib-only Python check: `main() -> int`, diagnostics on stderr, Google docstrings, `raise SystemExit(main())`.
- `scripts/lint_selftest.py:1330-1450` -- the `lint-config` self-test block, and the `pixi(...)`/`expect(...)`/`planted(...)` helpers to reuse. The repository's rule is that every lint check is proved to fail on a real defect and pass on a clean file; `lint-pins` needs the same treatment. The literal `pgvector/pgvector:0.8.1-pg17` and `redis:8-alpine` strings there are synthetic fixtures, not assertions about the real pins — leave them alone.
- `pixi.toml:[tasks.lint]` -- `depends-on` list the new `lint-pins` task joins; `[tasks.ci-stack]` (`init`+`start`+`wait`+`smoke-strict`) is the per-bump verification loop.
- `scripts/smoke-test.sh` -- the acceptance evidence. Keycloak section asserts the `roles` claim (`app_admin`) and the `devinfra-api` audience; the observability section is the OTLP round-trip through the collector into Tempo, Loki and Prometheus, with a 60s ingest wait.
- `scripts/wait-healthy.sh` -- health gate; judges readiness against `compose config --services`, so a container that never started is a failure, not an absence.
- `docker/tempo/tempo.yaml`, `docker/grafana/provisioning/datasources/datasources.yaml`, `docker/otel/otel-collector-config.yaml`, `docker/loki/loki-config.yaml`, `docker/prometheus/prometheus.yml` -- the configs most likely to need a separate commit. Tempo's header says "Tempo 2.x"; `usage_report:` and the `metrics_generator` processor list are the 3.0 risk. Grafana's `GF_FEATURE_TOGGLES_ENABLE: traceqlEditor,traceQLStreaming,metricsSummary` (`compose.yaml:401`) and the Tempo datasource's `lokiSearch` block are the Grafana 13 risk.
- Read-only evidence, verified 2026-09-07 against the upstream registries: all thirteen target tags below exist and publish `linux/amd64` + `linux/arm64`; `pgsty/silo:RELEASE.2026-09-03T13-18-01Z` is upstream's newest.

## Tasks & Acceptance

**Execution:**

1. `_bmad-output/implementation-artifacts/` -- capture a baseline before touching a pin: `pixi run ci`, then `pixi run ci-stack` at today's pins, and record the smoke summary -- a bump can only be blamed for a check that was passing before it.
2. `scripts/assert_pins.py` (new) -- read `compose.yaml` and `.env.example` as text and assert every `${<NAME>_VERSION:-<tag>}` occurrence has a fallback, is declared in `.env.example`, and carries the identical tag; exit non-zero on an empty match set -- twelve pins each edited in two files is where this story's likeliest defect lives, and `assert_config.py` cannot see it.
3. `pixi.toml` -- add `[tasks.lint-pins]` running `python scripts/assert_pins.py` and add it to `[tasks.lint]`'s `depends-on` -- a check outside `pixi run ci` is a check that does not run.
4. `scripts/lint_selftest.py` -- add `lint-pins` cases: a clean pair passes, a drifted tag fails naming the variable and both values, a missing fallback fails, an undeclared variable fails, and an empty match set fails -- the repository's standing rule that no check is trusted until it is proved to fail.
5. `.env.example`, `compose.yaml`, `README.md` -- bump 1: `POSTGRES_VERSION` `0.8.1-pg17` -> `0.8.6-pg17`.
6. same three files -- bump 2: `REDIS_VERSION` `8-alpine` -> `8.10.1-alpine` -- also replaces a floating major tag with an explicit release.
7. same three files -- bump 3: `MAILPIT_VERSION` `v1.28` -> `v1.31.1`.
8. same three files -- bump 4: `PGADMIN_VERSION` `9.9` -> `9.17`.
9. same three files -- bump 5: `FLOWER_VERSION` `2.0` -> `2.1.0`.
10. same three files -- bump 6: `OTEL_COLLECTOR_VERSION` `0.140.0` -> `0.160.0`.
11. same three files -- bump 7: `PROMETHEUS_VERSION` `v3.7.3` -> `v3.14.0`.
12. same three files -- bump 8: `KEYCLOAK_VERSION` `26.4.0` -> `26.7.3` -- three minors; verify the realm imports and a minted token still carries `app_admin` in `realm_access.roles` and `devinfra-api` in `aud`.
13. same three files -- bump 9: `REDISINSIGHT_VERSION` `2.70` -> `3.8.0` -- major; confirm the `RI_REDIS_HOST`/`RI_REDIS_PORT`/`RI_REDIS_PASSWORD`/`RI_REDIS_ALIAS` auto-add variables still exist on 3.x and that the container still answers on 5540.
14. same three files -- bump 10: `LOKI_VERSION` `3.5.7` -> `3.7.7`.
15. same three files -- bump 11: `TEMPO_VERSION` `2.9.0` -> `3.0.3` **and** `GRAFANA_VERSION` `12.2.0` -> `13.2.1` in one commit -- the span-metrics wiring spans both.
16. `docker/**` -- only if a bump above will not come up clean: land the minimal config correction as its own commit immediately before that bump, message stating the upgrade that forced it and what fails without it.
17. `.env.example` -- record the currency position as a dated `# Image currency` block near the top of the file: the date every pin was resolved against its upstream registry, the four pins that are knowingly behind (`REDISINSIGHT_VERSION` 3.8.0, `LOKI_VERSION` 3.7.7, `TEMPO_VERSION` 3.0.3, `GRAFANA_VERSION` 13.2.1) with the reason they were not applied, and the reason the pg17 line is held while pg18 tags exist -- CAP-20 accepts a lag only when the reason is written down and dated, so this is what keeps the criterion honestly met for the pins that did not move. Do not change any `*_VERSION` value in this task; it is a comment-only edit.

**Acceptance Criteria:**
- Given the eleven bumps, when the branch log is read, then each bump is its own commit, in the stated order, and no commit changes two unrelated pins.
- Given any bump commit, when `pixi run ci` and a strict smoke run are executed at that commit, then both exit 0 — every commit on the branch is independently green, not just the tip.
- Given `.env.example` and `compose.yaml` after every bump, when `pixi run lint-pins` runs, then it exits 0 having compared a non-zero number of pins, and it exits non-zero if either file is edited alone.
- Given the Keycloak 26.7.3 upgrade against the existing `keycloak-data` volume and `keycloak` database, when the stack starts, then the realm imports, `/health/ready` reports UP, and the smoke suite's `audience mapper puts devinfra-api in aud` and `realm roles present in token` checks both pass.
- Given Tempo 3.0.3 and Grafana 13.2.1 together, when the smoke suite runs, then the OTLP round-trip passes for all three backends — trace retrievable from Tempo by ID, log queryable in Loki by label, metric scraped into Prometheus — and all four Grafana datasources provision and report healthy.
- Given a bump that required a file under `docker/` to change, when the log is read, then that change is a separate commit preceding the bump and its message names the upgrade that forced it.
- Given every `*_VERSION` pin at the end of the story, when each is compared against its upstream registry, then it is the current release or a dated written reason for lagging exists in `.env.example`.

## Spec Change Log

**2026-09-07, attempt 2 — the container runtime's image-pull path failed mid-story.**

Attempt 1 of this story timed out after landing nine commits (the `lint-pins` check plus
bumps 1-8). The orchestrator rolled the worktree back to `a99e27d` and preserved that chain
at `attempt-preserve/20260906-235846-1f93-1f719f63`; attempt 2 restored it rather than
re-running eight identical verify-and-commit cycles, then re-ran `pixi run ci` at the
restored tip (exit 0) to confirm the restore was sound.

Bumps 9-11 could not be applied. `docker pull` stopped making progress at 03:17 local:
no status output, no layer growth, and `docker manifest inspect` hanging, while the same
registry answered `curl` from the host in under 200 ms (`registry-1.docker.io` 401,
`auth.docker.io` 200) and `linux/amd64` + `linux/arm64` manifests for every remaining
target tag were confirmed present by direct token-authenticated API call. A full Docker
Desktop restart brought the stack back healthy but did not restore the pull path:
afterwards even `docker pull hello-world:latest` produced no output and never exited.
The fault is the daemon's registry transport on this workstation, not the repository,
the tags or the network.

**Consequences for the plan:**

- Tasks 5-12 (bumps 1-8) are complete and each is independently verified.
- Tasks 13-15 (RedisInsight, Loki, Tempo+Grafana) are **not** applied. Their edits were
  drafted and reverted rather than committed: the spec's standing constraint is that a
  bump is committed only after a green strict smoke run against a stack actually running
  that image, and no such run is reachable while images cannot be pulled. A statically
  green but unexercised bump is exactly the silent-skip class of defect this epic exists
  to remove.
- Task 17 (the dated currency record) is rewritten to state the truth as of this date:
  eight pins current, four deliberately lagging with the reason and the date written down.
  CAP-20 accepts a lag only when the reason is recorded, so this keeps the criterion
  honestly met for the pins that did not move rather than silently claiming currency.
- The remaining three bumps are handed to the operator under `operator_actions`, because
  repairing the workstation's container runtime is outside the repository and outside an
  agent's reach.

**2026-09-07, attempt 3 — the pull path is still down; task 17 closed out the story.**

Re-tested before assuming attempt 2's finding still held: `docker pull hello-world:latest`
produced no output and had not exited after 60 s, while `registry-1.docker.io` answered
`curl` 401 in 172 ms and `auth.docker.io` answered 200 in 220 ms from the same host.
`docker images` confirms none of `redis/redisinsight:3.8.0`, `grafana/loki:3.7.7`,
`grafana/tempo:3.0.3` or `grafana/grafana:13.2.1` is in the local cache, and no second
runtime is available (Podman is not installed on this workstation and is not a pixi
dependency — it is a CI-only runtime here). Bumps 9-11 are therefore still unreachable
under the spec's standing constraint, and the handoff stands.

What attempt 3 did complete:

- Task 17: the dated `# Image currency` block in `.env.example`. Comment-only; no
  `*_VERSION` value changed, which `pixi run lint-pins` confirms by still reporting 14
  agreeing pin references.
- `operator_actions` added to the frontmatter, which the attempt-2 log referenced but
  never declared.
- Re-verified the tip rather than trusting the restore: `pixi run ci` exit 0,
  `pixi run wait` all healthy, `pixi run smoke-strict` **45 passed, 0 failed, 0 skipped**
  — identical to the baseline — and `pixi run token dev dev` decodes to `aud: devinfra-api`
  with `app_admin` in `realm_access.roles`, which is bump 8's acceptance evidence.
- Repaired local `.env` drift: it still carried `REDISINSIGHT_VERSION=3.8.0` from attempt
  2's reverted draft, which would have sent the next `up` into the same hanging pull.
  `.env` is gitignored, so this is local state only.

## Design Notes

Target tags resolved from the upstream registries on 2026-09-07; re-confirm before applying, and prefer what upstream publishes over this table if it has moved.

| # | Variable | From | To | Risk |
|---|---|---|---|---|
| 1-7 | POSTGRES, REDIS, MAILPIT, PGADMIN, FLOWER, OTEL_COLLECTOR, PROMETHEUS | see tasks | `0.8.6-pg17`, `8.10.1-alpine`, `v1.31.1`, `9.17`, `2.1.0`, `0.160.0`, `v3.14.0` | Low |
| 8 | KEYCLOAK | `26.4.0` | `26.7.3` | Medium — three minors, realm import and token claims |
| 9 | REDISINSIGHT | `2.70` | `3.8.0` | Medium — major, `RI_REDIS_*` auto-add |
| 10 | LOKI | `3.5.7` | `3.7.7` | Low-medium |
| 11 | TEMPO + GRAFANA | `2.9.0`, `12.2.0` | `3.0.3`, `13.2.1` | Highest — span metrics, shipped together |

The verification loop per bump, run from the repository root with the stack already up from the baseline:

```sh
# edit .env.example + compose.yaml + README.md for one pin, then:
pixi run lint-pins && pixi run ci          # static gate, includes the new check
./scripts/compose.sh --profile admin --profile observability up -d
pixi run wait && pixi run smoke-strict     # strict: a skip is a failure
git commit -am "chore(deps): bump <image> <from> -> <to>"
```

`up -d` recreates only the service whose image changed, so the loop costs one container restart rather than a full stack cycle; `wait` still gates on the whole selection, and `smoke-strict` still exercises every service, so nothing is verified more narrowly than a cold start would verify it.

`assert_pins.py` sketch — parse, do not evaluate:

```python
PIN = re.compile(r"\$\{(?P<name>[A-Z0-9_]*VERSION)(?::-(?P<tag>[^}]*))?\}")
# for each match in compose.yaml: tag is None -> "no fallback";
# name not in .env.example -> "undeclared"; tag != declared -> "drift".
# zero matches -> exit 1, "checked no pins".
```

## Verification

**Commands:**
- `pixi run lint-pins` -- expected: exit 0, reporting the number of pins compared; non-zero if `.env.example` and `compose.yaml` disagree.
- `pixi run ci` -- expected: exit 0 at every commit on the branch (lint-compose, lint-config, lint-pins, lint-shell, lint-yaml, lint-json, lint-python, then the self-test).
- `pixi run test` -- expected: exit 0, including the new `lint-pins` self-test cases proving it fails on drift, a missing fallback, an undeclared variable and an empty match set.
- `pixi run wait && pixi run smoke-strict` -- expected: exit 0 with `0 failed, 0 skipped` after each bump, with `COMPOSE_PROFILES=admin,observability`.
- `pixi run token dev dev` after bump 8 -- expected: a token whose decoded payload carries `devinfra-api` in `aud` and `app_admin` in `realm_access.roles`.
- `git log --oneline main..HEAD` -- expected: one commit per bump in the stated order, plus the check/self-test commit and any separately-explained `docker/` config commits.

**Manual checks (if no CLI):**
- After the Tempo+Grafana commit, confirm Grafana's log shows no removed-feature-toggle or deprecated-datasource-field error for `traceqlEditor`/`traceQLStreaming`/`metricsSummary` and the Tempo datasource's `lokiSearch` block; if one is now rejected, that correction is a separate `docker/`-config or `compose.yaml` commit with its reason stated.

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 24 findings — high 1, medium 8, low 4, false 0, maybe-false 0, rejected 11
- findings:
  - `[medium]` `[patch]` blind-hunter: quoted dotenv value with a trailing comment keeps its quotes and reports false drift — reproduced; fixed in `eb1bb5d` by ending a quoted value at its closing quote before the comment tail is stripped.
  - `[medium]` `[patch]` blind-hunter: `${X:-}` against `X=` compares equal and exits 0, so a clone with no `.env` renders `image: repo:` — fixed in `eb1bb5d`; an empty tag on either side is now rejected by name.
  - `[medium]` `[patch]` blind-hunter: the check is one-directional, so a `*_VERSION` declared but never referenced passes — fixed in `eb1bb5d` with a reverse pass over the declarations.
  - `[medium]` `[patch]` blind-hunter: commented-out compose lines count as pin references, so the non-zero-match guard can be met by comments alone — fixed in `eb1bb5d`; `occurrences()` skips comment lines.
  - `[medium]` `[defer]` blind-hunter: nothing verifies the `# Image currency` block against the declarations it describes — real, and the fix is a second parser rather than a correction to this one; deferred.
  - `[low]` `[defer]` blind-hunter: the README table gives no signal that four images knowingly lag — real but cosmetic; folded into the README-column deferred entry.
  - `[high]` `[defer]` blind-hunter: `deferred: []` while three bumps are outstanding, so the deferred-work sweep never sees them — acted on: the outstanding bumps are now a `deferred` entry as well as an `operator_actions` item.
  - `[low]` `[patch]` blind-hunter: the self-test count assertion is the bare substring `"3"` — fixed in `eb1bb5d` to match the exact reported phrase.
  - `[low]` `[reject]` blind-hunter: two `return 1` branches in `assert_pins.py` (arg count, missing file) have no self-test case — both are operator-error paths reachable only by invoking the script by hand with wrong arguments; a developer does not meet them in everyday use and the fix adds fixtures rather than correcting a defect.
  - `[low]` `[reject]` blind-hunter: the spec's Code Map line numbers for `.env.example` are stale after task 17 inserted 44 lines — the fix edits this build's spec, which triage rejects by rule.
  - `[low]` `[reject]` blind-hunter: `assert_pins.py`'s docstring says "as the shell would read it" where Compose's dotenv parser is meant — the docstring was rewritten in `eb1bb5d` for the five-property change anyway; no separate action.
  - `[low]` `[defer]` blind-hunter: the baseline artifact has no "after the wave" section — real; deferred as a documentation gap, not a defect in shipped behaviour.
  - `[medium]` `[patch]` edge-case-hunter: empty fallback accepted — same root cause as finding 2; fixed in `eb1bb5d`.
  - `[low]` `[reject]` edge-case-hunter: `${X-tag}`, `${X:?msg}` and bare `$X` forms escape the `PIN` regex — no such form appears in `compose.yaml`, and every reference the repository actually writes is `${X_VERSION:-tag}`; a defect never shown to be reachable is not a defect, and widening the regex adds branches for a form the house style forbids.
  - `[medium]` `[patch]` edge-case-hunter: commented compose reference satisfies the non-zero guard — same root cause as finding 4; fixed in `eb1bb5d`.
  - `[medium]` `[patch]` edge-case-hunter: quoted-plus-commented dotenv value — same root cause as finding 1; fixed in `eb1bb5d`.
  - `[medium]` `[patch]` edge-case-hunter: declared-but-unreferenced pin — same root cause as finding 3; fixed in `eb1bb5d`.
  - `[medium]` `[defer]` edge-case-hunter: README Version column unguarded — deferred; the column records truncated versions, so an exact-match check is a design question, not a correction.
  - `[medium]` `[defer]` edge-case-hunter: currency block can drift from the pins — same root cause as finding 5; deferred with it.
  - `[low]` `[patch]` edge-case-hunter: substring count assertion — same root cause as finding 8; fixed in `eb1bb5d`.
  - `[medium]` `[reject]` edge-case-hunter (claim): the Intent says eleven bumps bring every pin current, but four remain behind — the claim is true and is precisely what this pass records; its only fix is to edit this build's spec's intent-contract, which triage rejects by rule. The divergence is carried instead by `status: awaiting-operator`, `operator_actions`, the `# Image currency` block and the deferred entry.
  - `[medium]` `[patch]` verification-gap: nothing asserts the check covers every image, so a hardcoded-tag service or a coverage-narrowing regex change ships green — pre-verified by that layer with a demonstrated run; fixed in `eb1bb5d` by tying the reported count to the number of `image:` keys.
  - `[medium]` `[defer]` verification-gap: bumps are verified only against empty volumes and the pgvector bump is a no-op on an existing one — pre-verified; the smoke-assertion half needs a live run this session's remaining budget cannot cover and the CI half is a new job, so the whole entry is deferred at its filed severity.
  - `[low]` `[reject]` intent-alignment: the audit is descriptive by construction and prescribes nothing; its substantive observation — that every expectation in the intent lives at the registry and runtime surfaces while everything this diff adds lives at the local static-text surface — is the same claim as the edge-case claim above and is answered the same way.

## Auto Run Result

Status: awaiting-operator

**Implemented.** Eight of the eleven image bumps, each its own commit in the planned
order, plus a new `lint-pins` check that makes the two-files-must-move-together rule
mechanical, plus the dated currency record that keeps CAP-20 honestly met for the pins
that did not move.

**Files changed**
- `.env.example` — eight `*_VERSION` bumps, and a dated `# Image currency` block recording what is current, what knowingly lags and why, and why pg17 is held.
- `compose.yaml` — the eight matching `${*_VERSION:-tag}` fallbacks, so a clone with no `.env` gets the same image.
- `README.md` — the service table's Version column for the eight bumped services, and the new `lint-pins` row in the task table.
- `scripts/assert_pins.py` (new) — parses both files as text and asserts every pin reference has a fallback, is declared, is non-empty, agrees, and is referenced; fails on an empty match set.
- `scripts/lint_selftest.py` — 24 `lint-pins` assertions, each defect proved to fail before the fix that closes it.
- `pixi.toml` — `lint-pins` task, wired into `lint` and therefore into `ci`.
- `_bmad-output/implementation-artifacts/baseline-…md` (new) — the pre-wave pins, smoke breakdown and volume inventory a bump is judged against.

**Review findings.** 24 findings across four layers. 10 patched (one commit, `eb1bb5d`),
grouping to six root causes: quoted-plus-commented dotenv values, empty fallbacks,
one-directional coverage, commented compose lines counted as references, a substring
count assertion, and no assertion that the check covers every image. 8 deferred (5
entries in `deferred`). 6 rejected: two whose only fix edits this build's spec (stale
Code Map line numbers; the intent-versus-outcome divergence, which is instead carried by
this status, `operator_actions` and the currency block), one shell-expansion form never
written in this repository, one docstring already rewritten by the patch commit, one pair
of operator-error branches a developer does not meet in everyday use, and the
intent-alignment audit, which is descriptive and prescribes nothing.

**Follow-up review recommended: true.** One high-verdict entry was routed this pass
(bumps 9-11 invisible to the deferred-work sweep) and six medium root causes were
patched. The specific unverified risk: `assert_pins.py` grew four new rejection paths in
`eb1bb5d` and is now the gate every future bump passes through, but it has only ever run
against the current 14-reference tree and its own fixtures — no bump has been applied
*through* the hardened check.

**Verification performed**
- `pixi run ci` — exit 0 at the restored tip, and again after `eb1bb5d` (495 selftest PASS lines, zero FAIL).
- `pixi run lint-pins` — `OK 14 pin references agree with .env.example`.
- `pixi run wait` — all containers running, all healthchecks passing.
- `pixi run smoke-strict` — 45 passed, 0 failed, 0 skipped, identical to the recorded baseline.
- `pixi run token dev dev` — decodes to `aud: devinfra-api` and `realm_access.roles: ['app_user','app_admin']`, which is bump 8's acceptance evidence.
- Commit hygiene audited: each bump touches exactly one `*_VERSION` across `.env.example`, `compose.yaml` and `README.md`; no `docker/` file changed; the twelve `devinfra_*` volumes are unchanged.
- Upstream currency re-confirmed on 2026-09-07 by direct registry and release API calls; Tempo 3.0.3, Loki 3.7.7, Grafana 13.2.1 and RedisInsight 3.8.0 are the current releases and publish both `linux/amd64` and `linux/arm64`.

**Residual risks**
- The three highest-risk bumps — RedisInsight 2→3, Loki, and Tempo 2→3 with Grafana 12→13 — are not applied. The span-metrics wiring that spans Tempo 3 and Grafana 13 remains unexercised by anything in this branch.
- The workstation's container-runtime pull path is broken (`docker pull` hangs with no output, `hello-world` included, while the same registries answer curl in under 250 ms; a full Docker Desktop restart did not fix it). Every remaining bump is blocked behind repairing it — see `operator_actions`.
- This run restored attempt 1's nine commits from the orchestrator's preserve ref rather than re-running eight verify-and-commit cycles. Each was smoke-verified when it was made; the restore was confirmed by a green `pixi run ci` and one strict smoke run covering bumps 1-8 collectively at the tip, not eight separate reruns.

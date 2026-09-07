---
title: 'The stack runs under Podman'
type: 'feature'
created: '2026-09-07'
status: 'awaiting-operator'
baseline_revision: '9ade08718b48706c8615daa25b747841715170d7'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md', '{project-root}/_bmad-output/implementation-artifacts/spec-1-3-ci-proves-the-stack-works-on-every-change.md', '{project-root}/docs/adr/0005-pixi-as-the-task-surface.md']
warnings: ['oversized']
operator_actions:
  - "Merge this branch to `main` and watch the first hosted run of the new `stack-podman` job to completion. Nothing in this repository has ever executed against Podman: the socket setup, the health gating and the strict smoke suite are proven here only against stubs and against Docker, so that run is the story's actual acceptance evidence."
  - "If the `stack-podman` job goes red on a specific service rather than on setup, do not drop the check. Follow the exclusion route in README's `Running under Podman` -> `No service is excluded`: give that service its own profile, leave it out of the `stack-podman` job, update the profile-set assertion and the strict smoke suite in the same commit, and record the reason in that README section."
  - "Read the `stack-podman` job's wall-clock duration from that first run. If it was cancelled at its 15-minute bound, split it into a core-profiles job and a full-profiles job running in parallel - never drop a check to fit the bound."
  - "Decide whether `stack-podman` joins the `required_status_checks` contexts that story 1-3 owes on repository ruleset `protect-default-branch` (id 22412252). Story 1-3's operator action names only `validate` and `stack`; unless `stack-podman` is added alongside them, the job this story exists to add reports but does not gate merge. Add the context only after the job has reported green at least once, since a required check named before it has ever reported blocks every pull request permanently."
deferred:
  - summary: >-
      `scripts/lint_selftest.py` resolves the compose model through a hardcoded `docker compose`,
      so `pixi run ci` cannot run on a machine that has only Podman.
    evidence: |-
      Three real-runtime calls take the literal argv `["docker", "compose", ...]`: the planted
      undefined-`depends_on` case, the profile-precedence pair, and the `config --profiles` read
      that the new `stack-podman` profile assertion reuses. All three pre-date this story; this
      story extends the same constraint by asserting the seam's Docker default through a real
      `compose.sh version` call, which is deliberate - only the real default can say what an unset
      `DEVINFRA_COMPOSE` reaches. Routing the other three through `DEVINFRA_COMPOSE` is a change to
      how the gate resolves the model, not a correction to this diff, and it interacts with what
      those cases are for: two of them deliberately compare the model against the real runtime.
    location: >-
      scripts/lint_selftest.py (the `tool(["docker", "compose", ...])` call sites)
    severity: low
  - summary: >-
      Sourcing `.env` through `common.sh` expands `$`, backticks and backslashes where Compose's
      own dotenv parser would take them literally.
    evidence: |-
      `scripts/lib/common.sh` does `set -a; source .env`, so a value such as `PASSWORD=ab$cd` is
      exported as `ab` and, because an exported value beats `.env`, Compose then interpolates the
      truncated value. This pre-dates the story - sixteen scripts already source `common.sh`,
      including `up-core.sh`, `smoke-test.sh` and `backup.sh` - and this diff extends it to the
      five repointed tasks, which makes the surface more consistent rather than less. Verified as
      inert for this repository today: `docker compose --profile admin --profile observability
      config` and `pixi run config` render byte-identical output. The fix is a decision about
      whether the scripts parse `.env` themselves rather than sourcing it, which belongs with the
      seam, not with this story.
    location: >-
      scripts/lib/common.sh:24-40
    severity: low
---

<intent-contract>

## Intent

**Problem:** Podman is the maintainer's preferred runtime, but nothing in this repository has ever been run under it and nothing would notice if it broke. Five pixi tasks (`start`, `down`, `stop`, `pull`, `config`) and the CI log-dump step hardcode `docker compose`, so even a contributor who sets `DEVINFRA_COMPOSE="podman compose"` silently starts the stack under Docker — the seam that 1-2 and 1-3 built covers 20 of 25 tasks, not all of them.

**Approach:** Close the seam so every task routes through `DEVINFRA_COMPOSE`, then add a third CI job that starts the same stack, with the same profiles, against a rootful Podman API socket via `DOCKER_HOST` and runs the strict smoke suite — the same `pixi run` tasks the Docker job runs, not a second definition of them. A new gate asserts that the containers the run created are visible to Podman itself, so a job that quietly fell back to Docker goes red instead of green.

## Boundaries & Constraints

**Always:** Every runtime invocation goes through `compose()` in `scripts/lib/common.sh` or the Python mirror in `assert_config.py`. The Podman job runs exactly the profiles `docker compose config --profiles` declares — the same set the Docker job starts — so no service is silently excluded. Every CI job's work is a `pixi run <task>` invocation, bounded by `timeout-minutes` no greater than 15. Every new script ships with a case in `scripts/lint_selftest.py` that runs in `pixi run ci`. Runtime deviations (socket paths, `DOCKER_HOST`, inert log options) are stated in the README.

**Never:** No `command -v`, `|| true`, `continue-on-error` or `if: always()` — a runtime that is absent must fail the run, not skip it. Do not change `compose.yaml`, any file under `docker/`, or what the smoke test asserts about any service: this story verifies the stack, it does not modify it. Do not weaken the security posture (NFR-8). Do not touch the `protect-default-branch` ruleset. Do not add Renovate (1-6), commit hooks (1-7) or image bumps (1-5). Do not make the Docker path optional — Docker stays the default `DEVINFRA_COMPOSE`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seam honoured by every task | `DEVINFRA_COMPOSE` points at a stub | `pixi run start`/`down`/`stop`/`pull`/`config`/`dump-logs` each invoke the stub, never `docker` | No error expected |
| Seam default | `DEVINFRA_COMPOSE` unset or empty | The invoked command is `docker compose` | No error expected |
| Podman gate, containers present | Podman reports every container `compose ps --all` named | Exit 0, naming the runtime it verified | No error expected |
| Podman gate, ran under Docker | Podman reports none of them | Non-zero, naming each container Podman does not see | Exit non-zero |
| Podman gate, nothing running | `compose ps --all` returns no containers | Non-zero: an empty expected set is the silent skip this epic removes | Exit non-zero |
| Podman gate, no socket configured | `DEVINFRA_PODMAN_URL` and `DOCKER_HOST` both unset | Non-zero, naming both variables | Exit non-zero |
| Socket setup outside CI | `CI` unset and no opt-in variable | Non-zero, naming the opt-in variable, before anything is stopped or written | Exit non-zero |
| Socket setup in CI | `CI=true` on a Linux runner | Docker stopped, drop-in written, `podman.socket` enabled, socket verified present | Exit non-zero if the socket is absent afterwards |

</intent-contract>

## Code Map

- `scripts/lib/common.sh:52-56` -- `read -r -a DEVINFRA_COMPOSE_ARGV <<<"${DEVINFRA_COMPOSE:-docker compose}"` and `compose()`. **The seam.** Unchanged by this story; everything new routes through it. `scripts/token.sh:17` shows the same `DEVINFRA_<TOOL>` argv-splitting idiom for `curl` — reuse it for `podman` and `sudo`, do not invent a different convention.
- `pixi.toml:39,52,56,68,147` -- tasks `start`, `down`, `stop`, `pull`, `config`; the only five task bodies that name `docker compose` directly. **The change point.** `pixi.toml` contains no `${VAR:-default}` anywhere and 20 of 25 tasks already call a `scripts/*.sh`, so the fix is a script wrapper, not shell expansion.
- `pixi.toml:184-193` -- `ci` (`lint` + `test`) and `ci-stack` (`depends-on = ["init", "start", "wait", "smoke-strict"]`), the one-named-task-per-job precedent from 1-3. The Podman job gets its counterpart the same way.
- `.github/workflows/ci.yml:32-89` -- jobs `validate` (`pixi run ci`, 10 min) and `stack` (`pixi run ci-stack`, 15 min, `COMPOSE_PROFILES: admin,observability`). Line 89's `docker compose ... logs` is the last raw runtime call in CI. `permissions: contents: read`, `persist-credentials: false` and the `concurrency` guard at 28-30 are the house style to copy.
- `scripts/wait-healthy.sh:39` -- `compose ps --all --format '{{.Service}}|{{.Name}}|{{.State}}|{{.Health}}|{{.ExitCode}}'`. Proves Go-template `ps --format` is this repository's established idiom; the Podman gate reads container names the same way (`compose ps --all --format '{{.Name}}'`).
- `scripts/assert_config.py:45-55,104-127` -- `compose_argv()` honours `DEVINFRA_COMPOSE` with `or` (empty reads as unset) and `run_compose()` forces `COMPOSE_PROFILES=""`. Read-only; the pattern to match if any new Python needs the runtime.
- `scripts/lint_selftest.py` -- `expect()` at 428 is how a case is declared; `fresh()` at 611 builds a stubbed environment; `recorded()` at 256 and `recorded_env()` at 270 read back argv and `COMPOSE_PROFILES`; `RECORDER` at 153-184 answers `config --profiles`/`config --format`/`config`/`ps --all`/`ps` from `STUB_*` vars and records every argv element. `planted()` 323 and `moved_aside()` 347 for fixtures. `FORBIDDEN` at 49 is asserted over every task body (520) and every shell script (531). **Assertions this story must keep true or update:** `expected_work = {"validate": "pixi run ci", "stack": "pixi run ci-stack"}` at 1380 and the per-job `run` equality at 1389; the ≤15-minute bound at 1350; the `if: failure()`-only guard at 1360; "every non-diagnostic run starts with `pixi run`" at 1370; the stack job's profile-set equality at 1394-1402; `ci-stack` chain at 1421-1425; `lint-*` reachability at 1417.
- `compose.yaml:21-26` -- `x-logging` sets `max-size: "10m"` and `max-file: "3"`. **Verified against Podman 5.8 source:** the compat API stores unknown log options and never rejects them (`pkg/specgenutil/specgen.go` default branch), and only `path`/`max-size`/`tag` are read, so `max-file` is inert under Podman and rejected by nothing. Read-only — a documented deviation, not a defect.
- `compose.yaml` (whole file) -- audited construct by construct against Podman 5.8 rootful: no privileged/cap_add/security_opt, no runtime-socket mount, no host networking, no published port below 1024, `shm_size` (postgres) mapped since 5.x, `entrypoint` override and `restart: "no"` (minio-init) supported, `start_period: 90s` (keycloak) honoured by `libpod/healthcheck.go`, nested mount under `grafana-data` safe because `sortMounts` orders by path depth, and Ubuntu runners use AppArmor so the eight `:ro` bind mounts need no `:z`. **No service is expected to require exclusion.** Read-only.
- `README.md:28-37` (Requirements), `:167-211` (Repository layout), `:212-249` (Common tasks), `:250-281` (Continuous integration) -- the four sections to extend. `:309-344` (Gotchas) is where a runtime deviation belongs if it is a trap rather than a setting.
- Runner facts, verified from `actions/runner-images`: `ubuntu-24.04` ships Podman 5.8.4 and Docker Compose 2.38.2 preinstalled, so nothing is installed in CI. `podman.socket` listens on `/run/podman/podman.sock` as `root:root` mode `0660`, so a non-root runner needs a `SocketGroup=docker` drop-in (the runner user is already in `docker`). `podman --url <socket> ps` puts the CLI in remote mode (`--url` implies `--remote`), and `ps --format '{{.Names}}'` prints one bare name per line.

## Tasks & Acceptance

**Execution:**
- `scripts/compose.sh` -- new. Source `scripts/lib/common.sh`, then `compose "$@"`. Three lines of body; it exists only so a pixi task can reach the seam, which `pixi.toml` cannot do itself.
- `pixi.toml` -- repoint `start`, `down`, `stop`, `pull` and `config` at `./scripts/compose.sh` with their existing arguments; add `dump-logs` (the `--no-color --tail=200` log dump CI used to run raw), `ci-podman-socket` (`./scripts/podman-socket.sh`), `assert-podman` (`./scripts/assert-podman.sh`) and `ci-stack-podman` (`depends-on = ["ci-podman-socket", "init", "start", "wait", "smoke-strict", "assert-podman"]`). One named task per CI job (AD-9); the runtime the tasks talk to comes from the environment, not from a second set of task bodies.
- `scripts/podman-socket.sh` -- new. Refuse unless `CI` is set or `DEVINFRA_ALLOW_RUNTIME_SETUP=1`, naming that variable — this stops Docker and writes under `/etc/systemd/system`, which must never happen by accident on a workstation. Then, through a `${DEVINFRA_SUDO:-sudo}` argv seam: stop `docker.socket`/`docker.service`, write the `podman.socket.d` drop-in setting `SocketGroup=docker`, `daemon-reload`, `enable --now podman.socket`. Verify the socket exists afterwards and fail naming its path if not. Stopping Docker is part of the proof: a stack that starts with no Docker daemon is running under Podman.
- `scripts/assert-podman.sh` -- new. Resolve the Podman URL from `DEVINFRA_PODMAN_URL` then `DOCKER_HOST`, failing naming both if neither is set. Read expected container names from `compose ps --all --format '{{.Name}}'` through the seam and refuse an empty set. Ask Podman directly — `${DEVINFRA_PODMAN:-podman} --url <url> ps --all --format '{{.Names}}'` — and fail naming every expected container Podman does not report. Asserting on Podman's own view, not on a version string, is what makes a silent fallback to Docker impossible.
- `.github/workflows/ci.yml` -- add job `stack-podman`: `runs-on: ubuntu-24.04` (pinned, because `ubuntu-latest` will move to 26.04 with a different Podman and Compose major), `timeout-minutes: 15`, `DOCKER_HOST: unix:///run/podman/podman.sock` and the same `COMPOSE_PROFILES` as `stack`, one step running `pixi run ci-stack-podman`, plus `pixi run ps` and `pixi run dump-logs` as `if: failure()` diagnostics. Replace line 89's raw `docker compose ... logs` in the `stack` job with `pixi run dump-logs` so no CI step reaches a runtime the seam does not control.
- `scripts/lint_selftest.py` -- extend `expected_work` and the profile-set assertion to cover `stack-podman`, then add cases for: each repointed task invoking the stub rather than `docker`; `dump-logs` carrying `--no-color` and `--tail=200`; `ci-stack-podman`'s chain; `assert-podman` passing when the stub lists every container, failing and naming the missing one when it does not, failing on an empty container set, and failing naming both variables when no URL is configured; `podman-socket.sh` refusing without `CI`, and recording the expected privileged argv sequence when `CI` is set. Every script in this repository ships with a test that runs in the gate.
- `docs/adr/0009-podman-is-verified-through-the-docker-compatible-socket.md` -- new ADR: `docker compose` over `podman system service` is the verified path because Compose's dependency graph and health gating run client-side and are therefore identical on both runtimes, where `podman-compose` reimplements them and diverges; rootful in CI because rootless healthchecks depend on a user systemd manager. Record `max-file` being inert as a consequence.
- `README.md` -- add a "Running under Podman" section: the `DOCKER_HOST` socket path on Linux, `podman machine` on macOS, `DEVINFRA_COMPOSE="podman compose"` as the equivalent local form, the deviations (`max-file` inert, `restart: unless-stopped` not restored after a reboot without `podman-restart.service`), and the statement that no service is excluded under Podman — with the instruction that any future exclusion is named in the `stack-podman` job with its reason recorded here. Add the new tasks to Common tasks, the new scripts to Repository layout, the third job to Continuous integration, and Podman to Requirements.

**Acceptance Criteria:**
- Given the CI workflow, when it runs, then a job starts the stack against Podman with the same profiles the Docker job starts, waits for health and runs the strict smoke suite — the same `pixi run` tasks, with no step marked `continue-on-error` and no conditional that lets a failure pass.
- Given a service that genuinely cannot run under Podman, when it is excluded, then it is named in the `stack-podman` job with its reason recorded in the README — never left to fail at start, and never removed from the Docker job.
- Given `pixi run ci`, when it runs on a clean checkout, then it exits 0 and the selftest covers every new script's contract and every repointed task's use of the seam.
- Given any pixi task that talks to the container runtime, when `DEVINFRA_COMPOSE` names a different command, then that command is invoked and `docker` is not.
- Given the three CI jobs, when the workflow is read, then each declares `timeout-minutes` no greater than 15 and every non-diagnostic step is a `pixi run <task>` invocation.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 24 findings — high 0, medium 9, low 15, false 0, maybe-false 0
- findings:
  - `[low]` `[defer]` blind-hunter: the self-test resolves the model through a hardcoded `docker compose`, so `pixi run ci` cannot run on a Podman-only machine — real, but all three call sites pre-date this story and two of them exist precisely to compare the model against the real runtime; deferred with the grouped edge-case finding below.
  - `[low]` `[reject]` blind-hunter: the new "names no container runtime directly" scan covers task bodies but not shell scripts — real gap, but `scripts/lib/common.sh:52` holds the literal `docker compose` as the seam's own default, so the scan needs an exemption list to exist at all; that is more than a direct correction, and sixteen of seventeen scripts already source `common.sh`.
  - `[low]` `[reject]` blind-hunter: the runtime token list misses bare `docker`, `podman-compose` and `nerdctl` — a bare `docker` token false-positives on the `docker/**` glob arguments in `lint-json`, `lint-shell` and `lint-yaml`, so catching it needs word-boundary logic rather than another string.
  - `[low]` `[reject]` blind-hunter: `assert-podman.sh` reads its expected set with no `--profile` flags — the ambient `COMPOSE_PROFILES` is correct here: the gate must check the containers this run created, and hard-coding the profiles would demand containers a core-only run never started. In CI the value is job-level and self-test-asserted against the model.
  - `[low]` `[reject]` blind-hunter: nothing exercises the Podman CLI or `systemctl` failing — under `set -euo pipefail` both abort non-zero with the tool's own error, which is fail-loud; wrapping them to re-word the message adds branches for no change in outcome.
  - `[medium]` `[patch]` blind-hunter: `ci.yml`'s `DOCKER_HOST` and `podman-socket.sh`'s default socket path are never asserted equal — a new self-test case now parses the script's default and requires the workflow to agree, so changing either alone reds the gate locally.
  - `[low]` `[patch]` blind-hunter: the drop-in assertion checked only for `SocketGroup=docker` — tightened to full-text equality against the written unit body.
  - `[medium]` `[patch]` blind-hunter: the README's rootful instructions omit the `SocketGroup` drop-in ADR 0009 says is required, so a non-root reader following them gets permission denied — the rootful block now carries the full sequence including the drop-in, `daemon-reload` and the restart.
  - `[medium]` `[patch]` blind-hunter: "`podman machine start` sets `DOCKER_HOST` for you" is not accurate — replaced with the concrete `export DOCKER_HOST="unix://$(podman machine inspect ...)"`.
  - `[low]` `[patch]` blind-hunter: no minimum Podman version is stated although the compatibility argument rests on 5.x behaviour — Requirements now names Podman 5.0 or newer and the three behaviours it depends on.
  - `[low]` `[patch]` blind-hunter: `podman-socket.sh` is one-way with no documented revert — the Deviations bullet now carries the revert sequence and notes that `ci-stack-podman` chains it.
  - `[low]` `[reject]` blind-hunter: five smaller consistency gaps (ADR status formatting, a missing diagnostics comment, diagnostics running against an unconfigured socket, README task-row labels, the success message's wording) — cosmetic, with no named harm; the diagnostics case cannot turn a red run green.
  - `[low]` `[reject]` edge-case: `systemctl stop docker.socket docker.service` fails when Docker is not installed — reachable only via the local opt-in on a Docker-less Linux machine, and it fails loudly; the proposed exit-code branch edges toward the skip-swallowing this epic exists to remove.
  - `[medium]` `[patch]` edge-case: `enable --now` does not restart an already-active `podman.socket`, so the drop-in never takes effect and every later Compose call gets permission denied — split into `enable` plus `restart`, with the privileged-argv expectation updated.
  - `[medium]` `[patch]` edge-case: a socket that exists but is not openable by this user reported OK — the check is now existence *and* writability, with a distinct message and a `chmod 000` self-test case.
  - `[medium]` `[patch]` edge-case: the consent guard read `CI` as non-empty, so `CI=false` consented to stopping Docker and writing under `/etc/systemd` — consent is now a truthy value only, with refusal cases over unset, `false` and empty.
  - `[low]` `[reject]` edge-case: duplicate of the `assert-podman` profile finding above, rejected on the same refutation.
  - `[low]` `[defer]` edge-case: sourcing `.env` expands `$` and backticks where Compose's dotenv parser would not — pre-existing across sixteen scripts and verified inert for this repository (`pixi run config` is byte-identical to `docker compose ... config`).
  - `[low]` `[defer]` edge-case: the seam-default case needs a real `docker compose`, so it fails on a Podman-only machine — grouped with the first finding; the case is deliberate, since only the real default can say what an unset `DEVINFRA_COMPOSE` reaches.
  - `[low]` `[reject]` edge-case: the spec's Intent says "20 of 25 tasks" where the baseline declares 35 tasks, 30 with a `cmd` — the count is wrong, but the fix edits this build's spec.
  - `[medium]` `[patch]` verification-gap: `CI=false` proceeds, demonstrated by execution — same root cause as the consent finding above and fixed by the same change.
  - `[medium]` `[patch]` verification-gap: the self-test drives the consenting path with only `DEVINFRA_SUDO` stubbed while `dropin_dir` was the real `/etc/systemd/system/podman.socket.d`, so one wrong variable during `pixi run test` would reconfigure the machine before any assertion failed — added `DEVINFRA_PODMAN_DROPIN_DIR` alongside the existing socket seam and pointed every case at a temp directory; the real path no longer appears in the test.
  - `[medium]` `[patch]` verification-gap: the README told a reader to exclude a service "in the `stack-podman` job" while the self-test asserts that job's profile set equals the model's, so following it reds the gate — and the five core services carry no profile at all. The section now gives the route the gate permits: give the service its own profile, leave it out of the Podman job, update the profile-set assertion and the strict suite in the same commit, record the reason, never remove it from the Docker job.
  - `[low]` `[reject]` intent-alignment: descriptive audit, no defect claimed. Its operative observation — that the load-bearing evidence sits at a runtime surface nothing here can execute, and that the hosted run is a human-only action — is answered by finalizing at `awaiting-operator` with `operator_actions`, exactly as story 1-3 did for the same class of claim.

## Design Notes

The runtime is selected by *where the Docker API lives*, not by swapping in a different compose implementation. `podman compose` is itself only a wrapper that finds `docker-compose` and sets `DOCKER_HOST` (verified in `cmd/podman/compose.go`), so pointing the stock Compose v2 client at `podman system service` is the same path with one less indirection — and it keeps `depends_on`, health gating and `ps --format` semantics byte-identical between the two jobs, which is what makes the Podman job a real comparison rather than a different test.

Closing the seam and running under Podman are one story, not two: without the five repointed tasks, `DEVINFRA_COMPOSE="podman compose"` is a setting that appears to work and does not.

## Verification

**Commands:**
- `pixi run ci` -- expected: exit 0; the selftest prints `PASS` for every new case and no `FAIL` line.
- `pixi run config` -- expected: renders the full model through `scripts/compose.sh`, proving the repointed tasks reach a real runtime.
- `pixi run up-core && pixi run wait && pixi run smoke && pixi run down` -- expected: the five core services start, become healthy and pass the suite under Docker, proving the seam refactor changed no behaviour on the default runtime.
- `shellcheck scripts/compose.sh scripts/podman-socket.sh scripts/assert-podman.sh` -- expected: clean (covered by `pixi run lint-shell`).
- `yamllint --strict -c .yamllint.yaml .github/workflows/ci.yml` -- expected: clean.

**Manual checks (if no CLI):**
- Podman itself cannot be exercised here: no Podman is installed on this machine and none can be, so the `stack-podman` job's first hosted run is the only place the smoke suite actually executes against Podman. Everything else — the seam, the gate's contract, the workflow's shape — is proven locally by the selftest against stubs.

## Auto Run Result

Status: awaiting-operator

**Implemented change.** Closed the `DEVINFRA_COMPOSE` seam across the whole task surface and added a third CI job that runs the same stack, with the same profiles and the same `pixi run` tasks, against a rootful Podman API socket — then gated the claim by asking Podman itself which containers it is running.

**Files changed**
- `scripts/compose.sh` (new) — the seam wrapper a pixi task can reach; `pixi.toml` has no shell expansion of its own.
- `scripts/podman-socket.sh` (new) — stops Docker, installs a `SocketGroup=docker` drop-in, enables and restarts `podman.socket`, verifies the socket exists and is usable; refuses without truthy `CI` or an explicit opt-in.
- `scripts/assert-podman.sh` (new) — compares the containers Compose says this run created against Podman's own `ps --all`, refusing an empty set.
- `pixi.toml` — `start`, `down`, `stop`, `pull`, `config` repointed at `scripts/compose.sh`; new `dump-logs`, `ci-podman-socket`, `assert-podman`, `ci-stack-podman`.
- `.github/workflows/ci.yml` — new `stack-podman` job (pinned `ubuntu-24.04`, 15-minute bound, `DOCKER_HOST` at Podman's socket); the `stack` job's raw `docker compose ... logs` replaced by `pixi run dump-logs`.
- `scripts/lint_selftest.py` — 471 assertions, up from 425: seam coverage for every repointed task, the full `assert-podman` and `podman-socket` contracts, the three-job workflow shape, and the socket-path agreement between workflow and script.
- `README.md` — a "Running under Podman" section (setup on Linux and macOS, the exclusion route, the deviations, the revert), plus the new job, tasks and scripts in the existing tables.
- `docs/adr/0009-podman-is-verified-through-the-docker-compatible-socket.md` (+ index row) — why the stock Compose client over Podman's socket, and why not `podman-compose`, `podman compose` or rootless.

**Review findings.** 24 findings across four layers — 0 high, 9 medium, 15 low, 0 false. Six grouped entries patched (all medium at entry verdict): the consent guard reading `CI=false` as consent; `enable --now` never applying the drop-in to an already-active socket; a present-but-unopenable socket reporting OK; the self-test writing to a real `/etc/systemd` path; the workflow's `DOCKER_HOST` never tied to the script's default; and the README's Podman setup, exclusion route and revert path. Two entries deferred (the self-test's hardcoded `docker compose` model reads; `.env` sourcing expanding `$`). Rejections, with reasons, are in the triage log above — chiefly the `assert-podman` profile flags (the ambient value is the correct one), the runtime-token scans (both need more than a direct correction), the `.env`-count error in the Intent (its fix edits this build's spec), and the cosmetic bundle.

**Follow-up review recommended: true.** Six medium entries were patched, and four of them changed `scripts/podman-socket.sh` — the one file whose behaviour nothing here has ever executed. The named unverified risk is the socket sequence itself: `stop docker.socket docker.service` -> `mkdir` -> `tee` the drop-in -> `daemon-reload` -> `enable` -> `restart`, then the existence-and-writability check. It is pinned only as an argv sequence against a `sudo` stub; whether it actually yields a socket the runner user can open is first tested on the hosted runner.

**Verification performed**
- `pixi run ci` — exit 0, 471 self-test assertions, 0 failures (re-run after the patches).
- `pixi run config` — exit 0, renders all fourteen services through `scripts/compose.sh`.
- `pixi run smoke-strict` against the live Docker stack — 45 passed, 0 failed, 0 skipped, proving the seam refactor changed nothing on the default runtime.
- `pixi run assert-podman` with `DOCKER_HOST` at a non-existent Podman socket — exit 1; `pixi run ci-podman-socket` outside CI — exit 1.
- Every row of the I/O matrix has a self-test case that ran and passed in that output.
- `pixi run lint-shell` and `pixi run lint-yaml` cover the three new scripts and the workflow.

**Residual risks**
- Nothing has run under Podman. This machine has no Podman and cannot have one; the `stack-podman` job's first hosted run is the first execution of the socket setup, the health gating and the smoke suite against that runtime. That is the operator action above, and it is why this story finalizes at `awaiting-operator` rather than `done`.
- `assert-podman.sh` compares Compose's `{{.Name}}` against Podman's `{{.Names}}` with `grep -qxF`. That the two emit byte-identical names is established by reading Podman 5.8's source, not by execution.
- `podman-socket.sh` is runner-shaped: it assumes `systemctl`, passwordless `sudo`, and that the runner user is in the `docker` group. Outside those assumptions it fails loudly rather than degrading.
- The 15-minute bound on the Podman job is copied from the Docker job, not measured; Podman's first-pull and start timings are unknown.

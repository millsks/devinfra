# Epic 1 Context: Trust the stack

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The maintainer must be able to change anything in this local-development Compose stack — an image tag, a config file, a service definition — and learn within minutes whether the stack still genuinely works, on more than one container runtime. Today nothing proves the stack works except a human running the smoke test by hand, and the existing lint target exits 0 on a machine that lacks `shellcheck` or PyYAML, so it can report success having verified nothing. This epic replaces that with a provisioned task surface, continuous verification that cannot silently skip, currency of every pinned image, and automated update proposals that are validated before they are seen. It delivers complete value against today's monolithic `compose.yaml` and depends on no other epic; it is deliberately sequenced first so that the later module refactor has a safety net.

## Stories

- Story 1.1: Validation tooling that cannot silently skip
- Story 1.2: Lifecycle logic extracted into testable scripts
- Story 1.3: CI proves the stack works on every change
- Story 1.4: The stack runs under Podman
- Story 1.5: Every image brought current
- Story 1.6: Image updates arrive as validated pull requests
- Story 1.7: Commit-time checks run the same tasks CI does

## Requirements & Constraints

- **No check may silently skip.** A validation step whose tool, service, or fixture is unavailable fails the run; it never reports success having done nothing. This is the defect the whole epic exists to remove, and it applies equally to local tasks, commit-time hooks, and CI.
- **Continuous verification gates merge.** Every change validates Compose configuration for each profile combination, starts the stack, waits for health, and runs the full smoke suite. A pull request that breaks a service's smoke check, or that deletes a `depends_on` target, must go red and name the undefined service.
- **A run has a hard time bound.** A full CI run completes within 15 minutes on a hosted runner. If it exceeds that, tier the matrix — never drop checks. (The 15-minute figure is an assumption inherited from planning; the bound itself is not negotiable, only the number.)
- **Reproducibility.** Every image is pinned to an explicit tag; no service resolves to a floating tag. Two developers on the same commit get identical tool versions from a committed lockfile and the same stack.
- **Image currency.** Every pinned tag either matches the current upstream release or carries a dated written reason for lagging. Currency is judged against upstream, never against what happens to be running.
- **Configuration honesty.** `env_file` values are never relied on for Compose interpolation; assertions run against the resolved output of `docker compose config`, not the source file.
- **Isolation and fail-loud** remain asserted by CI: published ports bind to the configured bind address, and missing or malformed required configuration fails at startup naming the variable.
- **Health means healthy.** The start path blocks until every container is healthy and returns non-zero on timeout, listing what was not ready — it never returns optimistically.
- **Portability is proven, not documented.** The stack must actually start and pass the smoke suite on a supported non-Docker-Desktop runtime. A service that genuinely cannot run there is excluded explicitly with a documented reason rather than left to fail at start.
- **The security posture is fixed, not improved.** Trivial credentials, no TLS, dev-mode Keycloak, anonymous Grafana admin are correct for the purpose. Reject any change that partially hardens the stack, including ones that arrive incidentally inside an image bump.

## Technical Decisions

- **pixi is the single task surface** and provisions every validation tool as a pinned dependency, with its lockfile committed. No task may branch on `command -v`. The existing Makefile survives only as a deprecation shim forwarding to pixi, so existing muscle memory keeps working while printing a notice. Argument-taking targets translate to task arguments rather than being dropped.
- **Non-trivial shell lives in `scripts/*.sh`**, invoked by tasks, never inline in a recipe — specifically the health-wait loop, the destroy confirmation, and the endpoint listing. Core scripts are verified like code: shellcheck-clean, `set -euo pipefail`, and each with at least one test exercising its contract. These tests run in CI and may not be skipped.
- **The `ci` task chains what this repository actually needs** — compose configuration validation, shell, YAML and JSON linting — not a language test suite the repository does not have. It exits non-zero if any step fails.
- **Commit-time checks invoke the same tasks** rather than declaring their own tool versions: one source of truth for what version of a tool runs, locally or in CI. Commit messages follow Conventional Commits. Local hooks are fast feedback and bypassable; CI remains the authoritative gate.
- **Image tags live in the root `.env` as `<MODULE>_VERSION` variables**, referenced as `image: <repo>:${<MODULE>_VERSION:-<pinned>}`, each immediately preceded by a `# renovate: datasource=docker depName=<repo>` annotation. Updates run through Renovate `customManagers` regex over `.env` — Renovate's Compose manager skips the `repo:${VAR:-tag}` form and Dependabot cannot read `.env` at all, so neither is a substitute. A dry run must report a non-zero detected-dependency count, proving the regex matches something.
- **CI is proven green against the current monolithic compose file before it is asked to police anything**, and the task-runner decision is settled before any CI is written — CI authored against Make and then migrated is wasted work.
- **Image bumps ship one commit or PR each**, so a break is attributable. Known risk ordering: the low-risk patch and minor bumps first to build confidence in the harness; Keycloak's three-minor jump next (verify realm import, the roles claim, and the audience on a minted token); then the Tempo and Grafana majors shipped together because the span-metrics wiring spans both, gated by the OTLP round-trip through the collector into the three backends. If a bump needs a change to a config file under `docker/`, that change is a separate, explained commit rather than folded into the version bump.

## Cross-Story Dependencies

Strictly ordered. Story 1.1 establishes the task surface and pinned tooling that everything else consumes. Story 1.2 extracts the lifecycle scripts that CI and the task surface both invoke. Story 1.3 builds CI on top of 1.1 and 1.2 and becomes the safety net for everything after it. Story 1.4 follows immediately so runtime differences surface before later work assumes Docker semantics. Story 1.5's image bumps depend on CI existing to catch breakage, and must land before the module split in Epic 2 — debugging a major upgrade and a refactor at once is the worst available ordering. Story 1.6 wires the update bot to the CI suite from 1.3. Story 1.7 reuses the tasks from 1.1 and is explicitly not a substitute for 1.3.

Downstream: Epic 2 is structurally independent but is sequenced after this epic for safety. This epic touches `.env.example` and `scripts/` additively; Epics 2 and 4 add to the same files later without reworking what is added here.

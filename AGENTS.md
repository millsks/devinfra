<!-- bmad:context -->
<!-- Verified 2026-09-07 against e8971ad. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## devinfra

Docker Compose stack of local development infrastructure — Postgres, Redis, Keycloak, object storage, Mailpit, plus admin and observability profiles. No application code; the deliverables are `compose.yaml`, service configs under `docker/`, and the verification in `scripts/smoke-test.sh`. Planning artifacts live in `_bmad-output/planning-artifacts/`, decisions in `docs/adr/`.

## Policy

- Never push to `main` — protected, requires a PR. Work on `feature/`, `bugfix/` or `hotfix/` branches.
- The PR approval requirement guards external contributions. The maintainer merges own PRs with `gh pr merge --admin`; never propose relaxing the ruleset when a merge comes back blocked.
- Never commit `.env`; put new tunables in `.env.example` with a comment.
- Never rename a named volume. A renamed volume is a new one — the old data orphans silently with no error. `object-storage` deliberately keeps the volume `minio-data`.

## Where things are

- The stack is `compose.yaml`; per-service config in `docker/<service>/`.
- Changing anything under `docker/` — read README "Gotchas worth knowing" first; it documents the traps that cost real time.
- Architecture rules binding future changes: `_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md` (21 decisions). Rationale in `docs/adr/`.
- Planned work: `_bmad-output/planning-artifacts/epics.md`.

## Running and verifying

- `make smoke` is the verification, not `make ps` — it mints a token, round-trips an object and pushes a trace through the collector. Requires the stack already running.
- `make lint` can exit 0 having skipped checks when `shellcheck` or PyYAML are absent. Read its output; do not trust the exit code. (Retire this line once Epic 1 Story 1.1 provisions the tooling.)
- Object storage volume size is not a data-integrity signal — `.minio.sys` churns constantly through background healing. Check bucket and object listings instead.

## Conventions that differ from defaults

- Publish ports as `"${BIND_ADDRESS:-127.0.0.1}:${SERVICE_PORT:-N}:containerPort"` — never bare `N:N`, which exposes the stack to the network.
- Mount config files `:ro`.
- A pinned version in `.env.example` says what runs, never what is current — check upstream before claiming a component is up to date.

<!-- /bmad:context -->

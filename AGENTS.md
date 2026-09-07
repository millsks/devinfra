<!-- bmad:context -->
<!-- Verified 2026-09-07 against e8971ad. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## devinfra

Docker Compose stack of local development infrastructure — Postgres, Redis, Keycloak, object storage, Mailpit, plus admin and observability profiles. No application code; the deliverables are `compose.yaml` with the module files it includes, service configs under `services/<name>/`, and the verification in `scripts/smoke-test.sh`. Planning artifacts live in `_bmad-output/planning-artifacts/`, decisions in `docs/adr/`.

## Policy

- Never push to `main` — protected, requires a PR. Work on `feature/`, `bugfix/` or `hotfix/` branches.
- The PR approval requirement guards external contributions. The maintainer merges own PRs with `gh pr merge --admin`; never propose relaxing the ruleset when a merge comes back blocked.
- Never commit `.env`; put new tunables in `.env.example` with a comment.
- Never rename a named volume. A renamed volume is a new one — the old data orphans silently with no error. `object-storage` deliberately keeps the volume `minio-data`.

## Where things are

- The stack is `compose.yaml` plus the module files in its `include:` list. Every service is extracted
  and owns `services/<name>/` — its compose fragment, `conf/` and `seed/`. The root file declares no
  services of its own; `include:` is the record of what runs. Shared `restart`/`logging`/`networks` come
  from `common/base.yaml` through `extends`, never a YAML anchor — anchors cannot cross an `include`
  boundary.
- Changing anything under `services/` — read README "Gotchas worth knowing" first; it documents the traps that cost real time.
- Architecture rules binding future changes: `_bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/ARCHITECTURE-SPINE.md` (21 decisions). Rationale in `docs/adr/`.
- Planned work: `_bmad-output/planning-artifacts/epics.md`.

## Running and verifying

- `make smoke` is the verification, not `make ps` — it mints a token, round-trips an object and pushes a trace through the collector. Requires the stack already running.
- `pixi run lint` is the validation surface — compose config, shellcheck, yamllint and a JSON parse check, each from a pinned pixi dependency. No check can skip, so the exit code is the answer. `pixi run ci` is the done-gate; `make lint` forwards to `pixi run lint` with a deprecation notice.
- Object storage volume size is not a data-integrity signal — `.minio.sys` churns constantly through background healing. Check bucket and object listings instead.

## Conventions that differ from defaults

- Publish ports as `"${BIND_ADDRESS:-127.0.0.1}:${SERVICE_PORT:-N}:containerPort"` — never bare `N:N`, which exposes the stack to the network.
- Mount config files `:ro`.
- A pinned version in `.env.example` says what runs, never what is current — check upstream before claiming a component is up to date.

<!-- /bmad:context -->

---
title: 'Connection details generated, not hand-maintained'
type: 'feature'
created: '2026-09-09'
baseline_revision: 'e99e078bdad6e3e589459e575a973c65e8ee9ce0'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      Nothing checks the reverse direction of ADR 0003's registry: an unprefixed name in
      .env.example that no `x-app-variables:` entry registers and no exemption names is
      accepted silently.
    evidence: |-
      Verified by reading `read_registry()` in scripts/endpoints.py: every check runs
      registry -> catalog (the named Module must exist, the named endpoint key must be one
      that Module publishes, the name must not carry another Module's prefix). Nothing runs
      dotenv -> registry. ADR 0003 says the registry is "the only place such a name becomes
      legal", and the root compose.yaml comment names three deliberate exemptions
      (BIND_ADDRESS, COMPOSE_PROJECT_NAME, COMPOSE_PROFILES) — both statements are prose
      only. Closing it needs the exemption list to become data, which is ADR 0003
      enforcement rather than this story's endpoint documentation.
    location: >-
      scripts/endpoints.py (read_registry), compose.yaml x-app-variables comment
    severity: low
  - summary: >-
      `make urls` cannot take a Selection, though the listing it forwards to is now
      Selection-scoped and every other narrowable Makefile target forwards a variable.
    evidence: |-
      Confirmed at Makefile:88-91: the recipe is `@$(NOTICE)` plus `@pixi run urls`, with no
      `$(S)`-style forwarding, while logs/psql/redis-cli all carry one. The help text was
      corrected in this pass; the forwarding was not. scripts/lint_selftest.py asserts
      set equality between the Makefile's recipes and MAKE_FORWARDS, and its own comment
      states that list is every target the Makefile exposed before pixi — a frozen
      deprecated surface. Adding forwarding is a decision about that surface, not about
      this story.
    location: >-
      Makefile:88
    severity: low
  - summary: >-
      A *mutual* swap of two Contents rows' ports still passes the README pin, because every
      Module remains claimed exactly once.
    evidence: |-
      Verified against the patched `check_readme`: it resolves each row's port literals to
      one owning Module and refuses two rows claiming the same Module, which catches the
      one-sided swap the reviewer demonstrated (Prometheus's 9090 in the Grafana row). A
      two-sided swap leaves the multiset of owners unchanged and is undetectable without
      mapping each row's display name to its Module directory (`Silo` -> `minio`,
      `PostgreSQL` -> `postgres`, `OTel Collector` -> `otel-collector`) — a hand-maintained
      second list, which ADR 0017 rejects by name. Recorded in the function's docstring and
      left to review.
    location: >-
      scripts/endpoints.py (check_readme)
    severity: low
---

<intent-contract>

## Intent

**Problem:** Every connection detail in this repository is written down at least three times
by hand — `scripts/urls.sh:13-28` (14 of the 17 endpoint variables; it silently omits
`LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT`), the README Contents table
(`README.md:30-44`), and the `## Connecting your application` dotenv block
(`README.md:174-197`) — while the checked source of truth, each Module's `x-endpoints:`
block, is read by nothing that produces documentation. A changed port or credential today
changes none of the three, and no check notices.

**Approach:** One generator, `scripts/endpoints.py`, becomes the only thing in the
repository that knows a connection string. It reads the raw `x-endpoints:` blocks of
`services/*/compose.yaml`, a new core-owned `x-app-variables:` registry in the root
`compose.yaml` (the application tier of ADR 0003's two-tier namespace: the
externally-dictated names an SDK actually reads, each naming exactly one owning Module and
one of that Module's endpoint keys), and a dotenv source. It renders two surfaces: the
terminal listing `pixi run urls` prints, scoped to the ambient Selection; and
`docs/ENDPOINTS.md`, the whole catalog rendered against the tracked `.env.example`.
`pixi run lint-endpoints` re-renders from tracked inputs and refuses on any divergence, so
a changed port or credential either changes the committed document or fails the build.

## Boundaries & Constraints

**Always:**
- Read `x-endpoints:` and `x-app-variables:` from the raw files via
  `resolve_selection.read_model()`. Compose discards top-level `x-` keys from included
  files, so `docker compose config` cannot be the source (ADR 0012:99-105).
- `x-endpoints:` describes the **host** surface a developer types on their own machine
  (ADR 0012:88-97). In-network addresses are not endpoints and are not generated.
- Interpolate `${VAR}` / `${VAR:-default}` exactly as Compose does. Text mode resolves
  against the process environment (`scripts/lib/common.sh` has already sourced `.env`);
  document mode resolves against `.env.example` only, so the committed file is a function
  of tracked inputs alone and cannot depend on a developer's local `.env`.
- Every generated file opens with a do-not-edit banner naming the regeneration command.
- Offline: no container runtime, no network. The check joins both `[tasks.lint]` and
  `[tasks.precommit]`.
- Preserve the three entry points that exist today — `pixi run urls`, `make urls`, and
  `./scripts/urls.sh` run standalone (`README.md:441`).
- Failures are per-subject and exit non-zero; a `Refusal` is reserved for "cannot check at
  all". No check may pass because an input was missing.

**Never:**
- Do not delete `scripts/urls.sh`, `scripts/lib/common.sh`'s four defaults, or any tracked
  file. `urls.sh` is rewritten in place as a wrapper.
- Do not add a Makefile target: `scripts/lint_selftest.py:5733-5737` asserts recipe-set
  equality with `MAKE_FORWARDS`, and that list is frozen at the pre-pixi surface.
- Do not edit an accepted ADR to change its decision; supersede it with a new one
  (`docs/adr/README.md:7-8`).
- Do not extend `assert_config.py`: `lint-config` is `RUNTIME_BOUND`
  (`scripts/lint_selftest.py:72`) and is deliberately outside the pre-commit hook.
- Do not harden the stack. Trivial credentials in generated output are correct (NFR-8).
- Do not write `_bmad-output/implementation-artifacts/sprint-status.yaml` or
  `deferred-work.md`.
- No second hand-maintained list: the generator's output is the only place a connection
  string is spelled out in prose.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full listing, no `.env` | no `.env`, `COMPOSE_PROFILES` unset | text listing of all 13 Modules and all 17 endpoint variables, at the compose `:-default` values; exit 0 | No error expected |
| `.env` overrides | `.env` declares `GRAFANA_PORT=31337` | the listing prints `31337`, not `3000` | No error expected |
| Partial Selection | `COMPOSE_PROFILES=admin,observability` | only that closure's Modules appear; Keycloak's and Mailpit's endpoints are absent | No error expected |
| Explicit Selection argument | `./scripts/endpoints.py postgres` | Postgres's endpoints only | No error expected |
| Unknown Selection name | `./scripts/endpoints.py nope` | nothing on stdout | resolver diagnostic on stderr, exit 1 |
| Document is current | tracked tree unmodified | `--check` prints one OK line per Module and per app variable; exit 0 | No error expected |
| Document is stale | a port changed in `.env.example` or a compose default | `--check` names `docs/ENDPOINTS.md`, shows the differing lines and the regenerate command | exit 1 |
| Template disagrees with compose default | `.env.example` says `5432`, compose says `${POSTGRES_PORT:-5433}` | refuses, naming both files and the variable | exit 1 |
| Registry names an unknown Module | `x-app-variables.FOO.module: nope` | refuses, naming the variable and the Module | exit 1 |
| Registry names an unpublished endpoint | `module: postgres`, `endpoint: NOPE_PORT` | refuses, listing the Module's actual endpoint keys | exit 1 |
| Registry claims a Module-tier name | key `POSTGRES_URL` (an existing Module's prefix) | refuses: that name belongs to the Module tier (ADR 0003) | exit 1 |
| Undefined variable reference | `${NOPE}` with no default and no declaration | refuses, naming the variable and the file it came from | exit 1 |
| Stale port literal in README | README Endpoint cell says `:9999` | refuses, naming the port and the row | exit 1 |
| README table unparseable | Contents table moved or reshaped | refuses: parsed zero rows, so the pin reported on nothing | exit 1 |
| No Module files | `services/*/compose.yaml` matches nothing | `Refusal` — nothing to generate from | exit 1 |
| Registry absent or empty | root `compose.yaml` has no `x-app-variables:` | `Refusal`, naming the root file | exit 1 |

</intent-contract>

## Code Map

**The three hand-maintained copies this change removes or pins**
- `scripts/urls.sh:1-44` -- 14 `: "${VAR:=default}"` lines (`:15-28`) and 12 `printf` lines
  (`:30-43`). Header at `:13-14` claims "A service missing from this list is a service a
  developer cannot find" while omitting three. Non-port values (`POSTGRES_USER`,
  `POSTGRES_DB`, `REDIS_PASSWORD`, `KEYCLOAK_REALM`) come from `scripts/lib/common.sh:59-62`.
- `README.md:30-44` -- the `## Contents` table, `| Service | Version | Purpose | Endpoint |`,
  13 rows, 16 literal ports. `:46-47` the `BIND_ADDRESS` note. `:640-642` explains the
  Version column is knowingly manual — that reasoning does not extend to ports.
- `README.md:172-197` -- `## Connecting your application`; the dotenv block at `:174-197` is
  the exact set the new registry must reproduce: `DATABASE_URL`, `REDIS_URL`,
  `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
  `OIDC_CLIENT_SECRET`, `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `SMTP_HOST`, `SMTP_PORT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`.
- `README.md:308` layout tree line for `scripts/urls.sh`; `:402` and `:414` task-table rows;
  `:441-442` the standalone-runnable claim. `README.md:803-805` -- the `## Gotchas worth
  knowing` precedent: a section that holds no copies and points at the owning files.

**The source of truth**
- `services/*/compose.yaml` top-level `x-endpoints:` -- 13 files, 17 keys. Shape is
  `<VAR>: {url: <string with ${VAR:-default}>, description: <string>}`. Anchors:
  `services/postgres/compose.yaml:33-36` (DSN with user and db),
  `services/redis/compose.yaml:27-30` (password in the DSN),
  `services/keycloak/compose.yaml:32-40` (two keys), `services/minio/compose.yaml:36-42`,
  `services/mailpit/compose.yaml:27-33` (`smtp://`),
  `services/otel-collector/compose.yaml:14-18` (`grpc://`).
- Three descriptions carry a drift note to retire: `services/loki/compose.yaml:14`,
  `services/tempo/compose.yaml:15`, `services/keycloak/compose.yaml:38` ("Not in
  scripts/urls.sh"). Matching gotcha entries that name this story:
  `services/loki/gotchas.md:33-40`, `services/tempo/gotchas.md:42-47`,
  `services/keycloak/gotchas.md:85-91`.
- `compose.yaml:71-84` `x-bundles:` -- the precedent for a core-owned registry in the root
  file, with the `:44-70` comment block explaining why `x-` renders through as inert data.
  The new `x-app-variables:` block follows it and is commented the same way.
- `.env.example` -- ports at `:91,105,121-122,142-143,155-156,163,171,175,182-183,187,191,195,199`;
  credentials at `:92-94,106,124,127,130,144-145,167,200-201`; `BIND_ADDRESS=127.0.0.1` at `:81`;
  Redis db numbers at `:112-114`. `KEYCLOAK_CLIENT_SECRET` at `:130`. There is no
  `KEYCLOAK_CLIENT_ID` — the client id `devinfra-api` is fixed by the realm seed, so its
  registry entry is a literal, not an interpolation.

**Reuse**
- `scripts/resolve_selection.py` -- fully importable (`:411-412` guards `__main__`).
  `module_composes() -> list[Path]` `:96`; `read_model(path) -> dict[str, object]` `:105-130`
  (raises named `RuntimeError`s); `build_graph(paths) -> Graph` `:212`;
  `parse_request(values) -> list[str]` `:278`; `closure(graph, request) -> tuple[str, ...]`
  `:293`; `ALL_MODULES_REQUEST = "--all"` `:78`. `Graph.owners` maps service to Module.
- `scripts/assert_pins.py:66-108` `dotenv_declarations(path) -> dict[str, str]` -- reads a
  dotenv exactly as the shell would (strips `export `, quotes, trailing comment; last wins).
  Import it rather than re-implementing. `:60` `PIN` is the regex shape to copy for a
  general `${NAME}` / `${NAME:-default}` matcher.
- `scripts/assert_config.py:104-113` -- the precedent for importing from a sibling script;
  `:143-148` `PORT_VARIABLE`; `:314-376` `bundle_registry()` -- how a root-file registry is
  read and how absent/malformed/empty are refused distinctly; `:581-615` `published_ports()`;
  `:616-763` `module_contract()` legs 5 (`x-endpoints:`) and 7 (`x-requires:`).
- `scripts/check_gotchas.py` -- the house skeleton to match exactly: shebang `:1`, docstring
  `:2-28` (imperative first line, why-not-the-weaker-version prose citing ADRs, an indented
  invocation line, the stdout/stderr and exit contract, the stdlib-only paragraph),
  `from __future__ import annotations` `:30`, stdlib imports `:32-36`, `#:`-commented module
  constants `:38-64`, frozen dataclasses with Google `Attributes:` `:67-94`,
  `class Refusal(Exception)` `:97-98`, helpers `:101-302`, `build_parser()` `:305-321`
  (states every default so the pixi task body passes nothing), `main(argv) -> int` `:324-350`
  (returns 0/1, never `sys.exit`; `Refusal` caught only here), `raise SystemExit(main(sys.argv[1:]))`
  `:353-354`. stdout `f"{subject}: OK …"`, stderr `f"check-gotchas: {problem}"`.
- `scripts/select.sh:35-63` -- the wrapper shape `urls.sh` becomes: `set -euo pipefail`,
  source `lib/common.sh`, the `DEVINFRA_PYTHON` seam
  (`read -r -a DEVINFRA_PYTHON_ARGV <<<"${DEVINFRA_PYTHON:-python3}"`), drop empty
  arguments, `exec`.

**Wiring**
- `pixi.toml:114-116` `[tasks.urls]` (`cmd = "./scripts/urls.sh"`); `:52-54` `[tasks.up]`
  `depends-on = ["start", "wait", "urls"]`; `:179-181` the `# Validation` banner;
  `:198-200` `[tasks.lint-gotchas]` (the new task goes after it); `:214-216` `lint-python`;
  `:218-220` `[tasks.test]`; `:222-224` `[tasks.lint]` depends-on **line 224**;
  `:226-228` `[tasks.ci]` (no edit); `:249-256` the precommit rationale comment;
  `:262-264` `[tasks.precommit]` depends-on **line 264**.
- `.github/workflows/ci.yml:56-57` -- `validate` is one `pixi run ci`; a task joined to
  `[tasks.lint]` reaches CI with **no workflow edit**.
- `Makefile:88-91` `urls` target -- unchanged. `scripts/lint_selftest.py:78-103`
  `MAKE_FORWARDS` (`"urls": "@pixi run urls"` at `:92`) and `:5733-5737` recipe-set equality.

**Self-test**
- `scripts/lint_selftest.py` -- one 5100-line `main()` at `:650` with a closure
  `expect(name, condition, detail)` at `:658-662`; groups are `# --- Title. ---` banners.
  `planted(path, text)` `:418-439` (refuses to clobber a tracked path);
  `moved_aside(paths)` `:442-461`; `pixi(task, *args, env=...)` `:106-163`;
  `fresh(...)` `:1408`.
- **Cases that must be replaced**: `:1696-1732` — `pixi("urls")` with no `.env`, pinning a
  hardcoded tuple of twelve display names and fourteen default ports (this *is* the drift);
  `:1840-1843` — with `.env` planted at `:1805-1811`
  (`COMPOSE_PROFILES=admin,observability`, `POSTGRES_USER=zzuser`, `KEYCLOAK_REALM=zzrealm`,
  `GRAFANA_PORT=31337`), asserting the realm reaches stdout. Under Selection scoping Keycloak
  is outside that closure, so that assertion inverts into the AC3 proof.
- **Where new cases plug in**: planted-defect rows `:1074-1135` (loop `:1136-1145`);
  glob-empty rows `:1156-1184` (loop `:1185-1189`); the multi-row contract group modelled on
  the gotchas one at `:1191-1200` (which uses `moved_aside` + `planted` against
  `services/redisinsight/` because nothing bind-mounts it); table-parity idiom
  `:884-931` (`markdown_footprints()` — assert the parse found rows *before* asserting
  equality); `:1357-1379` "the README carries no copy"; membership assertions
  `:5286-5288` (every `lint-*` is a direct dependency of `lint`), `:5407-5411`
  (`precommit_members <= lint_members`), `:5417-5421` (precommit reaches nothing
  `RUNTIME_BOUND`); `FORBIDDEN` `:55` and the task-body scan `:744-758`;
  `:5042` fresh-clone survival; `:5065-5104` "every script that calls `compose` resolves its
  Selection first" (`endpoints.py` never calls `compose`, and `urls.sh` keeps no
  `compose` call, so the exemption list is untouched).

**ADR**
- `docs/adr/0012-every-module-carries-its-own-contract.md:84-86` -- "scripts/urls.sh is left
  alone rather than half-migrated. `x-endpoints:` is now the complete, checked source;
  generating the script from it is a separate change" — this story is that change.
  `:88-97` host-vs-network semantics; `:99-105` the raw-parse requirement.
- `docs/adr/0003-two-tier-configuration-namespace.md:20-31` -- Module variables
  `<MODULE>_<CONCERN>`; contract variables unprefixed, "listed in a Core-owned registry
  naming exactly one owning module". No such registry exists in code today.
- Template, identical across 0001-0016: `# <n>. <Title>` / blank /
  `Date: YYYY-MM-DD · Status: Accepted` / `## Context` / `## Decision` / `## Rejected` /
  `## Consequences`. Highest is `0016`, so the new one is **0017**.
  `docs/adr/README.md:10-27` is the index table, `| ADR | Decision | Status |`.
- `AGENTS.md:22-31` the Module-contract bullet (`:24` names `x-endpoints:`); `:48-50` the
  precedent phrasing for "the README carries no copies"; no mention of `urls.sh` anywhere.
- `CHANGELOG.md:9` `## [Unreleased]`, `### Changed` `:11`, `### Added` `:142`,
  `### Removed` `:224`; bullets open with a bolded noun phrase; ADR links as
  `[ADR 0015](docs/adr/0015-deferred-smoke-checks.md)` (`:168`).
- `pyproject.toml:4-22` -- ruff line-length 120, `select = ["E","F","I","UP","B","RUF","D"]`,
  google pydocstyle, mypy strict, `python_version = "3.12"`.
  `.yamllint.yaml` -- `line-length: max 120`, `--strict`.

## Tasks & Acceptance

**Execution:**
- `compose.yaml` -- add a top-level `x-app-variables:` registry after `x-bundles:`, with a
  comment block in the style of `:44-70` stating: this is the application tier of ADR 0003's
  two-tier namespace; each entry is one externally-dictated variable name an SDK reads,
  owned by exactly one Module and pinned to one of that Module's `x-endpoints:` keys; the
  three remaining unprefixed contract names (`BIND_ADDRESS`, `COMPOSE_PROJECT_NAME`,
  `COMPOSE_PROFILES`) are core's own, name no endpoint, and are deliberately out of this
  registry; `x-` renders through as inert data. Entry shape:
  `<NAME>: {module, endpoint, value, description}`. Register the fourteen names listed in
  the Code Map, reproducing `README.md:174-197` value for value with `${VAR:-default}`
  interpolation (`OIDC_CLIENT_ID` is the literal `devinfra-api`, fixed by the realm seed).
  `DATABASE_URL`'s value exceeds yamllint's 120 columns — use a double-quoted scalar with a
  trailing `\` line continuation, which folds without inserting a space; comment that once.
- `scripts/endpoints.py` -- new, in `check_gotchas.py`'s shape, importing `read_model`,
  `module_composes`, `build_graph`, `parse_request`, `closure` from `resolve_selection` and
  `dotenv_declarations` from `assert_pins`. CLI: positional `SELECTION...`;
  `--format {text,markdown}` (default `text`); `--env-file PATH` (default: resolve from the
  process environment); `--write PATH`; `--check`. Selection default is `COMPOSE_PROFILES`
  when non-empty, otherwise every Module — a listing that refuses to document is worse than
  one that documents everything, and `pixi run urls` must keep working on a fresh clone.
  `--check` fixes format `markdown`, Selection `--all`, env source `.env.example`, and
  compares against `docs/ENDPOINTS.md`. Implement Compose-compatible `${VAR}` /
  `${VAR:-default}` interpolation. `Refusal` for: no Module files, no/empty/non-mapping
  `x-app-variables:`, `docs/ENDPOINTS.md` absent under `--check`. Per-subject failures for
  every remaining row of the I/O matrix. stdout one line per Module and per app variable;
  stderr `endpoints: <problem>`.
- `scripts/endpoints.py` -- three `--check`-only legs beyond the document comparison, each
  preceded by an assert-the-parse-found-something guard: (a) for every variable an endpoint
  `url` or a registry `value` references, `.env.example`'s declaration must equal the
  compose `${VAR:-default}` fallback, so changing only one of the two fails; (b) every port
  literal in the `## Contents` table's Endpoint column must be the resolved value of some
  endpoint variable; (c) `README.md` carries no `://localhost:` connection string outside
  that table.
- `docs/ENDPOINTS.md` -- new, generated by `pixi run endpoints`. Do-not-edit banner naming
  the command; an `## Application variables` table with `Variable | Module | Endpoint |
  Value | Purpose` columns — the `Module` and `Endpoint` columns are what state unambiguously
  which service each variable reaches where names look alike; then `## Service endpoints`,
  one `###` per Module in sorted order, each a `Variable | URL | Description` table.
- `scripts/urls.sh` -- rewrite as a wrapper in `select.sh:35-63`'s shape: keep the
  `set -euo pipefail`, the `lib/common.sh` source and the header, add the `DEVINFRA_PYTHON`
  seam, drop empty arguments, and `exec` `scripts/endpoints.py` with the caller's Selection.
  Delete the 14 hardcoded defaults and the 12 `printf` lines. Rewrite the header to say the
  list is generated and that a Module missing from it is a Module missing an `x-endpoints:`
  block, which `lint-config` already refuses.
- `pixi.toml` -- add `[tasks.lint-endpoints]` (`python scripts/endpoints.py --check`) after
  `lint-gotchas` at `:200`, and `[tasks.endpoints]`
  (`python scripts/endpoints.py --format markdown --write docs/ENDPOINTS.md`,
  description "Regenerate docs/ENDPOINTS.md from module metadata") in the same block. Add
  `lint-endpoints` to `[tasks.lint]` depends-on (`:224`) and `[tasks.precommit]` depends-on
  (`:264`). Leave `[tasks.urls]`, `[tasks.up]` and `[tasks.ci]` untouched.
- `README.md` -- replace the dotenv block at `:174-197` with a pointer to
  `docs/ENDPOINTS.md` in the `## Gotchas worth knowing` style (`:803-805`): where the values
  live, that they are generated from `x-endpoints:` plus `x-app-variables:`, that
  `pixi run lint-endpoints` fails the build when they drift, and that
  `pixi run urls` prints the ambient Selection's endpoints. Keep the `## Contents` table but
  leave its ports to the new pin. Add `docs/ENDPOINTS.md` to the layout tree near `:308` and
  correct the `scripts/urls.sh` line; add `endpoints` and `lint-endpoints` rows to the task
  tables at `:402`/`:414`.
- `services/loki/compose.yaml:14`, `services/tempo/compose.yaml:15`,
  `services/keycloak/compose.yaml:38` -- delete the "Not in scripts/urls.sh" drift notes.
  `services/loki/gotchas.md:33-40`, `services/tempo/gotchas.md:42-47`,
  `services/keycloak/gotchas.md:85-91` -- these entries describe a drift that no longer
  exists; remove them, keeping every file at one entry or more so `lint-gotchas` still passes.
- `AGENTS.md` -- add one bullet, in the phrasing of `:48-50`: connection details live in the
  Module's `x-endpoints:` and the root `x-app-variables:` registry; `docs/ENDPOINTS.md` is
  generated and must never be hand-edited; regenerate with `pixi run endpoints`. Keep the
  line width of its neighbours (97-105 columns).
- `docs/adr/0017-endpoint-documentation-is-generated.md` + a row in `docs/adr/README.md` --
  Context: three hand-maintained copies, ADR 0012:84-86 deferring the generation to a later
  change, ADR 0003's registry never built. Decision: one generator, the `x-app-variables:`
  registry, `docs/ENDPOINTS.md` regenerated and diffed, the `.env.example`-versus-compose-
  default agreement rule, and the deliberate exclusion of the three core-owned contract
  names. Rejected: generating from `docker compose config` (0012:99-105 rules it out); a
  second hand-maintained list; rendering the committed document against a developer's `.env`
  (machine-dependent, so the drift check would fail for everyone with a customised
  environment); deleting `scripts/urls.sh`; asserting the README Version column. Consequences:
  supersedes 0012's `urls.sh` consequence only, states that adding a Module now adds a
  documented endpoint for free, and that a new application variable is a two-line registry
  edit plus a regenerate.
- `scripts/lint_selftest.py` -- replace `:1696-1732` with: `pixi("urls")` on a fresh clone
  exits 0 and prints, for every key in every Module's `x-endpoints:`, that key's resolved
  default — derived from the files, not from a hardcoded tuple, with a guard that the
  derivation found all 17 keys. Turn `:1840-1843` into the AC3 proof: with the planted
  `.env`'s `COMPOSE_PROFILES=admin,observability`, `31337` and `zzuser` reach stdout and
  `zzrealm` and Keycloak's port do not. Add: a planted-defect row and an empty-glob row for
  `lint-endpoints`; a `# --- The endpoint document cannot drift. ---` group covering every
  remaining I/O-matrix row via `planted`/`moved_aside` (stale document, template-versus-
  default disagreement, unknown Module, unpublished endpoint key, a Module-tier registry
  name, an undefined `${VAR}`, a stale README port, an unparseable README table, an absent
  registry); a clean-pass direction over the real tree asserting a non-empty walk; and an
  assertion that `docs/ENDPOINTS.md`'s banner names a task `pixi.toml` actually defines.
- `CHANGELOG.md` -- `### Added` bullets for `docs/ENDPOINTS.md`, the `x-app-variables:`
  registry, and `pixi run endpoints` / `pixi run lint-endpoints`, linking
  [ADR 0017]; a `### Changed` bullet under `#### Everything else` for `pixi run urls` now
  being generated and Selection-scoped, and for the README no longer carrying connection
  strings.

**Acceptance Criteria:**
- Given every Module in the catalog and its `x-endpoints:` block, when `pixi run endpoints`
  regenerates `docs/ENDPOINTS.md`, then all 13 Modules and all 17 endpoint variables appear,
  every registered application variable appears with its owning Module and endpoint key
  named, and the file's variable names are exactly those an application reads.
- Given a clean checkout, when `pixi run lint-endpoints` runs with no container runtime and
  no network, then it exits 0 and reports one line per Module and per application variable.
- Given `pixi run precommit` with every container runtime shadowed by a failing stub, when it
  runs, then it exits 0 with `lint-endpoints` among the checks it ran.
- Given `pixi run ci`, when it runs, then `lint-endpoints` joins `lint`, every self-test case
  passes, and the run exits 0.
- Given a fresh clone with no `.env`, when `pixi run urls` runs, then it exits 0 and prints
  every endpoint variable at its compose default, including `LOKI_PORT`, `TEMPO_PORT` and
  `KEYCLOAK_MGMT_PORT`, which the old script omitted.
- Given `./scripts/urls.sh` invoked directly and `make urls`, when either runs, then both
  still produce the listing, so no documented entry point regressed.
- Given the README, when it is read, then it carries no connection string and no dotenv block
  of endpoint values, and points at `docs/ENDPOINTS.md` instead.

## Spec Change Log

## Review Triage Log

### 2026-09-09 — Review pass
- verdicts: 39 findings — high 0, medium 15, low 19, false 5, maybe-false 0
- findings:
  - `[medium]` `[patch]` blind-hunter: generated table cells carry `<realm>`/`<id>` unescaped, so Markdown drops them — confirmed raw at `docs/ENDPOINTS.md:55` and `:119`; fixed with a `cell()` helper escaping `|`, `<`, `>` on every generated cell, document regenerated.
  - `[medium]` `[patch]` blind-hunter: the README port pin is set-membership only, so a row can state another Module's port — verification-gap demonstrated it (Prometheus's 9090 in the Grafana row exited 0); fixed by resolving each row's literals to one owning Module and refusing two rows that claim the same one.
  - `[low]` `[patch]` blind-hunter: `CONNECTION_STRING` missed `127.0.0.1:`, `0.0.0.0:`, `[::1]:` — latent (no such spelling in README today) but a one-line correction; alternation widened and a loopback prose case added.
  - `[low]` `[patch]` blind-hunter: `check_template` deduped on `(where, name)`, hiding a second disagreeing fallback in one file — latent today; key widened to `(where, name, fallback)`.
  - `[low]` `[patch]` blind-hunter: the port-set derivation reported `.env.example` as the source of an endpoint key it never declares — confirmed at the synthesized `${VAR}` lookup; `where` now names the owning module file.
  - `[low]` `[patch]` blind-hunter: the stale-document diff truncated at 40 lines with no marker — confirmed; a `… N more line(s)` marker is now appended.
  - `[medium]` `[patch]` blind-hunter: `--check` silently overrode `--format`/`--env-file`/Selection and ignored `--write` — confirmed in `main`; a new `settle()` refuses each combination by name.
  - `[low]` `[patch]` blind-hunter: `environment_for` ran before catalog problems were reported, so an absent template masked a malformed registry — confirmed by statement order; the report now runs first.
  - `[low]` `[patch]` blind-hunter: `make urls` help text still said "Print every service endpoint" — confirmed at `Makefile:89`; corrected there and in `[tasks.urls]`. The missing argument forwarding was left alone: the Makefile is a frozen deprecated shim pinned by `MAKE_FORWARDS` set equality.
  - `[low]` `[patch]` blind-hunter: the resolved OIDC discovery URL the old listing printed is no longer produced anywhere — confirmed against the deleted `printf`; `OIDC_DISCOVERY_URL` registered in `x-app-variables:` and the document regenerated.
  - `[medium]` `[patch]` blind-hunter: `scripts/lib/common.sh`'s four `:=` defaults are unexported, so the generator falls through to the compose fallbacks instead — confirmed; closed by the widened template pin below, which makes the two sources one statement.
  - `[medium]` `[patch]` blind-hunter: nothing ran `pixi run endpoints`, and nothing asserted `render_text`'s application-variable branch — confirmed by enumerating every `pixi(...)` call in the self-test; both cases added.
  - `[low]` `[patch]` blind-hunter: ADR 0012 carries no pointer to the ADR superseding its `urls.sh` consequence — confirmed; a `## Supersessions` table added to `docs/adr/README.md`, leaving 0012 unedited per the register's own rule.
  - `[low]` `[defer]` blind-hunter: no reverse check that every unprefixed `.env.example` name is registered or exempted — real gap, deferred as ADR 0003 enforcement beyond this story. The same finding's claim that `SILO_VERSION` breaks the "three unprefixed names" statement is refuted: `SILO_VERSION` is prefixed, so a statement about unprefixed names is unaffected.
  - `[medium]` `[patch]` edge-case: `pixi run endpoints postgres` overwrites the committed document with a one-Module render, because pixi appends task arguments — confirmed; refused by `settle()`.
  - `[low]` `[patch]` edge-case: an unreadable `README.md` escapes as a traceback — confirmed, `main` caught only `Refusal`/`RuntimeError`; `OSError`/`UnicodeDecodeError` now reported in the house shape.
  - `[low]` `[patch]` edge-case: same for an unreadable document or an undeletable `--write` target — same root cause, same fix.
  - `[low]` `[patch]` edge-case: only the Endpoint cell was scanned, so a port in another Contents cell was pinned by nothing while the whole table stayed exempt from the prose scan — confirmed; every cell of every row is scanned now.
  - `[low]` `[patch]` edge-case: a Contents row that is not four cells was silently skipped — confirmed; a row of unexpected width is reported.
  - `[medium]` `[patch]` edge-case: two rows' ports swapped both remain published — same defect as the set-membership pin above, fixed with it. A *mutual* swap that leaves every Module claimed exactly once is still out of reach and is recorded in the function's docstring; closing it needs the hand-written service-name map ADR 0017 refuses.
  - `[low]` `[reject]` edge-case: `$$` and `${VAR:?err}` interpolation forms — no value in the repository uses either, and handling them adds branches for a state never shown reachable.
  - `[medium]` `[patch]` edge-case: shell `: "${VAR:=default}"` defaults were unpinned — confirmed at `scripts/lib/common.sh:59-62` and `scripts/token.sh:11`; the template pin now walks them.
  - `[false]` `[reject]` edge-case: a Module with no `x-endpoints:` makes `pixi run urls` print nothing — that is the repository's stated fail-loud contract (AD-18, NFR-5), the same behaviour `select.sh` has, and `lint-config` already refuses that state; a partial listing would be the defect.
  - `[low]` `[reject]` edge-case: the fresh-clone derivation could pass over a partial glob — it requires `Path.glob` to return a subset of files that exist, which was not shown reachable; the empty case is already guarded by an explicit `bool(...)` expectation.
  - `[low]` `[reject]` edge-case: `contextlib.suppress(OSError)` could hide a surviving fixture directory — nothing writes into a fixture directory, and reporting instead would change a shared helper's behaviour for every caller.
  - `[low]` `[patch]` edge-case: the OIDC discovery URL was deleted rather than moved — same finding as the blind-hunter's, fixed with it.
  - `[low]` `[patch]` edge-case: "The README carries no connection strings" is contradicted by the Contents table — confirmed; routed to defer as an agent-context edit, then addressed anyway when the same sentence was corrected in `CHANGELOG.md`, ADR 0017 and `AGENTS.md` together.
  - `[low]` `[patch]` edge-case: "the only thing in the repository that knows a connection string" overstates — confirmed against `scripts/token.sh:11` and `services/prometheus/conf/prometheus.yml`; the claim is now "states a connection string as documentation", with both exceptions named.
  - `[medium]` `[patch]` verification-gap: the `pixi run endpoints` write path is executed by no test — filed pre-verified; a byte-for-byte regeneration case added.
  - `[medium]` `[patch]` verification-gap: the README port pin is set-membership only, demonstrated by editing the Grafana row to 9090 — fixed above, with a moved-port case added.
  - `[medium]` `[patch]` verification-gap: `--all` has no test — filed pre-verified; a case added under the planted `.env` asserting a Module outside the closure appears.
  - `[medium]` `[patch]` verification-gap: `urls.sh`'s `DEVINFRA_PYTHON` seam is unverified while `select.sh`'s is pinned — filed pre-verified; the stub-interpreter case mirrored for `urls.sh`.
  - `[medium]` `[patch]` verification-gap: `scripts/token.sh:11` keeps an unpinned hard-coded port default — filed pre-verified; closed by the widened template pin.
  - `[medium]` `[patch]` verification-gap (other findings): `lint-endpoints`'s membership in `[tasks.precommit]` is unpinned, since the existing assertions only require precommit ⊆ lint — verified; explicit membership assertion added.
  - `[false]` `[reject]` intent-alignment: 7 of 13 Modules have no application-variable entry — every Module has an entry in the generated document; the seven are web consoles for which no SDK dictates a name, and which Module "deserves" one is not machine-decidable.
  - `[medium]` `[patch]` intent-alignment: a credential default in a module's own `environment:` was a third, unpinned copy — confirmed at `services/postgres/compose.yaml`; the template pin now walks every `${VAR:-default}` in the module files for variables the catalog references, which leaves `POSTGRES_EXTRA_DATABASES` (a deliberate template-versus-fallback difference) out of scope.
  - `[false]` `[reject]` intent-alignment: the committed document is never partial — AC3 speaks of the endpoint listing, which is Selection-scoped and tested; `--format markdown` accepts a Selection, and fixing `--check` to the whole catalog is what makes the drift check meaningful.
  - `[false]` `[reject]` intent-alignment: the change edits Story 3.2's gotcha registers — removing an entry that documents a defect this change fixes is the register's own maintenance rule, recorded in ADR 0017.
  - `[false]` `[reject]` intent-alignment: the loop-protocol layer — no `sprint-status.yaml` change and no operator action owed; nothing to fix.

### 2026-09-09 — Review pass (follow-up)
- verdicts: 35 findings — high 0, medium 10, low 23, false 2, maybe-false 0
- findings:
  - `[low]` `[patch]` blind-hunter: a narrowed `--format markdown` render emitted the `## Application variables` heading, its prose and a header row over zero rows — confirmed with `--format markdown grafana`, and `render_text` already suppresses the same section; the section is now built only when the Selection owns a registered variable, and the separator before `## Service endpoints` is normalised with it.
  - `[low]` `[patch]` blind-hunter: the `--write` block sat outside the try/except whose own comment calls a traceback "the same silent-shape failure a named refusal exists to replace" — confirmed, `--write /zz-nope-root/x.md` raised `OSError`; the write now reports `endpoints: cannot write <path>: …` and returns 1.
  - `[low]` `[reject]` blind-hunter: `${VAR:+alt}` and `${VAR:?err}` are not resolved as Compose resolves them, though the docstring says "exactly" — real but latent: no endpoint URL or registry value in the repository uses either form (`grep` finds `:+` only in `scripts/smoke-test.sh` and `services/grafana/smoke.sh`, neither of which `interpolate()` ever reads), and an undeclared `${B:+alt}` already fails loudly. Same reasoning as the prior pass's reject of `$$`/`${VAR:?err}`: the fix adds branches for a state never shown reachable.
  - `[low]` `[defer]` blind-hunter: nothing pins the reverse direction — an unprefixed `.env.example` name that no registry entry declares and no exemption names — carried, matching the standing deferred entry; ADR 0003 enforcement, not this story's endpoint documentation.
  - `[medium]` `[patch]` blind-hunter: the README `## Contents` pin was one-way, so a Module the table names no port for was checked by nothing — confirmed by reading `check_readme`; `unclaimed = set(port_owner.values()) - set(claimed)` now refuses it, with a self-test case that blanks Grafana's Endpoint cell.
  - `[low]` `[patch]` blind-hunter: `port_owner[resolved] = module` was last-wins, so two Modules on one port would leave the README pin naming whichever the walk reached last — confirmed in `run_check`; replaced with `setdefault` plus a named collision problem.
  - `[low]` `[patch]` blind-hunter: `fallback_occurrences` walked `scripts/**/*.sh` only, while `pixi run lint-shell` covers `scripts/**/*.sh services/**/*.sh .githooks/*` — confirmed against `pixi.toml:215`; the shell scan now walks the same three sets, so a `: "${VAR:=default}"` in a module smoke script or a git hook can no longer drift from `.env.example`.
  - `[medium]` `[patch]` blind-hunter: the `pixi("endpoints")` case ran the real generator over the tracked document and asserted afterwards, so a diverging render would report the failure and leave the working tree dirty — confirmed; the case is now staged with `moved_aside` like every other case in the group.
  - `[medium]` `[patch]` blind-hunter: `read_endpoints`'s three per-entry diagnostics (non-mapping body, empty `url:`, empty `description:`) had no case, while the registry side has seven — confirmed by enumerating the group; three planted `services/grafana/compose.yaml` cases added, with a byte-for-byte restore assertion.
  - `[medium]` `[patch]` blind-hunter: neither of `settle()`'s refusals was executed by any test — confirmed with the verification-gap layer's independent enumeration; two cases added, each asserting the refusal, its wording, and that the tracked document survived.
  - `[low]` `[patch]` blind-hunter: the generated document ended with a trailing blank line, reproduced by every regeneration — confirmed with `od`; `render_markdown` now trims trailing blanks before joining.
  - `[medium]` `[patch]` blind-hunter: the document's own edit instructions said to change `.env.example` and your `.env`, omitting the compose `${VAR:-default}` that `check_template` pins to the template — confirmed, and ADR 0017's Consequences say the opposite; the paragraph now names both files and the check that binds them.
  - `[low]` `[reject]` blind-hunter: no paste-ready dotenv block replaces the one the README lost — the intent replaces that block with a pointer to `docs/ENDPOINTS.md`, and a `--format dotenv` is a new public surface the intent does not ask for.
  - `[low]` `[defer]` blind-hunter: `make urls` still cannot forward a Selection — carried, matching the standing deferred entry; the Makefile is a frozen deprecated shim pinned by `MAKE_FORWARDS` set equality.
  - `[low]` `[patch]` blind-hunter: `CHANGELOG.md` called the wrapper "fifteen-line" where `scripts/urls.sh` is 48 lines and 13 of code, and "run standalone are unchanged" reads as a claim about the output — confirmed by `wc -l`; corrected to "thirteen-line" and to "invoked exactly as before; what they print is the new listing".
  - `[low]` `[patch]` blind-hunter: `docs/adr/0017-…:33` is a 159-column line, the only one over 100 in either 0016 or 0017 — confirmed with `awk`; the paragraph is re-wrapped.
  - `[medium]` `[patch]` edge-case: `settle()` refused `--write docs/ENDPOINTS.md` only for a Selection, so an appended `--format text` or `--env-file` still overwrote the committed document — confirmed; the guard now names any of the three.
  - `[low]` `[patch]` edge-case: `--write` to an unwritable target escapes as a traceback — same root cause as the blind-hunter's, fixed with it.
  - `[low]` `[reject]` edge-case: a reference nested inside a `:-` default (`${MISSING:-${B}}`) renders the inner literal — real but latent: no value in the repository nests one, and the fix is a substitution loop or a new refusal for a state never shown reachable.
  - `[low]` `[reject]` edge-case: `${VAR:+alt}` — same finding as the blind-hunter's, rejected with it.
  - `[low]` `[reject]` edge-case: `PORT_LITERAL` is `\d{2,5}`, so a six-digit README literal matches nothing — a six-digit number is not a port, and widening to `\d+` would pin every `:digits` in the table (versions, dates) to the catalog, which is more than a direct correction.
  - `[medium]` `[patch]` edge-case: a deleted Contents row or a new Module with none passes — same finding as the blind-hunter's completeness gap, fixed with it.
  - `[low]` `[patch]` edge-case: two endpoint variables resolving to the same port overwrite silently — same finding as the blind-hunter's, fixed with it.
  - `[low]` `[reject]` edge-case: `./scripts/urls.sh` standalone now needs PyYAML and its diagnostic names `pixi run select` — confirmed by running it under `/usr/bin/env -i`, but the wrapper's interpreter dependency is the design the spec's Design Notes chose, the `DEVINFRA_PYTHON` seam is documented in the script's own header, and the message already says "or any `pixi run` task"; rewording it edits a diagnostic `select.sh` shares.
  - `[low]` `[patch]` edge-case: `assert_config.py:733` and `lint_selftest.py:3120,3149` describe `urls.sh`'s drift in the present tense — confirmed; all three now say "before ADR 0017 generated it".
  - `[low]` `[reject]` edge-case: `[tasks.urls]`'s description was edited though the spec fenced it off — carried; the prior pass corrected that description deliberately and recorded it.
  - `[low]` `[reject]` edge-case: the Contents table keeps thirteen addresses against the AC's wording — carried; the prior pass corrected the same sentence in `CHANGELOG.md`, ADR 0017 and `AGENTS.md`.
  - `[medium]` `[patch]` verification-gap: the registry's key set is pinned by nothing, so a registered application variable can be deleted with the whole gate green — filed pre-verified and independently reproduced by the layer in a scratch tree; an `APP_VARIABLES` constant now pins the set the way `MAKE_FORWARDS` pins the Makefile's recipes.
  - `[medium]` `[patch]` verification-gap: the `--write docs/ENDPOINTS.md` + Selection refusal is executed by no test — filed pre-verified; covered by the two `settle()` cases above.
  - `[medium]` `[patch]` intent-alignment: every new assertion runs at the pixi-task surface, and `./scripts/urls.sh` — a documented entry point — is executed once against a stub interpreter and never produces a listing — confirmed by enumerating the group; a standalone case now runs the real interpreter and asserts the Selection's endpoints reach stdout.
  - `[low]` `[reject]` intent-alignment: a registry `value:` is not reconciled against the `endpoint:` it declares, so a literal port in a value is pinned by nothing — real but latent: every shipped value states its port as `${VAR:-default}`, which `check_template` already pins, and the fix adds a check leg for a state never shown reachable.
  - `[low]` `[reject]` intent-alignment: the matrix's `POSTGRES_URL` row reads as "any Module prefix" while the code refuses only another Module's — the literal reading is self-contradictory (`REDIS_URL` must be legal), and the only fix is to edit this build's spec; ADR 0017 and `module_tier_owner`'s docstring both state the narrowing.
  - `[low]` `[reject]` intent-alignment: the prose ban rewrote three README paragraphs the intent never named, and the replacement prose is asserted by nothing — the ban itself is tested in both host spellings; asserting the replacement wording pins prose, not behaviour.
  - `[false]` `[reject]` intent-alignment: additions beyond the matrix (the shell/compose fallback reach, the README row-ownership pin, the per-Module missing-block failure, `OIDC_DISCOVERY_URL`, the application-variable scoping rule) — each strengthens a pin inside the Never list rather than violating one, and `OIDC_DISCOVERY_URL` is a recorded patch from the prior pass.
  - `[false]` `[reject]` intent-alignment: the tests are stronger than the intent asked, deriving endpoint defaults from the files rather than restating them — no defect claimed.


## Design Notes

**Why the committed document renders against `.env.example`, not `.env`.** A generated file
compared against a regeneration is only a real check if both sides are functions of tracked
inputs. Rendering against a developer's `.env` would make `lint-endpoints` fail for anyone
who changed a port locally — the check would be noise and would be disabled. `.env.example`
is tracked, is what `pixi run init` copies, and is what a reader of the document actually
gets on a fresh clone. The live `.env` still drives the *terminal* listing, which is where a
developer wants their own values.

**Why the template-versus-default agreement leg is required, not extra.** With the document
rendered from `.env.example`, changing only a compose `${VAR:-default}` would leave the
document unchanged and the build green — exactly the silent divergence AC2 forbids. Pinning
the two sources to each other closes it, and is the same shape `assert_pins.py` already
applies to `*_VERSION`.

**Registry entry, and the yamllint continuation:**

```yaml
x-app-variables:
  DATABASE_URL:
    module: postgres
    endpoint: POSTGRES_PORT
    description: SQLAlchemy and psycopg connection DSN.
    # A double-quoted scalar folds a trailing `\` with no inserted space, which is
    # the only way to keep this DSN inside yamllint's 120 columns.
    value: "postgresql://${POSTGRES_USER:-devinfra}:${POSTGRES_PASSWORD:-devinfra}\
      @localhost:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-devinfra}"
```

**Why a wrapper rather than deleting `scripts/urls.sh`.** `pixi run urls`, `make urls` and
`./scripts/urls.sh` are three documented entry points, `MAKE_FORWARDS` pins the second by
set equality, and `README.md:441` promises the third. A 15-line wrapper keeps all three and
makes the change reviewable as "the list moved", rather than as a deletion plus three
call-site edits.

**Why `endpoints.py` may import PyYAML.** `check_gotchas.py`'s "stdlib only" paragraph is
about running from `smoke-test.sh` in a bare checkout. This script runs only from pixi
tasks, and `resolve_selection.read_model()` — the required way to read a raw Module file
(ADR 0012:99-105) — already imports `yaml`, as `assert_renovate.py` does inside `precommit`.

## Verification

**Commands:**
- `pixi run endpoints` -- expected: rewrites `docs/ENDPOINTS.md`; `git diff` is empty on a
  second run.
- `pixi run lint-endpoints` -- expected: one OK line per Module and per application
  variable, exit 0.
- `pixi run urls` -- expected on a fresh clone: all 17 endpoint variables at their compose
  defaults, exit 0. With `COMPOSE_PROFILES=admin` in the environment: only that closure.
- `./scripts/urls.sh postgres` -- expected: Postgres's endpoint only, exit 0.
- `pixi run lint-python` -- expected: `ruff format --check`, `ruff check` and `mypy --strict`
  pass over `scripts/`.
- `pixi run lint-shell` -- expected: the rewritten `urls.sh` is shellcheck-clean.
- `pixi run lint-yaml` -- expected: the new `x-app-variables:` block passes at 120 columns.
- `pixi run lint-gotchas` -- expected: still 13 OK lines after the three entries are removed.
- `pixi run test` -- expected: the replaced `urls` cases, every new endpoint case, and the
  membership assertions all pass.
- `pixi run ci` -- expected: exit 0. This is the done-gate.
- `pixi run lint-config` -- expected: unaffected; the new root `x-` key adds no service,
  volume or network. If no container runtime is available in this environment, report it as
  not run rather than as passed.

## Auto Run Result

Status: done

**Implemented change.** `scripts/endpoints.py` is the only thing in the repository that
states a connection string as documentation. It reads every Module's raw `x-endpoints:`
block and the new core-owned `x-app-variables:` registry in the root `compose.yaml`, and
renders two surfaces: the Selection-scoped terminal listing `pixi run urls` prints against
the live environment, and `docs/ENDPOINTS.md`, the whole catalog rendered against the
tracked `.env.example`. `pixi run lint-endpoints` re-renders from tracked inputs and refuses
on any divergence — the document, the template-versus-compose-default agreement, and the
README's `## Contents` ports and prose — and joins both `[tasks.lint]` and
`[tasks.precommit]`. `scripts/urls.sh` is a wrapper, so all three documented entry points
survive.

**Files changed (this pass).**
- `scripts/endpoints.py` — suppress the application-variable section for a Selection that
  owns none; trim the generated document's trailing blank line; name the compose
  `${VAR:-default}` in the document's edit instructions; widen the `--write DOCUMENT`
  refusal from a Selection alone to `--format` and `--env-file` too; report a failed write
  in the house shape instead of a traceback; refuse two Modules resolving to one port;
  refuse a Module the README `## Contents` table states no port for; walk the same shell
  files `lint-shell` does.
- `scripts/lint_selftest.py` — pin the registry's key set with a new `APP_VARIABLES`
  constant; stage the `pixi run endpoints` case with `moved_aside`; cover both `settle()`
  refusals, the three malformed `x-endpoints:` entry shapes, the README-completeness leg,
  and `./scripts/urls.sh` standalone producing a real listing.
- `docs/ENDPOINTS.md` — regenerated.
- `scripts/assert_config.py`, `scripts/lint_selftest.py` — three comments describing
  `urls.sh`'s drift in the present tense, now past.
- `CHANGELOG.md` — the wrapper's line count and what "unchanged" claims.
- `docs/adr/0017-endpoint-documentation-is-generated.md` — re-wrap a 159-column line.

**Review findings breakdown.** 35 findings across four layers: 0 high, 10 medium, 23 low,
2 false. 15 entries patched (7 at medium, 8 at low); 3 carried to the existing deferred
entries without re-deferring (the ADR 0003 reverse-direction check, `make urls` Selection
forwarding, the mutual README port swap); 11 rejected, each with its reason recorded in the
triage log above — the unreachable interpolation forms (`${VAR:+alt}`, `${VAR:?err}`, a
nested default), the six-digit README literal, a `--format dotenv` surface the intent does
not ask for, the standalone PyYAML dependency the Design Notes chose, the registry
`value:`-versus-`endpoint:` pin, the `POSTGRES_URL` matrix wording, the unasserted README
prose rewrites, and the two carried rejects from the prior pass.

**Follow-up review recommendation: false.** This is a follow-up pass and it patched no
`high`; every remaining risk is either recorded in `deferred` or rejected above.

**Verification performed.**
- `pixi run ci` — exit 0. Every task ran, including the runtime-bound `lint-compose` and
  `lint-config` (a container runtime was available in this environment).
- `pixi run test` — exit 0, 1268 assertions, working tree clean afterwards; every new case
  confirmed present in the output by name.
- `pixi run lint-endpoints` — 13 Module lines, 15 application-variable lines, one document
  line, exit 0.
- `pixi run endpoints` — rewrites `docs/ENDPOINTS.md`; a second run leaves it byte-for-byte.
- Direct probes of the new guards: `--write /zz-nope-root/x.md` reports
  `endpoints: cannot write …` and exits 1; `--write docs/ENDPOINTS.md --format text` is
  refused by name; `--format markdown grafana` renders no empty application-variable table.

**Residual risks.** The three entries in `deferred` stand: the ADR 0003 registry is enforced
catalog-inward only, `make urls` still forwards no Selection, and a mutual swap of two
Contents rows' ports remains out of reach without the hand-written service-name map ADR 0017
refuses. Beyond those, the interpolation forms rejected above (`${VAR:+alt}`, a nested
default) would render silently wrong if a future value used one; nothing in the repository
does today, and no check would notice if one did.

---
title: 'The shared fragment and the first Module'
type: 'refactor'
created: '2026-09-07'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred:
  - summary: >-
      Nothing enforces the rest of the module contract this story establishes — that
      common/base.yaml never appears in an include list, that no module uses
      project_directory, that no cross-file `<<: *alias` survives, and that every module
      directory maps to a rendered service and back.
    evidence: |-
      The volume/network identifier-only half is now checked by scripts/assert_config.py.
      The remaining rules are asserted only by this story's one-off acceptance criteria and
      by prose in common/base.yaml's header, so the next twelve extractions copy them by
      hand with no gate. Epics story 2.4 ("Every Module carries its own contract, enforced")
      owns the bidirectional check; the base-in-include, project_directory and cross-file
      alias rules are not currently assigned to any story.
    location: >-
      scripts/ (no check exists)
    severity: medium
  - summary: >-
      lint-json still globs only renovate.json and docker/**/*.json, so service JSON config
      drops out of coverage the moment stories 2.2-2.3 move it into a module directory.
    evidence: |-
      pixi.toml's lint-json task was deliberately left alone: postgres contributes no JSON, and
      a services/**/*.json glob that matches nothing makes the task fail on an unmatched
      pattern. docker/keycloak/realms/devinfra-realm.json and docker/pgadmin/servers.json move
      in stories 2.2 and 2.3; the glob must be added in the same change that moves the first
      one, or those files stop being parsed with no error.
    location: >-
      pixi.toml (lint-json task)
    severity: low
  - summary: >-
      README's "Gotchas worth knowing" documents none of the four traps this story introduces,
      and there is no written recipe for adding the next module.
    evidence: |-
      The traps — a YAML anchor cannot cross an include boundary; common/base.yaml must never
      be included because that adds a service named `defaults`; a relative bind path resolves
      against the module file's own directory; a one-shot helper extending the base must
      override `restart: "no"` — live only in common/base.yaml's and services/postgres/
      compose.yaml's header comments, which a contributor adding a module has no reason to
      open first. AGENTS.md now routes `services/` work through the README section. Epic 3
      story 3.2 converts the gotchas into a maintained per-Module artifact.
    location: >-
      README.md (Gotchas worth knowing)
    severity: low
  - summary: >-
      .claude/settings.local.json allowlists a shellcheck command naming the deleted
      docker/postgres/initdb/ directory.
    evidence: |-
      Line 11 reads Bash(shellcheck scripts/smoke-test.sh docker/postgres/initdb/*.sh). Harmless
      — the entry simply never matches again — but it is a stale path of exactly the kind this
      story treated as a defect elsewhere. Deferred because the fix edits an agent-context
      configuration file.
    location: >-
      .claude/settings.local.json:11
    severity: low
  - summary: >-
      An untracked services/<name>/.env would be read as a fallback for any variable absent
      from the root .env, which AD-4 forbids and nothing detects.
    evidence: |-
      Compose reads a child env file for an included model; the root .env wins on conflict, so a
      per-module file can never override — but a variable the root does not declare at all is
      supplied silently by the module. Before this change there were no module directories, so
      the hazard is newly reachable. It requires someone to create a file the architecture
      already forbids, and the presence check belongs with epics story 2.4's contract check.
    location: >-
      services/<name>/.env (absent today)
    severity: medium
  - summary: >-
      AGENTS.md's "shared restart/logging/networks come from common/base.yaml through extends,
      never a YAML anchor" can read as applying to every service rather than to extracted
      modules only.
    evidence: |-
      The surrounding sentence does distinguish extracted modules from the services still
      inlined, but a later agent skimming the rule could strip the root file's still-live
      x-defaults anchors or add an extends to an inlined service. Deferred because the fix edits
      an agent-context file.
    location: >-
      AGENTS.md (Where things are)
    severity: low
  - summary: >-
      The self-test's FORBIDDEN-token scan over shell sources still walks scripts/ and
      .githooks/ only, so a module's own shell scripts are shellchecked but never scanned
      for `|| true` or a tool-presence branch.
    evidence: |-
      scripts/lint_selftest.py builds shell_sources from (REPO / "scripts").rglob("*.sh")
      plus the git hooks. services/postgres/seed/20-extra-databases.sh is reached by the
      lint-shell glob (widened to services/**/*.sh in this pass) but not by the token scan,
      and so is also absent from the "lint-shell covers every script at any depth"
      assertion. Pre-existing rather than caused by this story: the same file was outside
      shell_sources at docker/postgres/initdb/ before the move. Fixing it means deciding
      whether a seed script may legitimately use the tokens the scan forbids, which is a
      question about seed scripts rather than about this extraction.
    location: >-
      scripts/lint_selftest.py (shell_sources)
    severity: low
  - summary: >-
      assert_config.check() asserts only `logging` of the three keys common/base.yaml
      declares, so a module's `restart` (and `networks`) can diverge from the shared
      fragment with every check green.
    evidence: |-
      Reproduced: inserting `restart: "no"` into services/postgres/compose.yaml below an
      intact `extends:` block leaves lint-compose, lint-config, lint-yaml and lint-pins all
      green while the service renders with Docker's non-restarting policy. The accidental
      case is already caught — a module that drops `extends` loses `logging` with it — so
      what remains is an explicit override, and an override is sanctioned: common/base.yaml's
      own header tells a one-shot helper to set `restart: "no"`, and minio-init does exactly
      that, rendering `restart: no` today. A blanket equality check like the logging one would
      therefore reject a legitimate service. Closing this needs a way to declare the
      exception, which is the same design question as the no-exception-mechanism risk already
      recorded for logging; epics story 2.4 ("Every Module carries its own contract,
      enforced") owns it.
    location: >-
      scripts/assert_config.py (check)
    severity: medium
baseline_revision: '82ebb22a8164ca10bd2acac3990d218adc6ddeeb'
---

<intent-contract>

## Intent

**Problem:** All thirteen services are inlined in a 424-line root `compose.yaml`, so no Service can be taken without taking the file that defines every other one. The decomposition pattern that twelve later extractions will copy does not exist yet, and getting it wrong here means getting it wrong thirteen times.

**Approach:** Add `common/base.yaml` — a shared fragment consumed only through `extends` — and move Postgres, chosen because it has no dependencies, into `services/postgres/` with its config and seed scripts beside it, pulled back into the model by a root `include:`. The rendered Compose model must come out unchanged; the pin and Renovate validators must be taught to read module compose files so no image drops out of their coverage.

## Boundaries & Constraints

**Always:**
- `postgres-data` keeps its identifier. A renamed volume orphans real data with no error (AD-5).
- The module's top-level `volumes:` and `networks:` stanzas carry the identifier and nothing else — no `driver`, no `driver_opts`, no `attachable`. Driver keys stay in Core `compose.yaml` (AD-5).
- The module service inherits shared configuration through `extends: {file: ../../common/base.yaml, service: defaults}`; `common/base.yaml` declares only `restart`, `logging` and `networks`, and never appears in any `include:` list (AD-1, AD-2).
- Every bind-mount path in the module file is relative to the module's own directory (AD-6).
- Every check that reads compose text — pins, Renovate detection, shell lint, YAML lint — keeps covering the files that moved. A glob that stops matching is a silent skip.

**Never:**
- Never use `project_directory`, and never leave a `<<: *alias` that references an anchor defined in another file (AD-1, AD-6).
- Never extract a second Service, add `profiles:`, `x-bundles`, `x-requires`, `x-endpoints`, `smoke.sh`, `gotchas.md` or `scripts/select.sh` — those are stories 2.2 through 2.6.
- Never rename a volume, container, service, environment variable or port to tidy it.
- Never harden the stack incidentally: credentials, TLS, dev-mode flags and anonymous admin stay exactly as they are.
- Never delete or re-drive a volume; no `down -v`, no `pixi run destroy`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Pins across files | Root `compose.yaml` plus `services/postgres/compose.yaml`, every `${*_VERSION:-tag}` agreeing with `.env.example` | `assert_pins` reports OK with the total reference count across all files | No error expected |
| Pin declared, referenced only in a module | `POSTGRES_VERSION` declared in `.env.example`, referenced only in the module file | Accepted — the "declared but never referenced" check runs over the union of all compose files | No error expected |
| Pin drift in a module file | Module fallback tag differs from `.env.example` | Exit 1, diagnostic names the module file, not a bare `compose.yaml` | Non-zero exit, message identifies the file by repo-relative path |
| No pins anywhere | Compose files reference no `*_VERSION` | Exit 1 — a pass over an empty set verified nothing | Non-zero exit |
| Renovate blind to a module | `managerFilePatterns` selects only the root compose file | Exit 1: the pin is detected in `.env.example` but in no compose file | Non-zero exit naming the variable |

</intent-contract>

## Code Map

- `compose.yaml:16-29` -- `x-restart` / `x-logging` / `x-defaults` anchors, the source of `common/base.yaml`; the twelve services still inlined keep using them (same-document anchors are legal).
- `compose.yaml:31-70` -- the inline `postgres:` block to move, including the version-specific `postgres-data:/var/lib/postgresql/data` mount and its warning comment.
- `compose.yaml:57-59` -- the two bind mounts whose sources move: `./docker/postgres/postgresql.conf` and `./docker/postgres/initdb`.
- `compose.yaml:414-427` -- Core `volumes:` list (keeps `postgres-data:`) and `networks: devinfra` with `name` + `driver`, which win over a module's identifier-only stanza because Core sets them explicitly.
- `docker/postgres/postgresql.conf`, `docker/postgres/initdb/{10-extensions.sql,20-extra-databases.sh}` -- move to `services/postgres/conf/` and `services/postgres/seed/`.
- `scripts/assert_pins.py:102` `occurrences()`, `:133` `check()`, `:182-189` the reverse "declared but never referenced" scan, `:193` `main()` (argv guard `:207`, defaults `:210-211`), diagnostics using `compose.name` at `:159,165,171,178,187,225,235`.
- `scripts/assert_renovate.py:311` `detect()`, `:384` selection, `:433` `Detection.source = path.name`, `:549` `check()`, `:566` `detect(config, [dotenv, compose], ...)`, `:569-573` source comparisons, `:590-614` diagnostics, `:639-651` argv guard/defaults, `:676-679` OK line.
- `scripts/assert_config.py` -- reads only the *rendered* model via `docker compose config`; needs no change (verified: it passes no `-f` and reads back `config --profiles`).
- `scripts/lint_selftest.py:672` lint-shell empty-glob target (`docker/postgres/initdb`), `:1527-1532` `pins()` 2-arg driver, `:1642-1651` `image_keys` counted from root `compose.yaml` only, `:1662-1688` `renovate()` 4-arg driver, `:1925-1937` non-UTF-8 loop argv, `:2024-2038` `declared_versions + image_keys` total.
- `pixi.toml:179` lint-shell glob (`docker/postgres/initdb/*.sh`), `:183` lint-yaml globs (no `common/` or `services/` term). Task bodies run under `deno_task_shell`, which expands `**` recursively and passes an unmatched glob through literally.
- `renovate.json:39` `managerFilePatterns` — `["/^\\.env\\.example$/", "/^compose\\.yaml$/"]`.
- `.env.example:57-59,68` comments naming `compose.yaml` and `docker/postgres/initdb/20-extra-databases.sh`.
- `README.md:177-225` repository layout block; `README.md:611` the pg18 mount-path gotcha naming `compose.yaml`.
- `docs/adr/0001-modules-via-compose-include.md`, `0004-volume-names-are-frozen.md` -- the decisions this story first exercises; read-only.

## Tasks & Acceptance

**Execution:**
- `common/base.yaml` -- create with a single `defaults` service declaring exactly `restart: unless-stopped`, the `json-file` logging options from `x-logging`, and `networks: [devinfra]` -- the extends target every Module will use; nothing else may appear in it.
- `services/postgres/conf/postgresql.conf`, `services/postgres/seed/10-extensions.sql`, `services/postgres/seed/20-extra-databases.sh` -- `git mv` from `docker/postgres/`, content unchanged -- the Module owns its config and seed data; leave `docker/postgres/` gone.
- `services/postgres/compose.yaml` -- create with the `postgres` service moved verbatim from the root file, its `<<: *defaults` replaced by `extends: {file: ../../common/base.yaml, service: defaults}`, its two bind sources rewritten to `./conf/postgresql.conf` and `./seed`, plus identifier-only `volumes: {postgres-data:}` and `networks: {devinfra:}` stanzas -- the first Module, and the pattern twelve more will copy.
- `compose.yaml` -- add a top-level `include:` listing `./services/postgres/compose.yaml`, delete the inline `postgres:` block, keep the anchors and the Core `volumes:`/`networks:` declarations unchanged, and update the header comment to say where Postgres now lives -- Core keeps volume identity and driver.
- `scripts/assert_pins.py` -- accept one or more compose files (`main` argv becomes `[<compose>... <dotenv>]`, dotenv last), take the union of references before the "declared but never referenced" scan, and identify files in diagnostics by the path given rather than the basename; default to root plus `services/*/compose.yaml` -- every module file is named `compose.yaml`, so a basename no longer identifies one.
- `scripts/assert_renovate.py` -- accept extra compose files as `argv[4:]`, pass them all to `detect()`, and label `Detection.source` with the repo-relative name so the dotenv/compose split still holds -- keeps the 4-argument fixture contract working unchanged.
- `renovate.json` -- add `"/^services\\/[^/]+\\/compose\\.yaml$/"` to `managerFilePatterns` -- without it the bot sees only the `.env.example` half of the Postgres pin and would edit one file alone.
- `pixi.toml` -- retarget the lint-shell glob from `docker/postgres/initdb/*.sh` to `services/*/seed/*.sh`, and add `common/*.y*ml services/**/*.y*ml` to lint-yaml -- the new and moved files must stay linted.
- `scripts/lint_selftest.py` -- retarget the lint-shell empty-glob case to `services/postgres/seed/`, count `image:` keys across root plus `services/*/compose.yaml` for both coverage assertions, and add two cases: `assert_pins` over two compose fixtures where a pin declared once is referenced only in the second, and `assert_renovate` given a fifth path holding a module-style pin -- proves the union behaviour rather than assuming it.
- `.env.example`, `README.md` -- update the paths and file names the moves invalidate (`docker/postgres/...`, the layout block, the pg18 mount-path gotcha) -- documentation that names a path that no longer exists is a defect.

**Acceptance Criteria:**
- Given `common/base.yaml`, when it is read, then it declares only `restart`, `logging` and `networks`, never `depends_on`, `links`, `volumes_from` or `network_mode: service:*`, and no `include:` entry anywhere names it.
- Given the repository after the move, when `services/postgres/` is listed, then it holds the Compose fragment, `conf/postgresql.conf` and `seed/`, every relative path is written against that directory, `project_directory` appears nowhere, and no `<<: *alias` references an anchor defined in another file.
- Given the `docker compose config` output captured before the change, when it is compared with the output after, for the default profiles and for `--profile admin --profile observability`, then the two are identical apart from key ordering and the two relocated bind-mount `source` paths — no other line differs.
- Given the running stack, when `postgres` is recreated from this working tree, then it reaches `healthy`, `docker volume ls --filter name=devinfra` is byte-identical to the inventory captured before, and a query against the pre-existing database returns its existing rows.
- Given the module's `volumes:` and `networks:` stanzas, when they are inspected, then each names the identifier and nothing else, and the rendered network still carries Core's `name: devinfra` and `driver: bridge`.
- Given `pixi run ci`, when it is run, then it exits 0, `lint-pins` reports one reference per `image:` key across every compose file, and `lint-renovate` detects the Postgres pin in both `.env.example` and the module file.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 24 findings — high 1, medium 5, low 14, false 4, maybe-false 0
- findings:
  - `[false]` `[reject]` blind-hunter: every service count in the new prose is one low ("the twelve still inlined") — README.md:9-23 lists thirteen Services and minio-init is a declared helper of the object-storage Module under AD-8, not a Service; epics 2.2 (four Modules) plus 2.3 (eight) plus this story's one is thirteen, so twelve Services remain inlined and "any of thirteen files" is the eventual module count.
  - `[medium]` `[defer]` blind-hunter: nothing enforces the module contract (base never included, no project_directory, no cross-file alias, module-to-service both directions) — real; the volume/network half was patched into scripts/assert_config.py, the rest is deferred because epics story 2.4 owns the enforced contract check.
  - `[low]` `[defer]` blind-hunter: lint-json was not extended to services/**/*.json — real but not yet fixable: postgres contributes no JSON and an unmatched glob makes the task fail; due with the first module that carries JSON.
  - `[medium]` `[patch]` blind-hunter: the two new lint-yaml globs have no empty-glob self-test — confirmed load-bearing-but-unpinned; added an `empties` entry hiding common/*.yaml plus services/*/compose.yaml.
  - `[low]` `[defer]` blind-hunter: README "Gotchas worth knowing" documents none of the four include/extends traps AGENTS.md now routes module work through — real; epic 3 story 3.2 owns the gotchas register.
  - `[low]` `[defer]` blind-hunter: no documented recipe for adding the next module — real; grouped with the gotchas gap, same root cause (module-facing documentation not written yet).
  - `[low]` `[patch]` blind-hunter: assert_renovate.py's read_text()/load_config() still raise with path.name, printing a bare compose.yaml — fixed to raise with the full path, matching assert_pins.py.
  - `[low]` `[reject]` blind-hunter: assert_pins/assert_renovate argv contracts are order-sensitive and unvalidated — the misuse still exits non-zero (loudly, if with a misleading message), is only reachable by hand-invoking the script with reversed arguments, and the proposed fix adds shape guards rather than correcting anything.
  - `[low]` `[patch]` blind-hunter: the tombstone banner left where the postgres block was duplicates the header comment and the include entry, and 2.2 would copy it — deleted; the include list is the record.
  - `[low]` `[patch]` blind-hunter: common/base.yaml never says it is not renderable on its own, so `compose -f common/base.yaml config` fails in a way that reads like breakage — added one line saying so. The companion claim (the restart/logging/networks-only invariant is unenforced) is the module-contract gap deferred above.
  - `[low]` `[defer]` blind-hunter: .claude/settings.local.json:11 allowlists a command naming the deleted docker/postgres/initdb/ — real; deferred because the fix edits an agent-context configuration file.
  - `[medium]` `[defer]` edge-case-hunter: an untracked services/<name>/.env silently supplies variables the root .env omits — real and newly reachable, but requires creating a file AD-4 already forbids, and the presence check belongs with story 2.4's contract check.
  - `[medium]` `[patch]` edge-case-hunter: common/base.yaml's defaults and compose.yaml's x-defaults are two sources of one truth that can drift while both exist — added a self-test asserting the two resolve to the same mapping.
  - `[medium]` `[patch]` edge-case-hunter: the new lint-yaml globs are not pinned by any defect or empties case — same defect as the blind-hunter finding above; fixed by the same `empties` entry.
  - `[low]` `[reject]` edge-case-hunter: candidate_names' basename fallback for a file outside base restores the laxness just removed — the fallback is unreachable for the tracked file set (every candidate lies under the config's directory) and the fix adds a raise rather than correcting a wrong result.
  - `[low]` `[reject]` edge-case-hunter: a compose file selected by no managerFilePatterns entry passes silently — only when another file references the same pin, which no module does today; and when it does happen the bot's one-file pull request is rejected loudly by lint-pins. The fix adds a branch.
  - `[low]` `[defer]` edge-case-hunter: AGENTS.md's extends rule may read as applying to inlined services too — real ambiguity; deferred because the fix edits an agent-context file.
  - `[low]` `[defer]` edge-case-hunter: .claude/settings.local.json stale allowlist entry — same finding as the blind-hunter one above; same route.
  - `[low]` `[patch]` edge-case-hunter: renovate.json prBodyNotes still promises lint-pins checks "the compose.yaml fallback" — reworded to name the root file and every module file, matching the customManagers description edited in the same commit.
  - `[false]` `[reject]` edge-case-hunter: "twelve services still inlined" is wrong, thirteen remain — same refutation as the blind-hunter count finding: minio-init is a helper, not a Service.
  - `[false]` `[reject]` edge-case-hunter: the spec's "thirteen services in a 424-line compose.yaml" is wrong — thirteen Services is the repository's own vocabulary (README's table), the 424 figure is inherited verbatim from MIGRATION-PLAN.md, and the fix would edit this build's spec.
  - `[high]` `[patch]` verification-gap: a module's volumes stanza can carry name/driver/driver_opts, silently repointing postgres-data with CI green — reproduced; scripts/assert_config.py now asserts every services/*/compose.yaml declares identifiers only, failing on an empty module set, with three planted-defect self-test cases.
  - `[medium]` `[patch]` verification-gap: the two new lint-yaml glob terms are not load-bearing in the self-test — same defect as the two findings above; fixed by the same `empties` entry.
  - `[false]` `[reject]` intent-alignment: descriptive audit, no defect filed — it reports the awaiting-operator branch does not fire (no acceptance criterion requires a human action outside the repository), sprint-status.yaml is absent from the change set, and the diff implements the spec's reading; its observation that the module-contract criteria are enforced by prose is the same finding routed above.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 26 findings — high 2, medium 7, low 13, false 4, maybe-false 0
- findings:
  - `[high]` `[patch]` blind-hunter: identifier_only() checks a module stanza's body but never the identifier itself, so a module naming a volume the root never declares passes — reproduced (a body-less `postgres-dataa:` was accepted, exit 0); identifier_only now reads the root's own volumes/networks/configs/secrets keys and rejects any module identifier absent from them, with a body-less planted-defect case.
  - `[low]` `[patch]` blind-hunter: assert_config.main() discards the module-scan diagnostics when the container runtime is unavailable — confirmed at the `except RuntimeError` around combinations(); the runtime error is now appended to `problems` and the whole list is flushed to stderr before the early return.
  - `[medium]` `[defer]` blind-hunter: nothing ties services/*/compose.yaml to the root include: list, so a module directory that renders nothing still validates green — carried: same claim as the module-contract row logged on the first pass and recorded in `deferred` ("every module directory maps to a rendered service and back"); epics story 2.4 owns it.
  - `[low]` `[patch]` blind-hunter: defect_module.mkdir()/rmdir() is non-idempotent, so a hard kill leaves services/zz-selftest-defect/ behind and the next run dies on FileExistsError — confirmed (neither `finally` runs on SIGKILL, and rmdir cannot remove a directory a surviving fixture still occupies); changed to mkdir(exist_ok=True) with shutil.rmtree(ignore_errors=True).
  - `[medium]` `[patch]` blind-hunter: assert_config has no argv seam and its new empty-module-set guard is untested — the guard half is real and fixed (a moved_aside case now hides services/*/compose.yaml and asserts lint-config exits non-zero naming the empty set). The argv-seam and unreached-RuntimeError halves were not acted on: the seam adds public CLI surface, and a module file that does not parse is already rejected loudly by lint-yaml and lint-compose.
  - `[low]` `[patch]` blind-hunter: MODULE_STANZAS omits configs and secrets, which merge with the same last-include-wins semantics — real though unreachable today (nothing declares either); both added to the tuple with a planted `configs:` defect case, so the class is closed before a module introduces one.
  - `[low]` `[defer]` blind-hunter: services/*/seed/*.sh is outside the self-test's FORBIDDEN-token scan and its coverage assertion — real, and pre-existing: the same script was outside shell_sources at docker/postgres/initdb/ before the move.
  - `[low]` `[patch]` blind-hunter: pixi's lint-shell glob `services/*/seed/*.sh` silently skips any module shell script outside seed/ — real; widened to `services/**/*.sh`, which still matches the seed script today and keeps the empty-glob case load-bearing.
  - `[low]` `[patch]` blind-hunter: the identifier-only diagnostic says "Driver keys live in the root compose.yaml" while the root declares all twelve volumes identifier-only — the wording is ADR 0004's, but as a diagnostic it reads as a claim about current contents; reworded to "belong in", which states the rule.
  - `[low]` `[patch]` blind-hunter: README's Podman `max-file` deviation names only compose.yaml's x-logging, now one of two sources — real since the extraction; the note names both x-logging and common/base.yaml's defaults.
  - `[low]` `[patch]` blind-hunter: README's renovate.json row says "every compose file above" while services/postgres/compose.yaml is listed below it — confirmed by position; reworded to ".env.example, compose.yaml and every module file".
  - `[false]` `[reject]` blind-hunter: the spec's frontmatter contradicts the sprint status changed in the same commit — the `in-review` status is this review pass's own in-flight state, written by step-04 and set back to `done` on finalize; sprint-status.yaml is the orchestrator's bookkeeping and the fix would edit this build's spec.
  - `[low]` `[reject]` blind-hunter: the two transitional x-defaults assertions have no removal marker, and `## Spec Change Log` is an empty heading — when the last service is extracted the assertion fails loudly, naming itself, directly under a comment that states the condition ("until every service is extracted"), so the failure is self-explaining rather than silent; the change-log half would edit this build's spec.
  - `[medium]` `[defer]` edge-case-hunter: an included module file that does not match the services/*/compose.yaml glob drops out of the identifier-only, pin and Renovate scans — carried: the same include-versus-glob decoupling as the module-contract row already in `deferred`, read from the other direction.
  - `[medium]` `[defer]` edge-case-hunter: a module directory that exists but is absent from include: passes every check while contributing nothing to the model — carried: same logged row, same route.
  - `[low]` `[patch]` edge-case-hunter: MODULE_STANZAS omits configs and secrets — same defect as the blind-hunter finding above; fixed by the same tuple change and planted case.
  - `[medium]` `[patch]` edge-case-hunter: the lint-yaml empties entry hides common/*.yaml and services/*/compose.yaml together, so deleting either glob term alone still passes — reproduced by inspection (common/ holds only base.yaml and services/ only postgres/compose.yaml, so one entry empties both terms); split into two entries, one term each.
  - `[low]` `[patch]` edge-case-hunter: the self-test's planted module directory survives an interrupted run — same defect as the blind-hunter mkdir finding; same fix.
  - `[low]` `[patch]` edge-case-hunter: the lint-pins module-path assertion compares against a hardcoded POSIX spelling, which fails on win-64 — confirmed: the fixture lives outside the repository, so assert_pins.label() returns the path as given, with backslashes on Windows; the assertion now compares against str(module_fixture).
  - `[false]` `[reject]` edge-case-hunter: a managerFilePatterns entry that selects nothing is never reported — for every pattern in this configuration the lapse is caught elsewhere: kill the module pattern and POSTGRES_VERSION is detected in .env.example and in no compose file, which exits 1 naming the variable; kill the root pattern and the same happens for twelve pins. The fix adds a per-entry branch.
  - `[false]` `[reject]` edge-case-hunter: common/base.yaml is loaded in the self-test without a guard, so a missing file aborts with a traceback — a traceback naming the exact unreadable path is a correct loud failure for a file the repository must have, and it is what every other unguarded read_text() in that file already does.
  - `[high]` `[patch]` verification-gap: nothing verifies the module actually consumes common/base.yaml — reproduced: with the extends block replaced by `networks: [devinfra]`, lint-compose, lint-config and the self-test all passed while postgres rendered with no logging and Docker's default `restart: no`. assert_config.check() now compares every rendered service's logging against the fragment's, with a self-test case; the planted defect is rejected in all four profile combinations.
  - `[medium]` `[patch]` verification-gap: neither new lint-yaml glob term is individually load-bearing — same defect as the edge-case-hunter finding above; fixed by the same split.
  - `[medium]` `[patch]` verification-gap: "OK 0 module file(s) declare identifiers only" satisfies the clean-path assertion, so the empty-module-set guard is unpinned — same defect as the blind-hunter argv-seam finding's guard half; fixed by the same moved_aside case.
  - `[low]` `[patch]` verification-gap (other findings): the planted module directory is created and removed without recovery — same defect as the two mkdir findings above; same fix.
  - `[false]` `[reject]` intent-alignment: descriptive audit, no defect filed — it independently re-rendered both profile combinations against the pre-change tree and confirmed the only differences are the two relocated bind sources; its divergences D2/D4 are the module-contract row already deferred, D3 is the identifier-membership finding patched above, D6 is the live-stack criterion, and D7 is the lint-json row already deferred.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 18 findings — high 0, medium 4, low 12, false 2, maybe-false 0
- findings:
  - `[medium]` `[defer]` blind-hunter: assert_config.check() asserts only one of the three keys the shared fragment carries, so `restart` and `networks` can diverge — reproduced (`restart: "no"` below an intact `extends:` passes every check), but a blanket equality check is impossible today: base.yaml's header sanctions the override for one-shot helpers and minio-init renders `restart: no`. Deferred with the exception-mechanism question story 2.4 owns.
  - `[low]` `[patch]` blind-hunter: identifier_only()'s non-mapping diagnostic says "rendered as" for a function that reads the module's own text — confirmed against read_model's own "parsed as" wording twenty lines up; reworded to "parsed as".
  - `[low]` `[patch]` blind-hunter: assert_renovate's depName-disagreement diagnostic drops the file identity, saying "a compose file names X" — confirmed: from_compose was keyed variable → dep_names, discarding item.source, the exact laxness label() and source_name() removed elsewhere in this change. Now keyed by (source, dep_name) and the message names the file, pinned by a new self-test case over a module fixture.
  - `[low]` `[reject]` blind-hunter: root_identifiers() re-parses the root compose file once per stanza — true (four parses of a ~400-line file), but the cost is milliseconds no developer will ever meet, and the fix restructures a function rather than correcting a wrong result.
  - `[low]` `[patch]` blind-hunter: lint_selftest's renovate_module() runs assert_renovate twice and discards the first result — confirmed; the fixture writing is now a `write_renovate_fixtures()` helper both callers share, so the discarded subprocess is gone and a reader cannot mistake it for an assertion.
  - `[low]` `[patch]` blind-hunter: the module renovate case pins the magic counts 3 and 7 while the lint-pins case fifty lines up derives its own — confirmed; a comment now states how each number is composed and why deriving it would weaken the case.
  - `[false]` `[reject]` blind-hunter: a killed self-test leaves services/zz-selftest-defect/ and an ordinary `pixi run lint` then fails "on an unpinned image with no hint of where it came from" — planted the leftover directory and ran the real tasks: lint-pins, lint-renovate and lint-yaml all stay green (alpine:3.22 is pinned), and the one task that fails, lint-config, names `services/zz-selftest-defect/compose.yaml` in its diagnostic. The recovery half is the carried row below.
  - `[low]` `[patch]` blind-hunter: lint-yaml globs `common/*.y*ml` one level deep while `services/**/*.y*ml` recurses, so a YAML under common/<subdir>/ is silently unlinted — confirmed, and the same asymmetry lint-shell was widened to fix last pass; changed to `common/**/*.y*ml` (verified `**` matches zero directories in deno_task_shell: a planted lint error in common/base.yaml is still caught) with the empties entry moved to rglob.
  - `[low]` `[patch]` blind-hunter: assert_config.main() writes its per-combination OK lines during a failing run — reproduced: a planted module defect exits 1 with four `assert-config: OK ...` lines on stdout and the diagnostic alone on stderr. The OK lines are now held and flushed only when the run is clean, pinned by a new assertion on each planted-defect case.
  - `[false]` `[reject]` blind-hunter: assert_pins and assert_renovate accept an empty module set silently while assert_config guards it — demonstrated otherwise: hiding services/postgres/compose.yaml makes lint-pins exit 1 ("'POSTGRES_VERSION' is declared but no compose file references it") and lint-renovate exit 1 ("detected in .env.example but in no compose file"). Neither is silent.
  - `[low]` `[patch]` blind-hunter: the root compose header names the module and counts the services still inlined, both of which story 2.2 invalidates, duplicating the include list below it — real, and the same reason the first pass deleted the tombstone banner; trimmed to the rule, with the include list named as the record.
  - `[low]` `[defer]` blind-hunter: AGENTS.md routes `services/` work to a README section that documents none of the four traps this story introduces — carried: same claim as the README-gotchas row logged on the first pass and recorded in `deferred`; epic 3 story 3.2 owns it.
  - `[low]` `[patch]` edge-case-hunter: a killed self-test leaves the planted module directory behind — carried: same claim as the row patched on the previous pass (mkdir(exist_ok=True) with shutil.rmtree(ignore_errors=True)), and the code still reads as that row describes. No further action.
  - `[medium]` `[defer]` edge-case-hunter: `networks` and `restart` are unchecked while `logging` is compared — same root cause as the blind-hunter row above; same route and same evidence.
  - `[low]` `[patch]` edge-case-hunter: a compose file that is not UTF-8 aborts read_model with a traceback instead of a named diagnostic — reproduced (UnicodeDecodeError from pathlib, the offending path absent from the message), and it breaks a contract this repository states and self-tests for lint-json, commit-msg and lint-renovate. Added the `except UnicodeDecodeError` clause matching assert_renovate.py verbatim, with a planted non-UTF-8 module case.
  - `[medium]` `[defer]` verification-gap: `restart` is asserted nowhere, in either file or the rendered model — the gap is real as filed and its demonstration reproduces, but the filed fix ("compare service.get('restart') the same way logging is compared") would reject minio-init, which renders `restart: no` by design. Routed to defer rather than patch for that reason; grouped with the two rows above.
  - `[low]` `[patch]` verification-gap (other findings): the module docstring and check()'s inline comment both claim the assertion catches a module shipping with `restart: no`, which it does not — confirmed; both now say logging is the only one of the three keys compared, and why restart cannot be.
  - `[medium]` `[patch]` intent-alignment: descriptive audit, one substantive item acted on. Its D3 is a real verification gap: assert_pins.label()'s repo-relative branch — the only branch `pixi run lint-pins` ever takes — is asserted by no test, because the pins fixtures live outside the repository and last pass's win-64 fix moved the assertion onto the fallback branch. Confirmed by reverting label() to `path.name`: every existing case still passed. Added a case that plants a drifting pin in a tracked services/ module and asserts the repo-relative spelling. Of the rest, D1/D2 are the manual before/after rendering and the stub-rendered logging case, D4/D6 the module-contract row already deferred, D5 the residual risks recorded below, D7 the lint-json and token-scan rows already in the ledger, and D8 the live-stack criteria.

## Design Notes

The three mechanism questions were settled empirically against Compose v5.3.0 before this spec was written, with a throwaway project outside the repository:

- `extends` against a `defaults` service that has no `image:` or `build:` resolves fine — the base file is never loaded as a project of its own, only read as an extends source.
- A relative bind path inside an included file resolves against **that file's** directory, not the project directory. `./conf/postgresql.conf` in `services/postgres/compose.yaml` renders as `<repo>/services/postgres/conf/postgresql.conf`.
- An identifier-only `networks: {devinfra:}` in the module merges with Core's declaration and Core's explicit `name`/`driver` win.

The module file's shape:

```yaml
services:
  postgres:
    extends:
      file: ../../common/base.yaml
      service: defaults
    image: pgvector/pgvector:${POSTGRES_VERSION:-0.8.6-pg17}
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./conf/postgresql.conf:/etc/postgresql/postgresql.conf:ro
      - ./seed:/docker-entrypoint-initdb.d:ro
volumes:
  postgres-data:
networks:
  devinfra:
```

The bind-mount `source` paths are the one legitimate difference in the rendered model: the files moved, so their absolute paths moved with them. Everything else — image, container name, ports, environment, command, healthcheck, `shm_size`, volume identity, restart policy, logging options, network attachment — must match line for line.

## Verification

**Commands:**
- `docker compose config` and `docker compose --profile admin --profile observability config`, captured before and after -- expected: diff empty once the two relocated bind `source` paths are normalised; nothing else differs.
- `pixi run lint-compose` -- expected: OK, four profile combinations validated.
- `pixi run ci` -- expected: exit 0 across lint-compose, lint-config, lint-pins, lint-renovate, lint-shell, lint-yaml, lint-json, lint-python and the self-test.
- `docker compose up -d postgres && ./scripts/wait-healthy.sh` -- expected: `devinfra-postgres` healthy against the pre-existing `devinfra_postgres-data` volume.
- `docker exec devinfra-postgres psql -U devinfra -d devinfra -c '\dt' -c 'select extname from pg_extension'` -- expected: the pre-existing objects and extensions are still there.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.

**Manual checks (if no CLI):**
- The live stack was started from the main checkout, so recreating `postgres` from this worktree repoints its bind mounts at the worktree. After verifying, restore the container from the main checkout's own compose file so the shared stack is left exactly as it was found.

## Auto Run Result

Status: done

**Implemented change.** `common/base.yaml` holds the one fragment every Module extends —
`restart`, `logging` and `networks`, never included. Postgres moved to `services/postgres/`,
its config and seed scripts beside it, pulled back into the model by a root `include:`;
`<<: *defaults` became `extends: {file: ../../common/base.yaml, service: defaults}` and the
two bind sources became module-relative. The pin and Renovate validators read a *set* of
compose files, take the union of references before the reverse "declared but never
referenced" scan, and name files by the path given rather than by a basename that no longer
identifies one. `renovate.json`, the lint-shell and lint-yaml globs and the self-test were
retargeted so nothing that moved dropped out of coverage.

This second follow-up pass changed no compose file except one header comment, and the
rendered model is byte-identical across both profile combinations. It closed one verification
gap and corrected a set of diagnostics and comments that named the wrong thing.

**Files changed this pass:**
- `scripts/assert_config.py` — `read_model` raises a named diagnostic on a non-UTF-8 file
  instead of a traceback; the non-mapping diagnostic says "parsed", not "rendered"; the
  per-combination `OK` lines are held and flushed only when the run is clean; the module
  docstring and `check()`'s comment no longer claim the assertion covers `restart`.
- `scripts/assert_renovate.py` — the depName-disagreement diagnostic names the compose file
  that disagrees, instead of "a compose file".
- `scripts/lint_selftest.py` — a tracked-module case pins `assert_pins.label()`'s
  repo-relative branch; a non-UTF-8 module case; a module depName-disagreement case; an
  assertion that a failing lint-config signs off on nothing; the renovate fixture writing
  split out so the module case stops running the tool twice; a comment stating how its two
  counts compose; the lint-yaml `common` empties entry moved to rglob.
- `pixi.toml` — lint-yaml's `common/*.y*ml` widened to `common/**/*.y*ml`.
- `compose.yaml` — the header sentence naming the one extracted module and counting the
  services still inlined trimmed to the rule, with the `include:` list named as the record.

**Review findings breakdown.** 18 findings — high 0, medium 4, low 12, false 2.
- Patched: 11 findings in 10 entries — medium 1, low 10 (at entry verdict: medium 1, low 9,
  plus one carried row that needed no new action).
- Deferred: 4 findings in 2 entries — 3 new (the `restart`/`networks` half of the shared
  fragment is unasserted), 1 carried (AGENTS.md routing to the README gotchas section).
- Rejected, with reasons:
  - blind-hunter, `root_identifiers()` re-parses the root file per stanza — true, but four
    parses of a 400-line file is milliseconds no developer meets, and the fix restructures a
    function rather than correcting a wrong result.
  - blind-hunter, a leftover self-test module makes `pixi run lint` fail obscurely — planted
    it: lint-pins, lint-renovate and lint-yaml stay green, and lint-config names the
    directory in its diagnostic.
  - blind-hunter, assert_pins and assert_renovate accept an empty module set silently —
    hiding the module file makes both exit 1 naming `POSTGRES_VERSION`.

**Follow-up review recommended: false.** This pass patched no `high` entry; the single
`medium` patch was a missing test for an existing correct behaviour, not new behaviour. The
work has converged.

**Verification performed.**
- `pixi run ci` — exit 0. lint-compose, lint-config, lint-pins, lint-renovate, lint-shell,
  lint-yaml, lint-json, lint-python, and the self-test at 734 passing assertions (723 → 727 →
  734 across the three passes).
- Load-bearing proof of all four new checks: reverted `label()` to `path.name`, deleted the
  `UnicodeDecodeError` clause, restored the eager `OK` writes and dropped the source from the
  depName diagnostic, then ran the self-test — each new assertion failed by name, and the
  pre-existing "identifies the module by path" case failed alongside the new repo-relative
  one, so both branches of `label()` are now covered. Restored; `git diff` over
  `scripts/assert_pins.py` is empty.
- `docker compose --profile admin --profile observability config` rendered from this tree and
  from the tree without this pass's edits: identical, byte for byte. The only compose.yaml
  change is a comment.
- Not re-run this pass: the live-stack criteria (postgres healthy against the pre-existing
  volume, the `docker volume ls` inventory, a query returning existing rows). The rendered
  model is byte-identical to the one those checks passed against on the first pass.

**Residual risks.**
- The rendered-model logging rule is absolute and now demonstrably asymmetric: `logging` is
  compared, `restart` and `networks` are not, precisely because `restart` has a sanctioned
  override and `logging` does not. The first service that needs its own logging driver will
  have to introduce the exception mechanism the `restart` half is already waiting on.
- The identifier-membership rule assumes the root file stays the registry of every named
  resource. That is AD-5's intent, but nothing states it as a rule the root file itself must
  keep — a future story that moves a volume declaration *into* a module would trip the check
  rather than be caught by a check that explains why.
- The include-versus-glob decoupling stays open (deferred, epics story 2.4): the scans walk
  `services/*/compose.yaml` on disk and none reads the root `include:` list, so a module
  directory that renders nothing still validates green.

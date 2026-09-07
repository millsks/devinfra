---
title: 'The admin and observability Modules'
type: 'refactor'
created: '2026-09-07'
baseline_revision: '43ea35c654d83e8ced6e053e87119a6a819af803'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred:
  - summary: >-
      The live-stack half of this story's acceptance criteria was never exercised: the eight
      Services were never started from this working tree and `pixi run smoke` never ran.
    evidence: |-
      All thirteen devinfra-* containers are running from the main checkout, and every module
      pins a fixed `container_name`, so a second Compose project cannot start them at all
      without mutating the shipped files. Recreating them from this worktree would repoint
      their binds at a directory deleted when the run ends. What is proven instead: the
      rendered model is identical to the pre-change baseline apart from the seven relocated
      bind sources and the three deleted `x-*` extension blocks, and a new lint-config
      assertion proves all eleven rendered bind sources resolve on disk. Settling it needs one
      operator run of `docker compose --profile admin --profile observability up -d`,
      `./scripts/wait-healthy.sh` and `pixi run smoke` from the checkout that owns the live
      stack. Same shape as DW-23, which records the identical gap for story 2-2.
    location: >-
      spec Verification section (live-stack commands)
    severity: medium
  - summary: >-
      Nothing committed pins the assembled model's `depends_on` edges or per-service `command`
      flags, so a detail lost while transcribing eight service bodies ships green.
    evidence: |-
      `docker compose config -q` rejects an edge naming an undefined service but is blind to an
      edge simply deleted; deleting `- loki` from services/otel-collector/compose.yaml renders
      and validates clean, and the collector retries its exporter so smoke's Loki query still
      succeeds. Same for `--web.enable-remote-write-receiver` in services/prometheus: dropping
      it breaks Tempo's metrics_generator remote-write, which no smoke assertion observes. The
      before/after rendered diff proved it here but ran from a scratch directory and leaves no
      committed baseline. ADR 0002 makes `config -q` the sanctioned dependency gate, so
      committing a rendered baseline revisits a decided ADR; epics story 2.4 owns the per-Module
      contract check. Duplicates DW-25 and DW-28 in kind, now with eight more bodies behind it.
    location: >-
      scripts/lint-compose.sh; services/otel-collector/compose.yaml (depends_on)
    severity: medium
  - summary: >-
      scripts/lint-compose.sh treats a successfully-read empty profile list as "no profiles",
      validating one combination and reporting OK.
    evidence: |-
      scripts/lint-compose.sh:33-47 guards the case where `compose config --profiles` *fails*,
      and its comment says an unreadable enumeration is never treated as no profiles. A
      successful but empty read is not guarded: `count` is 0, `combinations` is 1, and the loop
      validates only the profile-less selection before printing OK. Pre-existing — the same
      vacuity existed while profiles were declared in the root file — but every profile now
      lives in a module file, so the model has more independent places to lose one. The literal
      member-set pins added to scripts/lint_selftest.py in this pass catch the realistic case
      (one service losing its key); this entry is the all-profiles-gone case.
    location: >-
      scripts/lint-compose.sh:46-47
    severity: low
  - summary: >-
      AGENTS.md still stamps "Verified 2026-09-07 against e8971ad" although this change
      hand-edited three claims inside its managed block.
    evidence: |-
      AGENTS.md:2 is the bmad-project-context managed-block header, which records the revision
      the block was verified against. The block's layout and "still inlined" claims were
      corrected here because leaving them false was worse than editing a managed region, but
      the stamp now names a revision that predates the edit. Routed to defer because the fix
      edits an agent-context file; a bmad-project-context refresh regenerates both the block
      and its stamp.
    location: >-
      AGENTS.md:2
    severity: low
---

<intent-contract>

## Intent

**Problem:** Eight Services — `pgadmin`, `redisinsight`, `flower` (profile `admin`) and `otel-collector`, `prometheus`, `loki`, `tempo`, `grafana` (profile `observability`) — are still inlined in the root `compose.yaml`, reading the `x-defaults` YAML anchor and mounting config from `docker/<service>/`. While they remain, the include registry is not the record of what the stack runs, and the shared fragment has a second, divergent source in the root file.

**Approach:** Extract all eight into `services/<name>/` Modules on the pattern stories 2-1 and 2-2 established, moving each one's config beside it under `conf/` (and `services/grafana/dashboards/` for the drop-zone), so `docker/` empties out and is deleted. The root `compose.yaml` is left with `name:`, `include:` (thirteen entries), `volumes:` and `networks:` — no `services:` key and no `x-restart`/`x-logging`/`x-defaults` anchors, which nothing reads once the last service leaves. Two lint globs (`lint-yaml`, `lint-json`) carry a `docker/**` term that would become an unmatched glob and hard-fail; both terms go in the same change.

## Boundaries & Constraints

**Always:**
- `pgadmin-data`, `redisinsight-data`, `flower-data`, `prometheus-data`, `loki-data`, `tempo-data` and `grafana-data` keep their identifiers; each Module's `volumes:` stanza names its own identifier and nothing else. `otel-collector` has no volume and therefore no `volumes:` stanza (AD-5).
- **No Module declares a `networks:` stanza.** The root declares `devinfra` with `name:` and `driver:`; Compose v2 rejects the whole model — exit 15 — when an included file names a resource the root declares with keys. Compose v5 merges instead, so this passes locally and fails every CI job (ADR 0004, 2026-09-07 amendment). `lint-config` is the only local check that sees it.
- Every Module service inherits `restart`/`logging`/`networks` through `extends: {file: ../../common/base.yaml, service: defaults}`, never a YAML anchor. None of these eight is a one-shot helper, so none overrides `restart`.
- Every bind-mount source is written against the Module's own directory. `project_directory` is never used.
- Each service keeps its `profiles:` key exactly as it is, per service — a profile attached at the include level is silently ignored.
- Every `depends_on` edge survives as plain `depends_on` in the assembled model: `pgadmin → postgres`, `redisinsight → redis`, `flower → redis`, `otel-collector → [loki, tempo]`, `grafana → [prometheus, loki, tempo]`. `docker compose config -q` over every profile subset is the gate (ADR 0002).
- The rendered `docker compose config` output for both the default and the `--profile admin --profile observability` combinations must be identical to the captured baseline apart from key ordering and the relocated bind-mount `source` paths.
- Every check that reads a moved file keeps reaching it. A glob that stops matching is a silent skip in one direction and a hard failure in the other: pixi's shell passes an unmatched glob through **literally**, so `docker/**/*.json` survives as an argument and `lint_json.py` reports `no such file`.

**Never:**
- Never rename a volume, container, service, environment variable, port or profile to tidy it. Never re-drive or delete a volume; no `down -v`, no `pixi run destroy`.
- Never add `x-requires`, `x-endpoints`, `smoke.sh`, `gotchas.md`, `x-bundles` or `scripts/select.sh` — those are stories 2.4 through 2.6. Do not carve `scripts/smoke-test.sh` into per-Module checks.
- Never harden the stack incidentally: `FLOWER_UNAUTHENTICATED_API`, `GF_AUTH_ANONYMOUS_ENABLED`, `PGADMIN_CONFIG_SERVER_MODE: "False"`, the default credentials and every other dev-mode flag stay exactly as they are.
- Never change a config file's contents while moving it. `git mv` only; the one exception is the stale path in a comment (`dashboards.yaml:1`).
- Never edit a decided ADR in place — an appended dated amendment is the pattern (ADR 0004, ADR 0008). No ADR needs one here.
- Never write `_bmad-output/implementation-artifacts/sprint-status.yaml` or `deferred-work.md`; both are orchestrator artifacts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Module JSON at depth | `services/pgadmin/conf/servers.json` present, `pixi run lint-json` run | Parsed and reported `OK` alongside `renovate.json` and the Keycloak realm | No error expected |
| Module YAML below the module root | `services/loki/conf/loki-config.yaml` present, `pixi run lint-yaml` run | Linted — `services/**/*.y*ml` reaches config below the Module root, not just `compose.yaml` | No error expected |
| Malformed JSON inside a Module subdirectory | Invalid `.json` planted under `services/pgadmin/conf/` | `lint-json` exits non-zero naming the planted file | Non-zero exit naming the file |
| Malformed YAML inside a Module subdirectory | Invalid `.yaml` planted under `services/loki/conf/` | `lint-yaml` exits non-zero naming the planted file | Non-zero exit naming the file |
| Every Module YAML hidden | All `services/**/*.y*ml` moved aside | `lint-yaml` exits non-zero — the term is proved load-bearing rather than masked by `common/` or `.github/workflows/` | Non-zero exit |
| Every Module JSON hidden | All `services/**/*.json` moved aside | `lint-json` exits non-zero — `renovate.json` alone must not carry the task | Non-zero exit |
| A service reappears in the root file | Root `compose.yaml` regains a `services:` key or an `x-defaults` anchor | The self-test fails naming the key — the registry is the only place Services are listed | Non-zero exit |
| A Module absent from the registry | `services/<name>/compose.yaml` exists but no matching `include:` entry | The self-test fails naming the Module — it would be linted, contribute nothing, and validate green | Non-zero exit |
| Module redeclares the keyed network | A Module file carrying `networks: {devinfra:}` | `lint-config` exits 1 naming the file and stanza, on every Compose version | Non-zero exit |
| Collector fan-out lost | `otel-collector`'s `depends_on` entry for `loki` or `tempo` deleted from the Module file | Renders and validates clean — nothing catches it statically; the rendered-model diff and the OTLP round-trip are the only proof | Recorded, not guarded (see Design Notes) |

</intent-contract>

## Code Map

- `compose.yaml:1-20` -- header comment; `:17-19` claims services are "still inlined below" and must become the record that extraction is complete. `:6-11` the profile listing stays true.
- `compose.yaml:27-32` -- the `include:` list, five entries; grows to thirteen in alphabetical order.
- `compose.yaml:34-54` -- the `x-restart` / `x-logging` / `x-defaults` anchors and the comment explaining them. Delete: nothing reads them once `services:` is gone. ADR 0001 puts the shared fragment in `common/base.yaml`; story 2-2's spec kept them only because "eight Services still read them".
- `compose.yaml:56-223` -- the eight inline service blocks to move. `:60-81` pgadmin (`./docker/pgadmin/servers.json`, `user: "5050:5050"`), `:83-102` redisinsight, `:104-123` flower (leading comment about running without a worker; `command:` list form), `:128-141` otel-collector (`depends_on` **list** form on loki and tempo; two published ports with trailing comments), `:143-161` prometheus (six-flag `command:` list, including the `--web.enable-remote-write-receiver` comment), `:163-178` loki (the no-healthcheck comment is load-bearing prose), `:180-194` tempo (same), `:196-223` grafana (three `depends_on`, two bind mounts, the `POSTGRES_*` vars the datasources file reads).
- `compose.yaml:225-245` -- root `volumes:` (all twelve identifiers bare) and `networks: devinfra` with `name:` + `driver:` (keyed). Unchanged.
- `services/postgres/compose.yaml` and `services/minio/compose.yaml` -- the templates. Postgres shows `conf/` + `seed/`; minio shows a two-service Module. Their header comments are the block DW-22 records as duplicated.
- `common/base.yaml:26-34` -- the `defaults` service; read-only.
- `docker/pgadmin/servers.json` -> `services/pgadmin/conf/servers.json`; `docker/otel/otel-collector-config.yaml` -> `services/otel-collector/conf/`; `docker/prometheus/prometheus.yml` -> `services/prometheus/conf/`; `docker/loki/loki-config.yaml` -> `services/loki/conf/`; `docker/tempo/tempo.yaml` -> `services/tempo/conf/`; `docker/grafana/provisioning/` -> `services/grafana/conf/provisioning/`; `docker/grafana/dashboards/.gitkeep` -> `services/grafana/dashboards/.gitkeep`. That is everything under `docker/` — the directory is deleted.
- `docker/grafana/provisioning/dashboards/dashboards.yaml:1` -- comment naming `docker/grafana/dashboards/`; the only in-file content edit in the move. `options.path: /var/lib/grafana/dashboards` is a container path and does not change.
- `pixi.toml:183` -- `lint-yaml` cmd, ends `... services/**/*.y*ml .github/workflows/*.y*ml docker/**/*.y*ml`; drop the last term.
- `pixi.toml:187` -- `lint-json` cmd, `python scripts/lint_json.py renovate.json docker/**/*.json services/**/*.json`; drop the middle term. `:179` lint-shell already carries `services/**/*.sh`; no change.
- `scripts/lint_selftest.py:634-654` -- compares `common/base.yaml`'s `defaults` against the root `x-defaults` anchor. The anchor is being deleted, so `expect("compose.yaml still declares the x-defaults anchor", ...)` fails by construction; this block is replaced, not retargeted. The `shared` "declares only restart, logging and networks" assertion at `:651-655` survives.
- `scripts/lint_selftest.py:635` -- comment naming "the eight inlined admin and observability services".
- `scripts/lint_selftest.py:710-744` -- planted-defect fixtures. `:726` `docker/loki/zz_selftest_defect.yaml` and `:727` `docker/pgadmin/zz_selftest_defect.json` both target directories that will not exist; `planted()` (`:372-392`) does a bare `write_text` with no `mkdir`, so they raise `FileNotFoundError`. `:728-733` the comment explaining the docker/services twin. `:736` the `services/keycloak/` JSON fixture is unaffected.
- `scripts/lint_selftest.py:757-780` -- the `empties` glob pins. `:759` (`docker` rglob `*.yaml`/`*.yml`) and `:773` (`docker` rglob `*.json`) both go empty, and `expect(f"{task} has a glob target to empty", bool(targets))` at `:777` fails first. `:765` pins only `services/*/compose.yaml`, which after this story no longer empties the `services/**/*.y*ml` term. `:760-763` and `:766-772` are the "two halves" rationale comments.
- `scripts/lint_selftest.py:3156` -- `docker/loki/zz_selftest_hook_defect.yaml`, the pre-commit-hook fixture; same `FileNotFoundError`.
- `scripts/lint_selftest.py:1989-2004` `tracked_composes` / `image_keys`, `:2466-2474` `declared_versions`, `:2323` "Thirteen module files" -- already generic or already correct for thirteen. No change.
- `scripts/assert_config.py:233-239` `module_composes()`, `:269-285` `root_declarations()`, `:305-386` `identifier_only()`, `:414-497` `check()` -- all enumerate `services/*/compose.yaml` and the rendered model generically; none indexes a root `services:` key. No code change. `:17-18`, `:447`, `:457` are comments describing "an inlined service"; they become inaccurate.
- `scripts/assert_pins.py:253`, `scripts/assert_renovate.py:690` -- default to root plus `services/*/compose.yaml` and aggregate across the list, so a root file with zero `image:` keys is fine (`counted`/`dependencies` are totals, and `assert_renovate`'s `hits == 0` diagnostic is per-pattern over all selected files). No change.
- `scripts/lint-compose.sh:33-68` -- reads profiles from `compose config --profiles` and validates every subset. Verified: profiles declared only inside included Module files are still enumerated. No change.
- `renovate.json:46-50` -- `managerFilePatterns` already carries `/^services\/[^/]+\/compose\.yaml$/`. No change; `/^compose\.yaml$/` stays, harmlessly selecting a pin-less file.
- `.github/workflows/ci.yml` -- no `docker/` path and no service enumeration; the profile list at `:67-69` and `:105` is `admin`/`observability`, unchanged.
- `README.md:222-227` the `services/` block and its "not extracted yet" note; `:239-244` the whole `docker/` block, which is deleted and folded into the `services/` listing; `:571` "the two sources every service reads, one inlined and one extracted"; `:616` "config file under `docker/`".
- `AGENTS.md:6,18-19,21` -- inside a `bmad:context` managed block; three claims about `docker/` and "the rest are still inlined" become false.
- `docs/adr/0001-modules-via-compose-include.md:24-30` -- the decision this story completes; read-only. `docs/adr/0002`, `0004`, `0006` read-only and uncontradicted.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- read-only. DW-22 (duplicated Module headers) is made worse by eight more files; DW-21 (no JSON depth guard) and DW-26 (nothing reconciles Modules against `include:`) are partly addressed here.

## Tasks & Acceptance

**Execution:**
- `<scratch>/before-default.yaml`, `<scratch>/before-all.yaml`, `<scratch>/before-volumes.txt` -- before touching anything, capture `docker compose config`, `docker compose --profile admin --profile observability config` and `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort` outside the repository tree -- the rendered-model diff is the primary verification and cannot be reconstructed afterwards.
- `services/pgadmin/conf/servers.json` -- `git mv docker/pgadmin/servers.json`, content unchanged.
- `services/pgadmin/compose.yaml` -- create with the `pgadmin` service moved verbatim (profile, `depends_on: postgres`, ports, environment, `user:`), `<<: *defaults` replaced by the `extends` block, the bind source rewritten to `./conf/servers.json`, a bare `volumes: {pgadmin-data:}`, no `networks:` stanza.
- `services/redisinsight/compose.yaml` -- create with `redisinsight` moved verbatim; bare `volumes: {redisinsight-data:}`; no config files.
- `services/flower/compose.yaml` -- create with `flower` moved verbatim including its `command:` list and the "runs without any worker" comment; bare `volumes: {flower-data:}`.
- `services/otel-collector/conf/otel-collector-config.yaml` -- `git mv docker/otel/otel-collector-config.yaml`.
- `services/otel-collector/compose.yaml` -- create with `otel-collector` moved verbatim including the list-form `depends_on` on `loki` and `tempo` and both port comments; bind source `./conf/otel-collector-config.yaml`; **no `volumes:` stanza** — this Module owns no named volume.
- `services/prometheus/conf/prometheus.yml` -- `git mv docker/prometheus/prometheus.yml`.
- `services/prometheus/compose.yaml` -- create with `prometheus` moved verbatim including all six `command:` flags and the remote-write comment; bind source `./conf/prometheus.yml`; bare `volumes: {prometheus-data:}`.
- `services/loki/conf/loki-config.yaml` -- `git mv docker/loki/loki-config.yaml`.
- `services/loki/compose.yaml` -- create with `loki` moved verbatim **including the no-healthcheck comment**, which records why the readiness gate lives in `smoke-test.sh`; bind source `./conf/loki-config.yaml`; bare `volumes: {loki-data:}`.
- `services/tempo/conf/tempo.yaml` -- `git mv docker/tempo/tempo.yaml`.
- `services/tempo/compose.yaml` -- create with `tempo` moved verbatim including its no-healthcheck comment; bind source `./conf/tempo.yaml`; bare `volumes: {tempo-data:}`.
- `services/grafana/conf/provisioning/` -- `git mv docker/grafana/provisioning`; edit only `dashboards/dashboards.yaml:1` to name `services/grafana/dashboards/`.
- `services/grafana/dashboards/.gitkeep` -- `git mv docker/grafana/dashboards/.gitkeep` -- git tracks no empty directory, so the drop-zone needs the marker to survive.
- `services/grafana/compose.yaml` -- create with `grafana` moved verbatim including all three `depends_on` entries and the `POSTGRES_*` variables the datasources file reads; bind sources `./conf/provisioning` and `./dashboards`; bare `volumes: {grafana-data:}`.
- `docker/` -- delete; every file it held has moved.
- `compose.yaml` -- add the eight Module files to `include:` in alphabetical order (thirteen entries), delete the whole `services:` mapping and the three `x-*` anchor blocks with their comment, rewrite the header comment to record that extraction is complete, leave `name:`, `volumes:` and `networks:` untouched.
- `pixi.toml` -- drop `docker/**/*.y*ml` from `lint-yaml` and `docker/**/*.json` from `lint-json` -- pixi's shell passes an unmatched glob through literally, so leaving either term makes both tasks fail with `no such file: docker/**/*.json` once `docker/` is gone.
- `scripts/lint_selftest.py` -- replace the `x-defaults`/`common/base.yaml` comparison block with assertions that the root `compose.yaml` declares no `services:` key and none of the three `x-*` anchors, and that every `services/*/compose.yaml` appears in its `include:` list (and every `include:` entry resolves to an existing file); retarget the three `docker/`-rooted planted fixtures to `services/loki/conf/`, `services/pgadmin/conf/` and (for the hook case) `services/loki/conf/`; delete the two `docker/` `empties` entries, widen the `services/` lint-yaml pin from `services/*/compose.yaml` to every `services/**/*.y*ml`, and add a `.github/workflows/*.y*ml` pin so lint-yaml keeps two provable halves; rewrite the "two halves" and "eight inlined services" comments.
- `scripts/assert_config.py` -- update the three comments describing "an inlined service" reaching the `x-logging` anchor; the diagnostic string at `:457` names a path that no longer exists. No logic change.
- `README.md` -- fold the `docker/` block into the `services/` listing with the eight new Modules, drop the "not extracted yet" note, retarget `:571` (there is now one source, not two) and `:616` (`services/<name>/conf/`).
- `AGENTS.md` -- correct the three claims about `docker/` and inlined services inside the managed block; a later `bmad-project-context` refresh regenerates it, but leaving a false layout claim standing is worse.

**Acceptance Criteria:**
- Given the baseline rendered output captured before the change, when `docker compose config` and `docker compose --profile admin --profile observability config` are rendered after it, then the two are identical apart from key ordering and the relocated bind-mount `source` paths — no service, image, port, environment value, command, healthcheck, volume identifier, restart policy, logging option, profile or network attachment differs, and no `depends_on` edge is added or lost.
- Given the eight new Module files, when each is read, then none declares a `networks:` stanza, each `volumes:` stanza names one root-declared identifier and nothing else (and `services/otel-collector/compose.yaml` declares none at all), every relative path is written against that Module's own directory, `project_directory` appears nowhere, and no `<<: *alias` references an anchor defined in another file.
- Given the root `compose.yaml` after the change, when it is read, then it declares `name:`, `include:` with thirteen entries, `volumes:` and `networks:` and nothing else — no `services:` key, no `x-restart`, no `x-logging`, no `x-defaults` — and `docker/` does not exist.
- Given the assembled model, when `docker compose config -q` is run for every profile subset, then every `depends_on` edge resolves across the include boundary, and `docker compose config --profiles` still reports exactly `admin` and `observability`.
- Given `pixi run ci`, when it is run, then it exits 0, `lint-config` reports thirteen Module files scanned, and `lint-pins` reports the same pin-reference count as before the change.
- Given the running stack, when the eight Services are recreated from this working tree with both profiles active, then each reaches `healthy` (except `loki` and `tempo`, which carry no healthcheck by design and are proved by `smoke-test.sh`'s `/ready` polls), `docker volume ls --filter name=devinfra` is byte-identical to the captured inventory, and the pre-existing pgAdmin server registration, Grafana datasources and Prometheus series are still present.
- Given `pixi run smoke` in strict mode, when it runs against the recreated stack, then the OTLP round-trip through the collector into Loki and Tempo still passes — the collector's fan-out is the one edge no static check proves.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 26 findings — high 0, medium 3, low 18, false 2, maybe-false 0
- findings:
  - `[medium]` `[patch]` README.md's layout tree still called compose.yaml the home of "the eight admin and observability services not yet extracted" — confirmed at README.md:176-179, and it contradicted the `services/` row 45 lines below; the row now names the include registry plus the volume and network declarations, and says the file declares no services of its own.
  - `[low]` `[patch]` README.md:247 put `prometheus/conf/` where every sibling row names its file, one column off — restored to `prometheus/conf/prometheus.yml` at the shared column; `otel-collector/conf/` stays a directory because naming that file overflows the column, which is why the pre-change block named `otel/` too.
  - `[low]` `[patch]` common/base.yaml:4 explained itself by naming the `x-defaults` anchor this change deleted — confirmed; the header now says an alias cannot reach an anchor in another file without naming one that no longer exists.
  - `[low]` `[patch]` scripts/assert_pins.py:11 and scripts/assert_renovate.py:576 still said "Services are being extracted" while assert_config.py's equivalent prose was rewritten here — confirmed; both are now past tense, so all three agree.
  - `[low]` `[reject]` renovate.json's `/^compose\.yaml$/` manager pattern now selects a pin-less file. Real, but the named harm is tidiness only: assert_renovate.py aggregates `hits` per pattern across all selected files, so it neither did nor could flag this, and the adjacent description sentence explains the pattern's *anchoring*, which is still true. Deleting it needs a description rewrite and removes the net that would catch a pin re-added to the root file.
  - `[low]` `[patch]` The root-file assertion denied only the literal key `services` and three named anchors, so a fragment re-added as `x-common` passed — confirmed by planting `x-common` and watching the old check go green; replaced with a whole-key-set pin (`name`, `include`, `volumes`, `networks` and nothing else), which now fails naming `unexpected ['x-common']`.
  - `[low]` `[patch]` Both registry loops iterated possibly-empty sequences and would have passed vacuously — confirmed; added the non-empty guards the file already uses at :1685.
  - `[low]` `[patch]` `included` was built with an `isinstance(entry, str)` filter over `root_model.get("include", [])`, so Compose's legal `- path:` long form was silently dropped and an empty `include:` key raised TypeError instead of a named diagnostic — confirmed; both shapes are now read and an unreadable entry fails by name.
  - `[low]` `[reject]` Deleting the anchor comparison removed the only cross-check on the shared fragment's *values*. Real, but the invariant that matters — every service inherits base.yaml's logging — is still asserted by assert_config.check() against the rendered model; only "the documented values are these" is unpinned, which is documentation drift a developer would rarely meet, and the fix adds a duplicate golden-value source rather than correcting anything.
  - `[low]` `[patch]` The new `.github/workflows/*.y*ml` pin and the widened `services/**` pin make `moved_aside()` rename the CI workflow and every module file in place, so a killed run leaves the repository without them — confirmed at scripts/lint_selftest.py:396-414; added the killed-run hazard note this file already writes for the Keycloak fixture, naming `git status` and `git checkout --` as the recovery.
  - `[medium]` `[patch]` compose.yaml:6-9 lists which service sits in which profile and nothing verified it once the `profiles:` keys moved into thirteen files — confirmed; grouped with the profile-pin finding below and settled by the same literal member-set assertions.
  - `[low]` `[patch]` The eight new module files each repeated the same eight-line header paragraph, taking the duplication DW-22 records from five copies to thirteen, against the spec's own Design Notes — confirmed; trimmed to a three-line pointer at services/postgres/compose.yaml and ADR 0004's amendment, keeping every service-specific line.
  - `[low]` `[defer]` AGENTS.md:2 still stamps "Verified 2026-09-07 against e8971ad" after this change hand-edited the managed block — confirmed; routed to defer because the fix edits an agent-context file.
  - `[low]` `[reject]` docs/architecture-walkthrough.html labels the `x-defaults` figure "Today — breaks under include". Confirmed present at :598, but it is one half of an explicit before/after contrast in a planning-phase design artifact whose whole point is that "Today" is the pre-decomposition baseline; the figure misleads nobody, and rewriting a design document's narrative frame is out of proportion to the defect.
  - `[low]` `[defer]` scripts/lint-compose.sh:46-47 treats a successful but empty `config --profiles` read as "no profiles" and validates one combination — confirmed, and pre-existing: the same vacuity existed while profiles lived in the root file. Recorded in `deferred`.
  - `[low]` `[patch]` include: long-form mapping entries dropped — same defect as the `included` finding above; fixed by the same change.
  - `[low]` `[patch]` `include:` present but empty yields None and a TypeError — same defect as the `included` finding above; fixed by the same change.
  - `[low]` `[patch]` Registry check vacuous when both sides are empty — same defect as the vacuity finding above; fixed by the same change.
  - `[low]` `[patch]` A shared fragment re-added under any other `x-*` name passes — same defect as the root-key-set finding above; fixed by the same change.
  - `[low]` `[patch]` An `include:` entry outside `services/` would put service definitions back where `module_composes()`, assert_pins.py and every per-module check cannot see them — confirmed; each entry must now match `./services/<name>/compose.yaml`.
  - `[low]` `[patch]` README.md:247 column — same defect as the README alignment finding above; fixed by the same change.
  - `[medium]` `[patch]` The only real-runtime profile assertion was `core_services < all_services`, a proper subset, so any of the eight hand-transcribed bodies could have lost its `profiles:` key and joined the default stack `pixi run up-core` starts with every check green — confirmed by deleting pgAdmin's key and watching the old assertion pass; replaced with literal `expected_core` / `expected_admin` / `expected_observability` member-set pins, which now fail naming `unexpected ['pgadmin']`.
  - `[medium]` `[patch]` Nothing asserted that a rendered bind `source` exists, and this change rewrote seven of them; pgAdmin is the silent case because Docker materialises a missing source as an empty directory and `/misc/ping` still returns 200 — confirmed; assert_config.check() gained a fifth property, and mistyping `./conf/servers.json` now exits 1 naming the service, the resolved path and the failure mode.
  - `[medium]` `[defer]` Nothing committed pins the assembled model's `depends_on` edges or `command` flags, so a lost collector fan-out edge or a lost Prometheus flag ships green — confirmed, and the spec's own Design Notes and DW-25/DW-28 already record it; ADR 0002 makes `config -q` the sanctioned gate and story 2.4 owns the contract check. Recorded in `deferred`.
  - `[false]` `[reject]` The intent-alignment layer's reading that the live-stack criteria are "actions only a HUMAN can perform outside the repo", requiring `status: awaiting-operator`. They are not: the intent's four examples are all gated by an external party or account, while starting containers is repo-local and agent-capable — it was declined here only because the user's live stack occupies every fixed `container_name`, a shared-resource conflict, not a capability gate. Story 2-2 settled the identical situation as `done` plus a deferred entry (DW-23), and this run follows that precedent.
  - `[false]` `[reject]` The same layer's observation that the tree is staged-uncommitted at `status: in-review`. That is the expected mid-flight state of this workflow: the review step sets `in-review`, and the Finalize step writes `done` and commits. The snapshot the layer read was taken before Finalize ran.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 28 findings — high 0, medium 6, low 18, false 4, maybe-false 0
- findings:
  - `[low]` `[patch]` renovate.json:38,41 still read "Services **are being extracted** into services/<name>/compose.yaml" and "an extracted service's pin" — confirmed; this change is what falsified them, and it rewrote the same paragraph in assert_pins.py:11 and assert_renovate.py:576 while missing renovate.json, the fourth copy. Both sentences are now past tense, so all four agree.
  - `[low]` `[patch]` README.md:246 is still one column off, so last pass's row claiming it was "restored to the shared column" was not true of the committed file — confirmed by measuring every row in the block: 64 descriptions start at index 32 and `prometheus/conf/prometheus.yml` starts at 33, because the 30-character name overruns a column set at 2 + 29 + 1 by `postgres/conf/postgresql.conf`. Fixed by the repo's own precedent for a name that will not fit: the row now names `prometheus/conf/`, as the `otel-collector/conf/` row already does.
  - `[medium]` `[patch]` assert_config.py's new bind-source property tested existence only, so it went green on exactly the state it exists to catch — confirmed: the check's own docstring says Docker materialises a missing source as an empty *directory*, and once that has happened `Path(source).exists()` is True forever while pgAdmin still has no server registration. An empty directory is now rejected in its own right; all eleven tracked sources are eight real files and three directories holding 1-2 entries, so the rule does not touch the clean tree, and services/grafana/dashboards/ passes on the .gitkeep that is already the only reason git tracks it.
  - `[medium]` `[patch]` That fifth property shipped with no self-test, breaking the pattern every other assert_config property follows — confirmed: `rendered()` at lint_selftest.py:1571 emits only `image`, `ports` and `logging`, so no stub document ever carries a `volumes` key and the bind loop never executed under `pixi run test`; the one real-model run asserts `returncode == 0` only. Added four cases in the file's own `rendered(...)` + `pixi("lint-config", env=fresh(document=...))` idiom: a real file source accepted, a missing source rejected by name, a planted empty directory rejected, and services/grafana/dashboards/ still accepted.
  - `[low]` `[patch]` lint_selftest.py:2652-2656 claimed the literal profile pins are "the only thing that verifies compose.yaml's header" — confirmed false: `expected_core`/`expected_admin`/`expected_observability` are an independently maintained list that never reads the header, and the two already disagree, `expected_core` carrying `minio-init` where compose.yaml:7 lists five services. The comment now says the header stays unverified prose and explains the one-member difference rather than claiming a check that does not exist.
  - `[low]` `[patch]` The registry check's three directions disagreed about what counts as the same path — confirmed: direction one compares `.resolve()`d paths, so `services/x/compose.yaml` satisfies it, while direction three demanded a literal `./` and rejected that same legal Compose spelling with a diagnostic saying the module is invisible to `module_composes()` — which globs `services/*/compose.yaml` from disk (assert_config.py:245) and would have found it. The `./` is now optional; the location rule, which is the part that is really load-bearing, is unchanged.
  - `[low]` `[reject]` Nothing reconciles the root `volumes:` list against the modules that claim its identifiers — `identifier_only()` runs module→root only, and this change decoupled the root block from all thirteen module files. Real but rejected: an orphaned top-level declaration is inert (Compose creates only volumes a selected service uses), so no user or developer meets it outside deleting a module, and the fix adds a whole new reverse-direction assertion rather than correcting anything.
  - `[low]` `[reject]` DW-26's text is now false — this change's three-direction registry check is exactly the reconciliation it says nothing performs — and DW-21's reason still cites a `docker/` `empties` pin this change deleted. Both confirmed. Rejected as out of scope by the intent itself, whose Never section reads "Never write sprint-status.yaml or deferred-work.md; both are orchestrator artifacts"; the orchestrator owns each entry's status and resolution.
  - `[false]` `[reject]` That DW-22's stated trajectory is falsified with the resulting two-tier header convention "recorded nowhere", and that the new modules' pointer to services/postgres/compose.yaml gives advice inapplicable to otel-collector. Both halves refuted: the decision is recorded, in this spec's own Design Notes ("The eight new files get a short header instead"), and postgres's block states the Module rules generally with postgres as the named worked example, while services/otel-collector/compose.yaml:8-9 carries its own note explaining the absent `volumes:` stanza. The ledger half is out of scope for the reason above.
  - `[false]` `[reject]` That services/tempo/compose.yaml:24's "No healthcheck, for the same reason as loki" leaves a cross-file reference dangling. It does not: the reason follows in the same sentence, immediately after the colon — Tempo 3.0 dropped the busybox layer, so a Docker healthcheck has nothing to run. No reader is sent anywhere to find it.
  - `[low]` `[reject]` services/flower/compose.yaml says "Celery monitoring" twice in seven lines, once in the new header and once in the moved inline comment. Real, but no developer meets a harm here, and the fix is a rewrite of prose the intent required to move verbatim rather than a correction of anything wrong.
  - `[false]` `[reject]` That .claude/settings.local.json:11 allowlists `shellcheck ... docker/postgres/initdb/*.sh`, a path this change deleted. No bad outcome occurs: the file is untracked local harness configuration outside the committed change, the entry named a path that per ADR 0008's amendment never existed, and an allowlist entry that matches nothing grants nothing and blocks nothing. The reviewer's own filing calls it harmless.
  - `[medium]` `[patch]` A bind source that exists as a directory where a file is mounted passes — same defect as the kind-blind check above; fixed by the same change.
  - `[low]` `[reject]` Nothing pins that `config --profiles` reports exactly `admin` and `observability`, so a service gaining a third profile name alongside its existing one is unobserved. Confirmed reachable only in that narrow shape — a service that *moved* profiles, or a new service, already fails the literal member-set pins — and the consequence is marginal, since such a service still starts under the profile it kept. The fix adds another assertion; rejected on both counts.
  - `[false]` `[reject]` That a module directory holding `compose.yml` rather than `compose.yaml` would be invisible to every check. The reachable case is caught: an entry spelled `./services/foo/compose.yml` fails the registry's module-file rule by name. An unregistered file is not a module and contributes nothing, which is true of any stray file in the tree.
  - `[medium]` `[patch]` The new bind-source property has no planted fixture — same defect as the missing self-test above; fixed by the same change.
  - `[low]` `[reject]` docs/adr/0009:74 still attributes `max-file: "3"` to `compose.yaml`'s `x-logging`, an anchor this change deleted. Confirmed — the value is unchanged in common/base.yaml and only the location named is stale. Rejected: a reader of ADR 0009's Podman rationale is unlikely to go looking for that key, and the intent forbids editing a decided ADR in place, so the fix is a whole dated amendment block for a one-line pointer rather than a direct correction.
  - `[low]` `[patch]` lint_selftest.py:1572-1573 still offered the deleted `x-logging` anchor as one of two places a service reads its logging from — confirmed; this change rewrote that same prose in assert_config.py, assert_pins.py and assert_renovate.py and missed the stub helper's comment. It now names common/base.yaml as the only source and records that the anchor left with the last inlined service.
  - `[low]` `[reject]` This spec's Tasks entry for scripts/assert_config.py says "No logic change" while the change adds a fifth asserted property. True, and rejected under the standing rule that a finding whose fix is to edit this build's spec is not actionable.
  - `[low]` `[reject]` ADR 0009 keeps a claim this change falsified although the spec says "No ADR needs one here" — same defect as the ADR 0009 finding above; rejected for the same reasons.
  - `[low]` `[patch]` README:247 column — same defect as the README alignment finding above; fixed by the same change.
  - `[medium]` `[patch]` The bind-source property is the one assert_config rule with no proof it fires: deleting the loop leaves `pixi run ci` green — same defect as the missing self-test above; fixed by the same change, and the four new cases now cover the clean file, the missing source, the materialised empty directory and the legitimate directory source.
  - `[low]` `[patch]` lint-yaml is pinned only below a module root, while its lint-json twin deliberately pins both depths — confirmed, and demonstrated: with a defect planted at services/redisinsight/zz_proof.yaml, `services/**/*.y*ml` exits 1 and `services/**/conf/*.y*ml` exits 0, so narrowing the term would leave every existing case green while none of the thirteen module compose files was yamllinted. Added the module-root fixture in redisinsight/, the one module with no directory bind-mounted into a container, so a fixture surviving a killed run reaches nothing.
  - `[low]` `[patch]` The profile pins do not read compose.yaml's header they claim to verify, and `expected_core` already carries a member the header omits — same defect as the header-claim finding above; fixed by the same change.
  - `[low]` `[patch]` carried — the widened `services/**` pin and the new `.github/workflows/` pin make `moved_aside()` hide the CI workflow and every module file at once. Logged and patched last pass; the killed-run hazard note naming `git status` and `git checkout --` still stands at scripts/lint_selftest.py:812-820, so the row keeps its verdict and route and is not patched again.
  - `[medium]` `[defer]` carried — the intent's primary proof lives at the rendered model and its final proof at the live runtime, while the change's durable tests land on the file surface: `depends_on` edges, `command` flags, environment values and healthchecks stay unpinned, and the live half never ran. Logged and deferred last pass, and recorded in `deferred` as the two entries the orchestrator harvested to DW-30 and DW-31; the code still reads as those rows describe, so it is neither re-deferred nor re-verified.
  - `[low]` `[reject]` The acceptance criterion's "identical to the captured baseline" is unsatisfiable as literally written, since the Approach mandates deleting three `x-*` blocks that `docker compose config` echoes; the change silently widened the permitted difference set and recorded that only in a deferred entry. True, and rejected under the standing rule that a finding whose fix is to edit this build's spec is not actionable.
  - `[low]` `[reject]` The "`project_directory` is never used" invariant has no committed guard, and the eight new short headers dropped the prose that carried it, so it now appears in five module files instead of thirteen. Confirmed — grep finds it only in the five older modules and ADR 0001. Rejected: the gap is pre-existing (no check existed before either), each new header points at services/postgres/compose.yaml where the rule is stated, and the fix is a new lint assertion rather than a correction.

## Design Notes

**Why the anchors go.** ADR 0001 decided the shared fragment lives in `common/base.yaml` and is consumed through `extends`; the root anchors were a transitional second copy, kept alive only because inlined services could not reach across an `include` boundary. With no inlined services they read to nothing. The self-test assertion that compared the two sources is replaced by one that pins the end state — a `services:` key or an `x-*` anchor reappearing in the root file is the regression to catch now, and it is exactly what a future "just add it here quickly" change looks like.

**Why a registry check belongs here.** This story adds eight `include:` entries at once. DW-26 records that nothing reconciles `services/*/compose.yaml` against that list: a Module absent from `include:` is linted by every check, contributes nothing to the model, and validates green. The forward direction — every Module directory appears in the registry — is three lines of the self-test and directly serves this story's "the registry is the only place Services are listed". The reverse direction (every rendered service maps back to a Module directory) stays with story 2.4, which owns the bidirectional contract check.

**Config layout.** `conf/` is where a Module keeps files the service reads to configure itself, matching `services/postgres/conf/` and `services/redis/conf/`. Grafana gets both: `conf/provisioning/` is configuration, and `dashboards/` is a live drop-zone re-scanned every 30 seconds — neither config nor first-boot seed. Keeping the directory named `dashboards/` preserves the README instruction and gives story 3-1 an obvious landing spot; how story 2.4's presence check treats it is that story's call.

**Module headers.** The five existing Modules each repeat the same ~20-line header about `networks:`, exit 15, identifier-only volumes and `project_directory` — DW-22, which this story would take from five copies to thirteen. The eight new files get a short header instead: what the service is, plus a pointer to `services/postgres/compose.yaml` and ADR 0004's amendment for the rules. Fewer copies to drift; the rules themselves are enforced by `lint-config`, not by the comment.

**The unguarded edge.** `otel-collector`'s `depends_on: [loki, tempo]` is a list-form dependency between two Modules. Deleting an entry renders clean and passes `config -q` — only the before/after rendered diff and the live OTLP round-trip would show it. This is DW-25's shape, recorded rather than fixed: committing a rendered-model baseline revisits ADR 0002, which makes `config -q` the sanctioned dependency gate.

## Verification

**Commands:**
- `docker compose config` and `docker compose --profile admin --profile observability config`, diffed against the pre-change captures -- expected: empty once the relocated bind `source` paths are normalised; nothing else differs.
- `docker compose config --profiles` -- expected: `admin` and `observability`, unchanged.
- `pixi run lint-compose` -- expected: OK, four profile combinations validated.
- `pixi run lint-config` -- expected: OK over thirteen module files, no identifier or redeclaration diagnostic.
- `pixi run lint-yaml` and `pixi run lint-json` -- expected: OK, with the moved config reported; then re-add `docker/**/*.json` to `pixi.toml` and confirm `lint-json` fails with `no such file: docker/**/*.json`, proving the term had to go rather than merely being tidy. Restore and confirm `git diff -- pixi.toml` shows only the intended removal.
- `pixi run ci` -- expected: exit 0 across every lint task and the self-test.
- Load-bearing proof of the widened `services/**/*.y*ml` pin: delete that term from the `lint-yaml` cmd and re-run the self-test -- expected: the widened `empties` case fails by name. Restore and confirm `git diff` over `pixi.toml` is empty.
- `docker compose --profile admin --profile observability up -d && ./scripts/wait-healthy.sh` -- expected: every container with a healthcheck reaches healthy against its pre-existing volume.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.
- `pixi run smoke` (strict) -- expected: the Loki and Tempo `/ready` polls and the OTLP round-trip all pass against the recreated containers.

**Manual checks (if no CLI):**
- The local Compose is v5.3.0, which merges a Module `networks:` stanza silently where CI's v2 line rejects the whole model. `lint-config` is the only local check that sees that defect; a green `lint-compose` proves nothing about it.
- The live stack runs from the main checkout, so recreating a container from this worktree repoints its bind mounts at the worktree. If the live-stack criteria cannot be settled from here, say so explicitly rather than reporting them as met.


## Auto Run Result

Status: done

**Implemented change.** The last eight Services left the root `compose.yaml` for `services/<name>/`
Modules on the pattern stories 2-1 and 2-2 established: shared configuration through
`extends: {file: ../../common/base.yaml, service: defaults}`, an identifier-only `volumes:` stanza
(none at all for `otel-collector`, which owns no volume), and no `networks:` stanza. Each service
body moved verbatim — profile, ports, environment, command, `user:`, the two deliberate healthcheck
absences and every `depends_on` edge — with only the inheritance mechanism and the bind sources
changed. Their config moved beside them under `conf/`, plus `services/grafana/dashboards/` for the
drop-zone Grafana re-scans every 30 seconds, which emptied `docker/` and deleted it. The root file
now holds `name:`, `include:` (thirteen entries, alphabetical), `volumes:` and `networks:` and
nothing else; the `x-restart` / `x-logging` / `x-defaults` anchors are gone, having been a
transitional second copy of `common/base.yaml` that no inlined service was left to read.

This follow-up pass changed no Compose file and no rendered value. It repaired the checks and the
prose the first pass left behind.

**Files changed:**
- `services/{flower,grafana,loki,otel-collector,pgadmin,prometheus,redisinsight,tempo}/compose.yaml`
  — the eight new Modules.
- `services/pgadmin/conf/servers.json`, `services/otel-collector/conf/otel-collector-config.yaml`,
  `services/prometheus/conf/prometheus.yml`, `services/loki/conf/loki-config.yaml`,
  `services/tempo/conf/tempo.yaml`, `services/grafana/conf/provisioning/`,
  `services/grafana/dashboards/.gitkeep` — renamed from `docker/`, content unchanged apart from the
  stale drop-zone path in `dashboards.yaml`'s first comment line. `docker/` is deleted.
- `compose.yaml` — eight `include:` entries added, the `services:` mapping and the three anchor
  blocks deleted, header rewritten to record that extraction is complete.
- `pixi.toml` — `docker/**/*.y*ml` dropped from `lint-yaml`, `docker/**/*.json` from `lint-json`.
- `scripts/assert_config.py` — a fifth asserted property: every rendered bind `source` must exist
  and must not be an empty directory, which is the state Docker leaves behind after starting once
  with a mistyped source.
- `scripts/lint_selftest.py` — the root-file end-state pin; the three-direction registry check,
  whose entry-shape rule now accepts the `./` Compose treats as optional; four planted cases for
  the bind-source property; a module-root `lint-yaml` fixture in `redisinsight/`, the YAML twin of
  the existing `keycloak/` JSON one; retargeted fixtures and rebalanced `empties` pins; literal
  member-set pins for the core, `admin` and `observability` selections.
- `common/base.yaml`, `renovate.json`, `scripts/assert_pins.py`, `scripts/assert_renovate.py`,
  `README.md`, `AGENTS.md` — prose that described the extraction as in progress or named `docker/`.

**Review findings, follow-up pass.** 28 findings across four layers — high 0, medium 6, low 18,
false 4. Nine entries patched (2 medium, 7 low, one of them carried and already applied), one
carried defer, thirteen rejected. Patched counts by verdict: medium 2, low 7.

Patched, medium: `assert_config.py`'s bind-source property tested existence only, so the empty
directory Docker creates for a mistyped source satisfied it forever after the first `up` — the
exact state the check was added for; and that property shipped with no self-test at all, because no
stub document in `lint_selftest.py` carries a `volumes` key, so the loop never ran under
`pixi run test` and deleting it left `pixi run ci` green.

Patched, low: `renovate.json`'s "Services are being extracted" prose, the fourth copy of a paragraph
the first pass corrected in three other files; `README.md:246`, still one column off despite the
first pass logging it as fixed; a self-test comment claiming the profile pins verify
`compose.yaml`'s header, which they never read and already disagree with by one member; the
registry check rejecting a legal `include:` spelling with a diagnostic that misstated why; a stub
comment still offering the deleted `x-logging` anchor as a logging source; and a missing module-root
`lint-yaml` fixture, whose absence let `services/**/*.y*ml` be narrowed to `services/**/conf/*.y*ml`
with every case green and no module compose file yamllinted.

Rejected: no reverse root-volumes reconciliation (inert orphan, and the fix is a new assertion);
DW-26 and DW-21 now carrying stale text (the intent forbids writing `deferred-work.md`); DW-22's
falsified trajectory and an allegedly inapplicable header pointer (both refuted — the decision is
in this spec's Design Notes and `otel-collector` carries its own note); tempo's "same reason as
loki" pointer (the reason follows in the same sentence); flower's repeated "Celery monitoring"
(prose the intent required to move verbatim); `.claude/settings.local.json`'s stale allowlist entry
(untracked local config, matches nothing, grants nothing); no pin on the declared profile *names*
(reachable only for a service gaining a second profile, which still starts); a `compose.yml` module
directory (the reachable case already fails by name); ADR 0009:74's stale anchor location, twice
(the intent forbids editing a decided ADR in place); this spec's own "No logic change" task note and
its unsatisfiable identity criterion, twice (a finding whose fix edits this build's spec); and the
unguarded `project_directory` invariant (pre-existing, and each new header points at where the rule
is stated).

**Follow-up review recommendation: false.** This is a follow-up pass and it patched no `high`
finding, so the work has converged. The two medium patches were both repairs to a check added last
pass, not to the extraction itself, and both are now proved by planted cases.

**Verification performed:**
- `pixi run ci` — exit 0. `lint-config` reports thirteen module files scanned; `lint-pins` reports
  14 pin references, the same count as before the change; `lint-compose` validates four profile
  combinations.
- `pixi run test` — 798 assertions pass, zero failures, including the five new ones: a real file
  source accepted, a missing source rejected by name, a materialised empty directory rejected, a
  `.gitkeep`-only directory accepted, and a module-root YAML defect caught.
- Load-bearing proof of the new module-root `lint-yaml` fixture: with a defect planted at
  `services/redisinsight/zz_proof.yaml`, `yamllint --strict services/**/*.y*ml` exits 1 and
  `services/**/conf/*.y*ml` exits 0 — the narrowing the fixture exists to catch. Fixture removed
  afterwards; `git status` clean of it.
- The eleven rendered bind sources enumerated from
  `docker compose --profile admin --profile observability config --format json`: eight real files
  and three directories holding one or two entries each, so the new empty-directory rule does not
  touch the clean tree.
- `pixi run lint-python` — ruff format, ruff check and mypy all clean over the six scripts.
- No Compose file was edited in this pass, so the rendered model is unchanged from the state the
  first pass diffed against its pre-change baseline.

**Residual risks:** the two carried deferred entries. The live-stack half of the acceptance criteria
was never exercised from this worktree — every fixed `container_name` is held by the stack running
from the main checkout — and nothing committed pins the assembled model's `depends_on` edges or
per-service `command` flags, so a detail lost while transcribing eight service bodies would still
ship green. Both are recorded in `deferred` and settled by an operator run of
`docker compose --profile admin --profile observability up -d`, `./scripts/wait-healthy.sh` and
`pixi run smoke` from the checkout that owns the live stack.

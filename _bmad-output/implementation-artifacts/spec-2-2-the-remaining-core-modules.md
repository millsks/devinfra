---
title: 'The remaining core Modules'
type: 'refactor'
created: '2026-09-07'
status: 'done'
baseline_revision: 'c6307edfa02e71606026cc87c7bf2ced758e2a9a'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred:
  - summary: >-
      DW-13 is closed by this change, but the deferred-work ledger still records it as
      status: open.
    evidence: |-
      pixi.toml's lint-json glob now carries `services/**/*.json`, which is exactly the fix
      _bmad-output/implementation-artifacts/deferred-work.md DW-13 describes. This run does not
      write that ledger: it is the bmad-loop orchestrator's sweep artifact, and the sweep's own
      job is to detect already-resolved entries. Recorded here so the next sweep has the claim.
    location: >-
      _bmad-output/implementation-artifacts/deferred-work.md (DW-13)
    severity: low
  - summary: >-
      lint-json has no "covers every JSON at any depth" assertion, though lint-shell has
      exactly that guard.
    evidence: |-
      scripts/lint_selftest.py expands lint-shell's own task patterns and diffs them against a
      recursive walk, so a script in a new tree cannot silently escape coverage. lint-json has
      only the two per-term `empties` pins added here, which cover docker/ and services/. A JSON
      file added under common/, docs/, .github/ or at the repository root would be unlinted with
      no error anywhere — the same class of lapse DW-13 records. Deferred because no such file
      exists to demonstrate the gap, and epics story 2.4 owns the bidirectional contract check.
    location: >-
      scripts/lint_selftest.py (lint-shell coverage assertion has no lint-json counterpart)
    severity: low
  - summary: >-
      The module-file header comment is now duplicated near-verbatim across five module files,
      with no single source.
    evidence: |-
      The "no networks: stanza / Compose v2 exit 15 / identifier-only volumes / project_directory
      is never used" block appears in services/{keycloak,mailpit,minio,postgres,redis}/compose.yaml.
      This story multiplied the duplication from one file to five; stories 2.3 onward take it to
      thirteen, and the copies will drift. DW-14 already owns the missing "adding a module" recipe
      and assigns the per-Module gotchas artifact to epic 3 story 3.2.
    location: >-
      services/*/compose.yaml (header comments)
    severity: low
  - summary: >-
      The live-stack half of this story's acceptance criteria was never exercised — no container
      was recreated against its pre-existing volume, and pixi run smoke never ran.
    evidence: |-
      The running devinfra-* containers are owned by the main checkout and their binds still point
      at the pre-story-2-1 layout. Recreating them from this worktree would repoint their binds at
      a directory that is deleted when the run ends, and restoring them requires operating in the
      main checkout, which this run is barred from. Verification used an isolated Compose project
      with fresh volumes instead, so "reaches healthy against its pre-existing volume" and "the
      pre-existing realm, keys and buckets are still present" are unproven. What is proven: the
      rendered model is byte-identical to the pre-change baseline apart from the two relocated bind
      sources, so no volume reference can have been renamed or re-driven, and the twelve devinfra_*
      volumes are unchanged. Settling it needs one operator run of `pixi run up` plus `pixi run smoke`
      from a checkout that owns the live stack.
    location: >-
      spec Verification section (live-stack commands)
    severity: medium
  - summary: >-
      DW-19's deferral rationale no longer covers the case this change created.
    evidence: |-
      DW-19 justifies leaving the shared-fragment restart/networks half unasserted on the grounds
      that "the accidental case is already caught — a module that drops `extends` loses `logging`
      with it". That covers an unwanted override. This change creates the opposite direction: a
      service whose `extends` block is intact and whose logging is therefore correct, but whose
      sanctioned `restart` override is missing. The targeted minio-init assertion added in this
      pass closes the one instance; the ledger entry's reasoning is stale for the general case.
    location: >-
      _bmad-output/implementation-artifacts/deferred-work.md (DW-19)
    severity: low
  - summary: >-
      A dropped depends_on edge is caught by no committed check — only by the one-off rendered-model
      diff, which leaves no baseline behind.
    evidence: |-
      scripts/lint-compose.sh runs `docker compose config -q` over every profile subset, which
      catches a *dangling* edge naming an undefined service. An edge simply deleted renders and
      validates cleanly. The before/after rendered diff is what actually proved "preserving every
      dependency edge" here, and it ran from a scratch directory outside the repository. ADR 0002
      makes `config -q` the sanctioned dependency gate, so committing an edge-set baseline would
      revisit a decided ADR; epics story 2.4 owns the contract check.
    location: >-
      scripts/lint-compose.sh
    severity: low
  - summary: >-
      Nothing reconciles the services/*/compose.yaml set against the root compose.yaml include:
      list, so a module directory absent from include: still validates green.
    evidence: |-
      scripts/assert_config.py enumerates services/*/compose.yaml from disk and never reads the
      root include: list; lint-pins and lint-renovate do the same. A module present on disk but
      missing from include: would be scanned by every check and would silently contribute nothing
      to the model. Pre-existing — story 2-1 recorded it as an open residual risk — but this change
      adds four more include lines that carry no automated coverage. Epics story 2.4 owns the
      bidirectional Module-to-service contract check.
    location: >-
      scripts/assert_config.py (module_composes) vs compose.yaml include:
    severity: medium
  - summary: >-
      Nothing checks that a Module's bind-mount source still exists, and the Keycloak seed
      directory is the case where its absence is silent.
    evidence: |-
      services/keycloak/compose.yaml mounts `./seed:/opt/keycloak/data/import:ro`. Rename or empty
      that directory and Docker creates a bare host directory at the source path, `--import-realm`
      finds no realm, and Keycloak starts and reports healthy with no realm and no error anywhere.
      `docker compose config -q` does not check bind sources, and lint-json's `services/**/*.json`
      term keeps matching the realm file wherever under services/ it lands. Pre-existing in kind —
      the same hole existed for `./docker/keycloak/realms` before this story moved it — and
      services/redis's identical shape fails loudly instead, because redis-server cannot read a
      directory as its config. Settling it needs a per-Module contract check that asserts each
      declared bind source resolves to an existing path; epics story 2.4 owns that check.
    location: >-
      services/keycloak/compose.yaml (./seed bind source)
    severity: medium
  - summary: >-
      The rendered-config diff is named the primary verification for every extraction, yet it
      leaves no committed baseline and cannot be re-run after the fact.
    evidence: |-
      The epic context and this spec both make `docker compose config` before-and-after the
      highest-value check in the migration. It was run for this story from a scratch directory
      outside the repository and proved the model byte-identical apart from the two relocated bind
      sources — but nothing in the tree can reproduce it, so stories 2.3 through 2.6 each have to
      re-capture their own baseline by hand or skip the check. Distinct from DW-25, which scopes
      the same absence to `depends_on` edges only; this is the whole rendered model, including
      environment, command and healthcheck values. ADR 0002 makes `config -q` the sanctioned
      model gate, so committing a rendered baseline revisits a decided ADR; epics story 2.4 owns
      the per-Module contract check that would replace it.
    location: >-
      spec Verification section (rendered-model diff); scripts/lint-compose.sh
    severity: medium
  - summary: >-
      The architecture memlog still repeats the `docker/minio/` claim that ADR 0008's amendment
      refutes.
    evidence: |-
      _bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/.memlog.md:71
      records "AD-5 held - minio-data volume name and `docker/minio/` config path both unchanged".
      `git log --all -- docker/minio` is empty, so the claim is false there as it was in ADR 0008
      and the README, both corrected by this story. Not corrected here because the memlog is a
      phase 1-3 planning artifact, upstream of this story's bounds, and this story's Tasks list
      scoped the correction to ADR 0008 and the README. It matters because a future architecture
      session reads the memlog first.
    location: >-
      _bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/.memlog.md:71
    severity: low
---

<intent-contract>

## Intent

**Problem:** Story 2-1 proved the extraction mechanism on Postgres, the one Service with no dependencies. Twelve Services are still inlined in the root `compose.yaml`, and the mechanism is unproven against the three things Postgres never exercised: a cross-module `depends_on`, a Module that owns a declared helper container, and a Module whose config is JSON that a lint glob must keep reaching.

**Approach:** Extract Redis, Keycloak, object storage (`minio` plus its `minio-init` helper) and Mailpit into `services/<name>/` Modules on the pattern `services/postgres/` established, moving each one's config and seed data beside it and pulling all four back in through the root `include:` list. Every dependency edge — `keycloak → postgres`, `keycloak → mailpit`, `minio-init → minio`, and the four inlined admin Services that still point at `redis` and `postgres` — must survive as plain `depends_on` in the assembled model. Extend `lint-json` to `services/**/*.json` in the same change that moves the first Module JSON, so the Keycloak realm never drops out of coverage.

## Boundaries & Constraints

**Always:**
- `redis-data`, `keycloak-data`, `minio-data` and `mailpit-data` keep their identifiers. The object-storage Module is `services/minio/` and its volume stays `minio-data`; renaming either orphans real data with no error (AD-5).
- Each Module's top-level `volumes:` stanza names the identifier and nothing else. The root's declarations of all four are bare, so a bare redeclaration is accepted (AD-5).
- **No Module declares a `networks:` stanza.** The root declares `devinfra` with `name:` and `driver:`, and Compose v2 rejects the whole model — exit 15 — when any included file names a resource the root declares with keys. Compose v5 merges instead, so this passes locally and fails every CI job (ADR 0004, 2026-09-07 amendment).
- `minio-init` sets `restart: "no"` explicitly. It extends the base like every other Module service, and the base declares `restart: unless-stopped`; without the override the one-shot helper restart-loops forever (AD-1).
- Every Module service inherits `restart`/`logging`/`networks` through `extends: {file: ../../common/base.yaml, service: defaults}`, never a YAML anchor — anchors are document-scoped and cannot cross an `include` boundary.
- Every bind-mount path inside a Module file is written against that Module's own directory.
- Every check that reads a moved file keeps reaching it. A glob that stops matching is a silent skip, and `lint-json` is the one that breaks here.
- The rendered `docker compose config` output for both the default and the `--profile admin --profile observability` combinations must be identical to the captured baseline apart from key ordering and the relocated bind-mount `source` paths.

**Never:**
- Never rename a volume, container, service, environment variable, port or profile to tidy it. Never re-drive or delete a volume; no `down -v`, no `pixi run destroy`.
- Never extract a Service outside this story's four (the eight admin and observability Services are story 2.3), and never add `profiles:`, `x-bundles`, `x-requires`, `x-endpoints`, `smoke.sh`, `gotchas.md` or `scripts/select.sh` — those are stories 2.3 through 2.6.
- Never remove the `x-restart` / `x-logging` / `x-defaults` anchors from the root file: eight Services still read them.
- Never use `project_directory`, and never leave a `<<: *alias` referencing an anchor defined in another file.
- Never harden the stack incidentally: credentials, `start-dev`, `MP_SMTP_AUTH_ACCEPT_ANY`, `MINIO_PROMETHEUS_AUTH_TYPE: public` and every other dev-mode flag stay exactly as they are.
- Never edit a decided ADR in place. `docs/adr/0008`'s stale claim is corrected by an appended amendment, the pattern ADR 0004 already uses.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| JSON in a Module directory | `services/keycloak/seed/devinfra-realm.json` present, `lint-json` run | Parsed and reported `OK` alongside `renovate.json` and the remaining `docker/**/*.json` | No error expected |
| Module JSON glob matches nothing | Every `services/**/*.json` hidden | `lint-json` exits non-zero — an unmatched pattern reaches the tool literally and fails as a missing file | Non-zero exit naming the unmatched path |
| `docker/` JSON glob matches nothing | Every `docker/**/*.json` hidden, Module JSON present | `lint-json` exits non-zero — the two terms are pinned separately so neither can be deleted from `pixi.toml` while the other masks it | Non-zero exit |
| Malformed JSON in a Module | A syntactically invalid `.json` planted under `services/keycloak/seed/` | `lint-json` exits non-zero and names the planted file | Non-zero exit naming the file |
| Module redeclares the keyed network | A Module file carrying `networks: {devinfra:}` | `lint-config` exits 1 naming the file and stanza, on every Compose version | Non-zero exit |
| Realm export destination | `pixi run keycloak-export` against the running stack | The realm is written to `services/keycloak/seed/<realm>-realm.json`, inside the Module | Non-zero exit if the copy fails |
| Cross-module dependency | `keycloak` in one Module file, `postgres` in another | `docker compose config -q` resolves `depends_on: postgres` across the `include` boundary | Exit 1 naming the undefined service if the edge is lost |

</intent-contract>

## Code Map

- `compose.yaml:22-23` -- the `include:` list; gains four entries. `:13-15` header comment naming the include list as the record of what has moved.
- `compose.yaml:33-45` -- `x-restart` / `x-logging` / `x-defaults` anchors. Keep: eight Services below still alias them, and `x-logging` is also aliased by `x-defaults` itself.
- `compose.yaml:51-70` -- the inline `redis:` block to move, with its `./docker/redis/redis.conf` bind and its `--requirepass` command form.
- `compose.yaml:75-132` -- the inline `keycloak:` block: `depends_on` on `postgres` (healthy) and `mailpit` (started), two published ports, the `./docker/keycloak/realms` bind, and the `/dev/tcp` healthcheck whose folded scalar must survive the move byte-for-byte.
- `compose.yaml:143-192` -- `minio:` and `minio-init:`. `minio-init` uses `<<: *logging` plus an explicit `networks: [devinfra]` and `restart: "no"` — the same three keys the base supplies, with `restart` overridden, so `extends` + `restart: "no"` renders identically.
- `compose.yaml:197-216` -- the inline `mailpit:` block; no config files, volume only.
- `compose.yaml:221-284` -- `pgadmin`, `redisinsight`, `flower`: stay inlined, and their `depends_on` on `postgres` and `redis` becomes cross-file. This already works for `pgadmin → postgres` today, which is the precedent.
- `compose.yaml:389-406` -- root `volumes:` (all four identifiers are bare) and `networks: devinfra` with `name:` + `driver:` (keyed — no Module may name it). Unchanged by this story.
- `services/postgres/compose.yaml` -- the template to copy, including the header comment explaining why there is no `networks:` stanza.
- `common/base.yaml:26-34` -- the `defaults` service; read-only here.
- `docker/redis/redis.conf` -> `services/redis/conf/redis.conf`; `docker/keycloak/realms/devinfra-realm.json` -> `services/keycloak/seed/devinfra-realm.json`. `docker/minio/` and `docker/mailpit/` do not exist and never have.
- `pixi.toml:146` keycloak-export description; `:187` `lint-json` glob (`renovate.json docker/**/*.json`) — the one task glob this story must widen. `:179` lint-shell and `:183` lint-yaml already carry `services/**` terms and need no change.
- `scripts/keycloak-export.sh:2,13,14` -- `:13` is functional: the `compose cp` destination path.
- `scripts/keycloak-reimport.sh:14`, `scripts/lib/common.sh:18`, `Makefile:130`, `.env.example:97` -- prose naming `docker/keycloak/realms/`.
- `scripts/lint_selftest.py:635` comment counting the inlined Services; `:693-703` the `empties` list (the `lint-json` entry at `:702` globs `docker/` only); `:673-674` the planted-defect fixtures; `:918` a hard assertion on `keycloak-export.sh`'s destination string; `:1917` `image_keys` and `:2394` `declared_versions`, both already counted across `services/*/compose.yaml`.
- `scripts/assert_config.py:233-239` `module_composes()`, `:305` `identifier_only()` -- already scans `services/*/compose.yaml` generically; no change needed, and it is the check that catches a stray `networks:` stanza.
- `scripts/assert_pins.py` and `scripts/assert_renovate.py` -- already default to root plus `services/*/compose.yaml`; no change needed.
- `renovate.json:46-50` -- `managerFilePatterns` already selects every Module file; no change needed.
- `README.md:166` realm path; `:177-179` the count of Services not yet extracted (twelve now, eight after — `minio-init` is a declared helper, not a Service); `:223-236` the layout block's `services/` and `docker/` sub-listings; `:608` "config files under `docker/`"; `:647` the false claim that the config directory is `docker/minio/`.
- `AGENTS.md:6,17-20` -- inside a `bmad:context` managed block; the routing rule stays true in form after the move.
- `docs/adr/0008-object-storage-replacement.md:43-45` -- the `docker/minio/` claim; `docs/adr/0004-volume-names-are-frozen.md:41+` -- the amendment pattern to follow.
- `docs/adr/0001-modules-via-compose-include.md`, `0002-dependency-validation-is-compose-native.md` -- read-only; the decisions this story exercises.

## Tasks & Acceptance

**Execution:**
- `<scratch>/before-default.yaml`, `<scratch>/before-all.yaml`, `<scratch>/before-volumes.txt` -- before touching anything, write `docker compose config`, `docker compose --profile admin --profile observability config` and `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort` to a directory outside the repository tree -- the rendered-model diff is the primary verification and cannot be reconstructed afterwards.
- `services/redis/conf/redis.conf` -- `git mv docker/redis/redis.conf`, content unchanged -- the Module owns its config.
- `services/redis/compose.yaml` -- create with the `redis` service moved verbatim, `<<: *defaults` replaced by the `extends` block, the bind source rewritten to `./conf/redis.conf`, plus a bare `volumes: {redis-data:}` and no `networks:` stanza.
- `services/keycloak/seed/devinfra-realm.json` -- `git mv docker/keycloak/realms/devinfra-realm.json`, content unchanged -- `seed/` is the name story 2-1 established for first-boot data, and the container-side mount point is unaffected.
- `services/keycloak/compose.yaml` -- create with the `keycloak` service moved verbatim including both `depends_on` edges and the `/dev/tcp` healthcheck, the `extends` block, the bind source rewritten to `./seed`, plus a bare `volumes: {keycloak-data:}` and no `networks:` stanza.
- `services/minio/compose.yaml` -- create with both `minio` and `minio-init`. `minio-init` extends the base and overrides `restart: "no"`; its inline `networks: [devinfra]` is dropped because the base supplies it. Bare `volumes: {minio-data:}` and no `networks:` stanza.
- `services/mailpit/compose.yaml` -- create with the `mailpit` service moved verbatim, the `extends` block, and a bare `volumes: {mailpit-data:}`.
- `compose.yaml` -- add the four Module files to `include:` in a stable alphabetical order, delete the five inlined service blocks, leave the anchors and the root `volumes:`/`networks:` declarations untouched, and update the header comment's account of what has moved.
- `pixi.toml` -- add `services/**/*.json` to the `lint-json` glob and retarget the `keycloak-export` description -- without the glob the realm JSON stops being parsed with no error anywhere (deferred item DW-13).
- `scripts/keycloak-export.sh` -- retarget the `compose cp` destination and the two messages to `services/keycloak/seed/` -- otherwise the export recreates the old directory and lands outside the Module.
- `scripts/keycloak-reimport.sh`, `scripts/lib/common.sh`, `Makefile`, `.env.example` -- update the prose naming `docker/keycloak/realms/`.
- `scripts/lint_selftest.py` -- retarget the `keycloak-export.sh` destination assertion; split the `lint-json` `empties` entry into a `docker/` half and a `services/` half, on the rationale already written for the two `lint-yaml` halves; add a planted malformed-JSON defect under `services/keycloak/seed/`; correct the comment counting the inlined Services -- the new glob term must be proved load-bearing, not merely present.
- `README.md` -- update the realm path, the count of Services not yet extracted, the layout block's `services/` and `docker/` sub-listings, and the object-storage gotcha's false `docker/minio/` claim.
- `docs/adr/0008-object-storage-replacement.md` -- append a dated amendment recording that the object-storage Module is `services/minio/`, that `docker/minio/` never existed, and that the frozen name is the volume's, not the directory's.

**Acceptance Criteria:**
- Given the baseline rendered output captured before the change, when `docker compose config` and `docker compose --profile admin --profile observability config` are rendered after it, then the two are identical apart from key ordering and the two relocated bind-mount `source` paths — no service, image, port, environment value, command, healthcheck, volume identifier, restart policy, logging option or network attachment differs.
- Given the four new Module files, when each is read, then none declares a `networks:` stanza, each `volumes:` stanza names one root-declared identifier and nothing else, every relative path is written against that Module's own directory, `project_directory` appears nowhere, and no `<<: *alias` references an anchor defined in another file.
- Given the rendered model, when `minio-init` is inspected, then its `restart` is `"no"` and its `logging` matches `common/base.yaml`'s — the helper neither restart-loops nor loses the shared logging policy.
- Given the assembled model, when `docker compose config -q` is run for every declared profile combination, then every `depends_on` edge resolves: `keycloak` on `postgres` and `mailpit` across Module boundaries, `minio-init` on `minio` within one, and `pgadmin`/`redisinsight`/`flower` from the root file into Modules.
- Given `services/keycloak/seed/devinfra-realm.json`, when `pixi run lint-json` runs, then it reports that file `OK`; and when every `services/**/*.json` is hidden, then the task exits non-zero rather than passing on the `docker/` half alone.
- Given the running stack, when `redis`, `keycloak`, `minio`, `minio-init` and `mailpit` are recreated from this working tree, then each reaches `healthy` (or, for `minio-init`, exits 0), `docker volume ls --filter name=devinfra` is byte-identical to the captured inventory, and the pre-existing Keycloak realm, Redis keys and object-storage buckets are still present.
- Given `pixi run ci`, when it is run, then it exits 0 and `lint-config` reports five Module files scanned.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 25 findings — high 0, medium 7, low 17, false 1, maybe-false 0
- findings:
  - `[low]` `[defer]` blind-hunter: DW-13 is closed by this change but the ledger still says `status: open` — real; the deferred-work ledger is the orchestrator's sweep artifact, not this run's to write, and the sweep's documented job is detecting already-resolved entries. Deferred with the claim recorded.
  - `[low]` `[patch]` blind-hunter: the ADR 0008 amendment omits the inline forward-pointer ADR 0004's pattern uses — confirmed; ADR 0004 carries a blockquote in its Decision body at :23-24 and ADR 0008 had only the appended half, so the refuted `docker/minio/` sentence read as current. Patched: blockquote added immediately after that sentence.
  - `[medium]` `[patch]` blind-hunter: the regenerated epic-2-context.md silently drops epic-level constraints — confirmed by diffing against the committed version; the dropped rules bind stories 2.3-2.6, which load this file as their primary planning context. Patched: five constraints restored in place.
  - `[low]` `[defer]` blind-hunter: lint-json has no depth-coverage assertion though lint-shell does — real, but no JSON exists outside docker/ and services/ to demonstrate the gap, and the fix adds a guard rather than correcting one. Deferred.
  - `[low]` `[patch]` blind-hunter: the planted lint-json fixture sits inside `services/keycloak/seed/`, which the module bind-mounts wholesale into Keycloak's `--import-realm` directory — confirmed; `planted()` is try/finally, so a killed run leaves a malformed realm file in the import path, and nothing gitignores the prefix. Patched: fixture moved to `services/keycloak/zz_selftest_defect.json`, verified still matched by the `services/**/*.json` term.
  - `[low]` `[reject]` blind-hunter: `x-restart` and `x-logging` are now vestigial, referenced only from `x-defaults` — true as an observation, but no harm to any user or developer is named: the anchors are inert and correct, and collapsing them is tidying the intent does not ask for and the story forbids.
  - `[low]` `[defer]` blind-hunter: ~50 lines of module-header boilerplate now duplicated five ways — real; this change multiplied it from one file to five and the copies will drift. DW-14 already owns the missing "adding a module" recipe, and epic 3 story 3.2 owns the per-Module gotchas artifact.
  - `[low]` `[patch]` blind-hunter: minio/ and mailpit/ headers justify avoiding `project_directory` with "it would re-base every relative path in the file" when neither file has a relative path — confirmed; the clause is inherited template prose. Patched: reworded in both files to state the rule without the vacuous justification.
  - `[low]` `[patch]` blind-hunter: the new compose.yaml header sentence does not agree in number and says "profiles" where it means "services" — confirmed. Patched: sentence reworded.
  - `[low]` `[patch]` blind-hunter: the README lists the module registry in extraction order while `include:` is alphabetical, and the `services/` caption reads as saying the inlined services are extracted — both confirmed against the file. Patched: caption reworded and sub-entries reordered to match `include:`.
  - `[low]` `[reject]` blind-hunter: the planted-defect label derives from `fixture.parent.name`, so the new fixture reported as "seed/" and collided with `services/postgres/seed/` — the collision is real, but relocating the fixture out of `seed/` (patched above) removes it, and no two remaining fixtures share a parent name; changing the label derivation would guard a case not demonstrated.
  - `[low]` `[defer]` blind-hunter: the data-durability half of the acceptance criteria has no recorded result — correct that the diff records nothing; the volume inventory was in fact run and is unchanged, but the live-stack recreation and `pixi run smoke` were not performed. Deferred with what would settle it, and stated under Auto Run Result.
  - `[false]` `[reject]` edge-case-hunter: `planted()` writes into `services/keycloak/seed/` without creating the directory, so an emptied `seed/` yields a FileNotFoundError traceback — refuted: `planted()` does not mkdir, but `seed/` holds the tracked `devinfra-realm.json` at every point the defects loop runs, and the `empties` block that hides files runs afterwards and uses `moved_aside`. The directory cannot be empty when the fixture is written.
  - `[low]` `[patch]` edge-case-hunter: the lint-json services/ fixture is planted in a directory bind-mounted into Keycloak's import path — same root cause as the blind-hunter finding above; patched by the same fixture relocation.
  - `[medium]` `[patch]` edge-case-hunter: epic-2-context dropped "the security posture is fixed, not improved — reject any change that partially hardens the stack" — confirmed against the committed version; restored.
  - `[medium]` `[patch]` edge-case-hunter: epic-2-context dropped "carving the central script into per-Module checks must leave the suite's pass count unchanged" — confirmed; it is story 2.4's only objective completeness check for the smoke carve-out. Restored.
  - `[medium]` `[patch]` edge-case-hunter: epic-2-context dropped "a Module carries a profile equal to its own name plus its Bundle profiles" — confirmed; without it story 2.5's resolver has no stated mechanism for selecting a single Module by name. Restored.
  - `[low]` `[patch]` edge-case-hunter: epic-2-context dropped the "published ports bind to the configured bind address (loopback by default)" clause — confirmed; assert_config.py still enforces it, so the loss is to the epic's stated bar rather than to enforcement. Restored.
  - `[low]` `[reject]` edge-case-hunter: the spec's Approach says "the four inlined admin Services that still point at redis and postgres" when only three exist — the count is genuinely wrong (pgadmin→postgres, redisinsight→redis, flower→redis), but the sentence is inside `<intent-contract>` and the fix would edit this build's own spec, which triage may not route.
  - `[medium]` `[patch]` verification-gap: `minio-init`'s `restart: "no"` became load-bearing in this change and no assertion observes it — the strongest finding of the pass. On `<<: *logging` the line restated Docker's own default and deleting it changed nothing; on `extends` the base supplies `restart: unless-stopped`, so deleting it makes the one-shot helper restart-loop forever while the full static gate stays green (assert_config.py compares `logging` only, by design). Patched: three assertions in lint_selftest.py read `services/minio/compose.yaml` and require the helper to exist, to extend the base, and to override `restart` with `"no"`. Proved load-bearing — deleting the line fails by name.
  - `[low]` `[defer]` verification-gap: DW-19's deferral rationale is stale on this branch — confirmed; it scopes the risk to an unwanted override, while this change creates the missing-override direction. The targeted assertion closes the one instance; the ledger reasoning is the orchestrator's to update.
  - `[low]` `[defer]` intent-alignment: a dropped `depends_on` edge is caught by no committed check — `config -q` catches a dangling edge, not a deleted one, and the rendered-model diff that actually proved edge survival leaves no baseline behind. ADR 0002 makes `config -q` the sanctioned gate, and story 2.4 owns the contract check.
  - `[medium]` `[defer]` intent-alignment: nothing reconciles the on-disk module set against the root `include:` list, so a module absent from `include:` validates green and contributes nothing — pre-existing; story 2-1 recorded it as an open residual and epics story 2.4 owns it. This change adds four more uncovered include lines.
  - `[medium]` `[patch]` intent-alignment: epic-2-context.md was rewritten wholesale and the rewrite is not purely editorial — same root cause as the blind-hunter and edge-case findings above; patched by the same restoration.
  - `[low]` `[defer]` intent-alignment: the live-stack verification is neither evidenced in the diff nor recorded anywhere as owed — same root cause as the blind-hunter finding above; deferred with what would settle it, and named as this pass's unverified risk.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 41 findings — high 0, medium 7, low 27, false 7, maybe-false 0
- findings:
  - `[low]` `[patch]` blind-hunter: ADR 0008's header is not marked amended, though ADR 0004 — the pattern the spec names — reads `Status: Accepted (amended 2026-09-07)` — confirmed; ADR 0008 read `Status: **Accepted**`, so a reader scanning headers for revised records saw an unamended one. Patched: `(amended 2026-09-07)` added to the status line.
  - `[false]` `[reject]` blind-hunter: the inline blockquote in ADR 0008's Decision body violates the spec's "never edit a decided ADR in place" — refuted: the same clause names ADR 0004's pattern as the sanctioned one, and ADR 0004 carries exactly such a blockquote in its Decision body at :23-24 alongside its appended amendment. The pattern is both halves; the previous pass added the missing half.
  - `[false]` `[reject]` blind-hunter: DW-20 says "this run does not write that ledger" inside an edit to that ledger — refuted: DW-20 through DW-26 carry `origin: spec-deferred <hash>`, meaning the orchestrator's sweep transcribed them from this spec's `deferred` frontmatter. The sentence was written in the spec, where it was true of the build-auto run, and the sweep copied it verbatim. No build-auto run wrote deferred-work.md.
  - `[low]` `[patch]` blind-hunter: the triage log points at an `## Auto Run Result` section that does not exist, so the Verification command list reads as executed — confirmed; the section was absent from the working tree. Patched: Finalize writes it, restoring what ran and what did not.
  - `[false]` `[reject]` blind-hunter: the spec says `in-review` while sprint-status says `done`, so the recommended follow-up has no route back — refuted: this invocation *is* that follow-up pass, dispatched from the `done` spec, and `in-review` is the transient state step-04 sets while running. sprint-status is the orchestrator's board and its rows are not this run's to write.
  - `[low]` `[reject]` blind-hunter: `epic-2: backlog` with two of six stories done is misleading — the epic key is the orchestrator's bookkeeping in a file this run is barred from writing, and no user or developer reads it as a progress signal; the fix is not this run's to make.
  - `[medium]` `[patch]` blind-hunter: `epic-2-context.md` uses "Core" as a defined term in five bullets after the regeneration deleted the definition — confirmed against the committed version; the Microkernel-split bullet defining the Core Substrate is gone, and with it "A Module touches Core solely to add its own registry entry, its identifier-only volume declaration, its port-table row and its Bundle membership" — the one rule saying what a story may edit in the root file, which this story had to obey four times. Patched: both restored.
  - `[false]` `[reject]` blind-hunter: the new "reports the rest as skipped" bullet contradicts "no step may skip a check" — refuted: they govern different conditions and both are sourced. epics.md:28 (FR-5) says the smoke suite reports skipped for Modules outside the Selection; epics.md:38 (FR-16) forbids skipping because a tool is unavailable, and the regenerated file keeps that at :58.
  - `[medium]` `[patch]` blind-hunter: the regeneration drops five further binding rules and adds one never agreed — mostly confirmed. Dropped and restored: the README half of the breaking-change lede (MIGRATION-PLAN.md:160), "Core scripts are verified like code — `set -euo pipefail`, shellcheck-clean, with tests" narrowed to the resolver alone (ARCHITECTURE-SPINE.md:288), "presence-based, never a judgement" (epics.md:442), and story 2.3's sequencing rationale. Refuted: "Malformed or missing required configuration fails at startup naming the variable" is not invented — it is NFR-5, at prd.md:380 and epics.md:51. Also refuted: the per-batch config diff and volume inventory survive, split across two retained bullets.
  - `[low]` `[patch]` blind-hunter: the `empties` comment claims "the two halves of the lint-json glob" while `pixi.toml` has three terms, leaving `renovate.json` unpinned — the count is confirmed. But a literal path cannot silently stop matching, which is the only failure this mechanism detects, and `assert_renovate.py:689` json-parses that file on every run. Patched as a comment correction: "the two *glob* halves", plus the reason the literal term is not pinned.
  - `[medium]` `[defer]` blind-hunter: nothing checks that a Module's bind source exists, and an emptied `services/keycloak/seed/` yields a healthy Keycloak with no realm — confirmed, and silent. Pre-existing in kind: the same hole existed for `./docker/keycloak/realms` before the move. Deferred; epics story 2.4 owns the per-Module contract check.
  - `[low]` `[patch]` blind-hunter: the `extends` assertion only checks `isinstance(..., dict)`, so a block pointing at any other file or service satisfies it while making the `restart` override meaningless again — confirmed, and weaker than the rationale its own comment states. Patched: the assertion now requires `common/base.yaml` and `defaults`, proved load-bearing by repointing `service:` and watching it fail by name.
  - `[low]` `[reject]` blind-hunter: no `.gitignore` rule guards a `zz_selftest_defect.*` left by a killed run — the gap is real, but the proposed fix makes it worse: an ignored leftover is invisible to `git status` while `pixi run lint-json` still fails on it, so the failure becomes mysterious instead of attributable. Untracked-and-visible is the better state.
  - `[low]` `[reject]` blind-hunter: the I/O matrix has no row for the `minio-init` restart override — the observation is fair, but the fix edits this build's own spec, which triage may not route.
  - `[low]` `[reject]` blind-hunter: no criterion asserts that a stale `docker/keycloak/realms` reference cannot return — the reviewer confirmed none remains, so the fix adds a repo-wide grep guard for a state never demonstrated, against a defect unlikely to be met in everyday use.
  - `[false]` `[reject]` blind-hunter: the root header's "their `depends_on` edges onto postgres and redis now cross the include boundary" overstates, since `pgadmin → postgres` already crossed — refuted as a defect: the sentence describes the present state of all three edges, and all three do now cross the boundary. It asserts nothing about testing.
  - `[low]` `[defer]` blind-hunter: the architecture memlog:71 still repeats the refuted `docker/minio/` claim — confirmed, and `git log --all -- docker/minio` is empty. Deferred: the memlog is a phase 1-3 planning artifact upstream of this story's bounds, and the Tasks list scoped the correction to ADR 0008 and the README.
  - `[low]` `[reject]` blind-hunter: the spec's Approach says "four inlined admin Services" when three exist — carried: same location and claim as the previous pass's rejected row, and the sentence still reads as logged. The count is genuinely wrong; the fix edits this build's own spec.
  - `[low]` `[reject]` edge-case-hunter: the new `services/` `empties` case renames the Keycloak realm aside inside the bind-mounted import directory, so a killed run breaks the next import — the hazard is real but loud, not silent: `git status` shows a deleted tracked file and the next `lint-json` goes red, and `git checkout` restores it. The fix restructures `moved_aside`, a shared helper with many callers, for a state reachable only by SIGKILL mid-subprocess.
  - `[low]` `[reject]` edge-case-hunter: the `lint-yaml` `empties` case hides all five module compose files at once, so an interrupted run leaves the root `include:` naming missing files — real, but pre-existing (that entry predates this story) and loud in exactly the same way: five deleted tracked files in `git status`.
  - `[low]` `[reject]` edge-case-hunter: `yaml.safe_load(...).get("services")` on `services/minio/compose.yaml` raises `AttributeError` if the file is empty — the file is tracked, and `lint-yaml` and `lint-config` both go red before the self-test runs, so the state was never shown reachable. The pre-existing `root_model` load at :641 has the same shape, so the fix would add a guard against the file's own convention.
  - `[low]` `[patch]` edge-case-hunter: the `extends` assertion should require `common/base.yaml` and `defaults` — same root cause as the blind-hunter row above; patched by the same change.
  - `[medium]` `[patch]` edge-case-hunter: the regeneration dropped the `x-endpoints:` key name and its role as the sole input to generated connection documentation — confirmed against ARCHITECTURE-SPINE.md:134 and epics.md:441; the regenerated file said only "an endpoint-contract declaration", leaving story 2.4 with no key name to implement. Restored.
  - `[low]` `[patch]` edge-case-hunter: "the change leads the README and release notes" lost its README half — confirmed against MIGRATION-PLAN.md:160. Restored.
  - `[false]` `[reject]` edge-case-hunter: "Each batch is its own reviewable change with its own config diff and volume inventory" was dropped — refuted: the substance survives in two retained bullets, "Capture the volume inventory before and after every extraction" and "the rendered `docker compose config` output ... is the primary verification", and the retained "Rollback for an extraction is reverting the commit" carries the per-batch commit granularity.
  - `[low]` `[patch]` edge-case-hunter: "Core scripts are verified like code" was narrowed to the resolver, losing `set -euo pipefail` and the generalization — confirmed against ARCHITECTURE-SPINE.md:288, which scopes the bar to `scripts/*.sh` and `services/*/smoke.sh`. Restored as a general rule with the resolver as one instance.
  - `[low]` `[patch]` edge-case-hunter: "no absolute paths" was dropped from the Module-relative-paths rule — confirmed against ARCHITECTURE-SPINE.md:118, which forbids them in the same breath as `project_directory`. Restored.
  - `[low]` `[reject]` edge-case-hunter: the spec's Approach miscounts the admin edges — carried: same claim as the blind-hunter row above and as the previous pass's rejected row.
  - `[low]` `[reject]` edge-case-hunter: the Tasks list still says the planted fixture sits under `services/keycloak/seed/` when triage relocated it — the staleness is real, but the fix edits this build's own spec.
  - `[low]` `[defer]` edge-case-hunter: the running-stack acceptance criterion was never exercised — carried: same claim as the previous pass's deferred row, recorded in `deferred` and unchanged. Still owed to an operator.
  - `[medium]` `[patch]` verification-gap: no committed check observes any relocated service's `environment`, `command`, `healthcheck` or mount values, and `MP_DATABASE` is the case where the loss is silent and destructive — the strongest finding of the pass, and demonstrated end to end. Deleting `MP_DATABASE: /data/mailpit.db` leaves `config -q`, `assert_config.py` (image, logging and ports, by design), every lint task and the smoke suite green — the smoke test sends and re-reads a message in one run, which succeeds against an in-memory store — while the volume is mounted and never written and every captured email is lost on the next restart. Patched: three assertions read `services/mailpit/compose.yaml` and require the service, `MP_DATABASE` under `/data/`, and the `mailpit-data:/data` mount. Proved load-bearing: deleting the key fails that assertion by name with every other check still green.
  - `[low]` `[reject]` verification-gap: DW-26 overstates its exposure, because `smoke-test.sh:69-79` turns a skip into a failure under `SMOKE_STRICT=1` — the correction is sound and is recorded here, but the fix edits the deferred-work ledger, which is the orchestrator's sweep artifact and not this run's to write.
  - `[low]` `[reject]` verification-gap: the `safe_load` mapping check — same root cause as the edge-case row above; rejected for the same reason.
  - `[medium]` `[defer]` intent-alignment: the rendered-config diff is the story's own primary verification and leaves no committed baseline, so it cannot be re-run — confirmed; it ran from a scratch directory outside the repository. Distinct from DW-25, which scopes the same absence to `depends_on` edges alone. Deferred: ADR 0002 makes `config -q` the sanctioned model gate, so committing a baseline revisits a decided ADR, and epics story 2.4 owns the contract check.
  - `[low]` `[defer]` intent-alignment: a deleted `depends_on` edge is caught by no committed check — carried: same claim as the previous pass's deferred row, unchanged.
  - `[low]` `[patch]` intent-alignment: the `minio-init` guard sits one surface below the failure it names, its `extends` check is satisfied by any block, and the paired `logging` half is asserted nowhere — the `extends` half is confirmed and patched with the rows above. The `logging` half is refuted: `assert_config.py` compares every rendered service's `logging` against `common/base.yaml`'s. The surface point stands as description — `assert_config.py` deliberately skips `restart`, so a static assertion is the sanctioned proxy.
  - `[low]` `[reject]` intent-alignment: the realm-export row is behavioral in the intent and textual in the diff — true, and the assertion pins only the argv string. Exercising the export needs the running stack that the live-stack deferral already records as owed; the string assertion is the sanctioned static proxy, and adding more is not a direct correction.
  - `[low]` `[defer]` intent-alignment: the live-stack half is unexercised and not evidenced in the diff — carried: same claim as the previous pass's deferred row.
  - `[false]` `[reject]` intent-alignment: the keyed-network row's real surface is unreachable locally, so the matrix's "on every Compose version" is unmet — refuted: `assert_config.py` enforces it by reading the module files' text, never by consulting Compose, so it fires identically on any version. The reviewer's own check confirms the generic machinery covers all four new files, and this run's `lint-config` reports `OK 5 module file(s)`.
  - `[low]` `[reject]` intent-alignment: three of seven matrix rows are lint-json and they took most of the new test effort — a description of emphasis, not a defect; no bad outcome at any cited location.
  - `[medium]` `[patch]` intent-alignment: `epic-2-context.md` was regenerated wholesale, which the intent never bounded — the regeneration itself is sanctioned (step-01 invalidates a cached context older than the planning artifacts, and the cached copy predated the AD-5 amendment), so what is actionable is the content it lost. Same root cause as the rows above; patched by the same restorations.

## Design Notes

The three things Postgres did not exercise, and how each is settled:

**A cross-module `depends_on`.** Nothing special is required — `include` assembles one model before dependencies resolve, and `pgadmin → postgres` already crosses the boundary today. This is AD-2's position: a dependency not expressed as `depends_on` does not exist, and `config -q` is the gate.

**A Module owning a helper.** `minio-init` currently aliases `x-logging` and then restates `networks` and `restart` itself. Extending the base gives the same three keys, so only the `restart` override must be carried across explicitly:

```yaml
  minio-init:
    extends:
      file: ../../common/base.yaml
      service: defaults
    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026-09-03T13-18-01Z}
    depends_on:
      minio:
        condition: service_healthy
    restart: "no"
```

Drop that last line and the one-shot container restart-loops forever, which is the trap ADR 0001 records and `common/base.yaml`'s header warns about. `assert_config.py` deliberately does not assert `restart`, precisely because this override is sanctioned — so nothing catches it but review and the running stack.

**Module JSON.** `lint-json`'s glob is the only task glob that does not already carry a `services/**` term, because story 2-1 could not add one: an unmatched glob makes the task fail, and Postgres contributes no JSON. The Keycloak realm is the first Module JSON, so the term goes in with it. Both halves are pinned separately in the self-test's `empties` list for the same reason the two `lint-yaml` halves are: hiding only `docker/`'s JSON would leave the `services/` term deletable from `pixi.toml` with every case still green.

`docker/minio/` is a directory ADR 0008 and the README both describe and that has never existed in this tree. ADR 0004 freezes *volume* identifiers; the directory was never in scope. The amendment says so rather than quietly deleting the sentence.

## Verification

**Commands:**
- `docker compose config` and `docker compose --profile admin --profile observability config`, diffed against the pre-change captures -- expected: empty once the relocated bind `source` paths are normalised; nothing else differs.
- `pixi run lint-compose` -- expected: OK, four profile combinations validated.
- `pixi run lint-config` -- expected: OK over five module files, no identifier or redeclaration diagnostic.
- `pixi run ci` -- expected: exit 0 across every lint task and the self-test, with the self-test's assertion count above its current 734.
- Load-bearing proof of the new `lint-json` term: delete `services/**/*.json` from the `pixi.toml` glob and re-run the self-test -- expected: the new `empties` case fails by name. Restore and confirm `git diff` over `pixi.toml` is empty.
- `docker compose up -d redis keycloak minio mailpit && ./scripts/wait-healthy.sh` -- expected: every container healthy against its pre-existing volume; `docker compose ps minio-init` shows `Exited (0)`.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.
- `pixi run smoke` -- expected: the Keycloak token mint, the object round-trip and the SMTP capture all pass against the recreated containers.

**Manual checks (if no CLI):**
- The live stack runs from the main checkout, so recreating a container from this worktree repoints its bind mounts at the worktree. After verifying, restore those containers from the main checkout's own compose file so the shared stack is left exactly as it was found.
- The local Compose is v5.3.0, which merges a Module `networks:` stanza silently where CI's v2 line rejects the whole model. `lint-config` is the only local check that sees that defect; a green `lint-compose` proves nothing about it.


## Auto Run Result

Status: done

**Implemented change.** Redis, Keycloak, object storage and Mailpit left the root `compose.yaml`
for `services/<name>/` Modules on the pattern story 2-1 established: shared configuration through
`extends: {file: ../../common/base.yaml, service: defaults}`, a bare one-identifier `volumes:`
stanza, and — the rule ADR 0004's 2026-09-07 amendment added — no `networks:` stanza at all, since
the root declares that network with keys and Compose v2 rejects the whole model when a module
names it. `docker/redis/redis.conf` and `docker/keycloak/realms/` moved beside the Modules that
mount them, as `git mv` renames. The object-storage Module is `services/minio/` and owns both
`minio` and the declared helper `minio-init`; the helper carries `restart: "no"` explicitly,
because on `extends` — unlike the `<<: *logging` alias it used before — the base supplies
`restart: unless-stopped`, and without the override a one-shot container restart-loops forever.

The recurring finding across both review passes is that shape: a value that lives only in a module
file, whose loss renders and validates clean and shows up days later on the running stack. The
first pass found it in `minio-init`'s restart override; the follow-up pass found it in Mailpit's
`MP_DATABASE`. Both are now pinned by assertions that fail by name.

**Files changed:**
- `services/redis/compose.yaml`, `services/keycloak/compose.yaml`, `services/minio/compose.yaml`,
  `services/mailpit/compose.yaml` — the four new Modules; each service moved verbatim, only the
  inheritance mechanism and the bind sources changed.
- `services/redis/conf/redis.conf`, `services/keycloak/seed/devinfra-realm.json` — renamed from
  `docker/`, content unchanged; `docker/redis/` and `docker/keycloak/` are gone.
- `compose.yaml` — four `include:` entries added alphabetically, the five inlined blocks deleted,
  the anchors and the root `volumes:`/`networks:` declarations untouched, header comment updated.
- `pixi.toml` — `lint-json` widened to `services/**/*.json`, closing DW-13 in the same change that
  moves the first Module JSON; `keycloak-export` description retargeted.
- `scripts/keycloak-export.sh` — the `compose cp` destination, the one functional path in the set.
- `scripts/lint_selftest.py` — assertions pinning `minio-init`'s `extends` target and its
  `restart: "no"`, and Mailpit's `MP_DATABASE` and `mailpit-data:/data` mount; a planted
  malformed-JSON defect under `services/keycloak/`; the `lint-json` `empties` entry split into a
  `docker/` half and a `services/` half; the `keycloak-export.sh` destination assertion
  retargeted; two stale service counts corrected.
- `scripts/keycloak-reimport.sh`, `scripts/lib/common.sh`, `Makefile`, `.env.example` — prose
  naming the moved realm directory.
- `README.md` — realm path, the count of Services still inlined, the layout block's `services/`
  and `docker/` listings (now ordered as `include:` reads them), and the object-storage gotcha's
  false `docker/minio/` claim.
- `docs/adr/0008-object-storage-replacement.md` — a dated amendment recording that `docker/minio/`
  never existed and that ADR 0004 freezes volume identifiers rather than paths, an inline
  blockquote in the Decision body so the refuted sentence no longer reads as current, and the
  header marked `(amended 2026-09-07)` as ADR 0004's does.
- `_bmad-output/implementation-artifacts/epic-2-context.md` — regenerated, because the cached copy
  predated the AD-5 amendment and still stated the superseded "volumes *and networks*" rule; the
  two review passes then restored twelve epic-level constraints the regeneration had dropped.

**Review findings breakdown.** Two passes, 66 findings total.

*First pass* — 25 findings: high 0, medium 7, low 17, false 1. Patched 11 findings in 7 entries
(the `minio-init` restart assertion; five restored epic-level constraints; the planted fixture
moved out of Keycloak's import directory; the ADR 0008 blockquote; two Module-header rewordings;
the root header sentence; the README caption and ordering). Deferred 8 findings in 7 entries.
Rejected 4, recorded in the triage log with reasons.

*Follow-up pass* — 41 findings: high 0, medium 7, low 27, false 7, maybe-false 0. Patched 12
findings in 6 entries — at entry verdict, **medium 2 and low 4**:
- Mailpit's `MP_DATABASE` and volume mount are now asserted; deleting the key leaves every other
  check green (medium).
- Seven further epic-level constraints restored to `epic-2-context.md`: the Core Substrate
  definition and the "what a Module may touch in Core" rule, the `x-endpoints:` key name and its
  role as FR-12's sole input, "no absolute paths", the general "Core scripts are verified like
  code" bar with `set -euo pipefail`, the README half of the breaking-change lede, "presence-based,
  never a judgement", and story 2.3's sequencing rationale (medium).
- The `minio-init` `extends` assertion now requires `common/base.yaml`'s `defaults` rather than any
  dict (low).
- ADR 0008's header marked `(amended 2026-09-07)` (low).
- The `lint-json` `empties` comment corrected to "the two *glob* halves", with the reason the
  literal `renovate.json` term is not pinned (low).
- This `## Auto Run Result` section restored (low).

Deferred this pass: 3 new entries — the unchecked bind-mount source (medium), the rendered-model
diff leaving no committed baseline (medium), and the architecture memlog's stale `docker/minio/`
claim (low). Three further findings were carried unchanged from the first pass's deferrals.

Rejected this pass: 20 findings, each with its refutation or reason in the triage log. The seven
graded `false` are worth naming, since they were checked and disproved rather than waved off: the
ADR blockquote does not violate the "no in-place edit" rule (ADR 0004, which the spec names as the
pattern, carries exactly one); DW-20 does not contradict itself (the orchestrator's sweep
transcribed it from this spec's frontmatter, where it was true); the spec/board status
disagreement has a route back (this pass); the smoke "skipped" and "no silent skip" rules govern
different conditions and are both sourced (FR-5 and FR-16); "each batch is its own reviewable
change" survives in two retained bullets; the root header sentence describes the present state
correctly; and `assert_config.py` enforces the keyed-network rule by reading module text, so it
fires on every Compose version. One claimed epic-context invention was also refuted — "malformed
configuration fails at startup naming the variable" is NFR-5, at `prd.md:380`.

**Follow-up review recommended: false.** This was the follow-up pass, and it patched no `high`
entry — the two `medium` entries are a targeted assertion and a documentation restoration, both
verified by re-running the gate. The work has converged. What remains open is not a review
question but an operator one, recorded in `deferred`.

**Verification performed.**
- `pixi run ci` — exit 0. lint-compose (4 profile combinations), lint-config (`OK 5 module
  file(s)`, no identifier or redeclaration diagnostic), lint-pins (14 pin references), lint-renovate
  (13 dependencies over 27 references across all five module files), lint-shell, lint-yaml,
  lint-json (`OK renovate.json`, `OK docker/pgadmin/servers.json`,
  `OK services/keycloak/seed/devinfra-realm.json`), lint-python (ruff format, ruff check, mypy
  strict — all clean), and the self-test at **748 passing assertions, zero failures** — 734 before
  this story, 742 after implementation, 745 after the first review pass, 748 after this one.
- Load-bearing proof of all three new assertions, each defect planted and reverted:
  - Deleting `MP_DATABASE` fails `mailpit stores its database on the mounted volume` **and nothing
    else** — the silent-loss path, demonstrated.
  - Repointing the mount to a non-existent volume fails `mailpit mounts mailpit-data at the path
    MP_DATABASE writes to` (and, loudly, `config -q`).
  - Repointing `minio-init`'s `extends` to `service: not-defaults` fails `minio-init inherits the
    shared fragment through extends` by name.
- Rendered model: unchanged by this pass. No file under `compose.yaml`, `services/`, `common/` or
  `pixi.toml` was touched — the patches are confined to `scripts/lint_selftest.py`,
  `docs/adr/0008-object-storage-replacement.md` and two `_bmad-output/` artifacts — so the first
  pass's rendered-model result stands: identical to the pre-change baseline apart from the two
  relocated bind `source` paths, across both the default and `--profile admin --profile
  observability` combinations.
- Every epic-context restoration was checked against its source before being written back:
  `ARCHITECTURE-SPINE.md:118` (absolute paths), `:134` (`x-endpoints`), `:288` (`set -euo pipefail`,
  shellcheck), `MIGRATION-PLAN.md:160` (README lede), `epics.md:442` (presence-based). One claimed
  omission was found to be correctly present and was not "restored": NFR-5 at `prd.md:380`.
- Not performed: recreating the live containers against their pre-existing volumes, and
  `pixi run smoke`. Both remain recorded in `deferred`.

**Residual risks.**
- The live-stack half of the acceptance criteria is still unproven. No container was recreated
  against its pre-existing volume and `pixi run smoke` never ran, because the running stack is
  owned by the main checkout and recreating it from this worktree would repoint its bind mounts at
  a directory that disappears with the run. Settling it needs one operator run of `pixi run up`
  plus `pixi run smoke` from a checkout that owns the live stack.
- The value-pinning assertions are targeted, not general. `minio-init`'s restart and Mailpit's
  database path are now covered by name; every other module value — `KC_PROXY_HEADERS`,
  `MINIO_PROMETHEUS_AUTH_TYPE`, every healthcheck block — still has no committed observer, and the
  rendered-model diff that would cover them all leaves no baseline behind. Deferred; epics story
  2.4 owns the per-Module contract check.
- A Module's bind-mount source is unchecked. An emptied `services/keycloak/seed/` yields a healthy
  Keycloak with no realm and no error. Pre-existing in kind, deferred.
- The local Compose is v5.3.0, which merges a module `networks:` stanza silently where CI's v2 line
  rejects the whole model with exit 15. `lint-config` reads the module files' text rather than
  asking Compose, so it fires on any version — but `lint-compose` passing still proves nothing
  about that defect, and a module authored locally can look correct until CI.
- The four new `include:` lines are what actually reassemble the extracted services, and nothing
  reconciles that list against the module directories on disk. Deferred; epics story 2.4 owns it.

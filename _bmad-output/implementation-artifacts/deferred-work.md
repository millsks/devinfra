### DW-1: The profile power set is implemented twice, in bash and in Python, with nothing asserting the two enumerations agree.
origin: spec-deferred 2e80ca6b32dc
location: scripts/lint-compose.sh and scripts/assert_config.py
source_spec: `spec-1-3-ci-proves-the-stack-works-on-every-change.md`
severity: low
reason: scripts/lint-compose.sh builds it with `for ((mask = 0; mask < combinations; mask++))` and scripts/assert_config.py with `for mask in range(1 << len(profiles))`, each carrying its own `(none)` label convention and its own COMPOSE_PROFILES clearing. Both are tested, but only independently: a future rule (canonical ordering, skipping the empty combination) can land in one and not the other with nothing going red. Merging them means either shelling from bash to the Python enumerator or folding `config -q` into assert_config.py, which is a restructure rather than a correction.
status: open

### DW-2: Enumerating 2^N profile combinations will not survive Epic 2 giving every service a profile.
origin: spec-deferred e9f467b12c9d
location: scripts/lint-compose.sh, scripts/assert_config.py
source_spec: `spec-1-3-ci-proves-the-stack-works-on-every-change.md`
severity: low
reason: Two profiles today, so four compose invocations taking about two seconds — nobody meets this now. The epics file states that Stage 5 gives every Service a profile; at fourteen services that is 16384 invocations and `pixi run lint` would never finish. The remedy (a cap, or enumerating only the selections that exist rather than the power set) is a design decision belonging to the story that introduces per-service profiles, not a guard to add here.
status: open

### DW-3: `pixi run ci` crashes with an uncaught FileExistsError if the developer already has a compose.override.yaml.
origin: spec-deferred 73132b1b6f93
location: scripts/lint_selftest.py:551
source_spec: `spec-1-3-ci-proves-the-stack-works-on-every-change.md`
severity: low
reason: scripts/lint_selftest.py:551 (pre-existing, not introduced by this story) and the new block both use `planted(REPO / "compose.override.yaml", ...)`, which deliberately raises rather than overwrite tracked content. compose.override.yaml is not gitignored, so a developer using one gets a traceback instead of a gate result. The fix is `moved_aside()` around an existing file, or planting under a unique name passed with `-f`.
status: open

### DW-4: `scripts/lint_selftest.py` resolves the compose model through a hardcoded `docker compose`, so `pixi run ci` cannot run on a machine that has only Podman.
origin: spec-deferred 86f0834808bd
location: scripts/lint_selftest.py (the `tool(["docker", "compose", ...])` call sites)
source_spec: `spec-1-4-the-stack-runs-under-podman.md`
severity: low
reason: Three real-runtime calls take the literal argv `["docker", "compose", ...]`: the planted undefined-`depends_on` case, the profile-precedence pair, and the `config --profiles` read that the new `stack-podman` profile assertion reuses. All three pre-date this story; this story extends the same constraint by asserting the seam's Docker default through a real `compose.sh version` call, which is deliberate - only the real default can say what an unset `DEVINFRA_COMPOSE` reaches. Routing the other three through `DEVINFRA_COMPOSE` is a change to how the gate resolves the model, not a correction to this diff, and it interacts with what those cases are for: two of them deliberately compare the model against the real runtime.
status: open

### DW-5: Sourcing `.env` through `common.sh` expands `$`, backticks and backslashes where Compose's own dotenv parser would take them literally.
origin: spec-deferred 7b874025359b
location: scripts/lib/common.sh:24-40
source_spec: `spec-1-4-the-stack-runs-under-podman.md`
severity: low
reason: `scripts/lib/common.sh` does `set -a; source .env`, so a value such as `PASSWORD=ab$cd` is exported as `ab` and, because an exported value beats `.env`, Compose then interpolates the truncated value. This pre-dates the story - sixteen scripts already source `common.sh`, including `up-core.sh`, `smoke-test.sh` and `backup.sh` - and this diff extends it to the five repointed tasks, which makes the surface more consistent rather than less. Verified as inert for this repository today: `docker compose --profile admin --profile observability config` and `pixi run config` render byte-identical output. The fix is a decision about whether the scripts parse `.env` themselves rather than sourcing it, which belongs with the seam, not with this story.
status: open

### DW-6: Nothing checks the new `# Image currency` block in `.env.example` against the pins it describes.
origin: spec-deferred 9f5fcd0b572d
location: .env.example (# Image currency block) / scripts/assert_pins.py
source_spec: `spec-1-5-every-image-brought-current.md`
severity: medium
reason: assert_pins.py deliberately skips comment lines, so the block can claim a tag or a lag that no longer matches the declaration twenty lines below it. It is the artifact CAP-20's "dated written reason" leans on, so silent drift there un-meets the criterion without any gate noticing. Settling it means a parser for the block's own lines, which is a second check rather than a fix to this one.
status: open

### DW-7: The README service table's Version column is guarded by nothing.
origin: spec-deferred 95b119121e13
location: README.md:11-23
source_spec: `spec-1-5-every-image-brought-current.md`
severity: medium
reason: The spec makes README the third file that must move with every pin, but the column records truncated versions (`8.10` for `8.10.1-alpine`, `1.31` for `v1.31.1`), so an exact-match check is not free. Grepping scripts/ for README finds only a comment. A bump that forgets the column passes ci and ci-stack.
status: open

### DW-8: Every image bump is verified only against empty volumes; the upgrade-over-existing-data path is exercised nowhere, and the pgvector half of bump 1 is a no-op on an existing volume.
origin: spec-deferred 8c6b86bf23c2
location: scripts/smoke-test.sh (PostgreSQL section) / .github/workflows/ci.yml
source_spec: `spec-1-5-every-image-brought-current.md`
severity: medium
reason: CI runs ci-stack on ephemeral runners, so both stack jobs test a first boot exclusively. Moving pgvector 0.8.1 -> 0.8.6 installs the new library but leaves pg_extension.extversion at 0.8.1 until ALTER EXTENSION vector UPDATE runs, which nothing in this repository does; smoke-test.sh asserts only that the <-> operator works, which is true on both. The same blind spot covers the Keycloak three-minor jump against a 26.4.0-created database. Settling it needs a CI job that starts the stack at the previous pins and restarts it at the new ones on the same volumes, plus an extversion assertion in the smoke suite.
status: open

### DW-9: Bumps 9-11 are recorded only as operator_actions prose and `.env.example` comments, so the deferred-work sweep never sees them and nothing links the lag to story 1-6.
origin: spec-deferred 3f25266bd1aa
location: .env.example (# Image currency block) / operator_actions
source_spec: `spec-1-5-every-image-brought-current.md`
severity: high
reason: Sibling stories 1-1 through 1-4 route carry-over through this `deferred` list, which is what populates deferred-work.md. This entry exists so the outstanding RedisInsight, Loki and Tempo+Grafana upgrades are visible to that sweep as well as to the operator.
status: open

### DW-10: The baseline artifact records only the "before" half of the comparison it was built for.
origin: spec-deferred a05c4f669f07
location: _bmad-output/implementation-artifacts/baseline-1-5-every-image-brought-current.md
source_spec: `spec-1-5-every-image-brought-current.md`
severity: low
reason: baseline-1-5-…md captures the pre-wave pins, the 45-check smoke breakdown and the twelve named volumes, and states that no bump may add, remove or rename a volume — but no "after" section ever evidences that. The closing 45/0/0 run, the token-claims check and the unchanged volume list exist only as prose in the Spec Change Log.
status: open

### DW-11: AGENTS.md still presents `pixi run lint` as the whole validation surface and never mentions `pixi run bootstrap`, the `precommit` task or the commit-message contract.
origin: spec-deferred 43e69d3f7b3b
location: AGENTS.md (Running and verifying)
source_spec: `spec-1-7-commit-time-checks-run-the-same-tasks-ci-does.md`
severity: low
reason: The `bmad:context` block in AGENTS.md is managed by bmad-project-context and edits inside it are replaced on refresh, so the correction belongs either in a section outside the markers or in the next context refresh. As shipped, an agent's first commit in a bootstrapped clone is rejected by a contract nothing in its instructions described. Routed to defer because the fix edits an agent-context file.
status: open

### DW-12: Nothing enforces the rest of the module contract this story establishes — that common/base.yaml never appears in an include list, that no module uses project_directory, that no cross-file `<<: *alias`
origin: spec-deferred 62aa60867b23
location: scripts/ (no check exists)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: medium
reason: The volume/network identifier-only half is now checked by scripts/assert_config.py. The remaining rules are asserted only by this story's one-off acceptance criteria and by prose in common/base.yaml's header, so the next twelve extractions copy them by hand with no gate. Epics story 2.4 ("Every Module carries its own contract, enforced") owns the bidirectional check; the base-in-include, project_directory and cross-file alias rules are not currently assigned to any story.
status: open

### DW-13: lint-json still globs only renovate.json and docker/**/*.json, so service JSON config drops out of coverage the moment stories 2.2-2.3 move it into a module directory.
origin: spec-deferred 4e4037273c79
location: pixi.toml (lint-json task)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: low
reason: pixi.toml's lint-json task was deliberately left alone: postgres contributes no JSON, and a services/**/*.json glob that matches nothing makes the task fail on an unmatched pattern. docker/keycloak/realms/devinfra-realm.json and docker/pgadmin/servers.json move in stories 2.2 and 2.3; the glob must be added in the same change that moves the first one, or those files stop being parsed with no error.
status: open

### DW-14: README's "Gotchas worth knowing" documents none of the four traps this story introduces, and there is no written recipe for adding the next module.
origin: spec-deferred 71da766b48fd
location: README.md (Gotchas worth knowing)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: low
reason: The traps — a YAML anchor cannot cross an include boundary; common/base.yaml must never be included because that adds a service named `defaults`; a relative bind path resolves against the module file's own directory; a one-shot helper extending the base must override `restart: "no"` — live only in common/base.yaml's and services/postgres/ compose.yaml's header comments, which a contributor adding a module has no reason to open first. AGENTS.md now routes `services/` work through the README section. Epic 3 story 3.2 converts the gotchas into a maintained per-Module artifact.
status: open

### DW-15: .claude/settings.local.json allowlists a shellcheck command naming the deleted docker/postgres/initdb/ directory.
origin: spec-deferred 370afacba4f2
location: .claude/settings.local.json:11
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: low
reason: Line 11 reads Bash(shellcheck scripts/smoke-test.sh docker/postgres/initdb/*.sh). Harmless — the entry simply never matches again — but it is a stale path of exactly the kind this story treated as a defect elsewhere. Deferred because the fix edits an agent-context configuration file.
status: open

### DW-16: An untracked services/<name>/.env would be read as a fallback for any variable absent from the root .env, which AD-4 forbids and nothing detects.
origin: spec-deferred 4751e8cbf0b1
location: services/<name>/.env (absent today)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: medium
reason: Compose reads a child env file for an included model; the root .env wins on conflict, so a per-module file can never override — but a variable the root does not declare at all is supplied silently by the module. Before this change there were no module directories, so the hazard is newly reachable. It requires someone to create a file the architecture already forbids, and the presence check belongs with epics story 2.4's contract check.
status: open

### DW-17: AGENTS.md's "shared restart/logging/networks come from common/base.yaml through extends, never a YAML anchor" can read as applying to every service rather than to extracted modules only.
origin: spec-deferred 6b7b75d04742
location: AGENTS.md (Where things are)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: low
reason: The surrounding sentence does distinguish extracted modules from the services still inlined, but a later agent skimming the rule could strip the root file's still-live x-defaults anchors or add an extends to an inlined service. Deferred because the fix edits an agent-context file.
status: open

### DW-18: The self-test's FORBIDDEN-token scan over shell sources still walks scripts/ and .githooks/ only, so a module's own shell scripts are shellchecked but never scanned for `|| true` or a tool-presence
origin: spec-deferred 2469e3a59c6e
location: scripts/lint_selftest.py (shell_sources)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: low
reason: scripts/lint_selftest.py builds shell_sources from (REPO / "scripts").rglob("*.sh") plus the git hooks. services/postgres/seed/20-extra-databases.sh is reached by the lint-shell glob (widened to services/**/*.sh in this pass) but not by the token scan, and so is also absent from the "lint-shell covers every script at any depth" assertion. Pre-existing rather than caused by this story: the same file was outside shell_sources at docker/postgres/initdb/ before the move. Fixing it means deciding whether a seed script may legitimately use the tokens the scan forbids, which is a question about seed scripts rather than about this extraction.
status: open

### DW-19: assert_config.check() asserts only `logging` of the three keys common/base.yaml declares, so a module's `restart` (and `networks`) can diverge from the shared fragment with every check green.
origin: spec-deferred 2a2208c180d5
location: scripts/assert_config.py (check)
source_spec: `spec-2-1-the-shared-fragment-and-the-first-module.md`
severity: medium
reason: Reproduced: inserting `restart: "no"` into services/postgres/compose.yaml below an intact `extends:` block leaves lint-compose, lint-config, lint-yaml and lint-pins all green while the service renders with Docker's non-restarting policy. The accidental case is already caught — a module that drops `extends` loses `logging` with it — so what remains is an explicit override, and an override is sanctioned: common/base.yaml's own header tells a one-shot helper to set `restart: "no"`, and minio-init does exactly that, rendering `restart: no` today. A blanket equality check like the logging one would therefore reject a legitimate service. Closing this needs a way to declare the exception, which is the same design question as the no-exception-mechanism risk already recorded for logging; epics story 2.4 ("Every Module carries its own contract, enforced") owns it.
status: open

### DW-20: DW-13 is closed by this change, but the deferred-work ledger still records it as status: open.
origin: spec-deferred 271f843ff488
location: _bmad-output/implementation-artifacts/deferred-work.md (DW-13)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: pixi.toml's lint-json glob now carries `services/**/*.json`, which is exactly the fix _bmad-output/implementation-artifacts/deferred-work.md DW-13 describes. This run does not write that ledger: it is the bmad-loop orchestrator's sweep artifact, and the sweep's own job is to detect already-resolved entries. Recorded here so the next sweep has the claim.
status: open

### DW-21: lint-json has no "covers every JSON at any depth" assertion, though lint-shell has exactly that guard.
origin: spec-deferred 38258a24dd41
location: scripts/lint_selftest.py (lint-shell coverage assertion has no lint-json counterpart)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: scripts/lint_selftest.py expands lint-shell's own task patterns and diffs them against a recursive walk, so a script in a new tree cannot silently escape coverage. lint-json has only the two per-term `empties` pins added here, which cover docker/ and services/. A JSON file added under common/, docs/, .github/ or at the repository root would be unlinted with no error anywhere — the same class of lapse DW-13 records. Deferred because no such file exists to demonstrate the gap, and epics story 2.4 owns the bidirectional contract check.
status: open

### DW-22: The module-file header comment is now duplicated near-verbatim across five module files, with no single source.
origin: spec-deferred bc18de915844
location: services/*/compose.yaml (header comments)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: The "no networks: stanza / Compose v2 exit 15 / identifier-only volumes / project_directory is never used" block appears in services/{keycloak,mailpit,minio,postgres,redis}/compose.yaml. This story multiplied the duplication from one file to five; stories 2.3 onward take it to thirteen, and the copies will drift. DW-14 already owns the missing "adding a module" recipe and assigns the per-Module gotchas artifact to epic 3 story 3.2.
status: open

### DW-23: The live-stack half of this story's acceptance criteria was never exercised — no container was recreated against its pre-existing volume, and pixi run smoke never ran.
origin: spec-deferred 7295c40b6786
location: spec Verification section (live-stack commands)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: medium
reason: The running devinfra-* containers are owned by the main checkout and their binds still point at the pre-story-2-1 layout. Recreating them from this worktree would repoint their binds at a directory that is deleted when the run ends, and restoring them requires operating in the main checkout, which this run is barred from. Verification used an isolated Compose project with fresh volumes instead, so "reaches healthy against its pre-existing volume" and "the pre-existing realm, keys and buckets are still present" are unproven. What is proven: the rendered model is byte-identical to the pre-change baseline apart from the two relocated bind sources, so no volume reference can have been renamed or re-driven, and the twelve devinfra_* volumes are unchanged. Settling it needs one operator run of `pixi run up` plus `pixi run smoke` from a checkout that owns the live stack.
status: open

### DW-24: DW-19's deferral rationale no longer covers the case this change created.
origin: spec-deferred 0bcf9a505da4
location: _bmad-output/implementation-artifacts/deferred-work.md (DW-19)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: DW-19 justifies leaving the shared-fragment restart/networks half unasserted on the grounds that "the accidental case is already caught — a module that drops `extends` loses `logging` with it". That covers an unwanted override. This change creates the opposite direction: a service whose `extends` block is intact and whose logging is therefore correct, but whose sanctioned `restart` override is missing. The targeted minio-init assertion added in this pass closes the one instance; the ledger entry's reasoning is stale for the general case.
status: open

### DW-25: A dropped depends_on edge is caught by no committed check — only by the one-off rendered-model diff, which leaves no baseline behind.
origin: spec-deferred 52212d7cfe1f
location: scripts/lint-compose.sh
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: scripts/lint-compose.sh runs `docker compose config -q` over every profile subset, which catches a *dangling* edge naming an undefined service. An edge simply deleted renders and validates cleanly. The before/after rendered diff is what actually proved "preserving every dependency edge" here, and it ran from a scratch directory outside the repository. ADR 0002 makes `config -q` the sanctioned dependency gate, so committing an edge-set baseline would revisit a decided ADR; epics story 2.4 owns the contract check.
status: open

### DW-26: Nothing reconciles the services/*/compose.yaml set against the root compose.yaml include: list, so a module directory absent from include: still validates green.
origin: spec-deferred dd782014685b
location: scripts/assert_config.py (module_composes) vs compose.yaml include:
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: medium
reason: scripts/assert_config.py enumerates services/*/compose.yaml from disk and never reads the root include: list; lint-pins and lint-renovate do the same. A module present on disk but missing from include: would be scanned by every check and would silently contribute nothing to the model. Pre-existing — story 2-1 recorded it as an open residual risk — but this change adds four more include lines that carry no automated coverage. Epics story 2.4 owns the bidirectional Module-to-service contract check.
status: open

### DW-27: Nothing checks that a Module's bind-mount source still exists, and the Keycloak seed directory is the case where its absence is silent.
origin: spec-deferred 58fa13ed7b2f
location: services/keycloak/compose.yaml (./seed bind source)
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: medium
reason: services/keycloak/compose.yaml mounts `./seed:/opt/keycloak/data/import:ro`. Rename or empty that directory and Docker creates a bare host directory at the source path, `--import-realm` finds no realm, and Keycloak starts and reports healthy with no realm and no error anywhere. `docker compose config -q` does not check bind sources, and lint-json's `services/**/*.json` term keeps matching the realm file wherever under services/ it lands. Pre-existing in kind — the same hole existed for `./docker/keycloak/realms` before this story moved it — and services/redis's identical shape fails loudly instead, because redis-server cannot read a directory as its config. Settling it needs a per-Module contract check that asserts each declared bind source resolves to an existing path; epics story 2.4 owns that check.
status: open

### DW-28: The rendered-config diff is named the primary verification for every extraction, yet it leaves no committed baseline and cannot be re-run after the fact.
origin: spec-deferred 48f4806a13e7
location: spec Verification section (rendered-model diff); scripts/lint-compose.sh
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: medium
reason: The epic context and this spec both make `docker compose config` before-and-after the highest-value check in the migration. It was run for this story from a scratch directory outside the repository and proved the model byte-identical apart from the two relocated bind sources — but nothing in the tree can reproduce it, so stories 2.3 through 2.6 each have to re-capture their own baseline by hand or skip the check. Distinct from DW-25, which scopes the same absence to `depends_on` edges only; this is the whole rendered model, including environment, command and healthcheck values. ADR 0002 makes `config -q` the sanctioned model gate, so committing a rendered baseline revisits a decided ADR; epics story 2.4 owns the per-Module contract check that would replace it.
status: open

### DW-29: The architecture memlog still repeats the `docker/minio/` claim that ADR 0008's amendment refutes.
origin: spec-deferred c22e7f2396d6
location: _bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/.memlog.md:71
source_spec: `spec-2-2-the-remaining-core-modules.md`
severity: low
reason: _bmad-output/planning-artifacts/architecture/architecture-devinfra-2026-09-06/.memlog.md:71 records "AD-5 held - minio-data volume name and `docker/minio/` config path both unchanged". `git log --all -- docker/minio` is empty, so the claim is false there as it was in ADR 0008 and the README, both corrected by this story. Not corrected here because the memlog is a phase 1-3 planning artifact, upstream of this story's bounds, and this story's Tasks list scoped the correction to ADR 0008 and the README. It matters because a future architecture session reads the memlog first.
status: open

### DW-30: The live-stack half of this story's acceptance criteria was never exercised: the eight Services were never started from this working tree and `pixi run smoke` never ran.
origin: spec-deferred 833c0ba02094
location: spec Verification section (live-stack commands)
source_spec: `spec-2-3-the-admin-and-observability-modules.md`
severity: medium
reason: All thirteen devinfra-* containers are running from the main checkout, and every module pins a fixed `container_name`, so a second Compose project cannot start them at all without mutating the shipped files. Recreating them from this worktree would repoint their binds at a directory deleted when the run ends. What is proven instead: the rendered model is identical to the pre-change baseline apart from the seven relocated bind sources and the three deleted `x-*` extension blocks, and a new lint-config assertion proves all eleven rendered bind sources resolve on disk. Settling it needs one operator run of `docker compose --profile admin --profile observability up -d`, `./scripts/wait-healthy.sh` and `pixi run smoke` from the checkout that owns the live stack. Same shape as DW-23, which records the identical gap for story 2-2.
status: open

### DW-31: Nothing committed pins the assembled model's `depends_on` edges or per-service `command` flags, so a detail lost while transcribing eight service bodies ships green.
origin: spec-deferred b2c31d6b6a3a
location: scripts/lint-compose.sh; services/otel-collector/compose.yaml (depends_on)
source_spec: `spec-2-3-the-admin-and-observability-modules.md`
severity: medium
reason: `docker compose config -q` rejects an edge naming an undefined service but is blind to an edge simply deleted; deleting `- loki` from services/otel-collector/compose.yaml renders and validates clean, and the collector retries its exporter so smoke's Loki query still succeeds. Same for `--web.enable-remote-write-receiver` in services/prometheus: dropping it breaks Tempo's metrics_generator remote-write, which no smoke assertion observes. The before/after rendered diff proved it here but ran from a scratch directory and leaves no committed baseline. ADR 0002 makes `config -q` the sanctioned dependency gate, so committing a rendered baseline revisits a decided ADR; epics story 2.4 owns the per-Module contract check. Duplicates DW-25 and DW-28 in kind, now with eight more bodies behind it.
status: open

### DW-32: scripts/lint-compose.sh treats a successfully-read empty profile list as "no profiles", validating one combination and reporting OK.
origin: spec-deferred 81114d6f867c
location: scripts/lint-compose.sh:46-47
source_spec: `spec-2-3-the-admin-and-observability-modules.md`
severity: low
reason: scripts/lint-compose.sh:33-47 guards the case where `compose config --profiles` *fails*, and its comment says an unreadable enumeration is never treated as no profiles. A successful but empty read is not guarded: `count` is 0, `combinations` is 1, and the loop validates only the profile-less selection before printing OK. Pre-existing — the same vacuity existed while profiles were declared in the root file — but every profile now lives in a module file, so the model has more independent places to lose one. The literal member-set pins added to scripts/lint_selftest.py in this pass catch the realistic case (one service losing its key); this entry is the all-profiles-gone case.
status: open

### DW-33: AGENTS.md still stamps "Verified 2026-09-07 against e8971ad" although this change hand-edited three claims inside its managed block.
origin: spec-deferred 14d4bbe24aa7
location: AGENTS.md:2
source_spec: `spec-2-3-the-admin-and-observability-modules.md`
severity: low
reason: AGENTS.md:2 is the bmad-project-context managed-block header, which records the revision the block was verified against. The block's layout and "still inlined" claims were corrected here because leaving them false was worse than editing a managed region, but the stamp now names a revision that predates the edit. Routed to defer because the fix edits an agent-context file; a bmad-project-context refresh regenerates both the block and its stamp.
status: open

### DW-34: Host port numbers are declared in three places — .env.example, each module file's `${VAR:-N}` ports fallback, and now each module's `x-endpoints:` url — with nothing reconciling them; assert_pins.py
origin: spec-deferred eaf58395bf3a
location: scripts/assert_pins.py:60 (PIN regex); services/*/compose.yaml x-endpoints urls
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: low
reason: Confirmed by reading assert_pins.py's PIN regex, which matches version pins alone. The class is pre-existing — the compose fallbacks already duplicated .env.example unchecked — but this story adds seventeen more instances of it inside the very block written to end endpoint drift. All three sources agree today, verified against .env.example lines 62-170. Settling it is an extension of assert_pins.py's existing occurrences() / dotenv_declarations() machinery to `*_PORT`, which is a check this story did not own.
status: open

### DW-35: The bidirectional contract check reads module-file text, so a Compose service reaching the rendered model from anywhere but a `services/<dir>/compose.yaml` is not caught by this check.
origin: spec-deferred def4e9fa01b8
location: scripts/assert_config.py module_contract() — service-ownership rule
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: low
reason: Real. module_contract() asserts that every service key in a module file is `<dir>` or `<dir>-<role>`; closure at the rendered surface depends on the separate, pre-existing pin that the root compose.yaml declares no `services:` key (lint_selftest.py:646-654). A service arriving via compose.override.yaml — which the self-test's own defects table already plants for lint-compose — or via an `include:` outside services/ would satisfy the new check. The trade is argued in this spec's Design Notes: asserting it against the rendered model instead breaks a dozen stub-document fixtures that name services no directory owns on purpose. Settling it needs a rendered-model pass that tolerates those fixtures, most likely by keying off the real runtime run rather than the stubs.
status: open

### DW-36: The live-stack acceptance criteria were settled against containers running images that are stale relative to the pins, so the pinned loki and tempo tags were never actually run.
origin: spec-deferred 13cbc40f538d
location: spec Verification section (live-stack commands)
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: medium
reason: `pixi run smoke` reports 47 passed, 0 failed, 0 skipped from this worktree, and the baseline suite run against the same stack produces a byte-identical set of PASS labels — but `docker inspect` shows the running containers are grafana/loki:3.5.7 and grafana/tempo:2.9.0 while .env.example pins 3.7.7 and 3.0.3. Every module pins a fixed container_name and the live stack runs from the main checkout, so recreating from here would repoint its binds at a directory deleted when the run ends. The five added healthchecks were each executed inside their live container and in a throwaway container from the pinned image; the two healthcheck.none exemptions were settled by exporting the pinned image filesystems. What remains unrun is `docker compose --profile admin --profile observability up -d` followed by `./scripts/wait-healthy.sh` and `pixi run smoke` from the checkout that owns the live stack, on the pinned images. Same shape as DW-30, which records the identical gap for story 2-3.
status: open

### DW-37: The healthcheck exemption is a property of the pinned image tag, so a version bump can silently make a marker false without any check noticing.
origin: spec-deferred 5cdc4e8d9012
location: services/{loki,tempo,otel-collector}/healthcheck.none
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: low
reason: Directly demonstrated by this story: the plan called for real healthchecks on loki and tempo because the *running* 3.5.7/2.9.0 images carry /busybox/wget, while the pinned 3.7.7/3.0.3 images hold only their own binary. The same movement can go the other way — a future tag that regains a shell leaves a healthcheck.none standing that is no longer true, and lint-config only checks the marker is justified, never that the justification still holds. ADR 0012 records that the exemption must be re-verified on a version bump; nothing enforces it. Settling it needs a check that runs against the image, which is a runtime dependency the static lint surface deliberately does not have.
status: open

### DW-38: Nothing pins that the rendered Compose document carries no top-level `x-endpoints:` or `x-requires:` key.
origin: spec-deferred d81b29ec8fdb
location: scripts/assert_config.py module_contract(); docs/adr/0012
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: low
reason: Verified absent on Compose v5.3.0: the rendered document's top-level keys are name, networks, services and volumes only. No bad outcome is reachable on any supported Compose — assert_config.py reads the raw module files, as ADR 0012 now states it must. If a future Compose merged them, `x-requires.postgres` is declared by both keycloak and pgadmin with different value lists and one would silently win. A one-line self-test assertion over the rendered stub document would settle it.
status: open

### DW-39: The `x-requires:` reconciliation runs one direction only, so a Module that consumes another and declares nothing passes the contract in silence.
origin: spec-deferred 8acff1451fde
location: scripts/assert_config.py module_contract() — x-requires leg; services/grafana/compose.yaml:22-28
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: medium
reason: Confirmed by reading module_contract(): the loop walks `requires.items()`, so it can only judge entries that exist. Grafana is the live instance — it provisions a `postgres` datasource (services/grafana/conf/provisioning/datasources/datasources.yaml:76-79), reads POSTGRES_USER/PASSWORD/DB from its own environment, and its smoke.sh asserts `datasource 'postgres' connects` — while its `x-requires:` names only prometheus, loki and tempo and its `depends_on` omits postgres. `lint-config` is green. The omission is pre-existing (git show 30fdb19:services/grafana/compose.yaml has the same depends_on and the same POSTGRES_* environment), so this story did not cause it; what the story adds is a declaration mechanism that cannot catch it. The mirror rule — a `depends_on` edge onto another Module with no `x-requires:` entry naming it — would close it, but the smallest fix for grafana specifically is adding `depends_on: postgres`, which is a runtime-model change this story's constraints forbid.
status: open

### DW-40: `scripts/urls.sh` is not reconciled against the `x-endpoints:` blocks that now supersede it, and already omits three ports those blocks declare.
origin: spec-deferred 2afb77eb0732
location: scripts/urls.sh:14-28; scripts/lint_selftest.py:1125-1160
source_spec: `spec-2-4-every-module-carries-its-own-contract-enforced.md`
severity: medium
reason: Verified: the union of `x-endpoints:` keys across the thirteen Modules is seventeen variables; `scripts/urls.sh` sets defaults for fourteen and omits LOKI_PORT, TEMPO_PORT and KEYCLOAK_MGMT_PORT — so `pixi run urls` prints no Loki, Tempo or Keycloak-management endpoint while `lint-config` certifies those three entries as complete. The drift is pre-existing, but enforcing the new list without reconciling the old one makes further divergence silent: a fourteenth port declared tomorrow (as lint-config now forces) and forgotten in urls.sh keeps `pixi run ci` green. The only urls case, lint_selftest.py:1125-1160, asserts a hardcoded twelve-name / fourteen-default tuple derived from nothing, so it encodes the drift rather than detecting it. ADR 0012 states the decision explicitly ("scripts/urls.sh is left alone rather than half-migrated") and assigns generation to story 3-3; the cheap interim is a self-test case comparing the union of `x-endpoints:` keys against urls.sh's text.
status: open

### DW-41: The 'dashboard provisioned from the bind mount' assertion issues a single un-retried curl, with no readiness allowance for Grafana's dashboard provisioning scan.
origin: spec-deferred d93021fa5c76
location: services/grafana/smoke.sh:52
source_spec: `spec-3-1-grafana-opens-on-working-dashboards.md`
reason: Two layers filed it; neither could be verified without a live stack, which this environment cannot start (another session owns the `devinfra` project's container names and host ports). Against it: the four datasource-provisioning assertions immediately above it are built the same way, have never been retried, and are green in CI — datasources and dashboards are loaded by the same provisioning service at Grafana startup. For it: `updateIntervalSeconds: 30` means a dashboard the first scan missed is 30s away, and `ci-stack-cycle` now runs this assertion a second time right after a restart, doubling any exposure. What would settle it: one `ci-stack` / `ci-stack-cycle` run against a cold Grafana volume, or a deliberate delay injected into the provisioning scan. If it does prove flaky, the fix is the same `await_url` the datasource health calls already use.
status: open

### DW-42: The new Postgres data-directory assertion accepts any non-`/` mount, so an anonymous volume created by the image's own VOLUME declaration would satisfy it while `down` still discards the data.
origin: spec-deferred 24f2efefe4c5
location: services/postgres/smoke.sh:49
source_spec: `spec-3-2-gotchas-become-a-maintained-register.md`
reason: Two layers filed it. What is verified: the assertion is accurate as described — it proves the server's `data_directory` sits inside some mount rather than on the container's writable layer, and it does catch the plain form of the gotcha (a wrong PGDATA path for the major version leaves the directory under `/`, which the check excludes deliberately). What is not verified: whether a PostgreSQL 18 image, whose VOLUME is declared at `/var/lib/postgresql`, would have Docker create an anonymous volume covering `/var/lib/postgresql/18/docker` — which would appear in /proc/mounts and satisfy the check while a container recreate still loses the data. Settling it needs one run against a pg18 image with the current `postgres-data:/var/lib/postgresql/data` mount left in place, which this environment could not do (the shared `devinfra` stack is pinned to pgvector/pgvector:0.8.6-pg17 and is owned by another session). If it proves real, the fix is to tie the covering mount to the named volume rather
status: open

### DW-43: AGENTS.md:29 wraps at 118 characters where every neighbouring line in that bullet wraps at 97-105; the Module-contract edit kept the old line's tail.
origin: spec-deferred 0503a9c5b9d2
location: AGENTS.md:29
source_spec: `spec-3-2-gotchas-become-a-maintained-register.md`
severity: low
reason: Confirmed by measuring the file: lines 22-28 and 30-32 are 97-105 characters, line 29 is 118. Cosmetic, and the fix edits an agent-context file, which this workflow routes to deferral rather than patching inside a story.
status: open

### DW-44: Nothing checks the reverse direction of ADR 0003's registry: an unprefixed name in .env.example that no `x-app-variables:` entry registers and no exemption names is accepted silently.
origin: spec-deferred 9e5db4974fda
location: scripts/endpoints.py (read_registry), compose.yaml x-app-variables comment
source_spec: `spec-3-3-connection-details-generated-not-hand-maintained.md`
severity: low
reason: Verified by reading `read_registry()` in scripts/endpoints.py: every check runs registry -> catalog (the named Module must exist, the named endpoint key must be one that Module publishes, the name must not carry another Module's prefix). Nothing runs dotenv -> registry. ADR 0003 says the registry is "the only place such a name becomes legal", and the root compose.yaml comment names three deliberate exemptions (BIND_ADDRESS, COMPOSE_PROJECT_NAME, COMPOSE_PROFILES) — both statements are prose only. Closing it needs the exemption list to become data, which is ADR 0003 enforcement rather than this story's endpoint documentation.
status: open

### DW-45: `make urls` cannot take a Selection, though the listing it forwards to is now Selection-scoped and every other narrowable Makefile target forwards a variable.
origin: spec-deferred b8b639ca9565
location: Makefile:88
source_spec: `spec-3-3-connection-details-generated-not-hand-maintained.md`
severity: low
reason: Confirmed at Makefile:88-91: the recipe is `@$(NOTICE)` plus `@pixi run urls`, with no `$(S)`-style forwarding, while logs/psql/redis-cli all carry one. The help text was corrected in this pass; the forwarding was not. scripts/lint_selftest.py asserts set equality between the Makefile's recipes and MAKE_FORWARDS, and its own comment states that list is every target the Makefile exposed before pixi — a frozen deprecated surface. Adding forwarding is a decision about that surface, not about this story.
status: open

### DW-46: A *mutual* swap of two Contents rows' ports still passes the README pin, because every Module remains claimed exactly once.
origin: spec-deferred 5639993a8c14
location: scripts/endpoints.py (check_readme)
source_spec: `spec-3-3-connection-details-generated-not-hand-maintained.md`
severity: low
reason: Verified against the patched `check_readme`: it resolves each row's port literals to one owning Module and refuses two rows claiming the same Module, which catches the one-sided swap the reviewer demonstrated (Prometheus's 9090 in the Grafana row). A two-sided swap leaves the multiset of owners unchanged and is undetectable without mapping each row's display name to its Module directory (`Silo` -> `minio`, `PostgreSQL` -> `postgres`, `OTel Collector` -> `otel-collector`) — a hand-maintained second list, which ADR 0017 rejects by name. Recorded in the function's docstring and left to review.
status: open

### DW-47: The `stack` CI job now performs three full stack bring-ups inside an unchanged 15-minute budget, and nothing has measured whether it still fits.
origin: spec-deferred 99a8e3a9e0cd
location: .github/workflows/ci.yml (the stack job), pixi.toml [tasks.ci-stack-restore]
source_spec: `spec-3-4-backup-covers-everything-stateful.md`
reason: `.github/workflows/ci.yml` gives the `stack` job `timeout-minutes: 15` and now runs `ci-stack` (init, start, wait, smoke-strict), `ci-stack-cycle` (down, start, wait, smoke-strict) and `ci-stack-restore` (backup, destroy, start, wait, restore, wait, smoke-strict) in sequence. The budget cannot simply be raised: the workflow header states the 15-minute rule and `scripts/lint_selftest.py` asserts every job carries a `timeout-minutes` at or below 15, so a breach means tiering the work across jobs rather than extending the clock. Settled by one hosted run of the `stack` job: read its wall-clock time, and if it is near the cap, split the round trip into its own job that brings up its own stack.
status: open

### DW-48: Nothing has run the backup round trip against a real runtime; the only end-to-end proof is a CI job that has not executed yet.
origin: spec-deferred d7674288c89e
location: scripts/verify-restore.sh, scripts/restore.sh
source_spec: `spec-3-4-backup-covers-everything-stateful.md`
severity: medium
reason: The capture half was exercised for real in this session against the running stack — five database dumps with `DROP DATABASE IF EXISTS` / `CREATE DATABASE`, three buckets with the empty ones preserved, the realm JSON, a correct manifest — and the Postgres and object-storage restore mechanics were each verified against throwaway containers. `scripts/verify-restore.sh` itself, and the orchestration in `scripts/restore.sh` (stop, apply, mirror, start, wait), have run only against the recording stub, because the round trip destroys every volume and the container stack on this machine holds the operator's own data. Settled by one green `stack` job in CI, or by `pixi run ci-stack` followed by `pixi run ci-stack-restore` on a machine whose volumes are expendable.
status: open

### DW-49: Which Modules are stateful is three literal branches in `backup.sh`, so a fourteenth stateful Module would be captured by nothing and named by no manifest line.
origin: spec-deferred 4f7cbfd3b440
location: scripts/backup.sh, scripts/restore.sh, docs/adr/0012 (the Module contract)
source_spec: `spec-3-4-backup-covers-everything-stateful.md`
severity: low
reason: `scripts/backup.sh` asks `selected postgres`, `selected minio` and `selected keycloak` in three hand-written branches, and `scripts/restore.sh` iterates the same three names. This is defensible today because each component has a bespoke capture mechanism — `pg_dump`, `mc mirror`, `kc.sh export` — and no generic one exists. It is still a hand-maintained statement of the catalog of the kind ADR 0018 rejects for bucket and database lists: a new stateful Module is silently uncaptured, and the manifest does not even record it as skipped. Closing it needs a per-Module backup contract (an `x-backup:` block, or a `services/<module>/backup.sh` the way `smoke.sh` works), which is a Module-contract change beyond this story.
status: open

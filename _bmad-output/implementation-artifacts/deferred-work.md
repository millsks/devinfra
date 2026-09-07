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

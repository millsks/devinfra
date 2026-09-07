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

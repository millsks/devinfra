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

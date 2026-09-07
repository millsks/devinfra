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

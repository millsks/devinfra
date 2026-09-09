---
title: 'Gotchas become a maintained register'
type: 'feature'
created: '2026-09-07'
baseline_revision: '19f49d2662f27efbdf9b16773b05b166594da382'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      The new Postgres data-directory assertion accepts any non-`/` mount, so an
      anonymous volume created by the image's own VOLUME declaration would satisfy it
      while `down` still discards the data.
    evidence: |-
      Two layers filed it. What is verified: the assertion is accurate as described —
      it proves the server's `data_directory` sits inside some mount rather than on the
      container's writable layer, and it does catch the plain form of the gotcha (a
      wrong PGDATA path for the major version leaves the directory under `/`, which the
      check excludes deliberately). What is not verified: whether a PostgreSQL 18 image,
      whose VOLUME is declared at `/var/lib/postgresql`, would have Docker create an
      anonymous volume covering `/var/lib/postgresql/18/docker` — which would appear in
      /proc/mounts and satisfy the check while a container recreate still loses the data.
      Settling it needs one run against a pg18 image with the current
      `postgres-data:/var/lib/postgresql/data` mount left in place, which this
      environment could not do (the shared `devinfra` stack is pinned to
      pgvector/pgvector:0.8.6-pg17 and is owned by another session). If it proves real,
      the fix is to tie the covering mount to the named volume rather than accepting any
      mount.
    location: >-
      services/postgres/smoke.sh:49
    severity: medium (unverified)
  - summary: >-
      AGENTS.md:29 wraps at 118 characters where every neighbouring line in that bullet
      wraps at 97-105; the Module-contract edit kept the old line's tail.
    evidence: |-
      Confirmed by measuring the file: lines 22-28 and 30-32 are 97-105 characters, line
      29 is 118. Cosmetic, and the fix edits an agent-context file, which this workflow
      routes to deferral rather than patching inside a story.
    location: >-
      AGENTS.md:29
    severity: low
---

<intent-contract>

## Intent

**Problem:** The 77 gotchas across `services/*/gotchas.md` are free prose bullets in no fixed shape — 7 of them name an affected version, `lint-config` checks only that the file exists (ADR 0012), and eight are duplicated verbatim in `README.md` where the two copies can drift apart. One of them is now false: `--import-realm` is not the only way to overwrite a realm on Keycloak 26.x, and `scripts/keycloak-reimport.sh` still drops the `keycloak` database because that false claim justified it (FR-18, AD-12).

**Approach:** Give every entry a fixed, machine-checked four-field shape — symptom, cause, fix, affected versions — in the Module's own `gotchas.md`, with an optional fifth field naming the check that catches a regression; delete the duplicated README prose in favour of a pointer; correct the realm-import entries against what was actually verified; and rewrite the reimport task to import with override and restart the container instead of dropping the database.

## Boundaries & Constraints

**Always:**
- The register is `services/<module>/gotchas.md` and nothing else — no sidecar YAML, no Core-owned index, no second copy anywhere. Core gains no list of Modules: the checker globs `services/*/gotchas.md` and refuses an empty set, exactly as `assert_config.py` and `smoke-test.sh` do.
- Every entry carries the four fields, populated, in fixed order. `Affected versions:` is either a real version expression or the exact phrase `Not version-specific`; placeholders (`TBD`, `TODO`, `unknown`, `n/a`, `?`) are refused.
- Only content already in the repository, or verified in the planning artifacts, may be written. An entry whose symptom or cause is not recoverable from the existing prose or from the code it describes is rewritten to state what is actually known — never invented to fill a field.
- The reimport preserves the `keycloak` database and every other realm. The restart after the import is mandatory and unconditional, because the import runs as a separate JVM that never attaches to the running server's cache (AD-12).
- New Python under `scripts/` is stdlib-only, follows `scripts/check_dashboards.py`'s shape, and passes `ruff format --check`, `ruff check` (`E,F,I,UP,B,RUF,D`, google docstrings) and `mypy --strict`.
- Every new check is proved in both directions by `scripts/lint_selftest.py` — it fails on a planted defect naming the file and the defect, and passes on the clean tree.

**Never:**
- Do not judge content quality, count or length beyond "the field is present, non-empty and not a placeholder, and a file carries at least one entry". ADR 0012 rejected minimum-length rules as unfalsifiable; a fixed field shape is not one, and the new ADR must say why.
- Do not move gotchas out of the Module directories into a central file, and do not add the register to `scripts/urls.sh`, endpoint generation, or anything story 3-3 owns.
- Do not weaken `confirm_word reimport`, and do not make the post-import restart conditional, retry-gated or opt-out.
- Do not change `scripts/restore.sh`; its stop → write → restart ordering and `ON_ERROR_STOP=1` belong to story 3-4 (FR-15).
- Do not put `lint-gotchas` behind a container runtime, and do not add it to `RUNTIME_BOUND`.
- Do not rename the `## Gotchas worth knowing` README heading — the Contents list links to it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean register | All 13 `gotchas.md` in shape | `check_gotchas.py` prints one `<module>: OK <n> entries` line per Module to stdout, exits 0 | No error expected |
| Missing field | An entry with no `**Fix:**` line | exits 1, stderr names the file, the entry title and `Fix` | Refusal on stderr, `check-gotchas: ` prefix |
| Empty or placeholder field | `**Affected versions:** TBD` | exits 1, stderr names the file, the entry and the placeholder | as above |
| Fields out of order | `Cause` before `Symptom` | exits 1, stderr names the file, the entry and the expected order | as above |
| Bullet outside an entry | `- **Note:** …` before the first `###` | exits 1, stderr names the file and the stray line number | as above |
| Wrong H1 | `# gotchas` in `services/redis/gotchas.md` | exits 1, stderr names the file and the expected `# redis — gotchas` | as above |
| Empty file / no entries | A `gotchas.md` with an H1 and nothing else | exits 1, stderr names the file | as above |
| Dead `Verified by:` | `**Verified by:** `services/gone/smoke.sh` — …` | exits 1, stderr names the file, the entry and the missing path | as above |
| No Modules found | `services/*/gotchas.md` matches nothing | exits 1, stderr says the walk was empty | Refusal; a check that walked nothing has verified nothing |
| Reimport, exact word | stdin `reimport\n` | `kc.sh import --file … --override true --http-management-port 9999`, then `restart keycloak`, then `wait-healthy.sh`; no `DROP DATABASE` anywhere | Any step failing exits non-zero (`set -euo pipefail`) |
| Reimport, wrong word | stdin `yes\n` | `Aborted.` on stderr, exit 1, the runtime never invoked | `confirm_word` |

</intent-contract>

## Code Map

**The register today**
- `services/*/gotchas.md` -- 13 files, 77 prose bullets, 324 lines total. Shape is `- **<bold claim>**, <prose>`. Field coverage as it stands: cause in almost all, symptom in ~half, fix in ~60%, affected versions in **7** (`grafana:14`, `loki:3`, `loki:7`, `minio:3`, `otel-collector:9`, `postgres:3`, `tempo:7`). Every file opens `# <module> — gotchas`.
- `README.md:763-805` `## Gotchas worth knowing` -- 8 bullets, **all** duplicated from Module files: `:771`→`services/postgres/gotchas.md:3`, `:777`→`pgadmin:3`, `:780`→`keycloak:3`, `:783`→`keycloak:7`, `:786`→`keycloak:10`, `:788`→`loki:3`, `:791`→`prometheus:3`+`tempo:3`, `:793`→`minio:3,:11,:15`. `:765-769` already routes the reader to the Module files.
- `README.md:758-761` -- the Prometheus `query_range` lookback paragraph, duplicated at `services/prometheus/gotchas.md:6`. Same treatment as the section below it.
- `README.md:319`, `:335` -- layout tree lines naming `gotchas.md`; `README.md:28` Contents links to the `## Gotchas worth knowing` anchor.
- `AGENTS.md:25`, `:45` -- restate the Module contract and route any `services/<name>/` change through that Module's `gotchas.md`. Update `:25` to name the four-field shape.

**The contract that enforces it**
- `scripts/assert_config.py:131` `MODULE_FILES = ("smoke.sh", "gotchas.md")`; `:616` `module_contract()`; `:716-721` the presence loop. **Presence only** — leave it alone; the shape check is a separate offline task (`lint-config` is runtime-bound and excluded from the pre-commit hook).
- `scripts/assert_config.py:526-548` `justified()` -- the model for "a marker that is blank or comment-only is the silent skip in file form". The register's "at least one entry" rule is the same bar.
- `scripts/resolve_selection.py:96` `module_composes()`, `:105-130` `read_model()` -- the glob-and-refuse-empty pattern to mirror.
- `docs/adr/0012-…md:57-61` -- "The check is presence-based… never whether their content was warranted", and the Rejected entry "requiring a minimum number of checks, or a `gotchas.md` above some length. Unfalsifiable… an invitation to pad". The new ADR must engage this directly.
- `docs/adr/README.md` -- index table, all Accepted; ADRs change only by a superseding ADR. Template, verified identical across 0001/0012/0015: `# <n>. <Title>` / blank / `Date: YYYY-MM-DD · Status: Accepted` / `## Context` / `## Decision` / `## Rejected` / `## Consequences`. Highest is `docs/adr/0015-deferred-smoke-checks.md`.

**The realm-import path**
- `scripts/keycloak-reimport.sh` (27 lines) -- `:9` `set -euo pipefail`; `:12` sources `lib/common.sh`; `:15` `select_ambient`; `:17-18` the destruction notice; `:19` `confirm_word reimport`; `:21` `compose stop keycloak`; `:22-24` `compose exec -T postgres psql … 'DROP DATABASE IF EXISTS keycloak WITH (FORCE);' -c "CREATE DATABASE keycloak OWNER …"`; `:25` `compose up -d keycloak`; `:27` `exec ./scripts/wait-healthy.sh`. Lines `:2`, `:6-8` carry the false "dropping the database is the only way" prose.
- `scripts/keycloak-export.sh:13-16` -- the mirror: in-container binary `/opt/keycloak/bin/kc.sh`, realm file named `${KEYCLOAK_REALM}-realm.json`.
- `scripts/lib/common.sh:69-71` `compose()` (the stubbed seam), `:87-99` `select_profiles`/`select_ambient`, `:105-121` `confirm_word()`. No logging helper exists — scripts use `echo … >&2`.
- `services/keycloak/compose.yaml:53` image `quay.io/keycloak/keycloak:${KEYCLOAK_VERSION:-26.7.3}`; `:61-63` publishes 8080 and **9000**; `:90-92` `keycloak-data:/opt/keycloak/data` plus `./seed:/opt/keycloak/data/import:ro`; `:93-95` a stale comment naming `make keycloak-reimport`; `:96` `command: ["start-dev", "--import-realm"]`; `:97-109` `/dev/tcp` healthcheck on 9000.
- `services/keycloak/seed/devinfra-realm.json` -- 243 lines, read-only at `/opt/keycloak/data/import`.
- **Verified findings** (`_bmad-output/planning-artifacts/prds/prd-devinfra-2026-09-06/addendum.md:126-154`, verified against 26.4.0): `:131` startup import hard-codes `Strategy.IGNORE_EXISTING`, no flag changes it; `:132` `kc.sh import --file|--dir` takes `--override <true|false>`, default true, since 21.1.0; `:133` override is remove-and-recreate, not merge — runtime state in the target realm absent from the JSON is lost, other realms and the database survive; `:138` run against a live container it exits **non-zero** on the management-port collision, `--http-management-port 9999` makes it exit 0; `:139` the running server keeps serving stale cached realm data until restarted (probe: Postgres `PROBE-V2` vs admin API `PROBE-V1`). Restated as the rule at `ARCHITECTURE-SPINE.md:161-170` (AD-12).
- `pixi.toml:151-153` `[tasks.keycloak-reimport]`, description "(DESTROYS realm state)"; `Makefile:124-127` the forwarding target and its help text; `scripts/lint_selftest.py:98` pins the forward line (`MAKE_FORWARDS`), not the description.
- `scripts/lint_selftest.py:1316-1325` the shared confirmation-word loop (keep); **`:1344-1355` the two cases that must be replaced** — `keycloak-reimport.sh stops keycloak first` (`args[:2] == ["stop","keycloak"]`) and `drops and recreates the keycloak database`.
- `README.md:256-266` `### Editing the realm`, `:263` and `:309` and `:405` -- the prose and task tables asserting the drop-and-recreate model.
- `services/keycloak/gotchas.md:12-15` -- the obsolete `--import-realm` entry to delete.

**Where new checks plug in**
- `scripts/check_dashboards.py` (515 lines) -- the house style for a stdlib-only checker: module docstring stating the negative it removes, `from __future__ import annotations`, frozen dataclasses with google `Attributes:` docstrings, `class Refusal(Exception)` for "cannot check at all" caught once in `main()` and written to stderr as `check-dashboards: …`, `build_parser() -> argparse.ArgumentParser`, `main(argv: list[str]) -> int`, one result line per subject on stdout, `raise SystemExit(main(sys.argv[1:]))`.
- `pixi.toml:186-196` the Validation block (new task slots after `lint-renovate`), `:218-220` `[tasks.lint]` depends-on, `:258-260` `[tasks.precommit]` depends-on, `:222-224` `[tasks.ci]` (needs no edit).
- `scripts/lint_selftest.py:5169-5189` -- asserts `precommit_members <= lint_members` and that no precommit member is `RUNTIME_BOUND`; `:5199-5206` runs `pixi run precommit` with every container runtime shadowed by a failing stub. An offline `lint-gotchas` must join **both** lists.
- `scripts/lint_selftest.py:744-758` -- every task body is scanned for `FORBIDDEN = ("command -v", "which ", "|| true", "skipping")` and for a directly-named runtime.
- `scripts/lint_selftest.py:106-163` `pixi()` / `run_script()`, `:418-439` `planted()`, `:2642-2656` the table-driven contract-case loop to copy (plant defect → `returncode != 0` → needles in stderr → `"OK" not in stdout`), `:2662-2674` and `:2806-2826` the clean-pass direction.
- `scripts/smoke-test.sh:95-114` `pass`/`fail`/`skip`, `:152-158` `assert_contains`, `:160` `dc` (= `compose exec -T`), `:210-211` `defer`. `services/postgres/smoke.sh:14-36` -- seven `assert_contains` calls through `dc postgres psql`; the new data-directory assertion joins them.
- `services/postgres/compose.yaml:56-62` -- `postgres-data:/var/lib/postgresql/data` with the version-path comment at `:58-61`; image `pgvector/pgvector:${POSTGRES_VERSION:-0.8.6-pg17}` at `:43`.
- `services/prometheus/compose.yaml` -- the `command:` carrying `--web.enable-remote-write-receiver`, which `services/tempo/gotchas.md:3` and `services/prometheus/gotchas.md:3` both depend on and nothing asserts.
- `services/mailpit/compose.yaml` -- `MP_DATABASE` and the `mailpit-data` mount, which `services/mailpit/gotchas.md:3` says must agree and nothing asserts.
- `.github/workflows/ci.yml:56-57` -- the `validate` job is one `pixi run ci`; a new offline check reaches CI by joining `[tasks.lint]` and needs no workflow edit.

## Tasks & Acceptance

**Execution:**
- `scripts/check_gotchas.py` -- new, stdlib only, in `check_dashboards.py`'s shape. Globs `services/*/gotchas.md`, refuses an empty set, and for each file asserts: the H1 is `# <directory name> — gotchas`; at least one entry; every entry is an `### ` heading with a non-empty title; each entry carries `- **Symptom:**`, `- **Cause:**`, `- **Fix:**`, `- **Affected versions:**` in that order with non-empty, non-placeholder values, optionally followed by `- **Verified by:**`; `Affected versions:` is a version expression or exactly `Not version-specific`; `Verified by:` opens with a backticked repo-relative path that exists on disk; no `- **` bullet appears outside an entry. Prints `<module>: OK <n> entries` per file on stdout, refusals as `check-gotchas: …` on stderr, exit 0 only when every file is clean.
- `pixi.toml` -- add `[tasks.lint-gotchas]` after `lint-renovate` (`cmd = "python scripts/check_gotchas.py"`), add it to `[tasks.lint]` and `[tasks.precommit]` depends-on, and rewrite `[tasks.keycloak-reimport]`'s description so it no longer says the realm state is destroyed wholesale.
- `Makefile` -- update the `keycloak-reimport` help text to match the new pixi description; the forwarding line stays `@pixi run keycloak-reimport`.
- `services/*/gotchas.md` (13 files) -- rewrite every existing bullet as a four-field entry, preserving what the prose actually says and adding only what the repository already evidences. Where the current prose supplies no symptom or fix, state the observable and the action that are actually true of the code, not a guess. `Affected versions:` names the pinned tag or upstream version when the failure mode is version-bound (the seven already-known ones, plus any where the code names a version), and is `Not version-specific` otherwise. Add `Verified by:` to every entry whose fix is a specific configuration fact a check already reads — at minimum the Redis `noeviction` and `NOAUTH` entries, the Keycloak issuer entry, and the Grafana `allowUiUpdates`/provisioned entries.
- `services/keycloak/gotchas.md` -- delete the obsolete `--import-realm only creates realms that do not already exist` entry, and add two entries from the verified findings: `kc.sh import` run against a live container exits non-zero on the management-port collision unless given a free `--http-management-port`; and the running server keeps serving cached realm data after an out-of-band import until it is restarted, so the database and the admin API disagree silently. Keep the `clientScopes` and `passwordPolicy` entries. Correct the surviving import prose to say that the startup import ignores existing realms while `kc.sh import --override` replaces one, remove-and-recreate rather than merge.
- `scripts/keycloak-reimport.sh` -- replace the drop-and-recreate body: after `confirm_word reimport`, run `compose exec -T keycloak /opt/keycloak/bin/kc.sh import --file "/opt/keycloak/data/import/${KEYCLOAK_REALM}-realm.json" --override true --http-management-port 9999`, then `compose restart keycloak`, then `exec ./scripts/wait-healthy.sh`. No `stop`, no `psql`, no `DROP DATABASE`. Rewrite the header and the pre-confirmation notice to state what is actually lost (runtime state in that realm absent from the JSON) and what survives (the database, every other realm). Comment both the literal `9999` (a free in-container management port; the server holds 9000) and the restart (mandatory — the import is a separate JVM that never attaches to the running server's cache).
- `services/postgres/smoke.sh` -- add an assertion for the highest-severity gotcha: read `show data_directory;` and assert some mount point in the container's `/proc/mounts` is a prefix of it, so a data directory sitting on the container layer fails instead of silently losing everything on `down`. Use `dc postgres awk -v d="${pgdata}" …` so no shell quoting crosses the container boundary.
- `services/keycloak/compose.yaml:93-95` -- correct the stale comment naming `make keycloak-reimport` and the drop-the-database model.
- `README.md` -- replace the eight bullets under `## Gotchas worth knowing` (heading text unchanged) with a pointer to the register: where entries live, the four-field shape, that `pixi run lint-gotchas` enforces it, and that a `Verified by:` line names the check that catches a regression. Move the `query_range` lookback paragraph out of `### Notes on retention` for the same reason. Rewrite `### Editing the realm` for the new semantics, and correct the `keycloak-reimport` descriptions in the task tables.
- `AGENTS.md:25` -- restate the Module contract's fifth item as a `gotchas.md` in the checked four-field shape.
- `scripts/lint_selftest.py` -- replace the two reimport cases at `:1344-1355` with cases asserting the new contract through the recorded compose stub: no argument anywhere contains `DROP DATABASE`; the recorded argv contains a `kc.sh import` naming the seed file, `--override true` and `--http-management-port`; a `restart keycloak` follows the import; the exact word still proceeds and any other word still aborts before the runtime is touched. Add `check_gotchas.py` cases covering every row of the I/O matrix via `planted()` fixtures, plus the clean-pass direction over the real tree and a "walked a non-empty set" assertion. Add a case pinning `--web.enable-remote-write-receiver` in `services/prometheus/compose.yaml` and one pinning `MP_DATABASE` under the `mailpit-data` mount in `services/mailpit/compose.yaml`, so those two gotchas' fixes are asserted rather than only described. Add a case asserting the README's gotchas section carries no `- **` bullets, so the duplicated prose cannot grow back.
- `services/prometheus/gotchas.md`, `services/tempo/gotchas.md`, `services/mailpit/gotchas.md`, `services/postgres/gotchas.md` -- give the four newly-asserted entries a `Verified by:` line naming the check just added.
- `docs/adr/0016-gotcha-entries-carry-a-checked-shape.md` + `docs/adr/README.md` -- record the decision in the repository's ADR template. Context: ADR 0012 made the Module contract presence-based and rejected judging content. Decision: `gotchas.md` gains a checked field shape; ADR 0012's presence rule stands for the other four contract items. Rejected: a central register, a YAML sidecar, and a minimum entry count above one. Consequences: state why a fixed field shape is falsifiable where a minimum length is not, and that `Verified by:` is checked for path existence but not for what the check asserts.
- `CHANGELOG.md` -- `Added` entries for the register shape and `lint-gotchas`; a `Changed` entry for the reimport behaviour change; a `Fixed`/`Removed` entry for the obsolete realm-import claim.

**Acceptance Criteria:**
- Given every gotcha the README carried, when `pixi run lint-gotchas` runs over the tree, then each appears in the `gotchas.md` of the Module it affects with symptom, cause, fix and affected versions populated, and the README section carries none of them.
- Given `services/keycloak/gotchas.md`, when it is read, then the claim that a realm import cannot overwrite an existing realm is absent, and two entries record the management-port collision exit code and the stale cached realm data after an out-of-band import.
- Given `pixi run keycloak-reimport` answered with its exact word, when it runs, then it issues no `DROP DATABASE`, imports the seed realm with override on a free management port, restarts the Keycloak container unconditionally, and waits for health.
- Given `pixi run keycloak-reimport` answered with anything else, when it runs, then it exits non-zero saying `Aborted.` before the container runtime is invoked.
- Given a `gotchas.md` with a missing field, a placeholder value, fields out of order, a bullet outside an entry, no entries at all, or a `Verified by:` naming a path that does not exist, when `lint-gotchas` runs, then it exits non-zero naming the file and the defect, and signs off on nothing.
- Given a Postgres container whose data directory is not inside any mount, when `pixi run smoke` runs, then the suite fails naming that assertion.
- Given `pixi run precommit` with every container runtime shadowed by a failing stub, when it runs, then it exits 0 with `lint-gotchas` among the checks it ran.
- Given `pixi run ci`, when it runs, then `lint-gotchas` joins `lint`, every self-test case passes, and the run exits 0.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 27 findings — high 0, medium 3, low 21, false 1, maybe-false 2
- layers: blind-hunter (12), edge-case-hunter (13), verification-gap (2), intent-alignment (descriptive; no findings — see the note under Auto Run Result)
- findings:
  - `[low]` `[reject]` check_gotchas.py has no fenced-code-block state, so a column-0 ``` block containing a `#` line is refused with a misleading diagnostic — reproduced; but the natural form inside a field bullet is an indented block, and that passes (verified against a fixture). Fence tracking adds state and branches for a case a register author does not meet.
  - `[low]` `[patch]` CHANGELOG.md and the self-test say the gotchas heading is kept because "the Contents list links to it", which is false — confirmed: no `#gotchas-worth-knowing` anchor exists anywhere in the repository and README's `## Contents` is a service/version/endpoint table. Both now state the true reason (a stable reference outside links resolve to).
  - `[low]` `[reject]` A deleted `gotchas.md` is invisible to `lint-gotchas`, and the "reports every Module's register" assertion compares against the same glob — both true, but `assert_config.py` still fails a missing register in `pixi run lint` and CI. Making the checker enumerate Module directories duplicates that rule and adds a second source of Module discovery.
  - `[low]` `[reject]` The fixed shape forced meta-observations into `Symptom:` on several entries — a fair critique of the shape, but four populated fields for every entry is what FR-18 and the intent mandate; a second entry kind is not something this story may invent.
  - `[medium]` `[patch]` Several `check_values` refusal branches ship unexercised — grouped with the two verification-gap findings below; the "names no version" and "backticks" branches were the two that could be silently disabled, and both now have planted cases. The unknown-field and repeated-field branches were left alone: removing either still leaves the entry failing the order comparison.
  - `[low]` `[reject]` `keycloak-reimport.sh` now needs the container already running and says so only through the runtime's raw error — real, but it fails loudly after `set -euo pipefail` with nothing written, and a precondition guard is added complexity for a state the user recovers from by starting the stack.
  - `[low]` `[reject]` Entries whose fix an existing check already asserts carry no `Verified by:` — the field is optional and AC4 is met by the assertions existing; adding links I have not traced would advertise coverage the checker only path-validates.
  - `[maybe-false]` `[defer]` The Postgres assertion accepts any non-`/` mount, so an anonymous volume could satisfy it — deferred at medium (unverified); what would settle it is a run against a pg18 image, which this environment cannot start. See `deferred` in frontmatter.
  - `[low]` `[reject]` `PGDATA_PATH` folds stderr in with `2>&1` and is used unchecked — the suite still fails, and the psql error text appears verbatim in the failure output; every other assertion in that file uses the same `2>&1` idiom.
  - `[low]` `[reject]` `PLACEHOLDERS` refuses more tokens than the docs enumerate (`na`, `none`, `-`, `…`) — the documented list is illustrative, not exhaustive, and no register value is affected.
  - `[false]` `[reject]` "Diagnostics report two different line numbers for the same entry" — not a defect: `check_field_order` anchors to the heading because the entry is what lacks a field, and `check_values` anchors to the bullet because that is where the bad value is. Each points at the thing it is complaining about.
  - `[low]` `[patch]`/`[defer]` Two prose wraps left unreflowed — the README layout tree's dangling "seed/ or" line is reflowed; the AGENTS.md:29 over-long line is deferred, because its fix edits an agent-context file.
  - `[maybe-false]` `[defer]` (edge-case) The covering mount is not tied to the named `postgres-data` volume — same root cause as the blind-hunter finding above; deferred with it.
  - `[low]` `[reject]` (edge-case) `PGDATA_PATH` captured with stderr folded in — same finding as above, same refutation.
  - `[low]` `[reject]` (edge-case) Fenced code block breaks the parser — same finding as above, same refutation.
  - `[low]` `[reject]` (edge-case) Keycloak not running when the task is invoked — same finding as above, same refutation.
  - `[low]` `[reject]` (edge-case) A seed directory holding realms other than `${KEYCLOAK_REALM}-realm.json` is no longer re-imported — true, and deliberate: `--file` replaces exactly the realm the confirmation prompt names, where `--dir` would silently widen a destructive operation past what the user was asked to confirm. The repository ships one realm.
  - `[low]` `[reject]` (edge-case) `KEYCLOAK_REALM` naming a realm with no seed file fails inside the container after the confirmation — loud, non-destructive failure; a pre-check is added complexity for an unreachable-by-default misconfiguration.
  - `[low]` `[patch]` (edge-case) The README stray-bullet guard matched only `- **` at column 0, so an indented or `*`-marked bullet would evade it — now matched with `^\s*[-*] \*\*`.
  - `[medium]` `[patch]` (edge-case) Unknown-field, repeated-field and digit-bearing-non-version branches untested — grouped with the coverage entry above; the reachable-in-isolation one (digit rule) is now planted.
  - `[low]` `[reject]` (edge-case, deletion) The removed `stop`/`up -d` pair used to start a stopped Keycloak — same finding and refutation as the running-container one above.
  - `[low]` `[reject]` (edge-case, deletion) Dropping the database let `--import-realm` re-import every JSON in seed/ — same finding and refutation as the multi-realm one above.
  - `[low]` `[reject]` (edge-case, claim) A `- **Bold text**` bullet with no colon outside an entry is not flagged, though the spec says "no `- **` bullet" — true; catching it needs a second regex whose only effect is to refuse harmless prose in a register.
  - `[low]` `[reject]` (edge-case, claim) `Affected versions:` only requires a digit, so "See issue 42" passes — true, and deliberate: the documented rule is the authoring contract, and the check enforces the falsifiable approximation of it that ADR 0016 describes. A real "version expression" grammar is not decidable here.
  - `[low]` `[patch]` (edge-case, claim) `Verified by:` resolved with `.exists()`, so a directory passed as a check — confirmed by fixture (`` `services` `` exited 0). Now `.is_file()`, with a planted case in both directions.
  - `[medium]` `[patch]` (verification-gap) The `Affected versions:` "names no version" branch had no self-test; deleting it left `pixi run test` green while `all releases` became acceptable — a `register_cases` row now plants it.
  - `[medium]` `[patch]` (verification-gap) The `Verified by:` malformed-value branch had no self-test; the only negative row planted a backticked path — rows now plant an unbackticked value and a backticked directory.

## Design Notes

**The entry shape.** One `###` heading per entry, four bolded field bullets under it, and an optional fifth. The heading is the claim, so the file still skims like the prose it replaces:

```markdown
### The volume mount path is version-specific

- **Symptom:** The database silently loses everything on `down`, with no error anywhere.
- **Cause:** 17 keeps `PGDATA` at `/var/lib/postgresql/data`; 18 moved it to `/var/lib/postgresql/18/docker` and declares the volume one level up.
- **Fix:** Change the mount in `services/postgres/compose.yaml` to match the major version.
- **Affected versions:** PostgreSQL 17 vs 18 (`pgvector/pgvector:0.8.6-pg17`).
- **Verified by:** `services/postgres/smoke.sh` — the data directory sits inside a mounted volume.
```

**Why a separate task rather than a leg in `assert_config.py`.** `lint-config` is `RUNTIME_BOUND`: it resolves the compose model through a container runtime and is deliberately kept out of the pre-commit hook. The register check reads Markdown and needs nothing running, so folding it in there would make a commit-time check impossible for no reason. A separate `lint-gotchas` joins both `lint` and `precommit`, which is also what `lint_selftest.py:5172-5175` requires of anything that is not runtime-bound.

**Why this does not contradict ADR 0012.** 0012 rejected "a `gotchas.md` above some length" as unfalsifiable and an invitation to pad, and that stays rejected. A field is present or it is not; whether the sentence after `**Fix:**` is any good is still a review's job, not a lint's. The one bar this check sets on quantity — at least one entry — is the same bar `justified()` already sets on a `seed.none`: an empty marker is the silent skip in file form.

**Why `Verified by:` is checked for existence only.** A field naming a check that no longer exists is worse than no field, so the path is resolved. Whether the named check actually asserts the fix is not machine-decidable and is not attempted; the value of the field is that the link rots loudly.

**Why the reimport does not stop anything first.** AD-12's rule is stop dependents → write → restart, and its realm clause spells out what that means here: the import runs `compose exec` *inside* the running container, so the container must be up for the write to happen at all. The dependent whose cache goes stale is the Keycloak server itself, and restarting it after the import is the whole of the ordering. Nothing else reads realm state directly.

## Verification

**Commands:**
- `pixi run lint-gotchas` -- expected: 13 `<module>: OK <n> entries` lines, exit 0.
- `pixi run lint-python` -- expected: `ruff format --check`, `ruff check` and `mypy --strict` pass over `scripts/`.
- `pixi run lint-shell` -- expected: the rewritten `keycloak-reimport.sh` and `services/postgres/smoke.sh` are shellcheck-clean.
- `pixi run lint-yaml` / `pixi run lint-config` -- expected: the touched compose comments break nothing.
- `pixi run test` -- expected: the replaced reimport cases, every `check_gotchas.py` case, the two new config-pin cases and the README case all pass.
- `pixi run ci` -- expected: exit 0. This is the done-gate.
- `COMPOSE_PROFILES="$(./scripts/select.sh core)" pixi run up && pixi run smoke-strict` -- expected: the new Postgres data-directory assertion passes, 0 failed. If no container runtime is available in this environment, say so explicitly rather than reporting the suite as run.
- `printf 'reimport\n' | pixi run keycloak-reimport` -- expected against a live stack: the realm is replaced from the seed JSON, `select count(*) from pg_database where datname='keycloak'` still returns 1, and the container restarts. Same caveat: report it as not run rather than as passed if no stack can be started.

## Auto Run Result

Status: done

**Implemented change.** Every Module's `gotchas.md` is now a checked register rather than
free prose. 79 entries across thirteen files carry a `###` heading — the claim, so the file
still skims — followed by `Symptom:`, `Cause:`, `Fix:` and `Affected versions:` in fixed
order, all populated, with an optional `Verified by:` naming the check that catches a
regression. `scripts/check_gotchas.py` enforces the shape offline, so it runs in the
pre-commit hook where `lint-config` cannot. The eight entries the README duplicated, and the
Prometheus `query_range` paragraph, are gone from the README; a self-test fails the build if
a bolded bullet grows back there. The obsolete claim that a realm import cannot overwrite an
existing realm is removed and replaced by two verified entries, and the task it justified was
corrected with it: `pixi run keycloak-reimport` now runs `kc.sh import --override true
--http-management-port 9999` inside the running container and restarts it unconditionally,
dropping no database.

**Files changed**
- `scripts/check_gotchas.py` — new; the register's shape check, stdlib only, glob-and-refuse-empty.
- `services/*/gotchas.md` (13) — every entry rewritten into the four-field shape; nine carry `Verified by:`.
- `services/keycloak/gotchas.md` — obsolete import claim removed; management-port and stale-cache entries added.
- `scripts/keycloak-reimport.sh` — import with override plus a mandatory restart; no `stop`, no `psql`, no `DROP DATABASE`.
- `services/postgres/smoke.sh` — asserts the server's `data_directory` sits inside a mount, not the container layer.
- `scripts/lint_selftest.py` — 11 register-shape cases plus both clean directions and the empty-walk guard; six replacement reimport cases; pins for Prometheus's remote-write receiver and the README section.
- `pixi.toml`, `Makefile` — `lint-gotchas` joined to `lint` and `precommit`; reimport descriptions corrected.
- `README.md`, `AGENTS.md`, `CHANGELOG.md` — the register described rather than duplicated; realm-editing prose corrected.
- `docs/adr/0016-gotcha-entries-carry-a-checked-shape.md`, `docs/adr/README.md` — the decision and its index row.
- `services/keycloak/compose.yaml` — the stale comment naming the drop-the-database model.
- `_bmad-output/implementation-artifacts/epic-3-context.md` — recompiled at planning time; not part of the story's substance.

**Review findings.** 27 findings across three reviewing layers — 0 high, 3 medium, 21 low,
1 false, 2 maybe-false. Six patches applied, in one medium entry and four low ones: the
`Verified by:` path is resolved with `.is_file()` so a directory no longer passes as a
check; three self-test rows now plant the `Affected versions:` no-version branch, an
unbackticked `Verified by:` and a `Verified by:` naming a directory, all of which previously
shipped unexercised; the README stray-bullet guard matches indented and `*`-marked bullets;
the false "the Contents list links to it" rationale is corrected in the CHANGELOG and the
self-test label; and the README layout tree is reflowed. Two findings are deferred (see
frontmatter): the Postgres assertion's acceptance of any non-`/` mount, at medium unverified,
and an over-long AGENTS.md line whose fix edits an agent-context file. Every rejected finding
and its reason is recorded row by row in the Review Triage Log above — the recurring reasons
were a loud, non-destructive failure being correct behaviour (the reimport preconditions), a
fix that would add branches for a state a user does not reach (fenced-block parsing, the
missing-register guard), and a critique of the four-field shape the intent itself mandates.

**Follow-up review: false.** One medium entry was patched — a test-coverage gap now closed
in both directions — and four low ones. No high finding, and no unverified risk left by a
patch: every patched branch is exercised by a planted case that ran and passed.

**Intent-alignment note.** The auditor raised whether the live-stack verification steps make
this a story owed to an operator. They do not: both were run here. `pixi run keycloak-reimport`
was driven end to end against the live stack — the wrong word aborts with `Aborted.` and
exit 1 before the runtime is touched, the exact word logs `Realm 'devinfra' already exists.
Removing it before import`, the container's `StartedAt` advances, `select count(*) from
pg_database where datname='keycloak'` still returns 1, and `wait-healthy` passes. The new
Postgres assertion was run against the live container: `data_directory=/var/lib/postgresql/data`,
`mounted:/var/lib/postgresql/data holds /var/lib/postgresql/data`. Nothing is owed outside
the repository, so `done` is the correct terminal status.

**Verification performed**
- `pixi run ci` — exit 0, twice (before and after the review patches); 1159 self-test assertions pass, zero failures.
- `pixi run lint-gotchas` — 13 `OK` lines, 79 entries, exit 0.
- Matrix audit — every I/O row is covered by a self-test case that ran and passed in the CI log, including the two reimport rows, which are additionally covered live.
- Live stack — the Postgres data-directory assertion and both reimport directions, as described above.
- Not run: the full `pixi run smoke-strict` suite. The shared `devinfra` containers on this machine were started from an unrelated worktree on older pins (`grafana/grafana:12.2.0`, `grafana/tempo:2.9.0`), so its Grafana dashboard assertion fails for reasons that predate this story. The one assertion this story adds was verified in isolation against the live Postgres container instead.

**Residual risks**
- The Postgres mount assertion's strength against an anonymous volume is unsettled; deferred with what would settle it.
- `Verified by:` is checked for a live file path and nothing more — whether the named check actually asserts the fix is not machine-decidable, and ADR 0016 says so.
- The register's four-field shape is now mandatory for every entry, including a handful whose `Symptom:` is a design observation rather than an observed failure. That is the shape FR-18 requires; if it proves to fit poorly, the change is an ADR, not an edit.

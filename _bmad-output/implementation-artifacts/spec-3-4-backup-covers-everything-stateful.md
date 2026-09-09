---
title: 'Backup covers everything stateful'
type: 'feature'
created: '2026-09-09'
baseline_revision: '2015934ee60cb3f40f2400ef3850250b5292fc0c'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      The `stack` CI job now performs three full stack bring-ups inside an unchanged
      15-minute budget, and nothing has measured whether it still fits.
    evidence: |-
      `.github/workflows/ci.yml` gives the `stack` job `timeout-minutes: 15` and now runs
      `ci-stack` (init, start, wait, smoke-strict), `ci-stack-cycle` (down, start, wait,
      smoke-strict) and `ci-stack-restore` (backup, destroy, start, wait, restore, wait,
      smoke-strict) in sequence. The budget cannot simply be raised: the workflow header
      states the 15-minute rule and `scripts/lint_selftest.py` asserts every job carries a
      `timeout-minutes` at or below 15, so a breach means tiering the work across jobs
      rather than extending the clock. Settled by one hosted run of the `stack` job: read
      its wall-clock time, and if it is near the cap, split the round trip into its own
      job that brings up its own stack.
    location: >-
      .github/workflows/ci.yml (the stack job), pixi.toml [tasks.ci-stack-restore]
    severity: medium (unverified)
  - summary: >-
      Nothing has run the backup round trip against a real runtime; the only end-to-end
      proof is a CI job that has not executed yet.
    evidence: |-
      The capture half was exercised for real in this session against the running stack —
      five database dumps with `DROP DATABASE IF EXISTS` / `CREATE DATABASE`, three
      buckets with the empty ones preserved, the realm JSON, a correct manifest — and the
      Postgres and object-storage restore mechanics were each verified against throwaway
      containers. `scripts/verify-restore.sh` itself, and the orchestration in
      `scripts/restore.sh` (stop, apply, mirror, start, wait), have run only against the
      recording stub, because the round trip destroys every volume and the container stack
      on this machine holds the operator's own data. Settled by one green `stack` job in
      CI, or by `pixi run ci-stack` followed by `pixi run ci-stack-restore` on a machine
      whose volumes are expendable.
    location: >-
      scripts/verify-restore.sh, scripts/restore.sh
    severity: medium
  - summary: >-
      Which Modules are stateful is three literal branches in `backup.sh`, so a fourteenth
      stateful Module would be captured by nothing and named by no manifest line.
    evidence: |-
      `scripts/backup.sh` asks `selected postgres`, `selected minio` and `selected
      keycloak` in three hand-written branches, and `scripts/restore.sh` iterates the same
      three names. This is defensible today because each component has a bespoke capture
      mechanism — `pg_dump`, `mc mirror`, `kc.sh export` — and no generic one exists. It
      is still a hand-maintained statement of the catalog of the kind ADR 0018 rejects for
      bucket and database lists: a new stateful Module is silently uncaptured, and the
      manifest does not even record it as skipped. Closing it needs a per-Module backup
      contract (an `x-backup:` block, or a `services/<module>/backup.sh` the way
      `smoke.sh` works), which is a Module-contract change beyond this story.
    location: >-
      scripts/backup.sh, scripts/restore.sh, docs/adr/0012 (the Module contract)
    severity: low
---

<intent-contract>

## Intent

**Problem:** `scripts/backup.sh` dumps Postgres and nothing else, so object-storage
contents and the Keycloak realm are lost on any `destroy`; `scripts/restore.sh` pipes a
cluster-wide `pg_dumpall` archive into `psql` with no `ON_ERROR_STOP`, so a restore that
half-applies still exits 0, and it rewrites Keycloak's database underneath a running
Keycloak.

**Approach:** Backup captures every stateful Module in the current Selection — Postgres
databases, object-storage bucket contents, the Keycloak realm — into one timestamped
directory with a manifest, skipping Modules the Selection does not include. Restore reads
that directory, stops the Postgres dependents the resolver names, rewrites the databases
with `psql -v ON_ERROR_STOP=1`, mirrors the objects back, restarts what it stopped, and
fails non-zero on any step. A round-trip task plants markers, destroys the volumes,
restores and re-runs the strict smoke suite, and CI runs it.

## Boundaries & Constraints

**Always:**
- The Selection is the oracle for *what to capture*, resolved through
  `select_ambient`/`select_profiles` before Compose sees it (AD-16). A Module outside the
  Selection is skipped and recorded as skipped; a Module inside it that cannot be captured
  is a non-zero exit (NFR-5).
- Every failure surfaces and exits non-zero. No `|| true`, no `command -v` branch, no
  "skipping" — `scripts/lint_selftest.py:55` fails the build on all four strings.
- `psql` runs with `-v ON_ERROR_STOP=1` on every restore invocation.
- Postgres restore follows stop → write → restart (AD-12): every service of every Module
  that transitively depends on `postgres` and is in the Selection is stopped first and
  started after, then health is awaited.
- A backup directory is moved into place only on success, the way the current `.partial`
  archive is (`scripts/backup.sh:16-26`).
- The four existing entry points keep working unchanged: `pixi run backup`,
  `pixi run restore <path>`, `make backup`, `make restore F=<path>`, plus the two scripts
  run standalone.
- Redis is deliberately excluded — cache and in-flight task state, neither meaningful to
  restore.

**Never:**
- Do not hard-code a Module list, a bucket list or a database list in either script; read
  the catalog, the server and the resolver.
- Do not import the captured realm JSON during restore (see Design Notes) and do not drop
  the `keycloak` database out of band.
- Do not harden the stack: credentials stay trivial, no TLS, no new auth (NFR-8).
- Do not introduce a floating image tag or a new runtime dependency; the object-storage
  image has no `tar`, `gzip`, `awk`, `sed`, `grep` or `find` (verified), so nothing may
  assume them.
- Do not add a fourth CI job — the round-trip attaches as a step to the existing `stack`
  job, whose 15-minute budget it shares.
- Do not touch `_bmad-output/implementation-artifacts/sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full selection backup | `COMPOSE_PROFILES` resolves to core; stack up | `backups/<ts>/` with `manifest.txt`, `postgres/<db>.sql.gz` per database, `minio/<bucket>/…`, `keycloak/<realm>-realm.json`; exit 0 | No error expected |
| Narrow selection backup | Selection is `postgres,redis` | Only `postgres/`; manifest records minio and keycloak as skipped, not in the Selection; exit 0 | No error expected |
| Selection holds nothing stateful | Selection resolves to `mailpit` only | Nothing captured, nothing written | Exit 1: a backup that captured nothing is not a backup |
| A capture step fails | `pg_dump` or `mc mirror` exits non-zero | Partial directory removed | Exit 1, diagnostic naming the Module and the step |
| Restore a backup directory | `backups/<ts>/` with all three components; stack up | Dependents stopped, databases rewritten, objects mirrored back, dependents started, health awaited; exit 0 | No error expected |
| Restore a component the Selection excludes | Archive holds `minio/`, Selection is `postgres` | Refusal before anything is written | Exit 1 naming the Module and the Selection |
| Restore a legacy `postgres-*.sql.gz` | A pre-3.4 cluster archive | Refusal naming the file and why | Exit 1: a `pg_dumpall` stream cannot apply under `ON_ERROR_STOP=1` |
| Restore a missing or non-backup path | Path absent, or a directory with no `manifest.txt` | Refusal before the runtime is touched | Exit 1 naming the path |
| A restore statement errors | A dump references a missing object | `psql` exits 3 | Exit non-zero; dependents are still started again |
| No argument | `pixi run restore` with no file | Usage on stderr | Exit 1, runtime untouched |

</intent-contract>

## Code Map

**The two scripts under change**
- `scripts/backup.sh:1-28` — 28 lines. `select_ambient` `:11`; `backups/postgres-<ts>.sql.gz`
  `:14`; the `.partial`-then-`mv` idiom `:16-26` (keep it, applied to a directory);
  `compose exec -T postgres pg_dumpall -U "$POSTGRES_USER"` `:21`.
- `scripts/restore.sh:1-38` — relative-path resolution *before* `common.sh` cds `:10-18`
  (keep verbatim — a self-test case pins it); usage refusal `:23-26`; missing-file refusal
  `:28-31`; `select_ambient` deliberately after the path checks `:33-35`; the whole restore
  is one line `:37`, `gunzip -c "$f" | compose exec -T postgres psql -U … -d postgres`.

**Reuse — the seams both scripts already sit on**
- `scripts/lib/common.sh:19` cds to the repository root; `:24-55` loads `.env`; `:59-62` the
  four defaults (`POSTGRES_USER`, `POSTGRES_DB`, `REDIS_PASSWORD`, `KEYCLOAK_REALM`);
  `:67-71` `compose()` over `${DEVINFRA_COMPOSE:-docker compose}`; `:87-91` `select_profiles`
  (note the separate `local` declaration — the substitution's status must not be swallowed);
  `:97-99` `select_ambient`; `:105-121` `confirm_word`. The new `selected()` predicate goes
  after `select_ambient`, reads only `COMPOSE_PROFILES`, and is documented as callable only
  after a resolve.
- `scripts/mc.sh:13-17` — the object-storage idiom to copy exactly: credentials are read
  *inside* the container from its own environment and never interpolated by the host shell,
  with `# shellcheck disable=SC2016` above the single-quoted command.
- `scripts/keycloak-reimport.sh:26-46` — the AD-12 shape written out: the
  `--http-management-port 9999` comment `:29-34`, the mandatory unconditional
  `compose restart keycloak` `:40-44`, and `exec ./scripts/wait-healthy.sh` `:46`.
- `scripts/keycloak-export.sh:13-16` — `kc.sh export --dir /tmp/kc-export --realm … --users
  realm_file` followed by `compose cp`. **This invocation is broken** (see Design Notes) and
  the same one-line fix it needs is what the backup's realm capture must carry.
- `scripts/destroy.sh:16-21` — `select_profiles --all` then `confirm_word destroy`; the
  round-trip task drives it with `printf 'destroy\n' |`.
- `scripts/select.sh:35-58` — the wrapper shape: `set -euo pipefail`, source `common.sh`,
  the `DEVINFRA_PYTHON` seam `:40`, empty-argument dropping `:48-56`, `exec`. Its docstring
  `:1-34` is the usage block a new mode must be added to.

**The resolver**
- `scripts/resolve_selection.py` — `ALL_MODULES_REQUEST` `:78`, `SELECTIONS_REQUEST` `:82`,
  `module_composes()` `:96`, `read_model()` `:105`, `Graph` `:166-189` (`modules`, `owners`
  service→module, `profiles`, `edges` module→modules), `build_graph()` `:212`,
  `parse_request()` `:278`, `closure()` `:293`, `selections()` `:349`, `main(argv)` `:380`,
  `__main__` guard `:411`. Contract: one line on stdout, nothing on stdout on refusal,
  `select: {exc}` on stderr, exit 1 (`:391-408`). The new `--dependents` mode is a third
  request form beside `--all` and `--selections`.
- Today's graph (verified): `keycloak -> (mailpit, postgres)`, `pgadmin -> (postgres,)`,
  everything else independent of `postgres`. `owners` carries one helper service today,
  `minio-init` → `minio`, which is why the new mode prints *services*, not Modules —
  `compose stop` speaks services and a future Module with a helper must not be half-stopped.

**Module facts the scripts depend on**
- `services/postgres/compose.yaml:38-74` — service `postgres`, profiles `[postgres, minimal,
  core, admin]`, `POSTGRES_EXTRA_DATABASES` `:52`.
- `services/minio/compose.yaml:44-101` — server `minio` and one-shot `minio-init`;
  `MINIO_BUCKETS` default `uploads,artifacts,backups` `:87`; the provisioning loop
  `:90-98` (`mc mb --ignore-existing`, `mc version enable`) is the shape restore reuses.
- `services/keycloak/compose.yaml:48-111` — seed bind-mounted read-only at
  `/opt/keycloak/data/import` `:92`; `KEYCLOAK_REALM` is the realm name.
- `services/postgres/smoke.sh:34-36` the `smoke_probe` write/read round-trip;
  `services/minio/smoke.sh:13-24` the alias/put/get/versioning checks;
  `services/keycloak/smoke.sh:24-46` issuer, client-credentials and password grants and the
  decoded-claim assertions. The round-trip script plants its markers where these already
  look, so a lost marker is visible in the same terms.

**Wiring**
- `pixi.toml:142-149` `[tasks.backup]` and `[tasks.restore]` (`args = [{ arg = "file",
  default = "" }]`); `:247-249` `[tasks.ci-stack]`; `:258-260` `[tasks.ci-stack-cycle]` and
  the comment `:251-257` explaining why a cycle is composed of tasks `ci-stack` already
  runs; `:243-246` the rule that the workflow may never become a second definition of what
  CI runs — the round-trip must be a named task.
- `.github/workflows/ci.yml:84` `pixi run ci-stack`, `:92` `pixi run ci-stack-cycle`,
  `:96-102` the `if: failure()` diagnostics. The new step goes between `:92` and `:96`.
  Header comment `:1-15` says "Three jobs" — still true if no job is added. `:9-11` the
  15-minute rule.
- `Makefile:114-122` `backup` and `restore` targets (`@pixi run restore $(F)`); recipe
  bodies are pinned by `MAKE_FORWARDS` in `scripts/lint_selftest.py:103-127` (`:121`
  backup, `:122` restore) and asserted at `:6285-6288` and `:6298-6320`. Only the `##` help
  text changes, so the constant stays as it is.

**Self-test — the cases that break and where new ones plug in**
- `scripts/lint_selftest.py:1869-1897` the lifecycle block: `moved_aside(env_files)` `:1872`,
  `stubs`/`record`/`compose_stub` `:1873-1875`, and `fresh(stdout=…, services=…,
  exit_code=…, profiles=…, document=…, request="postgres,redis")` `:1877-1897`, which
  truncates the record files and re-points `COMPOSE_PROFILES`. `RECORDER` `:195-226` is the
  stub that records argv and answers `ps`/`config`.
- **Breaks and must be rewritten:** `:2093-2097` asserts `recorded(record) == ["exec","-T",
  "postgres","pg_dumpall","-U","devinfra"]`; `:2098-2100` asserts exactly one
  `backups/postgres-*.sql.gz`; `:2104-2107` the failure case and its cleanup.
- **Survives, keep passing:** `:2063-2071` restore's argument and missing-file refusals with
  the runtime untouched; `:2079-2084` the relative-path case; `:2273-2288` the `restore`
  pixi-task cases including the argument containing a space.
- Helpers: `expect(name, condition, detail)` `:696-700`; `pixi()` `:131-157`; `run_script()`
  `:159-187`; `planted()` `:443-477`; `moved_aside()` `:479-499`; `write_recorder()`
  `:300-327`; `stub_env()` `:330-374`; `recorded()` `:376-388`; `recorded_env()` `:390-404`.
  Banner convention `# --- Title. ---` followed by prose saying why the bug cannot satisfy
  the assertion.
- Constants and set-equality checks a new script must satisfy: `FORBIDDEN` `:55` scanned over
  every `scripts/**/*.sh` at `:809-812`; "every script that calls compose resolves its
  Selection first" `:5620-5648` with `resolver_exempt = {"lint-compose.sh",
  "smoke-test.sh"}` `:5620` — the new script is **not** exempt; gitignore coverage `:5593`;
  `ci-stack*` chain assertions `:5834-5863`; `precommit ⊆ lint` `:5950-5954`.
- `scripts/endpoints.py:598-615` walks `scripts/**/*.sh` and pins every `${VAR:-default}`
  against `.env.example` — a shell default written in the new scripts must agree with the
  template, which is the reason to read object-storage credentials inside the container
  instead of interpolating them.

**Docs**
- `README.md:319-320` layout lines for the two scripts; `:421-422` the task-table rows;
  `:756-770` `## Data and persistence`, whose `:769-770` "Verified:" sentence is the
  precedent for how a proven claim is worded; `:455-461` the CI job table.
- `docs/adr/README.md:10-28` the index table, highest ADR **0017** — the new one is **0018**,
  appended at `:29`. Template, identical across 0015-0017: `# 18. <Title>` / blank /
  `Date: 2026-09-09 · Status: Accepted` / `## Context` / `## Decision` / `## Rejected` /
  `## Consequences`, bolded lead sentence per sub-decision with the `AD-n`/`NFR-n`
  identifier inline. `docs/adr/0013-…md:46-52` is the fail-loud paragraph to mirror.
- `CHANGELOG.md:9` `## [Unreleased]`, `:11` `### Changed` with `#### ⚠ BREAKING — …`
  subheadings at `:15`, `:49`, `:67` and `#### Everything else` `:87`; `:163` `### Added`;
  `:268` `### Removed`. Bullets open with a bold noun phrase and an em dash.
- `services/keycloak/gotchas.md:48-58` the *import*-side management-port entry — the new
  export-side entry is its sibling and carries a `**Verified by:**` line;
  `scripts/check_gotchas.py:39-64` the required four fields in order plus the optional
  `Verified by`.
- `AGENTS.md:32-38` every script that calls `compose` resolves first; `:48-50` and `:51-58`
  the README carries no copies; `:67` object-storage volume size is not a data-integrity
  signal — check bucket and object listings instead.

## Tasks & Acceptance

**Execution:**
- `scripts/resolve_selection.py` -- add a `--dependents <module>` request mode: a
  `dependents(graph, module, modules)` helper returning the sorted service names of every
  Module in the resolved request whose transitive `edges` closure reaches `<module>`,
  excluding `<module>`'s own Module; wire it into `main()` beside `--all`/`--selections`,
  keeping the one-line-on-stdout contract, printing an empty line for an empty set and
  refusing an unknown module name with exit 1 -- restore must not hard-code "keycloak and
  pgadmin", and `compose stop` needs services, not Modules.
- `scripts/select.sh` -- document `--dependents <module>` in the usage block and let it pass
  through; the empty-argument dropping loop already forwards it unchanged -- one documented
  surface for every resolver question.
- `scripts/lib/common.sh` -- add `selected <module>` testing membership of the resolved
  `COMPOSE_PROFILES` with a `case ",$COMPOSE_PROFILES," in *",$1,"*)` match, documented as
  valid only after `select_ambient`/`select_profiles` -- three call sites in two scripts
  need the same predicate and no shell idiom for it exists yet.
- `scripts/backup.sh` -- rewrite: resolve the Selection, create `backups/<ts>.partial/`,
  capture Postgres (enumerate databases from `pg_database`, one
  `pg_dump --create --clean --if-exists` per database, gzipped), object storage (`mc ls
  --json` for the bucket list, `mc mirror` each into a container staging directory, one
  `compose cp` out), and the Keycloak realm (`kc.sh export` with
  `--http-management-port 9999`, then `compose cp`), writing `manifest.txt` recording the
  Selection, each captured component and each skipped Module with its reason; `mv` into
  place only on success, remove the partial directory on any failure, refuse when nothing
  stateful was in the Selection -- AC1.
- `scripts/restore.sh` -- rewrite, keeping the pre-`common.sh` relative-path resolution and
  the two path refusals verbatim: require a backup directory carrying `manifest.txt`;
  refuse a legacy `postgres-*.sql.gz` file naming why; refuse any component whose Module the
  current Selection excludes; stop the services `select.sh --dependents postgres` names;
  apply every `postgres/*.sql.gz` through `psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d
  postgres`; `compose cp` the objects in and `mc mb --ignore-existing` + `mc mirror
  --overwrite --remove` them back; start what was stopped and `exec ./scripts/wait-healthy.sh`
  -- AC2, AC3, AC4.
- `scripts/keycloak-export.sh` -- add `--http-management-port 9999` to the `kc.sh export`
  invocation with the comment naming the collision -- verified defect: the command exits 1
  with `Unable to start the management interface on 0.0.0.0:9000` against a live container.
- `services/keycloak/gotchas.md` -- add the export-side entry in the four-field shape with a
  `**Verified by:**` line pointing at the self-test assertion -- the register is corrected,
  not merely migrated, and this failure mode is now mechanically checked.
- `scripts/verify-restore.sh` -- new: plant a Postgres marker row and an object-storage
  marker object, run the backup, destroy the volumes, start and wait, restore, then assert
  both markers came back and run `SMOKE_STRICT=1 ./scripts/smoke-test.sh` -- AC2's "verified
  by a smoke run" needs a marker to distinguish a restored stack from a freshly seeded one.
- `pixi.toml` -- add `[tasks.ci-stack-restore]` invoking the round-trip script, placed beside
  `ci-stack-cycle` with the same style of comment -- the workflow must never become a second
  definition of what CI runs.
- `.github/workflows/ci.yml` -- add the round-trip step to the `stack` job between the cycle
  step and the failure diagnostics -- it is only meaningful against a stack that is already
  up, exactly as the cycle step is.
- `Makefile` -- update the `restore` help text to name a backup directory -- the recipe body
  is pinned by `MAKE_FORWARDS` and must not change.
- `scripts/lint_selftest.py` -- rewrite the four broken backup assertions against the new
  argv and directory layout, and add cases for: the manifest naming a skipped Module rather
  than failing, a Selection with nothing stateful refusing, the legacy-archive refusal, the
  excluded-component refusal, `ON_ERROR_STOP=1` present on every `psql` invocation, the
  stop → write → restart ordering by argv index, `--dependents` output and its refusals,
  `selected()`'s two answers, and the export script's management-port flag -- every script
  is verified like code and the restore ordering is named in the epic as a required test.
- `README.md`, `CHANGELOG.md`, `docs/adr/0018-backup-follows-the-selection.md`,
  `docs/adr/README.md` -- document the new archive shape, the Selection-scoped skip, the
  restore ordering and the refusal of legacy archives; ADR 0018 records the decision and the
  rejected alternatives -- documentation is generated or verified, never invented.

**Acceptance Criteria:**
- Given a running Selection that includes Postgres, object storage and Keycloak, when
  `pixi run backup` runs, then one timestamped directory appears under `backups/` carrying
  a per-database Postgres dump, the bucket contents including any empty bucket, the realm
  JSON and a manifest, and the command exits 0.
- Given a Selection that excludes object storage, when a backup runs, then the manifest
  records object storage as skipped because it is not in the Selection, the command exits 0,
  and nothing under `minio/` is written.
- Given a backup taken from a populated stack and a stack whose volumes were destroyed and
  recreated, when `pixi run restore backups/<ts>` runs and the strict smoke suite follows,
  then the planted Postgres row and the planted object are both present and every smoke
  check passes.
- Given a restore, when it rewrites the databases, then every service of every Module that
  depends on Postgres and is in the Selection is stopped before the first write and started
  after the last, and health is awaited before the command exits.
- Given any failing step of a restore, when it runs, then the failure appears on stderr and
  the command exits non-zero — in particular a `psql` statement that errors ends the restore
  rather than being counted as applied.
- Given `pixi run ci`, when it runs, then it exits 0 with the rewritten and new self-test
  cases included.

## Spec Change Log

## Review Triage Log

### 2026-09-09 — Review pass
- verdicts: 42 findings — high 0, medium 26, low 14, false 1, maybe-false 1
- findings:
  - `[medium]` `[patch]` restore.sh never checked the manifest's per-item contents, so a manifest naming three databases with one dump on disk restored one and exited 0 — reproduced by reading `restore.sh:112-128`: the only checks were "the directory exists" and "postgres/ holds at least one .sql.gz". Fixed: every database, bucket and realm the manifest names must have its file, and `postgres_dumps` is now built from the manifest names rather than a glob.
  - `[medium]` `[patch]` Cluster globals — roles, grants, tablespaces — are no longer captured, and nothing recorded the loss where the object-versions gap gets a manifest note — real: `pg_dump` per database dumps no cluster-level objects, and `pg_dumpall` did. Fixed by recording it: a manifest `note:` and a Consequences paragraph in ADR 0018. No globals capture was added; it cannot apply under `ON_ERROR_STOP=1`.
  - `[medium]` `[patch]` backup.sh excluded the `postgres` database unconditionally, so `POSTGRES_DB=postgres` — the upstream image default and a legal value — skipped the only database holding data and still exited 0 — verified against `backup.sh`'s query and `.env.example:94`. Fixed: backup refuses, naming `POSTGRES_DB`, before anything is written.
  - `[low]` `[reject]` restore.sh takes no confirmation word despite dropping databases and removing live objects — restore is an explicitly destructive verb typed with an archive path, the pre-change script piped a whole cluster dump into psql with no confirmation either, and the smallest fix needs an opt-out so `verify-restore.sh` and CI can still drive it — a new public surface. Not worth it.
  - `[medium]` `[patch]` A component *directory* the manifest does not name never reached the Selection check, so a `postgres/` dropped into an archive was applied to a stack that never agreed to it — verified: the loop at `restore.sh:104` iterated only manifest-named components while `:120` globbed the directory. Fixed by the same two-way reconciliation: a directory the manifest does not name is a refusal.
  - `[low]` `[patch]` The archive's `skipped:` lines were written and never read, so a Postgres-only restore into a wider Selection said nothing about what it did not cover — real. Fixed: each is echoed as `Not in this archive: …` before the closing line.
  - `[medium]` `[patch]` verify-restore.sh re-introduced `MINIO_BUCKETS` as the bucket oracle, with a third hard-coded copy of its default, which ADR 0018 explicitly rejects — verified at the old `bucket="${MINIO_BUCKETS:-uploads,artifacts,backups}"`. Fixed: it asks the server with `mc ls --json` the way `backup.sh` does.
  - `[medium]` `[patch]` verify-restore.sh planted its marker in `smoke_probe`, the table `services/postgres/smoke.sh:36` truncates on every run, so the round trip survived only by step order — verified. Fixed: a `restore_probe` table of its own.
  - `[medium]` `[patch]` The archive was chosen with `find backups -type d | sort` and the last line, restoring whatever sorts last in an untracked directory — real. Fixed: the path comes from `backup.sh`'s own `Wrote …` line, refused when absent.
  - `[low]` `[patch]` ADR 0018's verified tool inventory named `cut` and `basename`, which the container-side programs do not use, and omitted `mkdir` and `rm`, which they do — verified against the programs. Fixed: the inventory now names `sh`, `mc`, `mkdir`, `rm`.
  - `[medium]` `[patch]` A bucketless server wrote a bare `minio: ` line the manifest could not distinguish from a parse that found nothing — real. Fixed: an explicit `(no buckets)` marker, which restore special-cases, plus a refusal when the listing is non-empty and no name parsed.
  - `[low]` `[patch]` README claimed "CI runs it on every push" when the workflow triggers only on pushes to `main` and pull requests, and repeated the truncation guarantee the first finding showed was not implemented — both verified. Fixed: the trigger sentence corrected; the guarantee is now true. The unmeasured 15-minute budget in the same bullet is deferred.
  - `[medium]` `[patch]` A `postgres/` or `minio/` directory the manifest does not name bypassed the documented Selection refusal — same root cause as the reconciliation finding above; fixed with it.
  - `[medium]` `[patch]` A bucket listing that parses to zero names while buckets exist captured nothing and reported success — real, and unguarded. Fixed with the `(no buckets)` marker and the non-empty-listing refusal.
  - `[medium]` `[patch]` A manifest naming buckets or a realm whose files are absent failed mid-mirror, after Postgres had already been rewritten — verified. Fixed: both are checked by name before the first write.
  - `[medium]` `[patch]` The staging `rm -rf` at the end of restore's object step was fatal, so a cleanup failure turned a fully applied restore into a non-zero exit that also skipped the health wait — verified under `set -e`. Fixed: cleanup moved to the exit path and downgraded to a warning on stderr.
  - `[medium]` `[patch]` The same fatality in backup discarded a complete capture when only the container-side cleanup failed — verified. Fixed the same way, in a `cleanup` EXIT trap alongside the partial directory.
  - `[medium]` `[patch]` `compose cp` into an existing `/tmp/devinfra-restore-$$` nests one level, so a leftover from a recycled PID made every mirror path miss — real, and the leak that creates one was itself unfixed. Fixed: the stage is cleared in the container before the copy (fatal there, deliberately) and on every exit path.
  - `[low]` `[reject]` `compose start` could error when a dependent has no container, turning a fully applied restore into a failure — the script starts only what it has just stopped, and a `compose stop` against a missing container fails first, before any write; making the start non-fatal would weaken the restart guarantee AD-12 requires.
  - `[low]` `[reject]` A missing `restore_probe` table makes the marker read abort under `set -e` before the friendly diagnostic — the run still fails loudly and psql's own message names the missing relation; `|| row=""` would misattribute a connection failure to a lost marker.
  - `[low]` `[reject]` `mc cat`'s non-zero exit hides the object-storage marker diagnostic — same refutation: the failure is loud and correctly attributed by mc's own message.
  - `[medium]` `[patch]` verify-restore.sh derived its bucket from `MINIO_BUCKETS`, which may name a bucket the server does not hold — same root cause as the oracle finding above; fixed with it.
  - `[medium]` `[patch]` With `POSTGRES_DB=postgres` the marker lands in the database the capture skips, so the round trip always reports a failed restore — same root cause as the `POSTGRES_DB` finding; backup now refuses that configuration outright.
  - `[medium]` `[patch]` A stale directory in `backups/` is restored instead of the fresh backup — same root cause as the archive-selection finding; fixed with it.
  - `[medium]` `[patch]` `pixi run ci-stack-restore` destroyed every volume with no prompt, piping past the confirmation this repository requires for exactly that act — verified. Fixed: `confirm_word destroy` when stdin is a terminal; CI, which is not, proceeds. No new environment variable.
  - `[maybe-false]` `[defer]` A third full stack bring-up under an unchanged `timeout-minutes: 15` may cancel the job and read as a backup failure — cannot be decided here: the budget has never been measured with the new step, and it cannot simply be raised because the self-test asserts every job stays at or below 15 minutes. Deferred at medium (unverified) with what would settle it.
  - `[low]` `[reject]` `--dependents` combined with `--all` reports "'--all' is not a Module" rather than naming the bad combination — the message is confusing, not wrong, the combination is meaningless rather than dangerous, and the fix adds a branch for it.
  - `[medium]` `[patch]` The exhaustive backup argv assertion was replaced by flat membership tests, so `-T` and the call's shape went unpinned — verified, and worse than filed: `args.index("-U")` found the listing `psql` call, not `pg_dump`. Fixed: the assertion locates the `pg_dump` token and pins the slice around it.
  - `[medium]` `[patch]` An empty `keycloak/` directory passed the truncation check and the realm JSON was never checked to exist — verified. Fixed by the by-name reconciliation.
  - `[low]` `[defer]` Three literal `selected <module>` branches are a hand-maintained statement of which Modules are stateful, so a fourteenth would be silently uncaptured — real, but each component has a bespoke capture mechanism and a generic one needs a per-Module backup contract, which is a Module-contract change beyond this intent. Deferred.
  - `[low]` `[patch]` `README.md:464` still said the two stack jobs "share their task list exactly" under a row saying `stack` now runs three tasks and `stack-podman` one — verified. Fixed: the sentence now says what each runs.
  - `[low]` `[patch]` ADR 0018's tool inventory omits `mkdir` and `rm` — same root cause as the inventory finding above; fixed with it.
  - `[medium]` `[patch]` The round trip proved one database and one bucket, so a restore covering only the first of each would still pass — verified by the reviewer's demonstration and confirmed: the destroyed volumes re-seed the other databases and buckets, so the strict suite cannot tell. Fixed: markers in two distinct databases and two distinct buckets, with a refusal when the server names fewer than two of either.
  - `[medium]` `[patch]` The truncated-archive refusals were exercised by no case, because `plant_backup` always wrote the manifest and its payload together — verified. Fixed: cases for a manifest naming a dump that is not on disk and a component directory the manifest does not name, each asserting a non-zero exit and an untouched runtime.
  - `[low]` `[patch]` The `--dependents` flattening in `select.sh` was reachable only through a form nothing ran, so the documented `pixi run select "--dependents postgres"` entry point could break with nothing red — verified. Fixed: one `pixi("select", "--dependents postgres", …)` case.
  - `[medium]` `[patch]` restore read its component list from the manifest and its contents from the filesystem, in both directions — same root cause as the reconciliation finding; fixed with it.
  - `[medium]` `[patch]` The "dumps each database self-contained, as POSTGRES_USER" assertion tied its flags and user to no particular invocation — same root cause as the argv finding; fixed with it.
  - `[low]` `[patch]` `README.md:464`'s shared-task-list sentence — same root cause as the README finding; fixed with it.
  - `[medium]` `[defer]` The story's ACs live at the live-runtime surface while every added test lives at the argv-recorder surface, and the round trip has never executed for real — accurate. The capture half and both restore mechanisms were verified against real containers in this session; the orchestration was not, because the round trip destroys every volume and the stack on this machine holds the operator's data. Deferred at medium with what would settle it.
  - `[false]` `[reject]` The run should have finalized to `status: awaiting-operator` with `operator_actions:` because proving AC3 destroys every volume — the intent reserves that state for acts only a human can perform outside the repo (buy a domain, publish a DNS record, grant an API key, click through a vendor console). Running a pixi task is none of those; the round trip is held back by whose data is on this machine, not by human-exclusivity, and CI runs it unattended.
  - `[low]` `[reject]` The new gotcha's `Verified by:` names a case that asserts the flag is passed rather than the collision itself — that is what the convention means in this repository: `check_gotchas.py` requires a path to the check that would catch a regression of the fix, and every existing `Verified by:` entry points at exactly such a check. The entry's own text says what the case asserts.
  - `[medium]` `[patch]` Container-side staging directories leak on every failure path, accumulating a full copy of every bucket in a container's writable layer — verified against `capture_failed`'s exit path. Fixed with the exit-path cleanup above.

### 2026-09-09 — Review pass (follow-up)
- verdicts: 38 findings — high 0, medium 8, low 19, false 11, maybe-false 0
- findings:
  - `[low]` `[patch]` CHANGELOG claims backup "records every Module the Selection does not include as skipped", but only postgres, minio and keycloak ever reach a `skipped:` line — verified against `backup.sh`'s three branches. Fixed: the CHANGELOG and the matching README bullet now say the three stateful Modules, and name Redis as covered by neither.
  - `[low]` `[reject]` The spec's Design Notes show an example manifest line (`skipped: pgadmin`) the implementation cannot emit — real, but the fix is to edit this build's spec, which triage does not do.
  - `[low]` `[patch]` Redis's deliberate exclusion is stated in four places, none of them the archive, so a reader of an old manifest cannot tell a deliberate exclusion from a lost component — real, and the three existing `note:` lines exist for exactly this. Fixed: a fourth `note:` naming Redis and saying a stateless Module is recorded by neither a component line nor an exclusion.
  - `[false]` `[reject]` Only `$KEYCLOAK_REALM` is exported, so a second realm is lost at exit 0 — the realm JSON is not the restore path: every realm's state lives in the `keycloak` database, which the Postgres restore rewrites. A second realm comes back; only its portable JSON artefact is absent.
  - `[low]` `[reject]` An archive naming `keycloak:` with no `postgres:` component prints "the realm is restored with the 'keycloak' database" having written no database — the Selection is a dependency closure, so keycloak in it implies postgres in it; only a hand-edited manifest reaches this, and the fix adds a branch.
  - `[low]` `[reject]` The round trip plants no Keycloak-specific marker — the `keycloak` database is restored by the same uniform loop the two marked databases prove, so a keycloak-only restore failure has no separate code path; a realm-level marker is new machinery for no distinct risk.
  - `[medium]` `[patch]` `README.md:807` opened with the repository's reserved `Verified:` convention for a round trip that DW-48, added in the same commit, records as never executed — verified against both. Fixed: the paragraph now describes what `ci-stack-restore` does and requires, and claims no observation.
  - `[medium]` `[patch]` Code paths added by the previous review pass — the object-storage restore branch, the `(no buckets)` marker, the `skipped:` replay — are exercised by no self-test case, so each can regress with `pixi run ci` green — verified by enumerating every `restore.sh` case. Fixed with the object-storage and bucketless cases below.
  - `[low]` `[reject]` DW-47 in `deferred-work.md` carries no `severity:` field while DW-48 and DW-49 do — real, but the ledger's existing entries are the orchestrator's to modify, not this run's; the severity is on record in the spec frontmatter.
  - `[low]` `[reject]` The spec's tool inventory (`cut`, `basename`) contradicts ADR 0018's corrected one (`mkdir`, `rm`) — real, and the ADR is the correct copy; the fix is to edit this build's spec.
  - `[low]` `[patch]` A bucket or database on the live server that the archive does not name survives a restore untouched, and neither the ADR nor the README said so where the object-versions and cluster-globals asymmetries are both stated — verified: the mirror loop runs per manifest-named bucket only. Fixed: a Consequences paragraph in ADR 0018 and a paragraph in the README's restore section.
  - `[false]` `[reject]` `sprint-status.yaml` is flipped to `done` against the spec's own Never clause — that row is the orchestrator's bookkeeping, written outside this story's implementation; this run is instructed never to write or revert it.
  - `[low]` `[reject]` `main()` now matches `--all` and `--selections` against `parse_request(argv)`, so `select.sh "--all,core"` resolves to every Module where it used to refuse — real, but a comma-joined flag is not an input anything produces, and the fix adds a branch. The second half is refuted: `parse_request` is a list comprehension over strings and cannot raise, so moving it out of the `try` changes no diagnostic.
  - `[false]` `[reject]` `verify-restore.sh` discards `backup.sh`'s exit status through the process substitution — the only way to reach a non-zero after the `Wrote` line is a trap whose every failure path is already a warning, and a backup that fails earlier prints no `Wrote` line and is refused at `:121` with its own stderr diagnostic already on the terminal. The failure surfaces and exits non-zero.
  - `[low]` `[reject]` `verify-restore.sh` leaves `restore_probe` rows and `/tmp/restore-marker.txt` behind — the markers are the evidence of the run, the containers are recreated by the destroy, and the CI stack is discarded; the fix adds cleanup for no named harm.
  - `[low]` `[reject]` A database name containing whitespace word-splits into two names — `pg_dump -d` then fails on a name that does not exist and the capture exits non-zero, so the outcome is a loud failure with an imprecise message, not a silent partial; the fix adds a guard.
  - `[low]` `[reject]` A bucket directory or dump file *inside* a component the manifest does not name is neither restored nor refused — real, but backup moves an archive into place only on success, so only a hand-edited archive reaches it, and the fix adds two more reconciliation loops.
  - `[low]` `[reject]` A manifest naming a component key other than the three is ignored rather than refused — no such manifest is produced today, a future stateful Module needs restore support regardless, and the proposed guard would refuse the `created:`, `selection:`, `skipped:` and `note:` lines as unknown components.
  - `[low]` `[reject]` A manifest saying `minio: (no buckets)` beside a non-empty `minio/` copies the stage in and mirrors nothing — reachable only by hand-editing, and the consequence is one wasted copy, not a wrong restore.
  - `[false]` `[reject]` `verify-restore.sh:115-124` treats a failed backup as a valid archive — same refutation as the exit-status finding above: no `Wrote` line means the refusal at `:121`, and a non-zero after that line is unreachable.
  - `[low]` `[reject]` `--all` inside a comma-joined value silently resolves to every Module instead of refusing — same root cause as the flag-surface finding above; rejected with it.
  - `[false]` `[reject]` `dependents()` may name a service outside the active profiles, so `compose stop` refuses — every service in the catalog carries its own Module's name in `profiles:`, and `assert_config.py`'s contract leg is what keeps them agreeing, so a Module in the Selection has every one of its services active.
  - `[low]` `[reject]` Objects are mirrored with `--remove` while every service still runs, so a live reader sees them deleted and rewritten — AD-12's stop → write → restart is specified for the Postgres dependents, whose `DROP DATABASE` genuinely fails under a held connection; a transient object view during an operator-invoked restore of a dev stack is not comparable harm, and the fix adds a second stop/start cycle.
  - `[false]` `[reject]` `sprint-status.yaml` was touched against the Never clause — same refutation as above: orchestrator bookkeeping, outside this run's write surface.
  - `[low]` `[defer]` carried — three literal `selected <module>` branches are a hand-maintained statement of which Modules are stateful. Logged and deferred in the previous pass (DW-49); the code still reads as that row describes.
  - `[false]` `[reject]` `restore.sh`'s usage string and its `-f` → `-e` existence test deviate from the "verbatim" refusals the spec's task named — a backup is a directory now, so `-f` would reject every valid archive and the old usage string would name a path shape that no longer exists. Both refusals are still there and still fire before the runtime is touched.
  - `[false]` `[reject]` Container-side staging removal is a warning rather than a non-zero exit, against "every failure surfaces and exits non-zero" — that downgrade is the previous pass's fix for a verified defect: a fully applied restore reported as a failure, which also skipped the health wait. A leftover directory in a container's `/tmp` is not a failure of the capture or the restore, and it is announced on stderr.
  - `[medium]` `[patch]` Restore's entire object-storage branch — the pre-clear, the `compose cp`, `mc mb --ignore-existing` and `mc mirror --overwrite --remove` — is executed by no test at any surface, so inverting the mirror leaves `pixi run ci` green — verified by enumerating every restore case. Fixed: an `objects` case over a two-bucket archive asserting the pre-clear precedes the copy, one mirror per manifest-named bucket in order, and the recorded container-side program text pinning both the direction and `--remove`. Mutation-checked: inverting the mirror now fails the suite.
  - `[medium]` `[patch]` The `(no buckets)` marker is written by backup and special-cased by restore, and no test on either side observes it — verified. Fixed: a backup case over a server that lists no bucket asserting the marker and that nothing is mirrored, and a restore case over that manifest asserting exit 0 with no mirror.
  - `[medium]` `[patch]` The by-name truncation refusals for buckets and for the realm are exercised by no case; only the Postgres variant had one — verified. Fixed: a manifest naming two buckets with one on disk, and one naming a realm with an empty `keycloak/`, each asserting a non-zero exit and an untouched runtime.
  - `[medium]` `[patch]` AC4's health wait (`exec ./scripts/wait-healthy.sh`) is asserted by nothing, so deleting it leaves every case passing while restore returns before Keycloak answers — verified. Fixed: the `ordered` case now asserts a `ps` call after the last `start`.
  - `[low]` `[patch]` The `ordered` case computes `write_at` as the *first* `psql` over a one-database fixture, so "starts the dependents after the last write" could not distinguish a start after the loop from one inside it — verified. Fixed: a two-database fixture, first and last write tracked separately, plus an assertion that one dump is applied per manifest-named database.
  - `[false]` `[reject]` Refusal paths precede the `trap on_exit EXIT` installation — the reviewer filed this explicitly as "no defect"; `stopped` is empty at every refusal, so `refuse()`'s "Nothing has been written" holds.
  - `[medium]` `[defer]` carried — `verify-restore.sh` is executed by no test at any level and has never run against a real runtime. Logged and deferred in the previous pass (DW-48); unchanged.
  - `[false]` `[reject]` The run breached the Never clause on `sprint-status.yaml` — same refutation as above.
  - `[low]` `[defer]` carried — the diff implements the enumerated reading of "every stateful Module" against the "do not hard-code a Module list" clause. Same root cause as DW-49; carried with it.
  - `[medium]` `[defer]` carried — the intent's expectations live at the live-runtime surface while the tests live at the argv-recorder surface. Logged and deferred in the previous pass (DW-48); this pass narrowed the gap for the object-storage branch but did not close it.
  - `[false]` `[reject]` The `POSTGRES_DB=postgres` refusal and the tty-gated `confirm_word destroy` are additions no reading of the intent requires — both are the previous pass's verified fixes (an archive holding none of the application's tables at exit 0; a destroy of every volume with no prompt), and the auditor filed them descriptively rather than as defects.

## Design Notes

**Why per-database `pg_dump`, not `pg_dumpall`.** Verified against
`pgvector/pgvector:0.8.6-pg17`: a `pg_dumpall` stream opens with `CREATE ROLE devinfra;`,
and applying it to *any* initialized cluster under `ON_ERROR_STOP=1` aborts at that line
with `role "devinfra" already exists` — including the "empty stack" of the acceptance
criterion, whose bootstrap role is created by initdb from `POSTGRES_USER`. `psql` then exits
3. So `ON_ERROR_STOP=1` and a cluster-wide dump are mutually exclusive, and the AC mandates
the former. One `pg_dump --create --clean --if-exists` per database emits
`DROP DATABASE IF EXISTS` + `CREATE DATABASE` + contents, which was verified to restore into
a separately initialized cluster at exit 0 with the rows intact. It is also why AC3's
ordering is load-bearing rather than decorative: `DROP DATABASE keycloak` fails while
Keycloak holds a connection to it.

**Consequence: pre-3.4 archives are refused, not silently mishandled.** `backups/` is
untracked, so real `postgres-*.sql.gz` files exist in working clones. A refusal naming the
file and the reason is the honest outcome; accepting one would mean dropping
`ON_ERROR_STOP=1` for that path, which is the partial-success NFR-5 forbids. This is a
`⚠ BREAKING` changelog entry.

**Why the captured realm JSON is not re-imported on restore.** Keycloak's entire state lives
in the `keycloak` Postgres database, which the Postgres restore rewrites — that is precisely
what AC3 describes. `kc.sh import --override` is remove-and-recreate, so importing the JSON
*after* the database restore would discard the state the database restore just returned. The
realm JSON is captured as a portable, diffable artifact (drop it into
`services/keycloak/seed/` and run `pixi run keycloak-reimport` to seed another stack); it is
not the restore path, and both scripts say so.

**Verified defect in `scripts/keycloak-export.sh`.** Against a live Postgres-backed
`quay.io/keycloak/keycloak:26.7.3`, `kc.sh export` exits 1 with `ERROR: Unable to start the
management interface on 0.0.0.0:9000 / Address already in use` — the same second-JVM
collision `scripts/keycloak-reimport.sh:29-34` already documents for `import`. Adding
`--http-management-port 9999` made the identical command exit 0 and write
`<realm>-realm.json`. The backup's capture carries the flag, the existing script is fixed,
and the register gains the sibling entry.

**Object storage: `compose cp`, not a tar stream.** The `pgsty/silo` image ships `mc`, `sh`,
`cut`, `tr` and `basename` and has no `tar`, `gzip`, `awk`, `sed`, `grep` or `find`
(verified), so the capture stages inside the container and travels as a directory. Verified
round trip: `mc mirror` each bucket into `/tmp/<stage>/<bucket>` (with `mkdir -p` first, so
an empty bucket survives as an empty directory), one `compose cp` out; then `compose cp`
back, `mc mb --ignore-existing`, `mc mirror --overwrite --remove` in — a deleted bucket was
recreated, a stray object removed and the captured objects returned. Bucket names come from
`mc ls --json local` rather than from `MINIO_BUCKETS`, so a bucket an application created is
captured too. Object *versions* are not: `mc mirror` moves current versions only, which the
manifest and the ADR state plainly.

**The manifest is the record of what was captured and what was skipped.** Restore reads the
component list from it and refuses when a component it names has no files, so a truncated
archive cannot restore quietly. Example shape:

```
# devinfra backup
created: 2026-09-09T15:30:00Z
selection: keycloak,mailpit,minio,postgres,redis
postgres: app_test devinfra keycloak
minio: artifacts backups uploads
keycloak: devinfra
skipped: pgadmin (not in the Selection)
```

## Verification

**Commands:**
- `pixi run lint-shell` -- expected: shellcheck-clean, including the two rewritten scripts
  and the new one.
- `pixi run lint-python` -- expected: `ruff format --check`, `ruff check` and `mypy --strict`
  pass over `scripts/`, the resolver's new mode included.
- `pixi run lint-gotchas` -- expected: still one OK line per Module with the new entry
  parsed in the four-field shape.
- `pixi run lint-endpoints` -- expected: unaffected; no new `${VAR:-default}` in a shell
  script diverges from `.env.example`.
- `pixi run test` -- expected: every rewritten and new self-test case passes by name.
- `pixi run select --dependents postgres` -- expected on a core Selection: `keycloak`; with
  `admin` in the Selection: `keycloak,pgadmin`; with a Selection of `postgres` alone: an
  empty line and exit 0; on an unknown module name: nothing on stdout, exit 1.
- `pixi run ci` -- expected: exit 0. This is the done-gate.
- `pixi run ci-stack` then `pixi run ci-stack-restore` -- expected against a real runtime:
  markers planted, volumes destroyed, restore applied, markers returned and the strict smoke
  suite green. If no container runtime is available in this environment, report it as not
  run rather than as passed.


## Auto Run Result

Status: done

**Implemented change.** `pixi run backup` captures every stateful Module in the current
Selection — the Postgres databases (one `pg_dump --create --clean --if-exists` each), the
object-storage bucket contents and the Keycloak realm — into one timestamped directory with
a `manifest.txt` recording what was captured and what was excluded, moved into place only on
success. `pixi run restore backups/<ts>` reconciles that manifest against the archive in
both directions, refuses any component the Selection excludes and any component, database,
bucket or realm whose files are absent, then stops the Postgres dependents the resolver
names, rewrites the databases under `psql -v ON_ERROR_STOP=1`, mirrors the objects back,
starts what it stopped and waits for health — exiting non-zero on any step. `pixi run
ci-stack-restore` drives the round trip against a real runtime and CI's `stack` job runs it.

**Files changed in this follow-up pass.**
- `scripts/lint_selftest.py` — six new self-test cases and two strengthened assertions:
  restore's object-storage branch, the `(no buckets)` marker on both sides, the by-name
  truncation refusals for buckets and the realm, the `skipped:` replay, the post-restart
  health wait, and a two-database ordering fixture.
- `scripts/backup.sh` — a fourth manifest `note:` recording Redis's deliberate exclusion.
- `docs/adr/0018-backup-follows-the-selection.md` — a Consequences paragraph: a restore
  applies the archive rather than resetting the stack to it.
- `README.md` — the round-trip paragraph no longer opens with the `Verified:` convention for
  an unrun check; the skipped-Module bullet is corrected to the three stateful Modules; a
  paragraph on what a restore leaves alone.
- `CHANGELOG.md` — the same two corrections in the Added entries.

**Review findings breakdown.** 38 findings across four layers: 0 high, 8 medium, 19 low,
11 false. Ten entries were patched (6 medium, 4 low), four carried as already-deferred
(DW-48 twice, DW-49 twice), and the rest rejected. No new deferrals. Rejected findings and
their reasons are recorded one per row in the triage log above; the recurring reasons were
a refutation at the cited location (11 findings), a fix that would edit this build's own
spec or the orchestrator-owned ledger (3), and a low-severity claim reachable only through
a hand-edited archive whose fix adds a guard (5).

**Follow-up review recommendation: false.** This pass patched no `high` entry, so the work
has converged. The two open risks are the deferred ones, both awaiting one CI run rather
than another review pass.

**Verification performed.**
- `pixi run ci` — exit 0. The done-gate: pre-commit, build, mypy, ruff and the full
  self-test, all clean.
- `pixi run lint-python` — `ruff format --check`, `ruff check` and `mypy --strict` clean
  over `scripts/`.
- `pixi run test` — every new case passes by name, including the twenty backup/restore
  assertions listed in the triage rows.
- Mutation check on the headline gap: inverting `mc mirror --overwrite --remove "$1/$2"
  "local/$2"` to write the live bucket into the archive stage now fails the suite, where
  before this pass it left it green.
- Not run: the round trip against a real runtime (`pixi run ci-stack` then
  `pixi run ci-stack-restore`). The container stack on this machine holds the operator's own
  data and the round trip destroys every volume — DW-48.

**Residual risks.** Both are the carried deferrals. `scripts/verify-restore.sh` and
restore's stop → apply → mirror → start → wait orchestration have executed only against the
recording stub, so the end-to-end proof is a CI job that has not run (DW-48); and the
`stack` job now performs three full bring-ups inside an unmeasured 15-minute budget (DW-47).
One green hosted `stack` run settles both.

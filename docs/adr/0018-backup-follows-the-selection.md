# 18. Backup follows the Selection

Date: 2026-09-09 · Status: Accepted

## Context

`scripts/backup.sh` dumped Postgres and nothing else. Object-storage contents and the
Keycloak realm were lost on any `pixi run destroy`, and nothing said so: the command printed
the archive it wrote and exited 0, so a developer who had just backed up believed the stack
was covered.

`scripts/restore.sh` was one line — `gunzip -c "$f" | compose exec -T postgres psql -U … -d
postgres` — and had three separate problems. It ran without `ON_ERROR_STOP`, so a stream
whose statements errored still exited 0 and a half-applied restore was reported as a
success. It rewrote the `keycloak` database underneath a running Keycloak, whose cached
state then disagreed with the database it was reading from — the same failure ADR 0013 and
AD-12 already describe for realm imports. And it accepted any path that existed, with no
record anywhere of what the archive was supposed to hold.

Verification made the underlying constraint plain. Against `pgvector/pgvector:0.8.6-pg17`, a
`pg_dumpall` stream opens with `CREATE ROLE devinfra;`, which aborts under
`ON_ERROR_STOP=1` against *any* initialized cluster — including an empty one, whose
bootstrap role initdb creates from `POSTGRES_USER`. `psql` then exits 3. So
`ON_ERROR_STOP=1` and a cluster-wide dump cannot both be had, and NFR-5 requires the former.

## Decision

**The Selection is the oracle for what a backup captures** (AD-16). Postgres, object storage
and the Keycloak realm are each captured only when their Module is in the resolved Selection
and recorded as skipped, with the reason, when they are not. A Module inside the Selection
that cannot be captured is a non-zero exit, never a smaller archive (NFR-5). Redis is
deliberately excluded: cache and in-flight task state, neither meaningful to restore. The
predicate is `selected` in `scripts/lib/common.sh`, valid only after a resolve.

**A backup is a timestamped directory carrying a manifest**, not a stream. `backups/<ts>/`
holds `manifest.txt`, `postgres/<db>.sql.gz`, `minio/<bucket>/…` and
`keycloak/<realm>-realm.json`. The manifest records the Selection, each captured component
with what it holds, and each skipped Module with its reason; restore reads its component
list from there and refuses a component whose files are missing, so a truncated archive
cannot restore quietly. The directory is written under a `.partial` name and moved into
place only on success, as the single archive was.

**Postgres is captured one database at a time, with `pg_dump --create --clean
--if-exists`.** That emits `DROP DATABASE IF EXISTS` + `CREATE DATABASE` + contents, which
was verified to restore into a separately initialized cluster at exit 0 with the rows
intact. The database list comes from `pg_database` on the server, never from
`POSTGRES_EXTRA_DATABASES`, so a database an application created is captured too. The
`postgres` maintenance database is the one exclusion: it is what restore connects to, and
`DROP DATABASE postgres` cannot run against the connection applying it.

**Restore follows stop → write → restart** (AD-12). Every service of every Module in the
Selection that transitively depends on `postgres` is stopped before the first write and
started after the last, then health is awaited — because `DROP DATABASE keycloak` fails
while Keycloak holds a connection to it. Which services those are comes from the resolver's
new third request form, `./scripts/select.sh --dependents postgres`, which answers in
*services* rather than Modules: `compose stop` speaks services, and a Module with a helper
must not be half-stopped. Nothing in either script names a dependent.

**Every restore `psql` runs with `-v ON_ERROR_STOP=1`, and every failure exits non-zero**
(NFR-5). A statement that errors ends the restore rather than being counted as applied, and
the dependents are started again on that path too, so a failed restore never also leaves the
stack half down. The self-test asserts the flag over the recorded argv and the ordering by
argv index.

**Object storage travels as a directory, not a tar stream.** The `pgsty/silo` image ships
`sh`, `mc`, `mkdir` and `rm` — the four the container-side programs here depend on — and has
no `tar`, `gzip`, `awk`, `sed`, `grep` or `find` (verified), so the capture stages inside the
container — `mc mirror` per bucket into
`/tmp/<stage>/<bucket>`, with `mkdir -p` first so an empty bucket survives as an empty
directory — and travels out through one `compose cp`. Restore reverses it with `compose cp`
in, `mc mb --ignore-existing` and `mc mirror --overwrite --remove`. Bucket names come from
`mc ls --json local`, not from `MINIO_BUCKETS`, so a bucket an application created is
captured. Object *versions* are not: `mc mirror` moves current versions only, and the
manifest says so.

**The captured realm JSON is not re-imported on restore.** Keycloak's entire state lives in
the `keycloak` Postgres database, which the Postgres restore rewrites. `kc.sh import
--override` is remove-and-recreate, so importing the JSON after the database restore would
discard exactly what that restore returned. The file is captured as a portable, diffable
artefact — drop it into `services/keycloak/seed/` and run `pixi run keycloak-reimport` to
seed another stack — and both scripts say so.

**Pre-3.4 `postgres-*.sql.gz` archives are refused, naming the file and the reason.**
`backups/` is untracked, so real ones sit in working clones. Accepting one would mean
dropping `ON_ERROR_STOP=1` for that path, which is the partial success NFR-5 forbids.

**The round trip is a named task CI runs.** `pixi run ci-stack-restore` drives
`scripts/verify-restore.sh`: it plants a marker row in Postgres and a marker object in
object storage, backs up, destroys every volume, brings the stack back, restores, asserts
both markers returned, and then runs the strict smoke suite. It attaches to the existing
`stack` job as a step rather than a fourth job, sharing that job's 15-minute budget, because
it is only meaningful against a stack that is already up.

## Rejected

**Keep `pg_dumpall` and drop `ON_ERROR_STOP=1`.** This is the status quo, and it is the
partial success NFR-5 exists to remove: a restore that half-applies exits 0 and reports that
it restored. Verified, not assumed — the stream's first statement fails against every
initialized cluster.

**Keep `pg_dumpall` and filter the `CREATE ROLE` lines out.** A second, hand-maintained idea
of what a cluster dump contains, wrong the first time the dump grows a statement the filter
does not know about, and silently so.

**Import the realm JSON on restore.** It would run *after* the `keycloak` database was
rewritten and, being remove-and-recreate, would discard the state the database restore had
just returned. The JSON is a seed artefact, not a restore path.

**Read the bucket list from `MINIO_BUCKETS` and the database list from
`POSTGRES_EXTRA_DATABASES`.** Both are hand-maintained statements of what the stack was
provisioned with, not of what it holds. A bucket or database an application created would be
lost with the build green — the same class of drift ADR 0017 removed from the endpoint
documentation.

**Accept a legacy archive by special-casing it.** See above: the only way to apply one is
without `ON_ERROR_STOP=1`.

**Add a fourth CI job for the round trip.** It needs a started, populated stack, which is
exactly what the `stack` job already has; a fourth job would start one again and pay for it
twice.

## Consequences

A backup covers the authored state in the Selection — Postgres databases, object storage
buckets and the Keycloak realm — and says in its manifest what it covered and what it did
not. It is not a volume-level image of the stack: eight Modules own a named volume whose
contents are derived rather than authored (grafana, loki, tempo, prometheus, flower,
pgadmin, redisinsight, mailpit) and are deliberately not captured, each named in the
manifest with that reason. Restoring derived data over freshly generated data would be a
regression, not a recovery.

This also means a backup is not a migration path for a runtime change: moving the stack to
a different container runtime starts every volume empty, and only the three captured
components come across. A restore either completes or exits non-zero having named the
step that failed, and never leaves dependents stopped.

Archives written before this change are refused, with a diagnostic naming the file and why.
There is no migration: take a fresh backup. This is the breaking change in the changelog.

Object versions are not captured, so a bucket restored from a backup carries the current
version of each object and no history. The manifest and this ADR both state it rather than
leaving it to be discovered.

Cluster globals are not captured either — roles, their passwords and grants, and
tablespaces. `pg_dumpall` carried them and `pg_dump` per database carries none, and the
`CREATE ROLE` line that makes a cluster dump unusable under `ON_ERROR_STOP=1` is exactly the
part that carried them. A restore therefore relies on the cluster it applies into already
having the bootstrap role initdb creates from `POSTGRES_USER`, which is what every stack
this repository starts has. A role a developer added by hand is not in the archive; the
manifest carries a `note:` saying so, alongside the object-versions one.

A restore applies the archive; it does not reset the stack to it. Both directions of the
manifest reconciliation refuse an archive that disagrees with itself, but a database or a
bucket that exists on the *server* and is named by no manifest line is left alone rather
than dropped — the archive is the record of what was captured, not a statement about what
else may exist. Within a bucket the manifest does name, `mc mirror --remove` makes that
bucket end as captured rather than as a union with what the re-provisioned stack seeded.

`POSTGRES_DB=postgres` is refused rather than backed up. `postgres` is the one database the
capture must exclude, so a stack keeping its application tables in it would otherwise
produce an archive holding none of them at exit 0.

The resolver gained a third request form. `--dependents <module>` is the only place the
"who breaks if I rewrite this" question is answered, so a thirteenth Module that grows a
Postgres edge is stopped by a restore without anyone editing a script.

`scripts/keycloak-export.sh` was fixed in passing: `kc.sh export` against a live container
exits 1 on the management port the running server holds, the same collision
`keycloak-reimport.sh` already documented for `import`. The export-side entry is now in
`services/keycloak/gotchas.md` with a `Verified by:` line, and the self-test asserts the
flag.

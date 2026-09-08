# postgres — gotchas

### The volume mount path is version-specific

- **Symptom:** The database silently loses everything on `down`, with no error anywhere.
- **Cause:** PostgreSQL 17 keeps `PGDATA` at `/var/lib/postgresql/data`; 18 moved it to
  `/var/lib/postgresql/18/docker` and declares the volume one level up at
  `/var/lib/postgresql`. Mount the wrong path for your major version and the data
  directory sits on the container layer, which `down` throws away.
- **Fix:** Change the mount in `services/postgres/compose.yaml` to match the major version
  before bumping the image.
- **Affected versions:** PostgreSQL 17 vs 18 (`pgvector/pgvector:0.8.6-pg17`).
- **Verified by:** `services/postgres/smoke.sh` — asserts the running server's
  `data_directory` sits inside one of the container's mounts.

### `seed/` runs once, only while the data volume is empty

- **Symptom:** Editing `seed/10-extensions.sql` or `seed/20-extra-databases.sh` has no
  effect on an existing volume.
- **Cause:** The official entrypoint runs `/docker-entrypoint-initdb.d` on an uninitialised
  `PGDATA` and never again.
- **Fix:** Create the object by hand — a database added to `POSTGRES_EXTRA_DATABASES` after
  first boot needs `create database` — or run `pixi run destroy`, which deletes everything.
- **Affected versions:** Not version-specific

### `POSTGRES_EXTRA_DATABASES` is comma-separated with no spaces

- **Symptom:** Keycloak fails to start because its database does not exist.
- **Cause:** The list is split on commas only, so a stray space becomes part of a database
  name — and Keycloak's database comes from this variable.
- **Fix:** Keep the value comma-separated with no spaces in `.env`, and keep `keycloak` in
  it.
- **Affected versions:** Not version-specific

### The config file is loaded explicitly, replacing the image default wholesale

- **Symptom:** A setting deleted from `conf/postgresql.conf` reverts to the server built-in
  rather than to whatever the image shipped.
- **Cause:** `command:` passes `-c config_file=/etc/postgresql/postgresql.conf`, so that
  file replaces the image's own configuration rather than layering on it.
- **Fix:** State every setting you depend on in `conf/postgresql.conf`; do not assume an
  image default survives.
- **Affected versions:** Not version-specific

### `synchronous_commit = off`

- **Symptom:** A crash can lose the last few committed transactions.
- **Cause:** The setting is off in `conf/postgresql.conf`, traded for local write speed.
- **Fix:** Local development only. Turn it on for anything whose durability matters.
- **Affected versions:** Not version-specific

### The healthcheck uses `-h 127.0.0.1` deliberately

- **Symptom:** Dropping the flag looks like a simplification and makes the probe weaker.
- **Cause:** Without it `pg_isready` proves only the local Unix socket, not the TCP
  listener every other container connects over.
- **Fix:** Keep `-h 127.0.0.1` in the healthcheck in `services/postgres/compose.yaml`.
- **Affected versions:** Not version-specific

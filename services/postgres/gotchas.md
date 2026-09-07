# postgres — gotchas

- **The volume mount path is version-specific.** PostgreSQL 17 keeps `PGDATA` at
  `/var/lib/postgresql/data`; 18 moved it to `/var/lib/postgresql/18/docker` and
  declares the volume one level up at `/var/lib/postgresql`. Using the wrong path
  for your major version gives you a database that silently loses everything on
  `down`, with no error anywhere. Change the mount in `compose.yaml` if you ever
  move to 18.
- **`seed/` runs once, only while the data volume is empty.** The official
  entrypoint runs `/docker-entrypoint-initdb.d` on an uninitialised `PGDATA` and
  never again, so editing `seed/10-extensions.sql` or
  `seed/20-extra-databases.sh` has no effect on an existing volume. Adding a
  database to `POSTGRES_EXTRA_DATABASES` after first boot needs `create database`
  by hand — or `pixi run destroy`, which deletes everything.
- **`POSTGRES_EXTRA_DATABASES` is comma-separated with no spaces.** Keycloak's
  database comes from here; drop it and Keycloak fails to start.
- **The config file is loaded explicitly.** `command:` passes
  `-c config_file=/etc/postgresql/postgresql.conf`, so `conf/postgresql.conf`
  replaces the image default wholesale rather than layering on it. A setting
  removed from that file reverts to the server built-in, not to the image's.
- **`synchronous_commit = off`.** Local development only — a crash can lose the
  last few committed transactions.
- **The healthcheck uses `-h 127.0.0.1` deliberately**, so it proves the TCP
  listener is up rather than only the local socket.

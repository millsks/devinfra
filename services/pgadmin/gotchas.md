# pgadmin — gotchas

### pgAdmin validates `PGADMIN_DEFAULT_EMAIL` and refuses to start otherwise

- **Symptom:** The container exits during startup complaining about the email address.
- **Cause:** It rejects both a bare `dev@localhost` and the special-use TLDs `.local` and
  `.test`.
- **Fix:** Use a syntactically ordinary address — `dev@example.com` is what
  `services/pgadmin/compose.yaml` ships.
- **Affected versions:** `dpage/pgadmin4:9.17`.

### The smoke check is liveness, not function

- **Symptom:** `/misc/ping` returns 200 from a pgAdmin that came up with no server
  registration at all, so a green check says nothing about the registration.
- **Cause:** A mistyped `./conf/servers.json` leaves the container runtime creating the
  path as an empty directory, and pgAdmin starts anyway.
- **Fix:** `assert_config.py` asserts every bind source exists and is not an empty
  directory — this Module is the case that motivated that rule. Upgrading the check itself
  to a real assertion is a change of behaviour story 2-4 did not make, and the healthcheck
  added there has the same shape and the same limit.
- **Affected versions:** Not version-specific

### `conf/servers.json` is read at startup only

- **Symptom:** Editing the file after first boot has no visible effect.
- **Cause:** It is bind-mounted read-only, and pgAdmin copies the registration into
  `pgadmin-data` on first boot and never looks at the file again.
- **Fix:** Destroy the `pgadmin-data` volume, or re-register the server through the UI,
  after changing the file.
- **Affected versions:** Not version-specific

### The container runs as uid and gid `5050`

- **Symptom:** A bind mount put where `pgadmin-data` goes is unwritable and pgAdmin fails
  to start.
- **Cause:** The volume must be writable by uid 5050, which a fresh named volume is —
  the runtime creates it owned by the container's user — and a host directory is not.
- **Fix:** Keep `pgadmin-data` a named volume rather than a bind mount.
- **Affected versions:** `dpage/pgadmin4:9.17`.

### Server mode and the master password are both off

- **Symptom:** pgAdmin skips the login screen and the master-password prompt entirely.
- **Cause:** `PGADMIN_CONFIG_SERVER_MODE: "False"` and
  `PGADMIN_CONFIG_MASTER_PASSWORD_REQUIRED: "False"`.
- **Fix:** Local development only. Leave the published port on `127.0.0.1` and never carry
  these settings anywhere else.
- **Affected versions:** Not version-specific

# pgadmin — gotchas

- **pgAdmin validates `PGADMIN_DEFAULT_EMAIL`** and refuses to start otherwise.
  It rejects both bare `dev@localhost` and special-use TLDs (`.local`, `.test`),
  hence `dev@example.com`.
- **The smoke check is liveness, not function.** `/misc/ping` returns 200 from a
  pgAdmin that came up with no server registration at all — a mistyped
  `./conf/servers.json` leaves Docker creating the path as an empty directory,
  and the check still passes. That is why `assert_config.py` asserts every bind
  source exists and is not an empty directory: this Module is the case that
  motivated the rule. The healthcheck added in story 2-4 has the same shape and
  the same limit; upgrading either to a real assertion is a change of behaviour
  this story did not make.
- **`conf/servers.json` is read at startup only.** It is bind-mounted read-only;
  pgAdmin copies the registration into `pgadmin-data` on first boot and never
  looks at the file again. Editing it after that has no visible effect.
- **The container runs as `5050:5050`.** The `pgadmin-data` volume must be
  writable by that uid, which it is because Docker creates a fresh named volume
  owned by the container's user. A bind mount put there instead would not be.
- **`PGADMIN_CONFIG_SERVER_MODE: "False"` and
  `PGADMIN_CONFIG_MASTER_PASSWORD_REQUIRED: "False"`** skip the login and the
  master-password prompt. Local development only.

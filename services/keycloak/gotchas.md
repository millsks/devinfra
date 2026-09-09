# keycloak — gotchas

### A `clientScopes` array in a realm import replaces Keycloak's built-ins

- **Symptom:** Every client in the imported realm loses `profile`, `email`, `roles` and
  friends, and tokens come back without the claims those scopes carry.
- **Cause:** The array replaces the built-in scope set rather than adding to it.
- **Fix:** Leave `clientScopes` out of `seed/devinfra-realm.json`. The audience and role
  mappers are attached per-client instead.
- **Affected versions:** Keycloak 26.x (`quay.io/keycloak/keycloak:26.7.3`).

### A realm-level `passwordPolicy` is enforced against imported users

- **Symptom:** The container fails its whole boot on the realm import, not just the one
  user.
- **Cause:** The policy is applied to users the import creates, so a `length(4)` policy
  rejects the user `dev` whose password is `dev`.
- **Fix:** Keep `passwordPolicy` out of the seed realm, or give every seeded user a
  password that satisfies it.
- **Affected versions:** Keycloak 26.x (`quay.io/keycloak/keycloak:26.7.3`).

### The image has `bash` but no `curl`, `wget` or `nc`

- **Symptom:** A healthcheck written with any of those three reports the container
  unhealthy forever, because the binary is not there to run.
- **Cause:** The Keycloak image ships none of them.
- **Fix:** Drive the request over bash's `/dev/tcp`, which is what the healthcheck in
  `services/keycloak/compose.yaml` does.
- **Affected versions:** Keycloak 26.x (`quay.io/keycloak/keycloak:26.7.3`).

### The startup import ignores an existing realm; `kc.sh import --override` replaces one

- **Symptom:** Editing `seed/devinfra-realm.json` and restarting has no effect on a realm
  that is already there, and the log says `Realm '<name>' already exists. Import skipped`.
- **Cause:** `start-dev --import-realm` hard-codes `Strategy.IGNORE_EXISTING`, and no flag
  or environment variable changes it — the `IMPORT` option category is removed from `start`
  and `start-dev`, so there is no `KC_IMPORT_REALM` and a `KC_OVERRIDE` in `environment:`
  does nothing. The separate `kc.sh import --file` command takes `--override <true|false>`,
  defaulting to true, and that path *is* remove-and-recreate rather than a merge: runtime
  state in the target realm that the JSON does not carry is lost, while the `keycloak`
  database and every other realm survive.
- **Fix:** Run `pixi run keycloak-reimport`, which imports with `--override true` and then
  restarts the container, or `pixi run keycloak-export` to write the live realm back over
  the JSON instead.
- **Affected versions:** `--override` since Keycloak 21.1.0; the ignore-existing startup
  behaviour verified against 26.4.0 and shipped by the pinned 26.7.3.

### `kc.sh import` against a live container exits non-zero on the management port

- **Symptom:** The import writes the realm to the database and *still* exits non-zero,
  failing to bind the management interface. Under `set -e` that reads as a failed import
  when the data actually landed.
- **Cause:** The import runs as a second JVM inside the container and tries to bind port
  9000, which the running server already holds.
- **Fix:** Pass `--http-management-port 9999` — any free in-container port — and the
  command exits 0. Environment variables such as `KC_DB*` are inherited, so nothing else
  needs restating.
- **Affected versions:** Verified against Keycloak 26.4.0; the pinned 26.7.3 publishes the
  same management port 9000.

### An out-of-band import leaves the running server serving stale realm data

- **Symptom:** The database and the admin API disagree silently: a probe realm read back
  `PROBE-V2` from Postgres while the admin API still answered `PROBE-V1`, with no error on
  either side.
- **Cause:** The import command is a separate JVM against the same database and never
  attaches to the running server's cache cluster, so the server keeps serving what it
  cached at boot.
- **Fix:** Restart the container after any import. `scripts/keycloak-reimport.sh` does that
  unconditionally, and the restart is not optional or retry-gated (AD-12).
- **Affected versions:** Verified against Keycloak 26.4.0; the pinned 26.7.3 runs the same
  `start-dev` cache configuration.

### The issuer is pinned by `KC_HOSTNAME`, regardless of `BIND_ADDRESS`

- **Symptom:** A client rejects a token whose `iss` does not match its discovery document,
  even though the stack is bound somewhere other than `localhost`.
- **Cause:** `KC_HOSTNAME` fixes the issuer at `http://localhost:${KEYCLOAK_PORT}` whatever
  `BIND_ADDRESS` says.
- **Fix:** Read and assert the issuer against `localhost`, not against the bind address.
- **Affected versions:** Keycloak 26.x (`quay.io/keycloak/keycloak:26.7.3`).
- **Verified by:** `services/keycloak/smoke.sh` — asserts the discovery document's `issuer`
  against `http://localhost:${KEYCLOAK_PORT}`.

### `KEYCLOAK_MGMT_PORT` is missing from `scripts/urls.sh`

- **Symptom:** A developer reading that script finds no `/health/ready` and no `/metrics`
  for this Module, even though both are published.
- **Cause:** `urls.sh` is hand-maintained and has drifted from what the Module declares.
- **Fix:** Read `x-endpoints:` in `services/keycloak/compose.yaml`, which is the complete
  list. Generating `urls.sh` from it is story 3-3, so the drift is recorded here rather
  than half-fixed.
- **Affected versions:** Not version-specific

### `start-dev` is a development mode and nothing else

- **Symptom:** No TLS, no hostname strictness, and caching disabled.
- **Cause:** `command:` is `["start-dev", "--import-realm"]`, chosen so the stack boots
  without certificates.
- **Fix:** Local development only. Never carry this command anywhere else.
- **Affected versions:** Keycloak 26.x (`quay.io/keycloak/keycloak:26.7.3`).

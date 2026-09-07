# keycloak — gotchas

- **A `clientScopes` array in a realm import replaces Keycloak's built-ins**,
  which strips `profile`, `email`, `roles` and friends from every client. The
  audience and role mappers in `seed/devinfra-realm.json` are attached per-client
  instead.
- **A realm-level `passwordPolicy` is enforced against imported users.** A
  `length(4)` policy makes the import of a user with password `dev` fail the
  whole boot.
- **The image has `bash` but no `curl`, `wget` or `nc`**, so the healthcheck
  drives a raw HTTP request over bash's `/dev/tcp`.
- **`--import-realm` only creates realms that do not already exist.** Editing
  `seed/devinfra-realm.json` has no effect on a realm that is already there. Use
  `pixi run keycloak-reimport` (drops the realm, destroys realm state) or
  `pixi run keycloak-export` (writes the live realm back over the JSON).
- **The issuer is pinned by `KC_HOSTNAME` to `http://localhost:${KEYCLOAK_PORT}`,
  regardless of `BIND_ADDRESS`.** Clients reject tokens whose issuer does not
  match their discovery document, so the smoke check asserts the issuer against
  `localhost`, not against the bind address.
- **`KEYCLOAK_MGMT_PORT` is missing from `scripts/urls.sh`.** `/health/ready` and
  `/metrics` live on it and a developer reading that script will not find them.
  `x-endpoints:` in `compose.yaml` is the complete list; generating `urls.sh`
  from it is story 3-3, so the drift is recorded here rather than half-fixed.
- **`start-dev`.** No TLS, no hostname strictness, caching disabled. Never in
  production.

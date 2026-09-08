# redisinsight — gotchas

### The smoke check is liveness, not function

- **Symptom:** A RedisInsight that started but never reached Redis answers the check
  exactly as a working one does, so a green check says nothing about the connection.
- **Cause:** The check asks for `/` and accepts 200 or 302; the healthcheck added in story
  2-4 uses `/api/health` and has the same limit.
- **Fix:** Read a pass as "the UI is up" and nothing more. Upgrading either to a real
  assertion — querying the registered database through the API — is a change of behaviour
  story 2-4 did not make.
- **Affected versions:** Not version-specific

### `RI_APP_HOST: 0.0.0.0` binds inside the container only

- **Symptom:** The value reads as though the stack has been opened to the LAN.
- **Cause:** It is the in-container bind address; the published port is still
  `${BIND_ADDRESS}:${REDISINSIGHT_PORT}`.
- **Fix:** Read `BIND_ADDRESS` for the host binding, not this variable.
- **Affected versions:** Not version-specific

### The Redis connection is auto-added on first start, and only on first start

- **Symptom:** Changing `RI_REDIS_PASSWORD` in `.env` does not update the registered
  database, and the UI keeps failing to connect.
- **Cause:** The registration is written into `redisinsight-data` from `RI_REDIS_*` the
  first time the container boots, and never reconciled afterwards.
- **Fix:** Re-add the database by hand through the UI, or destroy the
  `redisinsight-data` volume and let it be re-created.
- **Affected versions:** `redis/redisinsight:3.8.0`.

### No `conf/` and no `seed/`

- **Symptom:** There is nowhere in this Module to put a configuration file.
- **Cause:** The volume is the whole of this Module's state, which is why a `seed.none`
  marker sits beside `compose.yaml`.
- **Fix:** Configure it through `RI_*` environment variables. This is also why
  `services/redisinsight/` is where the self-test plants fixtures that must reach no
  container.
- **Affected versions:** Not version-specific

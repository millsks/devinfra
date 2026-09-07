# redisinsight — gotchas

- **The smoke check is liveness, not function.** It asks for `/` and accepts 200
  or 302. A RedisInsight that started but never reached Redis answers it exactly
  the same way, so a green check says the UI is up and says nothing about the
  connection. The healthcheck added in story 2-4 uses `/api/health` and has the
  same limit. Upgrading either to a real assertion — querying the registered
  database through the API — is a change of behaviour this story did not make.
- **`RI_APP_HOST: 0.0.0.0` binds inside the container only.** The published port
  is still `${BIND_ADDRESS}:${REDISINSIGHT_PORT}`, so the stack stays off the
  LAN. Do not read that value as the host binding.
- **The Redis connection is auto-added from `RI_REDIS_*` on first start**, and
  only on first start: once `redisinsight-data` holds the registration, changing
  `RI_REDIS_PASSWORD` in `.env` does not update it. Re-adding the database by
  hand, or destroying the volume, is the fix.
- **No `conf/` and no `seed/`.** The volume is the whole of this Module's state,
  which is why `services/redisinsight/` is where the self-test plants fixtures
  that must reach no container.

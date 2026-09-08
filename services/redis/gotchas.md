# redis — gotchas

### `maxmemory-policy` must stay `noeviction`

- **Symptom:** Queued Celery tasks disappear under memory pressure, and nothing reports it.
- **Cause:** This instance is the Celery broker; any LRU policy is free to evict queue
  entries, which Redis treats as ordinary keys.
- **Fix:** Keep `maxmemory-policy noeviction` in `services/redis/conf/redis.conf`.
- **Affected versions:** Not version-specific
- **Verified by:** `services/redis/smoke.sh` — asserts `config get maxmemory-policy`
  returns `noeviction`.

### The password is passed on the command line, not in `conf/redis.conf`

- **Symptom:** A `requirepass` line added to the config file has no effect and the two
  places disagree silently.
- **Cause:** `command:` appends `--requirepass ${REDIS_PASSWORD}`, and the flag overrides
  the config file.
- **Fix:** Change `REDIS_PASSWORD` in `.env`; leave `requirepass` out of the config file.
- **Affected versions:** Not version-specific

### `conf/redis.conf` replaces the image defaults wholesale

- **Symptom:** A directive deleted from the file reverts to the Redis built-in rather than
  to whatever the image shipped.
- **Cause:** `command:` names the file as the config file, so it is the whole configuration
  rather than an overlay.
- **Fix:** State every directive you depend on in `services/redis/conf/redis.conf`.
- **Affected versions:** Not version-specific

### Three databases, by convention only

- **Symptom:** Nothing stops a consumer from using the wrong database number, and Redis
  reports no conflict when two do.
- **Cause:** db 0 is the cache, db 1 is `REDIS_BROKER_DB` and db 2 is `REDIS_BACKEND_DB` —
  numbers that exist in `.env.example` and in every consumer's URL and nowhere else.
- **Fix:** Read the numbers out of `.env` rather than hard-coding them in a consumer.
- **Affected versions:** Not version-specific

### Unauthenticated clients get `NOAUTH`, not a connection refusal

- **Symptom:** An anonymous client connects successfully and only the command fails.
- **Cause:** Redis authenticates per command, so the TCP connection is accepted either way.
- **Fix:** Assert the `NOAUTH` string rather than a refused connection — a Redis that
  accepted anonymous traffic would still answer `PING` and look healthy.
- **Affected versions:** Not version-specific
- **Verified by:** `services/redis/smoke.sh` — asserts an unauthenticated `PING` answers
  `NOAUTH`.

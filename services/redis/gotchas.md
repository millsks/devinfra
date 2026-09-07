# redis — gotchas

- **`maxmemory-policy` must stay `noeviction`.** This instance is the Celery
  broker: an LRU policy would silently discard queued tasks under memory
  pressure, and nothing would report it. The smoke check asserts it for that
  reason.
- **The password is passed on the command line, not in `conf/redis.conf`.**
  `command:` appends `--requirepass ${REDIS_PASSWORD}`, so a `requirepass` line
  added to the config file would be overridden by the flag and the two would
  disagree silently.
- **`conf/redis.conf` replaces the image defaults wholesale**, because
  `command:` names it as the config file. A directive deleted from it reverts to
  the Redis built-in, not to the image's.
- **Three databases, by convention only.** db 0 cache, db 1 `REDIS_BROKER_DB`,
  db 2 `REDIS_BACKEND_DB`. Redis enforces none of that; the numbers exist in
  `.env.example` and in every consumer's URL.
- **Unauthenticated clients get `NOAUTH`, not a connection refusal.** The smoke
  check asserts that string: a Redis that accepted anonymous traffic would still
  answer `PING` and look healthy.

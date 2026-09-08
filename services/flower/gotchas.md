# flower — gotchas

### An empty cluster is the expected state

- **Symptom:** The dashboard lists no workers and no tasks, on a Flower that started
  cleanly and answers `/healthcheck`.
- **Cause:** Flower monitors whatever Celery workers connect to the same broker, and this
  stack ships none.
- **Fix:** Point a worker on your host at
  `redis://:...@localhost:${REDIS_PORT}/${REDIS_BROKER_DB}`. Until one connects, the empty
  dashboard is correct rather than broken.
- **Affected versions:** Not version-specific

### `FLOWER_UNAUTHENTICATED_API: "true"` takes no credentials at all

- **Symptom:** Every REST endpoint answers an unauthenticated request.
- **Cause:** `FLOWER_UNAUTHENTICATED_API: "true"` is set in `compose.yaml`, deliberately,
  so the smoke check can read `/api/workers` without minting anything.
- **Fix:** Local development only. Leave the published port on `127.0.0.1`, which is what
  keeps the open API off the network, and never carry this setting anywhere else.
- **Affected versions:** Not version-specific

### The smoke check and the healthcheck read different endpoints

- **Symptom:** `/api/workers` and `/healthcheck` look like two spellings of the same
  liveness probe, and one of them looks safe to drop.
- **Cause:** They prove different things. `/api/workers` proves the API answers with the
  broker attached; `/healthcheck` is the cheap liveness probe Docker polls every 15
  seconds.
- **Fix:** Keep both. Collapsing the smoke check onto `/healthcheck` would leave a Flower
  that never reached Redis passing.
- **Affected versions:** Not version-specific

### `--persistent=True` needs the volume

- **Symptom:** The task history resets on every restart, with no error anywhere.
- **Cause:** State goes to `/data/flower.db` on the `flower-data` volume; with the mount
  dropped, `--persistent=True` writes into the container layer and loses it on recreate.
- **Fix:** Keep the `flower-data:/data` mount in `services/flower/compose.yaml` for as long
  as `--persistent=True` is in `command:`.
- **Affected versions:** Not version-specific

### Broker and backend are different Redis databases

- **Symptom:** Queued tasks and task results share one keyspace, and a result overwrites or
  is overwritten by a queue entry.
- **Cause:** `REDIS_BROKER_DB` (1) and `REDIS_BACKEND_DB` (2) are separate numbers by
  convention only; Redis enforces nothing.
- **Fix:** Keep the two variables at different database numbers in `.env`.
- **Affected versions:** Not version-specific

# flower — gotchas

- **An empty cluster is the expected state.** Flower monitors whatever Celery
  workers connect to the same broker, and this stack ships none. Until a worker
  on your host points at `redis://:...@localhost:${REDIS_PORT}/${REDIS_BROKER_DB}`
  the dashboard is empty, and that is correct rather than broken.
- **`FLOWER_UNAUTHENTICATED_API: "true"`.** The REST API takes no credentials at
  all. Local development only; the port binds to `127.0.0.1`.
- **The smoke check reads `/api/workers`, the healthcheck reads
  `/healthcheck`.** They are different endpoints on purpose: `/api/workers`
  proves the API answers with the broker attached, `/healthcheck` is the cheap
  liveness probe Docker polls every 15 seconds.
- **`--persistent=True` needs the volume.** State goes to `/data/flower.db` on
  `flower-data`; drop the volume and the task history resets on every restart
  with no error.
- **Broker and backend are different Redis databases.** `REDIS_BROKER_DB` (1)
  and `REDIS_BACKEND_DB` (2). Pointing both at the same database mixes queued
  tasks with results.

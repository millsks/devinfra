# loki — gotchas

### Loki 3.x needs `allow_structured_metadata: true` to accept OTLP at all

- **Symptom:** Every OTLP log push is refused and nothing reaches Loki, on a Loki that is
  otherwise healthy and answers `/ready`.
- **Cause:** OTLP ingestion carries structured metadata, which 3.x refuses unless the limit
  is enabled; the dedicated `loki` exporter that avoided it is deprecated in the collector.
- **Fix:** Keep `allow_structured_metadata: true` in `conf/loki-config.yaml` and keep the
  collector pointed at Loki's native `/otlp` endpoint, configured under
  `services/otel-collector/conf/`.
- **Affected versions:** Loki 3.x (`grafana/loki:3.7.7`).

### The pinned image is distroless and carries no healthcheck

- **Symptom:** There is no shell and no HTTP client in the container, so any `healthcheck:`
  written for it fails on a missing binary.
- **Cause:** `grafana/loki:3.7.7` holds `/usr/bin/loki` and nothing else. This is a
  property of the pinned tag, not of Loki: 3.5.7 still shipped `/busybox`.
- **Fix:** The exemption is declared in `healthcheck.none` beside `compose.yaml`.
  Re-verify against the image on any version bump before assuming either way.
- **Affected versions:** `grafana/loki:3.7.7` is distroless; 3.5.7 was not.

### `wait-healthy.sh` can only see that this container is running

- **Symptom:** The wait returns for a Loki that is up but not yet ready, and the next check
  reads a not-ready backend as lost data.
- **Cause:** With no healthcheck there is nothing for the wait to wait on.
- **Fix:** `services/loki/smoke.sh` polls `/ready` from the host before the OTLP
  round-trip, and `SMOKE_STRICT=1` fails on it rather than skipping.
- **Affected versions:** Not version-specific

### Retention is until compaction, not a wall clock

- **Symptom:** Log volume does not fall on any schedule you can name, unlike Prometheus's
  15 days.
- **Cause:** Retention here is bounded by the compactor's schedule in
  `conf/loki-config.yaml`.
- **Fix:** Change the compactor settings in that file, or use `pixi run destroy` as the
  blunt fix if the volume grows inconveniently.
- **Affected versions:** Not version-specific

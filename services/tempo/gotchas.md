# tempo — gotchas

### Tempo's span metrics need `--web.enable-remote-write-receiver` on Prometheus

- **Symptom:** Grafana's service map stays permanently empty even though traces arrive.
- **Cause:** The `metrics_generator` block in `conf/tempo.yaml` remote-writes span metrics
  to Prometheus, which refuses them unless the receiver flag is set. The two halves are one
  feature split across two Modules.
- **Fix:** Keep `--web.enable-remote-write-receiver` in `command:` in
  `services/prometheus/compose.yaml` alongside the generator block here.
- **Affected versions:** `grafana/tempo:3.0.3` with `prom/prometheus:v3.14.0`.
- **Verified by:** `scripts/lint_selftest.py` — pins the flag in
  `services/prometheus/compose.yaml`.

### The pinned image is distroless and carries no healthcheck

- **Symptom:** There is no shell and no HTTP client in the container, so any `healthcheck:`
  written for it fails on a missing binary.
- **Cause:** `grafana/tempo:3.0.3` holds `/tempo` and nothing else. This is a property of
  the pinned tag, not of Tempo: 2.9.0 still shipped `/busybox`.
- **Fix:** The exemption is declared in `healthcheck.none` beside `compose.yaml`.
  Re-verify against the image on any version bump before assuming either way.
- **Affected versions:** `grafana/tempo:3.0.3` is distroless; 2.9.0 was not.

### `wait-healthy.sh` can only see that this container is running

- **Symptom:** The wait returns for a Tempo that is up but not yet ready, and the next
  check reads a not-ready backend as a lost trace.
- **Cause:** With no healthcheck there is nothing for the wait to wait on.
- **Fix:** `services/tempo/smoke.sh` polls `/ready` from the host before the OTLP
  round-trip, and `SMOKE_STRICT=1` fails on it rather than skipping.
- **Affected versions:** Not version-specific

### A trace is not searchable the instant it is accepted

- **Symptom:** A search immediately after a successful push returns nothing.
- **Cause:** Tempo moves the span from the WAL into a block before search can see it.
- **Fix:** Retry. The round-trip check retries for 60 seconds instead of asserting
  immediately, for this reason.
- **Affected versions:** Not version-specific

### `TEMPO_PORT` is missing from `scripts/urls.sh`

- **Symptom:** A developer reading that script does not find this Module at all.
- **Cause:** `urls.sh` is hand-maintained and has drifted from what the Module declares.
- **Fix:** Read `x-endpoints:` in `services/tempo/compose.yaml`, which is the complete
  list. Generating `urls.sh` from it is story 3-3, so the drift is recorded here rather
  than half-fixed.
- **Affected versions:** Not version-specific

### Retention is 7 days

- **Symptom:** Traces older than a week are simply gone.
- **Cause:** The retention window is set in `conf/tempo.yaml`.
- **Fix:** Raise it there if you need longer; `pixi run destroy` is the blunt fix if the
  volume grows inconveniently.
- **Affected versions:** Not version-specific

# prometheus — gotchas

### Tempo's span metrics need `--web.enable-remote-write-receiver`

- **Symptom:** Grafana's service map stays permanently empty, and Tempo's remote writes are
  refused with nothing in Grafana saying so.
- **Cause:** Prometheus does not accept remote-write traffic unless the receiver is
  enabled, and Tempo's `metrics_generator` block writes span metrics here.
- **Fix:** Keep `--web.enable-remote-write-receiver` in `command:` in
  `services/prometheus/compose.yaml`. It is there for that reason alone.
- **Affected versions:** `prom/prometheus:v3.14.0` with `grafana/tempo:3.0.3`.
- **Verified by:** `scripts/lint_selftest.py` — pins the flag in this Module's
  `compose.yaml`.

### A `query_range` with a large step can miss a short-lived series

- **Symptom:** Prometheus reports nothing for data that is demonstrably present.
- **Cause:** Every evaluation point of a wide-step `query_range` can land outside the
  5-minute lookback window.
- **Fix:** Use `/api/v1/series`, which is step-independent, or a small `step`. The smoke
  check uses `/api/v1/series` for exactly this reason.
- **Affected versions:** Not version-specific

### The scrape-target check belongs to this Module even though the OTLP round-trip proves it

- **Symptom:** Before story 2-4 a stack without the collector neither passed, failed nor
  skipped this check, and nothing said so.
- **Cause:** It sat inside the collector's block, gated on `running otel-collector` and
  carrying no `else` arm at all.
- **Fix:** It now lives in `services/prometheus/smoke.sh` with a skip arm, so a Selection
  without the collector says so out loud.
- **Affected versions:** Not version-specific

### Retention is 15 days

- **Symptom:** Metrics older than a fortnight are simply gone.
- **Cause:** `--storage.tsdb.retention.time=15d` in `command:`.
- **Fix:** Raise the flag if you need longer; `pixi run destroy` is the blunt fix if the
  volume grows inconveniently.
- **Affected versions:** Not version-specific

### `--web.enable-lifecycle` exposes `/-/reload`

- **Symptom:** Anything that can reach the port can make Prometheus reload its
  configuration.
- **Cause:** The flag is in `command:`, for the local convenience of reloading without a
  restart.
- **Fix:** Keep this port bound to `127.0.0.1`; that is another reason it must stay there.
- **Affected versions:** Not version-specific

# prometheus — gotchas

- **Tempo's span metrics need `--web.enable-remote-write-receiver`** on
  Prometheus, or those writes are refused and Grafana's service map stays
  permanently empty. The flag is in `command:` for that reason alone.
- **When querying for a short-lived series, use `/api/v1/series` or a small
  `step`.** A `query_range` with a large step can land every evaluation point
  outside the 5-minute lookback window and report nothing for data that is
  present. The smoke check uses `/api/v1/series`, which is step-independent, for
  exactly this reason.
- **The scrape-target check belongs to this Module even though the OTLP
  round-trip proves it.** Before story 2-4 it sat inside the collector's block,
  gated on `running otel-collector` and carrying no `else` arm at all, so a stack
  without the collector skipped it silently. It now lives here with a skip arm.
- **Retention is 15 days** (`--storage.tsdb.retention.time=15d`). Anything older
  is gone; if the volume grows inconveniently, `pixi run destroy` is the blunt
  fix.
- **`--web.enable-lifecycle` exposes `/-/reload`.** Convenient locally, and
  another reason this port must stay bound to `127.0.0.1`.

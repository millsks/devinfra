# otel-collector — gotchas

### The image is distroless and carries no healthcheck

- **Symptom:** `sh` is not on the container's PATH and it ships no HTTP client, so any
  `healthcheck:` written for it fails on a missing binary.
- **Cause:** The pinned collector image is distroless.
- **Fix:** The exemption is declared in `healthcheck.none` beside `compose.yaml`. This is
  the one Module whose smoke check already proves more than a healthcheck could: a real
  trace, log line and metric posted over OTLP from the host and read back out of Tempo,
  Loki and Prometheus.
- **Affected versions:** `otel/opentelemetry-collector-contrib:0.160.0`.

### Loki ingests via its native `/otlp` endpoint

- **Symptom:** Logs configured through the collector's dedicated `loki` exporter either
  warn as deprecated or are refused outright by Loki.
- **Cause:** That exporter is deprecated, and Loki 3.x additionally needs
  `allow_structured_metadata: true` before it will accept OTLP at all.
- **Fix:** Export to Loki's `/otlp` endpoint from `services/otel-collector/conf/`, and keep
  the limit enabled in `services/loki/conf/loki-config.yaml`.
- **Affected versions:** Loki 3.x (`grafana/loki:3.7.7`).

### A brace-heavy JSON literal inside `"$( ... )"` is brace-expanded by the shell

- **Symptom:** The payload is shredded into fragments and one request fires per fragment,
  so the backend receives several malformed pushes instead of one good one.
- **Cause:** Brace expansion happens before the command substitution's output is used.
- **Fix:** Build every OTLP payload into a variable first, which is what
  `services/otel-collector/smoke.sh` does, for that reason and no other.
- **Affected versions:** Not version-specific

### The three ingest verdicts share one 60-second poll

- **Symptom:** The three assertions are not independent, which looks like a shortcut.
- **Cause:** They all read the same emitted `TRACE_ID`/`MARKER`, so splitting them would
  triple the wait for no extra evidence.
- **Fix:** Leave the shared poll. The backends are pre-waited silently first, so a Tempo
  that never came up reads as itself rather than as a lost trace.
- **Affected versions:** Not version-specific

### `depends_on` here is list form, so it waits for start, not for health

- **Symptom:** The collector starts before Loki and Tempo are ready and the first pushes go
  nowhere.
- **Cause:** Loki and Tempo carry no healthcheck to wait on — see their `healthcheck.none`
  markers — so the long form has no condition to use.
- **Fix:** Keep the pre-wait in `services/otel-collector/smoke.sh`, which is why it exists
  at all.
- **Affected versions:** Not version-specific

### Two ports, one pipeline

- **Symptom:** `OTEL_GRPC_PORT` and `OTEL_HTTP_PORT` look like two different ingest paths
  with different capabilities.
- **Cause:** 4317 (gRPC) and 4318 (HTTP) accept the same signals into the same pipeline.
- **Fix:** Use either. The smoke check uses HTTP because `curl` is already a required tool.
- **Affected versions:** Not version-specific

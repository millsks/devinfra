# otel-collector — gotchas

- **The image is distroless and carries no healthcheck.** `sh` is not on its
  PATH and it ships no HTTP client, so the exemption is declared in
  `healthcheck.none` beside `compose.yaml`. It is the one Module whose smoke
  check already proves more than a healthcheck could: a real trace, log line and
  metric posted over OTLP from the host and read back out of Tempo, Loki and
  Prometheus.
- **Loki ingests via its native `/otlp` endpoint.** The dedicated `loki`
  exporter in the collector is deprecated, and Loki 3.x additionally needs
  `allow_structured_metadata: true` to accept OTLP at all.
- **A brace-heavy JSON literal written inline inside `"$( ... )"` gets
  brace-expanded by the shell**, which shreds the payload into fragments and
  fires one request per fragment. Every OTLP payload in `smoke.sh` is built into
  a variable first, for that reason and no other.
- **The three ingest verdicts share one 60-second poll.** Splitting them would
  triple the wait, and they all read the same emitted `TRACE_ID`/`MARKER`. The
  backends are pre-waited silently first, so a Tempo that never came up reads as
  itself rather than as a lost trace.
- **`depends_on` here is list form, so it waits for start, not for health.**
  Loki and Tempo have no healthcheck to wait on (see their `healthcheck.none`),
  which is why the pre-wait in `smoke.sh` exists at all.
- **Two ports, one pipeline.** `OTEL_GRPC_PORT` (4317) and `OTEL_HTTP_PORT`
  (4318) accept the same signals; the smoke check uses HTTP because `curl` is
  already a required tool.

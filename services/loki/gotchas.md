# loki — gotchas

- **Loki 3.x needs `allow_structured_metadata: true`** to accept OTLP at all,
  and ingests via its native `/otlp` endpoint — the dedicated `loki` exporter in
  the OTel Collector is deprecated. Both settings live in
  `conf/loki-config.yaml` and `services/otel-collector/conf/`.
- **The pinned image is distroless and carries no healthcheck.** `grafana/loki:3.7.7`
  holds `/usr/bin/loki` and nothing else — no shell, no wget — so the exemption
  is declared in `healthcheck.none` beside `compose.yaml`. Note that this is a
  property of the pinned tag, not of Loki: 3.5.7 still shipped `/busybox`.
  Re-verify against the image before assuming either way.
- **`wait-healthy.sh` can only see that this container is running.** With no
  healthcheck there is nothing for it to wait on, which is why `smoke.sh` polls
  `/ready` from the host before the OTLP round-trip and `SMOKE_STRICT=1` fails
  on it.
- **`LOKI_PORT` is missing from `scripts/urls.sh`.** A developer reading that
  script will not find this Module at all, even though its own header says "a
  service missing from this list is a service a developer cannot find".
  `x-endpoints:` in `compose.yaml` is the complete list; generating `urls.sh`
  from it is story 3-3, so the drift is recorded here rather than half-fixed.
- **Retention is until compaction.** Log volume is bounded by the compactor's
  schedule in `conf/loki-config.yaml`, not by a wall-clock retention like
  Prometheus's.

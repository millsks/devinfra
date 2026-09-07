# tempo — gotchas

- **Tempo's span metrics need `--web.enable-remote-write-receiver`** on
  Prometheus, or Grafana's service map stays permanently empty. The
  `metrics_generator` block in `conf/tempo.yaml` and that Prometheus flag are one
  feature split across two Modules.
- **The pinned image is distroless and carries no healthcheck.**
  `grafana/tempo:3.0.3` holds `/tempo` and nothing else — no shell, no wget — so
  the exemption is declared in `healthcheck.none` beside `compose.yaml`. This is
  a property of the pinned tag, not of Tempo: 2.9.0 still shipped `/busybox`.
  Re-verify against the image before assuming either way.
- **`wait-healthy.sh` can only see that this container is running.** With no
  healthcheck there is nothing for it to wait on, which is why `smoke.sh` polls
  `/ready` from the host before the OTLP round-trip and `SMOKE_STRICT=1` fails
  on it.
- **A trace is not searchable the instant it is accepted.** Tempo moves the span
  from the WAL into a block first, which is why the round-trip check retries for
  60 seconds instead of asserting immediately.
- **`TEMPO_PORT` is missing from `scripts/urls.sh`.** A developer reading that
  script will not find this Module at all. `x-endpoints:` in `compose.yaml` is
  the complete list; generating `urls.sh` from it is story 3-3, so the drift is
  recorded here rather than half-fixed.
- **Retention is 7 days**, set in `conf/tempo.yaml`.

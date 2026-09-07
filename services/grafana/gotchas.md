# grafana — gotchas

- **Two directories, and they are not the same kind of thing.**
  `conf/provisioning/` is configuration read at startup — datasources and the
  dashboard provider — while `dashboards/` is a live drop-zone Grafana re-scans
  every 30 seconds. Both are bind-mounted read-only.
- **`dashboards/` is tracked only by its `.gitkeep`.** That file is the reason
  git tracks the directory at all, and the reason `assert_config.py` accepts it:
  the rule that a bind source must not be an empty directory would otherwise
  reject the one legitimately near-empty source in the stack.
- **`GF_AUTH_ANONYMOUS_ENABLED: "true"` with `GF_AUTH_ANONYMOUS_ORG_ROLE: Admin`**
  skips the login screen entirely and hands every visitor admin. Local
  development only.
- **Tempo's Grafana datasource implements no health endpoint.** The smoke suite
  checks `prometheus`, `loki`, `tempo` and `postgres` for provisioning presence
  but only `prometheus`, `loki` and `postgres` for `/health` — Tempo is checked
  by provisioning alone, deliberately, not by an omission.
- **The Postgres datasource reads `POSTGRES_*` from Grafana's own environment.**
  `conf/provisioning/datasources/datasources.yaml` interpolates them, so those
  three variables are duplicated into this service on purpose.
- **Only the datasource *health* checks are gated on the backing Module.** The
  "provisioned" checks read Grafana's own provisioning through Grafana's API and
  never touch the backend, so they run regardless of the Selection. The
  "connects" checks make Grafana open a connection, so a backend out of the
  current Selection is a skip rather than a failure, matching FR-5.

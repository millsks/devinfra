# grafana — gotchas

- **Two directories, and they are not the same kind of thing.**
  `conf/provisioning/` is configuration read at startup — datasources and the
  dashboard provider — while `dashboards/` is a live drop-zone Grafana re-scans
  every 30 seconds. Both are bind-mounted read-only.
- **`dashboards/` ships `devinfra-overview.json`, and it keeps its `.gitkeep`.**
  The dashboard is provisioned from the read-only bind mount, never from the
  `grafana-data` volume, so it is present on a fresh volume and again after
  `pixi run down && pixi run up`. The `.gitkeep` stays because it is what lets
  git track the directory when the only dashboard in it is deleted, and
  `assert_config.py`'s message still points at this directory as the way to keep
  a legitimately empty bind source out of its empty-directory rule.
- **The dashboard provider sets `allowUiUpdates: false`, deliberately.** With UI
  updates allowed, "Save dashboard" in the browser writes a second copy into the
  `grafana-data` volume that the tracked file no longer describes, and Grafana
  reports `meta.provisioned: false` even for a dashboard the file provider
  loaded — verified against `grafana/grafana:13.2.1`. Editing in the browser
  still works; exporting the JSON back into `dashboards/` is how you keep a
  change. `lint_selftest.py` pins the setting, because the smoke suite's
  "provisioned from the bind mount" assertion depends on it.
- **The smoke suite runs that dashboard's own panel queries.**
  `scripts/check_dashboards.py` reads the queries out of the JSON and runs them
  through Grafana's datasource proxy, so a panel edited into a broken query
  fails `pixi run smoke` rather than rendering "No data" for the next person to
  open the UI. Editing a panel in the browser and not exporting it back into
  this file changes nothing the check sees — the file is the subject.
- **A dashboard you drop into `dashboards/` is checked too, and only `$service`
  is substituted.** The check scans every `*.json` in the directory and runs
  every panel in them pinned to the `tempo`, `loki` or `prometheus` datasource —
  a panel on any other datasource is not a signal this check is defined over and
  is not run at all. Every panel it does run has to come back with data: a
  second panel for a signal that renders "No data" fails the check even when its
  sibling returned rows, because "No data" is the state this whole check exists
  to catch. Only `$service` is substituted, and only where that is the entire
  variable name, so a panel using any other dashboard variable —
  `$__rate_interval`, `$__range`, `$service_tier`, a variable of your own —
  sends the raw `$name` straight to the backend, gets a 400, and turns
  `pixi run smoke` red on a stack that is perfectly healthy. Either write the
  panel with literal values, or give the variable a default the backend accepts
  as a literal string.
- **That panel check runs at the end of the suite, not in Grafana's block.**
  It is registered with the driver's `defer` seam because it queries the trace,
  log and metric the observability Module's own checks inject, and that Module
  runs after this one in glob order. See
  [ADR 0015](../../docs/adr/0015-deferred-smoke-checks.md).
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

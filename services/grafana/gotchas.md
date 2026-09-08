# grafana — gotchas

### `conf/provisioning/` and `dashboards/` are not the same kind of thing

- **Symptom:** A file dropped in the wrong one of the two either never loads, or loads once
  and then stops tracking the tracked copy.
- **Cause:** `conf/provisioning/` is configuration read at startup — the datasources and the
  dashboard provider — while `dashboards/` is a live drop-zone Grafana re-scans every 30
  seconds. Both are bind-mounted read-only.
- **Fix:** Put datasource and provider YAML under `conf/provisioning/`, dashboard JSON under
  `dashboards/`, and restart Grafana after touching the former.
- **Affected versions:** Not version-specific

### `dashboards/` keeps its `.gitkeep` even though it ships a dashboard

- **Symptom:** Deleting `.gitkeep` looks like tidying and silently makes the directory
  untrackable the moment the last dashboard in it is deleted.
- **Cause:** `devinfra-overview.json` is provisioned from the read-only bind mount, never
  from the `grafana-data` volume, so it is present on a fresh volume and again after
  `pixi run down && pixi run up`. Git tracks files, not directories, so an empty
  `dashboards/` needs the `.gitkeep` to exist at all — and `assert_config.py` refuses an
  empty bind source.
- **Fix:** Leave `.gitkeep` in place. `assert_config.py`'s message points at this directory
  as the way to keep a legitimately empty bind source out of its empty-directory rule.
- **Affected versions:** Not version-specific

### The dashboard provider sets `allowUiUpdates: false`, deliberately

- **Symptom:** "Save dashboard" in the browser is refused, and with the setting flipped
  Grafana reports `meta.provisioned: false` even for a dashboard the file provider loaded.
- **Cause:** With UI updates allowed, saving writes a second copy into the `grafana-data`
  volume that the tracked file no longer describes.
- **Fix:** Leave it `false` and export the JSON back into `dashboards/` to keep a change.
  Editing in the browser still works; only saving over the provisioned copy does not.
- **Affected versions:** `grafana/grafana:13.2.1` — verified against that tag.
- **Verified by:** `scripts/lint_selftest.py` — pins the setting, because the smoke suite's
  "provisioned from the bind mount" assertion depends on it.

### The smoke suite runs the shipped dashboard's own panel queries

- **Symptom:** A panel edited into a broken query renders "No data" in the browser and
  nothing anywhere else notices.
- **Cause:** `scripts/check_dashboards.py` reads the queries out of the JSON and runs them
  through Grafana's datasource proxy, so the tracked file is the subject of the check.
- **Fix:** Export a browser edit back into `services/grafana/dashboards/`. A panel changed
  only in the UI changes nothing the check sees.
- **Affected versions:** Not version-specific
- **Verified by:** `services/grafana/smoke.sh` — the deferred `grafana_dashboard_panels`
  check runs every panel and fails on an empty one.

### A dashboard you drop into `dashboards/` is checked too, and only `$service` is substituted

- **Symptom:** `pixi run smoke` turns red on a perfectly healthy stack, reporting a 400 or
  an empty panel for a dashboard that renders fine in the browser.
- **Cause:** The check scans every `*.json` in the directory and runs every panel pinned to
  the `tempo`, `loki` or `prometheus` datasource; a panel on any other datasource is not a
  signal this check is defined over and is not run at all. Every panel it does run has to
  come back with data, so a second panel for a signal that renders "No data" fails even
  when its sibling returned rows. Only `$service` is substituted, and only where that is
  the entire variable name, so `$__rate_interval`, `$__range`, `$service_tier` or a
  variable of your own reaches the backend raw.
- **Fix:** Write the panel with literal values, or give the variable a default the backend
  accepts as a literal string.
- **Affected versions:** Not version-specific

### The panel check runs at the end of the suite, not in Grafana's block

- **Symptom:** Reading `services/grafana/smoke.sh` top to bottom does not tell you when the
  panel check runs.
- **Cause:** It is registered with the driver's `defer` seam because it queries the trace,
  log and metric the observability Module's own checks inject, and that Module runs after
  this one in glob order.
- **Fix:** Read the comment block above the registration, and
  [ADR 0015](../../docs/adr/0015-deferred-smoke-checks.md), before moving it.
- **Affected versions:** Not version-specific

### Anonymous access is on, with the Admin role

- **Symptom:** Grafana never shows a login screen and every visitor is an admin.
- **Cause:** `GF_AUTH_ANONYMOUS_ENABLED: "true"` with `GF_AUTH_ANONYMOUS_ORG_ROLE: Admin`.
- **Fix:** Local development only. Leave the published port on `127.0.0.1` and never carry
  these two settings anywhere else.
- **Affected versions:** Not version-specific

### Tempo's Grafana datasource implements no health endpoint

- **Symptom:** The smoke suite checks four datasources for provisioning presence and only
  three for `/health`, which reads like an omission.
- **Cause:** Tempo's datasource answers no `/health` call for Grafana to make.
- **Fix:** Leave it out of the health loop. Tempo is checked by provisioning presence
  alone, deliberately.
- **Affected versions:** `grafana/tempo:3.0.3` with `grafana/grafana:13.2.1`.

### The Postgres datasource reads `POSTGRES_*` from Grafana's own environment

- **Symptom:** Three Postgres variables appear in Grafana's `environment:` block and look
  like a copy-paste mistake.
- **Cause:** `conf/provisioning/datasources/datasources.yaml` interpolates them, and
  provisioning is read in Grafana's process.
- **Fix:** Keep them. They are duplicated into this service on purpose.
- **Affected versions:** Not version-specific

### Only the datasource *health* checks are gated on the backing Module

- **Symptom:** The "provisioned" assertions run for a backend that is out of the current
  Selection while the "connects" assertions skip, which reads as an inconsistency.
- **Cause:** The provisioned checks read Grafana's own provisioning through Grafana's API
  and never touch the backend; the connects checks make Grafana open a connection.
- **Fix:** Leave the gating as it is — a backend out of the Selection is a skip rather than
  a failure, matching FR-5.
- **Affected versions:** Not version-specific

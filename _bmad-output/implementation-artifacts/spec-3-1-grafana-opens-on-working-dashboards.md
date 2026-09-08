---
title: 'Grafana opens on working dashboards'
type: 'feature'
created: '2026-09-07'
status: 'done'
baseline_revision: 'b410e27e99b114bb76f824a53eff4637848c4d13'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      The 'dashboard provisioned from the bind mount' assertion issues a single un-retried
      curl, with no readiness allowance for Grafana's dashboard provisioning scan.
    evidence: |-
      Two layers filed it; neither could be verified without a live stack, which this
      environment cannot start (another session owns the `devinfra` project's container
      names and host ports). Against it: the four datasource-provisioning assertions
      immediately above it are built the same way, have never been retried, and are green
      in CI — datasources and dashboards are loaded by the same provisioning service at
      Grafana startup. For it: `updateIntervalSeconds: 30` means a dashboard the first
      scan missed is 30s away, and `ci-stack-cycle` now runs this assertion a second time
      right after a restart, doubling any exposure.
      What would settle it: one `ci-stack` / `ci-stack-cycle` run against a cold Grafana
      volume, or a deliberate delay injected into the provisioning scan. If it does prove
      flaky, the fix is the same `await_url` the datasource health calls already use.
    location: >-
      services/grafana/smoke.sh:52
    severity: medium (unverified)
---

<intent-contract>

## Intent

**Problem:** Grafana's dashboard provider watches `services/grafana/dashboards/`, which holds nothing but `.gitkeep` — so telemetry that already reaches Tempo, Loki and Prometheus is invisible until a developer builds panels by hand (FR-13, CAP-13, UJ-4).

**Approach:** Ship a provisioned dashboard covering all three signals, and add a smoke check that runs each panel's own query through Grafana against the trace, log and metric the smoke suite itself injects, failing when a panel returns nothing.

## Boundaries & Constraints

**Always:**
- Dashboards load from the read-only bind mount through the existing file provider, never from `grafana-data` volume state.
- Panel queries are read out of the shipped dashboard JSON — never restated in the checker — so editing a panel into a broken query fails the check.
- The check reaches the backends **through Grafana** (`/api/datasources/proxy/uid/<uid>/…`) using the pinned datasource UIDs `prometheus`, `loki`, `tempo`.
- The check runs inside `pixi run smoke` and asserts against the telemetry `services/otel-collector/smoke.sh` injects (`MARKER`, `TRACE_ID`) — the PRD requires "the trace, log and metric the Smoke Test itself injects".
- `scripts/smoke-test.sh` stays free of every Module name (`lint_selftest.py:3667`); any new Core mechanism is generic.
- A Module out of the current Selection is `skip`ped, never failed (FR-5); `SMOKE_STRICT=1` turns those skips into failures unchanged.
- New Python is stdlib-only, `ruff format`/`ruff check`/`mypy --strict` clean; new shell is shellcheck-clean; new JSON parses under `lint-json`.

**Never:**
- Do not move `dashboards/` to `services/grafana/seed/`. The spine's FR-13 row says `seed/`, but every existing `seed/` is copied into a volume on first init, which is precisely the volume state AC-2 forbids; the shipped bind mount already satisfies the AC. Keep `seed.none` and correct its wording instead.
- Do not add a second telemetry emitter, and do not reorder or rename Modules to fix check ordering.
- Do not touch `_bmad-output/implementation-artifacts/sprint-status.yaml`.
- No security hardening, no new services, no changes to the datasource file's UIDs or to the collector's pipeline.

## I/O & Edge-Case Matrix

Applies to the new `scripts/check_dashboards.py`.

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| All three signals resolve | Dashboards ship a panel per signal; Grafana proxy returns non-empty results | One `<signal>: OK …` line per signal on stdout; exit 0 | No error expected |
| A signal's panels all return empty | Proxy answers 200 with zero series/streams/traces | `<signal>: EMPTY …` naming the dashboard, panel and query; exit 1 | Loud, non-zero |
| A panel query is malformed | Proxy answers 400/500 | `<signal>: ERROR …` with the status and the body's first line; exit 1 | Loud, non-zero |
| A signal has no panel at all | No panel targets any of the three UIDs | `<signal>: MISSING …`; exit 1 | Loud, non-zero |
| No dashboard files found | `dashboards/` holds only `.gitkeep` | Refuses naming the directory; exit 1 | Never exit 0 over zero dashboards |
| A dashboard file is not JSON | Malformed file present | Refuses naming the file and the parse error; exit 1 | Loud, non-zero |
| Backend has not ingested yet | Proxy returns empty on the first attempt | Retries the empty signals on a bounded budget (about 60s) before reporting `EMPTY` | Bounded, then loud |

</intent-contract>

## Code Map

- `services/grafana/dashboards/.gitkeep` -- the placeholder the story replaces; the directory is bind-mounted `:ro` at `/var/lib/grafana/dashboards` (`services/grafana/compose.yaml:60`). Keep the `.gitkeep`.
- `services/grafana/conf/provisioning/dashboards/dashboards.yaml` -- file provider `devinfra`, folder `devinfra`, 30s rescan. Already correct; no change expected.
- `services/grafana/conf/provisioning/datasources/datasources.yaml` -- pinned UIDs `prometheus` / `loki` / `tempo` / `postgres`, with the correlation wiring. Dashboards must reference these UIDs literally.
- `services/grafana/compose.yaml:44-60` -- anonymous Admin, admin user/password env, the two provisioning mounts.
- `services/grafana/seed.none` -- justification that ends "tracked only by its `.gitkeep`"; that clause becomes false.
- `services/grafana/smoke.sh` -- Module's checks; today asserts datasource provisioning and health. Sourced 2nd in glob order.
- `services/otel-collector/smoke.sh:30-110` -- the injector. Sets `TRACE_ID`, `SPAN_ID`, `NOW_S`, `MARKER="devinfra-smoke-<8 hex>"` (used as `service.name`), posts OTLP traces/logs/metrics and polls all three backends. Sourced 7th — **after** Grafana.
- `scripts/smoke-test.sh:46` (`REQUIRED_TOOLS`), `:80-199` (helpers `pass`/`fail`/`skip`/`assert_contains`/`running`/`await_url`), `:209-229` (the glob loop). Module scripts are sourced into one shared global namespace.
- `scripts/select.sh:33-40` -- the `DEVINFRA_PYTHON` seam (`python3` default); reuse it, do not hardcode an interpreter.
- `scripts/lint_selftest.py:3624-3673` -- the carve rules: one `smoke.sh` per Module, every `smoke.sh` carries a counted assertion (`^\s*(assert_contains|assert_ready|check_http|pass|fail)`), the driver names no Module. `:3676-3700` -- the all-absent run asserts exactly one `"<module> not running"` line per Module. `:2784-2792` -- a `lint-config` case whose title claims Grafana's dashboards directory "carries only .gitkeep".
- `scripts/lint_selftest.py:102` `pixi()`, `:130` `run_script()`, `:379` `planted()`, `:425` `throwaway_repo()` -- the harness helpers for new cases.
- `services/prometheus/conf/prometheus.yml` -- scrapes `otel-collector:8889`, where `resource_to_telemetry_conversion` turns `service.name` into the `service_name` label; the injected counter lands as `devinfra_smoke_counter_total`.
- `pyproject.toml` -- ruff (`E,F,I,UP,B,RUF,D`, google docstrings, line-length 120) and `mypy strict` over `scripts/`.
- `pixi.toml` -- `lint-json` already globs `services/**/*.json`; `lint-shell` already globs `services/**/*.sh`; `smoke`/`smoke-strict`; `ci` = `lint` + `test`.
- `README.md:44`, `:341` -- the catalog row and the "drop dashboard JSON here" line.
- `docs/adr/README.md`, `docs/adr/0012-…`, `docs/adr/0014-…` -- ADR index and the shape a new ADR follows.

## Tasks & Acceptance

**Execution:**
- `services/grafana/dashboards/devinfra-overview.json` -- add the provisioned dashboard: stable `uid: devinfra-overview`, a `service` template variable (Prometheus `label_values(service_name)`), and one panel per signal pinned to the datasource UIDs — traces via Tempo TraceQL `{resource.service.name="$service"}`, logs via Loki `{service_name="$service"}`, metrics via Prometheus `{service_name="$service"}` -- so the panels are genuinely useful and are also exactly what the check exercises.
- `services/grafana/seed.none` -- correct the sentence that says `dashboards/` is tracked only by its `.gitkeep`; state that it now ships provisioned dashboards, still bind-mounted read-only and re-read on every start, which is why it is provisioning rather than seed data.
- `scripts/check_dashboards.py` -- new, stdlib only. Reads every `*.json` under a `--dashboards-dir`, resolves each panel's datasource UID and query (`expr` for Prometheus/Loki, `query` for Tempo), substitutes `$service`/`${service}` with `--service`, and for each of the three signals runs the panel queries through the Grafana datasource proxy over a bounded retry budget; prints one `<signal>: OK|EMPTY|ERROR|MISSING <detail>` line per signal and exits non-zero unless all three are `OK`. Refuses a directory with no dashboards and an unparseable file. This is where the non-trivial logic lives, so it is testable without a stack.
- `scripts/smoke-test.sh` -- add a generic `defer <function-name>` helper plus the post-loop pass that invokes each registered function in registration order, and add the interpreter to the preflight tool list. Name no Module; document that the seam exists for a check that depends on another Module's side effects.
- `services/grafana/smoke.sh` -- assert the dashboard is provisioned (`/api/dashboards/uid/devinfra-overview` reports `"provisioned":true`, which is the observable difference between the file provider and volume state), and `defer` a function that runs the panel check: skip with a reason when `otel-collector`, `prometheus`, `loki` or `tempo` is not running or `MARKER` is unset, otherwise one counted assertion per signal over the checker's output. Document the deliberate read of the collector's `MARKER` global.
- `scripts/lint_selftest.py` -- add cases: the driver runs deferred functions after every Module's script and reports their results; a registered function that is never invoked would be caught; `check_dashboards.py` exits 0 against a stub Grafana returning results and non-zero (naming the panel) against one returning empty, one returning an error status, a dashboard directory with no dashboards, and a malformed dashboard file; the shipped dashboard parses, targets only the pinned UIDs, and covers all three signals. Repoint the `:2784` "carries only .gitkeep" case at a planted directory so its title stays true.
- `docs/adr/0015-deferred-smoke-checks.md` + `docs/adr/README.md` -- record why a check that depends on another Module's side effects is deferred by the driver rather than solved by reordering Modules, duplicating the emitter, or filing Grafana's check under the collector.
- `README.md` -- update the `services/grafana/dashboards/` line and the Grafana catalog row to say a dashboard ships and is provisioned on first boot.
- `CHANGELOG.md` -- add an `Added` entry under `[Unreleased]` for the provisioned dashboard and the panel-render smoke check.

**Acceptance Criteria:**
- Given the observability Selection started from a fresh volume with no manual steps, when `pixi run smoke` runs, then Grafana reports the `devinfra-overview` dashboard with `"provisioned": true` and the suite records that as a pass.
- Given the stack is cycled with `pixi run down` then `pixi run up`, when the same check runs, then the dashboard is present again, sourced from the read-only bind mount rather than from `grafana-data`.
- Given the trace, log and metric the smoke suite injects, when the deferred dashboard check runs, then one panel per signal returns non-empty and the suite records three passes.
- Given a dashboard whose panels all carry broken queries, when the check runs, then it exits non-zero naming the dashboard, the panel and the failing query, and the smoke suite fails.
- Given a Selection in which `otel-collector`, `prometheus`, `loki` or `tempo` is absent, when the deferred check runs, then it skips naming the absent Module — and under `SMOKE_STRICT=1` that skip is a failure.
- Given `pixi run ci`, when it runs, then `lint-json`, `lint-shell`, `lint-python`, `lint-config` and `test` all pass, and the self-test proves the driver still names no Module.

## Spec Change Log

- **2026-09-07 — `services/grafana/conf/provisioning/dashboards/dashboards.yaml` changed
  after all.** The Code Map says "Already correct; no change expected", but the AC that
  Grafana report `"provisioned": true` cannot be met while the provider carries
  `allowUiUpdates: true`. Verified against `grafana/grafana:13.2.1` with a side-car
  container: with UI updates allowed the meta block reads
  `{'provisioned': False, 'provisionedExternalId': 'devinfra-overview.json'}`, and with
  `allowUiUpdates: false` it reads `{'provisioned': True, …}`. The flag was flipped to
  `false`, which is also the semantically correct setting for AC-2 — with UI updates
  allowed, a browser "Save dashboard" forks a second copy into `grafana-data` that the
  tracked file no longer describes, which is exactly the volume state the AC forbids. The
  UIDs in `datasources.yaml` and the collector's pipeline were not touched, and the
  setting is now pinned by a `lint_selftest.py` case so it cannot drift back silently.

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 37 findings — high 0, medium 11, low 19, false 7, maybe-false 0
- findings:
  - `[low]` `[patch]` blind-hunter: `count_results` maps an absent `traces` key to `None`, so a not-yet-flushed Tempo search would report a never-retried ERROR — the shape claim is refuted against the live Tempo, which answers `{"traces":[],"metrics":{...}}`, but the pinned 3.0.3 is unverified here; patched defensively — an absent `traces` key now counts as zero for Tempo only, Prometheus/Loki stay strict, and `PROXY_EMPTY["tempo"]` is now the absent-key shape `{}`.
  - `[medium]` `[patch]` blind-hunter: `defer` accepts a name it never proves callable — reproduced: a mistyped name prints `command not found`, counts nothing, and the suite exits 0, which is the silent skip ADR 0015 claims to have closed; patched — the deferred loop guards on `declare -F` and `fail`s naming the function, with a self-test case.
  - `[low]` `[patch]` blind-hunter: `--lookback-seconds` is inert on the Prometheus path, which sends only `query` and `time` — real; patched by correcting the help text and the `proxy_request` docstring to say the flag reaches Loki and Tempo only. The request shape was left alone deliberately (see the rejected row below).
  - `[low]` `[reject]` blind-hunter: the checker does not run the panel's query *kind* (instant vs `range: true`, `/api/search` vs `queryType: traceql`) though four documents say it runs "the panel's own query" — the property those documents assert is that a panel edited into a broken query fails, and a malformed expression fails both query kinds; only the window differs, and it is inside Prometheus's 5-minute staleness window for this suite's timing. Changing the query model is more than a direct correction.
  - `[medium]` `[patch]` blind-hunter: the userinfo URL form the smoke suite actually passes has zero coverage — real; patched with a case asserting `http://user:pass@host` reaches the stub as an `Authorization: Basic` header. The percent-encoding half is pre-existing: `services/grafana/smoke.sh:17` built `GF` this way before this change and four existing datasource assertions already use it.
  - `[medium]` `[patch]` blind-hunter: the preflight self-test still asserts only `("curl", "openssl", "base64")`, so the interpreter could be dropped from `REQUIRED_TOOLS` unnoticed — real; patched by adding it to that tuple.
  - `[false]` `[reject]` blind-hunter: `DEVINFRA_PYTHON` defaults to `python3` while pixi tasks use `python` — `scripts/select.sh:40` already defaults the same named seam to `python3`, so the driver follows the existing convention rather than diverging from it.
  - `[medium]` `[patch]` blind-hunter: `datasource_uids()` recurses the whole document and the `service` template variable itself carries `uid: prometheus`, so "covers all three signals" survives deleting the metrics panel — verified against the shipped JSON; patched to collect UIDs from panel targets only.
  - `[low]` `[patch]` blind-hunter: `shipped_titles` is read flat while the checker descends rows, and `probe` recorded only the first empty panel — patched as part of the `probe` change below; the titles assertion was re-checked and still holds.
  - `[medium]` `[patch]` blind-hunter: the check scans the whole drop-zone while README invites arbitrary dashboards into it, so a dropped dashboard using `$__rate_interval` turns `pixi run smoke` red on a healthy stack — real; patched by stating the constraint in `README.md` and `services/grafana/gotchas.md`.
  - `[low]` `[patch]` blind-hunter: `probe` was first-wins per signal, so a second broken panel was never reached though the docs promise otherwise — patched: every panel runs, ERROR if any errors, OK only when none errored and one returned rows.
  - `[medium]` `[patch]` blind-hunter: the `down`/`up` acceptance criterion is asserted nowhere — real; patched with a `ci-stack-cycle` task composed only of existing tasks and a second step in CI's `stack` job.
  - `[low]` `[patch]` blind-hunter: the `allowUiUpdates` bullet is filed under `### Added` and quotes `"provisioned": true` with a space the assertion does not match — patched: moved under `### Changed`, string corrected.
  - `[false]` `[reject]` blind-hunter: `"editable": true` contradicts `allowUiUpdates: false` — it does not. `allowUiUpdates: false` blocks saving; `editable: true` deliberately keeps in-browser exploration working, which is the combination a local dev stack wants.
  - `[low]` `[patch]` edge-case: Tempo empty search returning no `traces` key — same root cause as the first blind-hunter row; patched there.
  - `[low]` `[patch]` edge-case: Prometheus branch ignores `lookback` — same root cause as the third blind-hunter row; help text patched there.
  - `[low]` `[patch]` edge-case: two panels for one signal, the second malformed — same root cause as the `probe` row; patched there.
  - `[false]` `[reject]` edge-case: a panel pinned to the `postgres` datasource UID would be silently unverified — no such panel ships, and `SIGNALS` covers exactly the three signals the story is about; a postgres panel is not a signal this check is defined over.
  - `[medium]` `[patch]` edge-case: a query using any variable other than `service` reaches the backend raw — same root cause as the drop-zone row; documented there.
  - `[low]` `[reject]` edge-case: a negative `--interval-seconds` raises `ValueError` — reachable only by deliberately passing a bad flag from a call site that passes no such flag, and the fix adds a validation branch.
  - `[medium]` `[patch]` edge-case: `defer` of an undefined function — same root cause as the second blind-hunter row; patched there.
  - `[low]` `[reject]` edge-case: a whitespace-only `DEVINFRA_PYTHON` leaves the argv array empty and trips `set -u` — requires deliberately setting the seam to whitespace, and the fix adds a guard.
  - `[low]` `[patch]` edge-case: claim that the documented retry-then-EMPTY path is unreachable for traces — same root cause as the first blind-hunter row; patched there.
  - `[low]` `[reject]` edge-case: claim that "each panel's own query" is untrue — same refutation as the fourth blind-hunter row.
  - `[low]` `[patch]` edge-case: `PROXY_EMPTY["tempo"]` states a shape Tempo may not emit — same root cause as the first blind-hunter row; the stub now carries the absent-key shape.
  - `[medium]` `[patch]` verification-gap: the deferred check's FR-5 skip path is executed by no test — filed with evidence; patched with a driver case whose stub `ps` lists only `grafana`, asserting the SKIP names the absent Modules and no panel assertion runs. Exit status is deliberately not asserted there, since Grafana's datasource curls reach a real host port.
  - `[medium]` `[patch]` verification-gap: the interpreter added to `REQUIRED_TOOLS` is not asserted by the preflight case — same root cause as the sixth blind-hunter row; patched there.
  - `[low]` `[patch]` verification-gap (other): `count_results` and the hand-written empty-Tempo fixture — same root cause as the first blind-hunter row; patched there.
  - `[medium]` `[patch]` verification-gap (other): the stub answers any path, so a wrong native path or parameter would keep every offline case green — real; patched with a `NATIVE_ENDPOINTS` table that 404s unless the path and its query parameter match that datasource's native endpoint.
  - `[low]` `[reject]` intent-alignment (a): the gate the spec names, `pixi run ci`, does not reach the AC surface — the assertions do run in `ci-stack`, which CI's `stack` job runs on every push and pull request; only this environment could not run it, which is recorded as a residual risk rather than fixed by moving the gate.
  - `[medium]` `[patch]` intent-alignment (b): the two new smoke assertions are executed by no committed test — the skip half is now covered (see the verification-gap row); the running half is live-only by construction and is named under residual risks.
  - `[low]` `[patch]` intent-alignment (c): the stub's fixtures are asserted, not captured — patched by the absent-key shape and the native-endpoint table.
  - `[low]` `[reject]` intent-alignment (d): "the panel's own query" holds at the string surface, not the execution surface — same refutation as the fourth blind-hunter row.
  - `[medium]` `[patch]` intent-alignment (e): the fresh-volume and `down`/`up` criteria have no surface in the diff — same root cause as the `ci-stack-cycle` row; patched there. The "Grafana is opened" half is satisfied by the provisioned assertion plus the panel checks.
  - `[low]` `[reject]` intent-alignment (f): the live `allowUiUpdates` verification exists only as prose — the setting itself is pinned by a self-test case; the Grafana behaviour it was chosen for is a fact about the image, and the observation with both meta blocks is recorded in the Spec Change Log, which is where such evidence belongs.
  - `[low]` `[patch]` intent-alignment (g): `probe` granularity — same root cause as the `probe` row; patched there.
  - `[false]` `[reject]` intent-alignment (h): `epic-3-context.md` is wider than the story — it is the workflow's own compiled planning context for epic 3, not product change, and it is written by the planning step rather than by the implementation.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 34 findings — high 0, medium 6, low 20, false 6, maybe-false 2
- findings:
  - `[low]` `[reject]` blind-hunter: the Prometheus panel is `range: true` but the checker sends `api/v1/query` — carried from the pass above (`the checker does not run the panel's query *kind*`); `proxy_request` still reads as that row describes, so the row's verdict and refutation stand and it is not re-litigated.
  - `[low]` `[reject]` blind-hunter: `grafana_dashboard_panels` ignores the checker's exit status, so one `Refusal` reports as three failures — real, but `assert_contains` prints the haystack on failure (`smoke-test.sh:156`), so the refusal line appears in all three; the cost is noise, not a lost diagnostic, and the fix adds a status branch.
  - `[false]` `[reject]` blind-hunter: the `service` template variable's own `label_values(service_name)` query is never exercised, so the dropdown could be empty while smoke is green — it could not. The metrics panel runs `{service_name="$service"}` against the same Prometheus; if that returns rows then `service_name` carries that value and `label_values(service_name)` cannot be empty. The dropdown-empty state fails as `metrics: EMPTY`.
  - `[medium]` `[patch]` blind-hunter: `substitute()` does a bare string replace, so `$service_tier` becomes `<marker>_tier` — verified by running it. Worse than not substituting: the backend answers a valid query about a label value nobody emits and the check reports EMPTY for an unrelated reason, while README and gotchas promise a raw `$name` and a loud 400. `service_name` is the label every shipped panel filters on, so `$service_name` is a name someone will write. Patched with a word-boundary regex (`SERVICE_VARIABLE`) and a self-test case; the old spelling fails that case.
  - `[low]` `[patch]` blind-hunter: `postgres` and unrecognised-UID panels are dropped while README and gotchas claim the check runs "every panel in every file here" — the code half is carried from the pass above (`no such panel ships`, rejected). The documentation overclaim is new and its fix is a wording correction: both now say the check runs every panel targeting the tempo, loki or prometheus datasource.
  - `[low]` `[reject]` blind-hunter: `defer` validates nothing — reproduced: `defer` with no argument trips `set -u` and aborts the driver mid-run with no summary (exit 1). Loud and non-zero, needs a Module author to write `defer` with no name, and the fix adds a guard branch. Same for a duplicate registration.
  - `[low]` `[patch]` blind-hunter: README's catalog row said the dashboard is "provisioned on first boot" — real, and misleading in a table whose other "first boot" rows (`keycloak/seed/`, `postgres/seed/`) mean data copied into a volume once, which is the exact category this story argues the dashboard is not in. Patched to "provisioned from the tracked JSON on every start".
  - `[false]` `[reject]` blind-hunter: `services/grafana/smoke.sh` hard-codes repo-relative paths, adding an undocumented cwd requirement — the driver already requires it: `DRIVER_MODULE_SMOKES=(services/*/smoke.sh)` at `smoke-test.sh:77` is itself cwd-relative and `:78` exits naming the failure. No new requirement was added.
  - `[maybe-false]` `[defer]` blind-hunter: the "provisioned" assertion has no readiness allowance, unlike the retried checks around it — could not be settled without a live stack; recorded in `deferred` with the evidence both ways and what would settle it.
  - `[false]` `[reject]` blind-hunter: the two-panel case's malformed expr is decoration, since `error-later` refuses the second call regardless — the case's own comment says exactly that ("`error-later` answers the first call per datasource with a row and refuses every one after it"), and its assertion is that the second panel was reached (`the broken panel` in stdout). It claims nothing the stub does not do.
  - `[low]` `[reject]` blind-hunter: the drop-zone warning is prose inside README's ASCII tree and duplicates gotchas — a presentation preference; no named harm, and restructuring the tree entry is more than a direct correction.
  - `[false]` `[reject]` blind-hunter: `sprint-status.yaml` marks the story `done` under `epic-3: backlog` — that file is the orchestrator's own board, written and committed by it in separate `chore(board)` commits, and this run is directed not to write or revert it. Not a defect in this change.
  - `[low]` `[reject]` blind-hunter: the `.gitkeep` case plants a scratch directory under `services/pgadmin/conf/` rather than in `stubs` — it follows the `materialised` pattern eight lines above it, is removed in a `finally`, and its comment states why it is planted rather than pointed at a tracked directory. Moving it risks the `lint-config` bind-source rule the case exists for.
  - `[low]` `[patch]` blind-hunter: `Panel.dashboard` stores `path.name` while `read_panels` walks with `rglob` and the provider sets `foldersFromFilesStructure: true` — verified in `dashboards.yaml`; two `overview.json` files in different folders would produce identical evidence lines. Patched to `str(path.relative_to(directory))`.
  - `[low]` `[reject]` blind-hunter: `probe()`'s unreachable final arm returns a plausible verdict instead of raising, and argparse accepts negative intervals — the arm is unreachable (`report` never calls `probe` with an empty list) and its comment says so; the argparse half is carried from the pass above. Both fixes guard states never shown reachable.
  - `[maybe-false]` `[defer]` edge-case: the provisioned assertion needs `await_url` — same root cause as the blind-hunter readiness row; deferred there.
  - `[low]` `[reject]` edge-case: `parts.port` can raise `ValueError` outside the `Refusal` path — real for a non-numeric `GRAFANA_PORT` or a password containing `/`, but `.env.example` ships `GRAFANA_ADMIN_PASSWORD=admin`, there is no password generator, and the same `GF` string has fed eight existing datasource assertions since before this story. Loud either way, and the fix adds a try/except.
  - `[low]` `[reject]` edge-case: `defer` called from inside a deferred function is dropped, because the loop snapshots the array — same root cause as the `defer` validation row; rejected there.
  - `[medium]` `[patch]` edge-case: plain replace rewrites `$service_name` — same root cause as the `substitute()` row; patched there.
  - `[low]` `[reject]` edge-case: a signal-pinned target whose query key is absent or blank is silently dropped — real only when a *sibling* panel covers that signal; with the shipped one-panel-per-signal dashboard a blanked query is `MISSING` and fails. Fix adds a refusal branch for a state not shown reachable.
  - `[low]` `[patch]` edge-case: `http.client.HTTPException` is not an `OSError`, so a truncated proxy response escapes `fetch`'s handler as a traceback — real: urllib wraps only what is raised while *sending*, and `fetch`'s own docstring promises a transport failure never becomes a traceback. Patched by adding it to the except tuple, with the docstring saying why.
  - `[low]` `[reject]` edge-case: whitespace-only `DEVINFRA_PYTHON` trips `set -u` — carried from the pass above; `smoke-test.sh:42` still reads as that row describes.
  - `[low]` `[reject]` edge-case: `defer` with no argument aborts the suite — same root cause as the `defer` validation row; rejected there.
  - `[medium]` `[patch]` verification-gap: the checker's panel-discovery rules beyond a flat visible list — the collapsed-row descent, the `hide: true` skip and the Grafana-8 bare-string `datasource` — are exercised by no fixture; every planted dashboard is flat, so deleting `yield from iter_panel_dicts(panel)` leaves the whole suite green. Filed pre-verified. Patched with one `dashboards-row` fixture posed against `error-later` that pins all three at once: `logs: OK` holds only if the folded child was found and the hidden target was not run. Both mutations were run and both fail the case.
  - `[medium]` `[patch]` verification-gap (other): `substitute()` word boundary — same root cause as the `substitute()` row; patched there.
  - `[medium]` `[patch]` verification-gap (other): `probe()` reduces to `error or found or empty`, so a signal with one panel returning rows and a second returning nothing reports `OK` — verified in the source. That second panel is a "No data" box, which is the state the story exists to remove, and CHANGELOG and gotchas both say without qualification that a panel returning nothing is a named failure. Patched to `error or empty or found`, with the docstring, an `empty-later` stub mode and a case; the old order fails it. No shipped behaviour changes — one panel per signal ships — and the drop-zone caveat now states the requirement.
  - `[low]` `[patch]` verification-gap (other): `substitute()`'s docstring justifies the replacement order with a hazard that does not exist (`$service` cannot match inside `${service}`; the brace intervenes) — true; the docstring was rewritten with the regex, and now states the real rule.
  - `[low]` `[reject]` verification-gap (other): Prometheus range-vs-instant — same claim as the first blind-hunter row; carried-reject there.
  - `[false]` `[reject]` intent-alignment (a): `sprint-status.yaml` is modified, violating the contract's "Do not touch" — same refutation as the blind-hunter board row: the orchestrator owns and writes that file, and this run is directed neither to write nor revert it.
  - `[low]` `[reject]` intent-alignment (b): `epic-3-context.md` restates the `seed/` claim the contract overrides, so the repo carries both statements — it is the workflow's own compiled record of what the epic spine says, written by the planning step; the override and its reasoning live in the spec's Boundaries, and `seed.none` and the gotchas carry it in the repository proper.
  - `[low]` `[reject]` intent-alignment (c): the interpreter in `REQUIRED_TOOLS` is a hard preflight that now fails every Selection, Grafana or not — this is the file's stated convention, not a divergence from it: `openssl` is equally unconditional though only the Keycloak check uses it, and the comment argues the fail-loud preflight explicitly. `python3` is also what every `pixi run` task in this repository already needs.
  - `[low]` `[reject]` intent-alignment (d): the new verification is offline-only — the stub's shapes and the `ci-stack-cycle` ordering assert self-consistency, not Grafana's behaviour — carried from the pass above (rows (a), (b), (c)); the live path remains unrun here and is named again under residual risks.
  - `[medium]` `[patch]` intent-alignment (e): the panel's *expression* comes out of the JSON but its execution mode does not, and only `$service` is substituted — the substitution half is real and patched with the `substitute()` row; the query-kind half is carried-reject from the pass above.
  - `[false]` `[reject]` intent-alignment (f): the contract names `MARKER` and `TRACE_ID` but only `MARKER` is used — `services/otel-collector/smoke.sh:35` sets `MARKER="devinfra-smoke-${TRACE_ID:0:8}"`, so the marker carries this run's trace id and no prior run's or foreign telemetry can match it. The requirement — proving the panels against the telemetry this suite injected — is met.

## Design Notes

**Why the driver gains a `defer` seam.** The driver sources `services/*/smoke.sh` in glob order, so `grafana` runs 2nd and `otel-collector` — the only injector — runs 7th. A panel check written straight into Grafana's script would query backends that hold nothing yet. The three alternatives were considered and rejected: emitting a second copy of the telemetry from Grafana's script duplicates the payload builder, adds another ~60s ingest wait, and creates an undeclared Grafana→collector port coupling that would drag an `x-requires`/`depends_on` edge with it; filing the assertion in `services/otel-collector/smoke.sh` puts Grafana's verification where nobody adding a dashboard will look, against ADR 0012; renaming or reordering Modules is not available. `defer` is about ten lines, names no Module, and states the real constraint — this check depends on side effects another Module's checks produce.

**The accepted coupling.** The deferred function reads `MARKER` (and may read `TRACE_ID`) out of the shared global namespace the driver documents at `scripts/smoke-test.sh:206-208`. That is deliberate and must be commented in both files: the PRD requires the panels be proved against the telemetry the smoke suite itself injected, and the injector is the collector's script.

**Why the proxy, not `/api/ds/query`.** `/api/datasources/proxy/uid/<uid>/…` speaks each backend's native API, so the checker needs no per-datasource query-model translation and stays testable against a stub HTTP server; it still proves Grafana can reach the datasource with the panel's own expression. Shapes to read: Prometheus `data.result`, Loki `data.result`, Tempo `traces`.

**Tempo search may lag by-ID lookup**, which is all the collector's script proves. The bounded retry in the checker is the mitigation; do not lower it to a single attempt.

## Verification

**Commands:**
- `pixi run lint-json` -- expected: the new dashboard parses.
- `pixi run lint-shell` -- expected: the changed driver and Grafana smoke script are shellcheck-clean.
- `pixi run lint-python` -- expected: `ruff format --check`, `ruff check` and `mypy --strict` pass over `scripts/`.
- `pixi run lint-config` -- expected: Grafana's Module contract still passes with the populated dashboards directory.
- `pixi run test` -- expected: every new self-test case passes and the carve rules still hold.
- `pixi run ci` -- expected: exit 0. This is the done-gate.
- `COMPOSE_PROFILES="$(./scripts/select.sh observability)" pixi run up && pixi run smoke-strict` -- expected: the dashboard-provisioned assertion and three panel assertions pass, 0 failed, 0 skipped. If no container runtime is available in this environment, say so explicitly rather than reporting the suite as run.


## Auto Run Result

Status: done

**Implemented change.** Grafana opens on a provisioned dashboard covering all three
signals, and the smoke suite proves its panels return data rather than rendering "No
data". `services/grafana/dashboards/devinfra-overview.json` ships one panel per signal
pinned to the provisioned UIDs `tempo` / `loki` / `prometheus`, driven by a `service`
template variable; the existing file provider loads it from the read-only bind mount, and
`allowUiUpdates: false` makes `meta.provisioned: true` the observable that says so.
`scripts/check_dashboards.py` reads each panel's query out of the JSON, substitutes
`$service`, and runs it through Grafana's datasource proxy with a bounded retry on empty.
The smoke driver gained a generic `defer` seam — it names no Module — because the injector
that produces the telemetry under test runs after Grafana in glob order.

This follow-up pass tightened the checker rather than extending it: a signal is now EMPTY
when *any* of its panels returned nothing, `$service` is substituted only where that is the
whole variable name, a truncated proxy answer is an ERROR rather than a traceback, and the
evidence line names a dashboard by its path under the drop-zone. The three panel-discovery
rules no fixture reached — the collapsed-row descent, the hidden-target skip and the
Grafana-8 bare-string `datasource` — are now pinned by one case.

**Files changed.**
- `services/grafana/dashboards/devinfra-overview.json` — the provisioned dashboard.
- `services/grafana/conf/provisioning/dashboards/dashboards.yaml` — `allowUiUpdates: false`.
- `services/grafana/smoke.sh` — the `"provisioned":true` assertion and the deferred panel check.
- `services/grafana/seed.none`, `services/grafana/gotchas.md` — corrected and extended.
- `scripts/check_dashboards.py` — new; the panel-render checker.
- `scripts/smoke-test.sh` — the `defer` seam, its guarded post-loop pass, the interpreter preflight.
- `scripts/lint_selftest.py` — cases for the seam, the checker's verdicts and refusals, the shipped dashboard's shape.
- `pixi.toml`, `.github/workflows/ci.yml` — `ci-stack-cycle` and its step in the `stack` job.
- `docs/adr/0015-deferred-smoke-checks.md`, `docs/adr/README.md`, `README.md`, `CHANGELOG.md` — the decision and its documentation.

**Review findings.** 34 findings across four layers — 0 high, 6 medium, 20 low, 6 false,
2 maybe-false. Patched: 8 grouped entries (3 medium, 5 low) — the `$service` word boundary
and its docstring, the EMPTY-outranks-OK reduction in `probe`, the collapsed-row /
hidden-target / bare-string coverage gap, `http.client.HTTPException` in `fetch`'s handler,
the dashboard-relative evidence path, README's "first boot" catalog row, and the
"every panel in every file" overclaim in README and the gotchas. Deferred: 1 — the
un-retried "provisioned" assertion, `maybe-false`, medium if true, unsettleable without a
live stack. Rejected: the Prometheus query-kind divergence, the whitespace-only
`DEVINFRA_PYTHON`, the negative `--interval-seconds` and the offline-only verification (all
carried unchanged from the first pass); `grafana_dashboard_panels` not reading the checker's
exit status (the refusal text reaches all three failures through `assert_contains`); the
template variable's own query going unrun (an empty dropdown fails as `metrics: EMPTY`);
`defer`'s missing argument validation, a duplicate registration and a nested `defer` (all
loud or unreachable, all needing a new guard); the repo-relative paths in Grafana's script
(the driver's own module glob already requires that cwd and exits naming it); the
two-panel case's "decorative" malformed expression (its comment claims only what the stub
does); the README tree's prose warning; the planted `.gitkeep` directory; `probe`'s
unreachable final arm; `parts.port` raising `ValueError`; a blank query key on a
signal-pinned target; `sprint-status.yaml` (the orchestrator's board, twice filed);
`epic-3-context.md`'s `seed/` restatement (the workflow's own compiled planning record);
the unconditional interpreter preflight (`openssl` is equally unconditional); and the
`TRACE_ID` narrowing (`MARKER` embeds this run's trace id).

**Follow-up review recommended: false.** This was a follow-up pass and it patched no
`high`, so the work has converged. Patched entries by verdict: 3 medium, 5 low, 0 high.

**Verification performed.**
- `pixi run ci` — exit 0 (lint-compose, lint-config, lint-pins, lint-renovate, lint-shell,
  lint-yaml, lint-json, lint-python, test). 1110 self-test cases pass, 0 fail. This is the
  done-gate.
- Each of the three behaviour changes was mutation-checked against its new case: reverting
  `substitute` to the string replace, `probe` to `error or found or empty`, the row descent
  (`yield from iter_panel_dicts`) and the `hide` skip each makes exactly the case written
  for it fail, and nothing else. All four mutants were built and run.
- `substitute()` was exercised directly: `$service_tier` and `$__rate_interval` survive
  untouched, `$service` and `${service}` are replaced.
- `defer` with no argument was reproduced in isolation: `$1: unbound variable`, exit 1, no
  summary — which is what put that finding at `low` rather than higher.
- Not run: `COMPOSE_PROFILES="$(./scripts/select.sh observability)" pixi run up &&
  pixi run smoke-strict`. A container runtime was available, but another session's stack
  owned the `devinfra` project's container names and host ports; starting this worktree's
  stack would have repointed its containers at this worktree and destroyed that session's
  work.

**Residual risks.**
- The live smoke path is still unrun here. CI's `stack` job runs it, now in two steps
  (`ci-stack` then `ci-stack-cycle`).
- The stricter `probe` reduction turns a *second* panel that legitimately has no data for
  the run's marker into a red smoke run. Nothing shipped is affected — one panel per signal
  ships — and the drop-zone caveat in README and the gotchas now states the requirement,
  which is the same bargain the first pass struck for the `$__rate_interval` footgun.
- The un-retried "provisioned" assertion is deferred, not resolved.
- The Tempo absent-key read remains defensive: the pinned 3.0.3 was not available to query,
  and the observed 2.9.0 emits the key.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` is left modified and
  uncommitted. It is the orchestrator's board, written and committed by it in its own
  `chore(board)` commits, and this run was directed neither to write it nor to revert it.

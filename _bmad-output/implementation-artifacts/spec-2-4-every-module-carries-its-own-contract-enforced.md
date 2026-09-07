---
title: 'Every Module carries its own contract, enforced'
type: 'feature'
created: '2026-09-07'
baseline_revision: '30fdb199a248a5ab04853a8c8d3507df199256f0'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred:
  - summary: >-
      Host port numbers are declared in three places — .env.example, each module file's
      `${VAR:-N}` ports fallback, and now each module's `x-endpoints:` url — with nothing
      reconciling them; assert_pins.py checks `*_VERSION` drift only.
    evidence: |-
      Confirmed by reading assert_pins.py's PIN regex, which matches version pins alone. The
      class is pre-existing — the compose fallbacks already duplicated .env.example unchecked —
      but this story adds seventeen more instances of it inside the very block written to end
      endpoint drift. All three sources agree today, verified against .env.example lines
      62-170. Settling it is an extension of assert_pins.py's existing occurrences() /
      dotenv_declarations() machinery to `*_PORT`, which is a check this story did not own.
    location: >-
      scripts/assert_pins.py:60 (PIN regex); services/*/compose.yaml x-endpoints urls
    severity: low
  - summary: >-
      The bidirectional contract check reads module-file text, so a Compose service reaching
      the rendered model from anywhere but a `services/<dir>/compose.yaml` is not caught by
      this check.
    evidence: |-
      Real. module_contract() asserts that every service key in a module file is `<dir>` or
      `<dir>-<role>`; closure at the rendered surface depends on the separate, pre-existing pin
      that the root compose.yaml declares no `services:` key (lint_selftest.py:646-654). A
      service arriving via compose.override.yaml — which the self-test's own defects table
      already plants for lint-compose — or via an `include:` outside services/ would satisfy the
      new check. The trade is argued in this spec's Design Notes: asserting it against the
      rendered model instead breaks a dozen stub-document fixtures that name services no
      directory owns on purpose. Settling it needs a rendered-model pass that tolerates those
      fixtures, most likely by keying off the real runtime run rather than the stubs.
    location: >-
      scripts/assert_config.py module_contract() — service-ownership rule
    severity: low
  - summary: >-
      The live-stack acceptance criteria were settled against containers running images that
      are stale relative to the pins, so the pinned loki and tempo tags were never actually run.
    evidence: |-
      `pixi run smoke` reports 47 passed, 0 failed, 0 skipped from this worktree, and the
      baseline suite run against the same stack produces a byte-identical set of PASS labels —
      but `docker inspect` shows the running containers are grafana/loki:3.5.7 and
      grafana/tempo:2.9.0 while .env.example pins 3.7.7 and 3.0.3. Every module pins a fixed
      container_name and the live stack runs from the main checkout, so recreating from here
      would repoint its binds at a directory deleted when the run ends. The five added
      healthchecks were each executed inside their live container and in a throwaway container
      from the pinned image; the two healthcheck.none exemptions were settled by exporting the
      pinned image filesystems. What remains unrun is `docker compose --profile admin --profile
      observability up -d` followed by `./scripts/wait-healthy.sh` and `pixi run smoke` from the
      checkout that owns the live stack, on the pinned images. Same shape as DW-30, which
      records the identical gap for story 2-3.
    location: >-
      spec Verification section (live-stack commands)
    severity: medium
  - summary: >-
      The healthcheck exemption is a property of the pinned image tag, so a version bump can
      silently make a marker false without any check noticing.
    evidence: |-
      Directly demonstrated by this story: the plan called for real healthchecks on loki and
      tempo because the *running* 3.5.7/2.9.0 images carry /busybox/wget, while the pinned
      3.7.7/3.0.3 images hold only their own binary. The same movement can go the other way — a
      future tag that regains a shell leaves a healthcheck.none standing that is no longer true,
      and lint-config only checks the marker is justified, never that the justification still
      holds. ADR 0012 records that the exemption must be re-verified on a version bump; nothing
      enforces it. Settling it needs a check that runs against the image, which is a runtime
      dependency the static lint surface deliberately does not have.
    location: >-
      services/{loki,tempo,otel-collector}/healthcheck.none
    severity: low
  - summary: >-
      Nothing pins that the rendered Compose document carries no top-level `x-endpoints:` or
      `x-requires:` key.
    evidence: |-
      Verified absent on Compose v5.3.0: the rendered document's top-level keys are name,
      networks, services and volumes only. No bad outcome is reachable on any supported Compose
      — assert_config.py reads the raw module files, as ADR 0012 now states it must. If a future
      Compose merged them, `x-requires.postgres` is declared by both keycloak and pgadmin with
      different value lists and one would silently win. A one-line self-test assertion over the
      rendered stub document would settle it.
    location: >-
      scripts/assert_config.py module_contract(); docs/adr/0012
    severity: low
  - summary: >-
      The `x-requires:` reconciliation runs one direction only, so a Module that consumes
      another and declares nothing passes the contract in silence.
    evidence: |-
      Confirmed by reading module_contract(): the loop walks `requires.items()`, so it can only
      judge entries that exist. Grafana is the live instance — it provisions a `postgres`
      datasource (services/grafana/conf/provisioning/datasources/datasources.yaml:76-79), reads
      POSTGRES_USER/PASSWORD/DB from its own environment, and its smoke.sh asserts
      `datasource 'postgres' connects` — while its `x-requires:` names only prometheus, loki and
      tempo and its `depends_on` omits postgres. `lint-config` is green. The omission is
      pre-existing (git show 30fdb19:services/grafana/compose.yaml has the same depends_on and
      the same POSTGRES_* environment), so this story did not cause it; what the story adds is a
      declaration mechanism that cannot catch it. The mirror rule — a `depends_on` edge onto
      another Module with no `x-requires:` entry naming it — would close it, but the smallest fix
      for grafana specifically is adding `depends_on: postgres`, which is a runtime-model change
      this story's constraints forbid. Story 3-3 generates documentation from these blocks, so an
      under-declared Module produces under-reported docs.
    location: >-
      scripts/assert_config.py module_contract() — x-requires leg; services/grafana/compose.yaml:22-28
    severity: medium
  - summary: >-
      `scripts/urls.sh` is not reconciled against the `x-endpoints:` blocks that now supersede
      it, and already omits three ports those blocks declare.
    evidence: |-
      Verified: the union of `x-endpoints:` keys across the thirteen Modules is seventeen
      variables; `scripts/urls.sh` sets defaults for fourteen and omits LOKI_PORT, TEMPO_PORT and
      KEYCLOAK_MGMT_PORT — so `pixi run urls` prints no Loki, Tempo or Keycloak-management
      endpoint while `lint-config` certifies those three entries as complete. The drift is
      pre-existing, but enforcing the new list without reconciling the old one makes further
      divergence silent: a fourteenth port declared tomorrow (as lint-config now forces) and
      forgotten in urls.sh keeps `pixi run ci` green. The only urls case,
      lint_selftest.py:1125-1160, asserts a hardcoded twelve-name / fourteen-default tuple
      derived from nothing, so it encodes the drift rather than detecting it. ADR 0012 states
      the decision explicitly ("scripts/urls.sh is left alone rather than half-migrated") and
      assigns generation to story 3-3; the cheap interim is a self-test case comparing the union
      of `x-endpoints:` keys against urls.sh's text.
    location: >-
      scripts/urls.sh:14-28; scripts/lint_selftest.py:1125-1160
    severity: medium
---

<intent-contract>

## Intent

**Problem:** The thirteen Modules are directories with a Compose fragment and nothing else. Nothing obliges one to prove it works, to say what it publishes, or to record what bites — `smoke.sh`, `gotchas.md`, `x-endpoints:`, `x-requires:` and any no-seed marker exist in zero Modules. The one verification the stack has, `scripts/smoke-test.sh`, is a 436-line central script that Core owns: a new Service enters the catalog by editing Core, and seven Modules ship no healthcheck at all, so `wait-healthy.sh` can only see that their containers are running.

**Approach:** Give each Module the five things the contract names — a healthcheck, a `smoke.sh`, an `x-endpoints:` block, a `seed/` directory or a justified `seed.none` marker, and a `gotchas.md` — and make `assert_config.py` refuse a Module missing any of them. Carve `smoke-test.sh` into a driver that keeps the counters, the helpers and the preflights, and thirteen sourced per-Module scripts holding the checks verbatim; the driver enumerates `services/*/smoke.sh` and skips a Module that is not running, so it learns nothing about which Modules exist. The check runs both directions: a service that no Module directory owns fails it too.

## Boundaries & Constraints

**Always:**
- The suite's pass count is **47** with the full stack and `.env.example` defaults, before and after. Every check moves verbatim — same command, same expected substring, same label text — and no check becomes liveness that was not liveness already.
- Every Module in the current Selection reports its checks; every Module out of it reports `skip`, never a pass and never a fail. Under `SMOKE_STRICT=1` a skip is still a failure naming the Module, and no `SKIP` line is printed. `keycloak not running` stays reachable as a literal string — `scripts/lint_selftest.py:2636-2709` pins it.
- The contract check is **presence-based**: it asks whether the five things exist, never whether their content was warranted. A `seed.none` marker is satisfied by a non-empty justification line; nothing judges whether seed data was needed.
- Adding a healthcheck is the only runtime-behaviour change sanctioned here. Every probe added is one already proved to run inside the live image — `wget` at `/busybox/wget` for loki and tempo, `/bin/sh` plus `wget` for grafana, prometheus, pgadmin, redisinsight and flower. `otel-collector` is distroless (`sh` is not on its `PATH`, verified) and is the one Module that carries a `healthcheck.none` marker instead.
- A Module owns its primary service, named exactly for its directory, plus helpers named `<module>-<role>`. `minio-init` is the only helper today. The healthcheck leg is asserted on the primary only: a one-shot helper has nothing to keep healthy.
- No Module declares a `networks:` stanza, every `volumes:` stanza stays identifier-only, and no bind source or `project_directory` rule changes. The rendered model for every profile combination is unchanged apart from the seven added `healthcheck:` blocks.
- Core gains no list of Modules. The driver globs `services/*/smoke.sh`; `assert_config.py` globs `services/*/compose.yaml` through the existing `module_composes()`. A check that walked an empty set is a failure, as it already is for the module scan.
- Every new glob term is proved load-bearing: a `defects` fixture that makes the task fail, and an `empties` entry that makes it fail when the term matches nothing.

**Never:**
- Never rename a volume, container, service, environment variable, port or profile. Never delete or re-drive a volume; no `down -v`, no `pixi run destroy`.
- Never add `x-bundles`, a Bundle registry or `scripts/select.sh` — those are stories 2.5 and 2.6. Never make `smoke-test.sh` read `COMPOSE_PROFILES` or a resolver; the `running` oracle stays observational.
- Never harden the stack incidentally: `FLOWER_UNAUTHENTICATED_API`, `GF_AUTH_ANONYMOUS_ENABLED`, `PGADMIN_CONFIG_SERVER_MODE`, the default credentials and every other dev-mode flag stay exactly as they are.
- Never rewrite a smoke check while moving it. A check that is liveness today (pgadmin `/misc/ping`, redisinsight `/`) stays as it is and says so in that Module's `gotchas.md`; upgrading it is a change of behaviour this story did not ask for.
- Never edit a decided ADR in place — a dated amendment is the pattern (ADR 0004, ADR 0008).
- Never write `_bmad-output/implementation-artifacts/sprint-status.yaml` or `deferred-work.md`; both are orchestrator artifacts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Module missing `smoke.sh` | A `services/<m>/` with no `smoke.sh` | `lint-config` exits 1 naming the module and the missing file | Non-zero exit naming both |
| Module missing `gotchas.md` | Same, no `gotchas.md` | `lint-config` exits 1 naming the module and the missing file | Non-zero exit naming both |
| Module with neither seed nor marker | No `seed/` and no `seed.none` | `lint-config` exits 1 naming the module and both accepted forms | Non-zero exit |
| Empty no-seed marker | `seed.none` present but blank or comment-only | `lint-config` exits 1 — a marker with no justification is the silent skip in file form | Non-zero exit |
| Module with no healthcheck and no marker | Primary service has no `healthcheck:`, no `healthcheck.none` | `lint-config` exits 1 naming the module | Non-zero exit |
| Module missing `x-endpoints:` | No top-level `x-endpoints:` block, or an empty one | `lint-config` exits 1 naming the module | Non-zero exit |
| Published port absent from `x-endpoints:` | Module publishes `${TEMPO_PORT}` but declares no endpoint naming it | `lint-config` exits 1 naming the module and the variable — this is the `urls.sh` drift the block exists to end | Non-zero exit naming the variable |
| Service a Module does not own | `services/loki/compose.yaml` declares a service `grafana` | `lint-config` exits 1 — a service must be `<dir>` or `<dir>-<role>`, and no other file may declare one | Non-zero exit naming file and service |
| Module file with no primary | `services/<m>/compose.yaml` declares only `<m>-worker` | `lint-config` exits 1 — the directory maps to no primary service | Non-zero exit |
| `x-requires:` naming an unknown Module | `x-requires: {nosuch: [X]}` | `lint-config` exits 1 naming the absent provider | Non-zero exit |
| `x-requires:` naming an endpoint the provider does not publish | `x-requires: {postgres: [NOSUCH_PORT]}` | `lint-config` exits 1 naming requirer, provider and endpoint | Non-zero exit |
| `x-requires:` without the matching `depends_on` | Requirer names a provider it does not `depends_on` | `lint-config` exits 1 — a dependency not expressed as `depends_on` does not exist (ADR 0002) | Non-zero exit |
| Two Modules on one host port | Rendered config publishes `127.0.0.1:5432` twice | `lint-config` exits 1 naming both services and the port — already asserted at `assert_config.py:530-538`; this story pins it against a *module-file* fixture too | Non-zero exit |
| Module `volumes:` stanza with a driver key | `volumes: {postgres-data: {driver_opts: …}}` | `lint-config` exits 1 — already asserted by `identifier_only()`; unchanged | Non-zero exit |
| Full stack, default mode | 13 Modules running, `pixi run smoke` | `47 passed, 0 failed, 0 skipped` | — |
| Nothing running, default mode | No container up, `pixi run smoke` | Exit 0, thirteen `SKIP` lines, one per Module, including `keycloak not running` | Exit 0 by design |
| Nothing running, strict mode | `SMOKE_STRICT=1` | Non-zero exit, no `SKIP` line, thirteen failures naming each absent Module | Non-zero exit |
| Partial Selection | Only `postgres` and `redis` running | Their 13 checks run; the other eleven Modules skip; nothing fails | Exit 0 |
| `services/**/*.sh` term deleted from `lint-shell` | The glob term removed from `pixi.toml` | The `empties` case fails by name — the term is load-bearing now that thirteen `smoke.sh` files live under it | Non-zero exit |

</intent-contract>

## Code Map

- `scripts/smoke-test.sh:1-436` -- becomes the driver. Keep `:1-19` header (rewritten), `:20` `set -uo pipefail`, `:23` the `lib/common.sh` source, `:32-40` the tool preflight, `:46-49` the runtime preflight, `:51-54` `BIND`/`PASS`/`FAIL`/`SKIP`, `:56-58` colour, `:60-79` `pass`/`fail`/`skip`, `:81` `section`, `:86-90` `running`, `:93-99` `assert_contains`, `:101` `dc`, `:270-280` `assert_ready`, `:412-423` `check_http` (de-gated: the driver gates now), `:429-436` the summary. Everything between moves out.
- `scripts/smoke-test.sh:103-132` postgres 8 checks (5 fixed, the `POSTGRES_EXTRA_DATABASES` loop, the round-trip); `:134-157` redis 5; `:159-201` keycloak 9 (`KC`/`TOKEN_URL` at `:162-163` are built *outside* the gate — they move inside the module script); `:203-221` minio 5 (bucket loop plus two); `:223-259` mailpit 1 (raw SMTP over `/dev/tcp`); `:282-286` loki `/ready` 1; `:288-292` tempo `/ready` 1; `:294-388` the otel-collector block, 7 checks of which `:377-385` is Prometheus's own scrape-target check mis-gated under `running otel-collector` and carrying **no `else` arm at all** — it moves to `services/prometheus/smoke.sh` and gains one; `:390-407` grafana 7; `:424-426` pgadmin/redisinsight/flower 1 each. 8+5+9+5+1+1+1+7+7+3 = **47**.
- `scripts/smoke-test.sh:264-269` -- the "Loki 3.7 and Tempo 3.0 ship distroless images holding nothing but their own binary" comment. **False**, and this story disproves it: the pinned images are `grafana/loki:3.5.7` and `grafana/tempo:2.9.0`, both of which carry `/busybox/busybox` (loki has `/bin/sh -> /busybox/sh`; tempo has no `/bin/sh`, so its healthcheck must use the exec form with the literal `/busybox/wget`). Both get a real healthcheck here and the comment goes with it.
- `scripts/smoke-test.sh:322-350` -- the shared 60s ingest poll driving `TEMPO_OK`/`LOKI_OK`/`PROM_OK`. It stays whole in `services/otel-collector/smoke.sh`: the emitter's `TRACE_ID`/`MARKER` are what the three verdicts read, and splitting them triples the wait. It gains a silent readiness pre-wait on its backends so alphabetical execution order cannot make a not-yet-ready Tempo read as a lost trace.
- `scripts/lib/common.sh:19` `cd` to repo root, `:24-47` `.env` loading and the four defaults, `:52-54` the `compose` seam. Read-only; module scripts inherit all of it through the driver.
- `scripts/assert_config.py:1-57` -- module docstring enumerating five rendered properties and two text properties; grows a third text property. `:81` `MODULE_STANZAS`; `:241-247` `module_composes()` — the canonical enumeration, reuse it; `:250-274` `read_model()` — the named-diagnostic reader, reuse it; `:277-293` `root_declarations()`; `:313-394` `identifier_only()` — the shape to copy for the new function; `:422-540` `check()`, the rendered pass; `:505-538` the bind-address and duplicate-published-port rules (**AD-17 is already implemented at `:530-538`** — verify and pin, do not rewrite); `:543-608` `main()`, where `identifier_only(modules)` is called at `:566` and `passed` seeded at `:573`.
- `scripts/lint_selftest.py:505-509` `expect`; `:102-127` `pixi`; `:371-392` `planted` (raises `FileExistsError` if the path exists); `:395-414` `moved_aside`; `:261-301` `stub_env`; `:897-906` `fresh(document=…)`; `:1589-1599` `rendered`; `:1601-1608` `port`; `:1610-1622` `clean_doc`.
- `scripts/lint_selftest.py:1626-1755` -- the `services/zz-selftest-defect/` fixture module. **Hard constraint:** this directory exists on disk while `lint-config` runs in ~10 cases, one of which (`:1712-1720`, "accepts a module redeclaring a volume the root declares bare") expects **exit 0**. The new contract check would fail it. Make the fixture contract-complete instead of exempting `zz-*` in production code: create `smoke.sh`, `gotchas.md` and `seed.none` beside it in the `defect_module.mkdir()` block at `:1631`, and give every planted body a shared prefix carrying `x-endpoints:` and a `healthcheck:`.
- `scripts/lint_selftest.py:1911-1923` -- the existing duplicate-published-port case (`postgres` + `pgbouncer` on 5432). Note it renders a service named `pgbouncer`, which owns no Module directory: this is why the reverse direction must be asserted **statically from the module files**, not against the rendered stub documents, which name services no directory owns all over the file.
- `scripts/lint_selftest.py:769-830` `defects` -- add a `lint-shell` fixture under a module directory so `services/**/*.sh` is proved to reach a module's own scripts; `services/redisinsight/` is the placement precedent (`:809-817`), the one module with nothing bind-mounted into a container.
- `scripts/lint_selftest.py:841-868` `empties` -- `:842` pins `lint-shell` on `services/postgres/seed/*.sh` alone. **This breaks the moment thirteen `smoke.sh` files land**: hiding the seed scripts no longer empties `services/**/*.sh`, so `lint-shell` passes and the case fails. Widen it to every `services/**/*.sh`, exactly as story 2-3 widened the `lint-yaml` entry at `:852`.
- `scripts/lint_selftest.py:2636-2709` -- the smoke-suite cases. They pin: exit 0 with nothing running, a `SKIP` line present, the literal `keycloak not running`, strict mode exiting non-zero with no `SKIP` line and still naming `keycloak not running`, the runtime reached only through the compose seam, and both preflights printing neither `PASS` nor `SKIP`. The carve must keep all of it true.
- `scripts/lint_selftest.py:2983-2985` -- every `lint-*` task must be reachable from `lint`. No new task is added here (the contract check lives inside `assert_config.py`), so this stays satisfied.
- `pixi.toml:177` `lint-shell` = `shellcheck scripts/**/*.sh services/**/*.sh .githooks/*` -- already reaches the new files; no change.
- `pixi.toml:95-102` `smoke` / `smoke-strict`; `:161-193` the eight `lint-*` tasks; `:197` `lint`; `:201` `ci`. No task is added or removed.
- `services/<m>/compose.yaml` × 13 -- each gains a top-level `x-endpoints:`, some gain `x-requires:`, seven gain `healthcheck:`. Healthcheck idiom to match: `services/mailpit/compose.yaml:40-45` (`["CMD","wget","-q","-O","-",…]`), `services/redis/compose.yaml:40-45` (`CMD-SHELL`), `services/postgres/compose.yaml:51-58`.
- Verified probes (run against the live containers, all exit 0): loki `/ready`, tempo `/ready`, prometheus `/-/healthy`, grafana `/api/health`, pgadmin `/misc/ping`, redisinsight `/api/health`, flower `/healthcheck`. `/bin/sh` exists in grafana, prometheus, pgadmin, redisinsight, flower and loki; **not** in tempo (exec form, `/busybox/wget`) and **not** in otel-collector at all.
- `services/loki/compose.yaml:21-25`, `services/tempo/compose.yaml:22-25` -- the "no healthcheck by design" comments, falsified above; replaced by the healthchecks themselves.
- `services/minio/compose.yaml:59-87` -- the `minio-init` helper, `restart: "no"`, the only non-primary service in the stack.
- `services/keycloak/compose.yaml:35-39` `depends_on` on postgres and mailpit; `services/grafana/compose.yaml:19-22` list-form on prometheus/loki/tempo; `services/otel-collector/compose.yaml:18-20` list-form on loki/tempo; `services/pgadmin/:14-16`, `services/flower/:16-18`, `services/redisinsight/:16-18`. These are what `x-requires:` is reconciled against.
- `.env.example:52` `BIND_ADDRESS`, and the 17 `*_PORT` variables at `:62,76,92-93,113-114,126-127,134,142,146,153-154,158,162,166,170`. The `x-endpoints:` blocks name these; `assert_pins.py` checks `*_VERSION` drift only, so ports are unpinned across the two files and stay that way here.
- `scripts/urls.sh:13-28` -- the hand-maintained endpoint list, **already drifted**: it omits `LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT` while its own header says "a service missing from this list is a service a developer cannot find". `x-endpoints:` is the replacement source; generating from it is story 3-3, so leave the script alone and record the drift in the Modules' `gotchas.md` rather than half-migrating it.
- `README.md:174-252` the repository-layout block (every `services/*/` file is enumerated; add `smoke.sh`, `gotchas.md`, `seed.none`); `:216` the `assert_config.py` description; `:628-665` "Gotchas worth knowing" — the raw material for `gotchas.md`, attributed: postgres `:632-637`, pgadmin `:638-640`, keycloak `:641-648`, loki `:649-651`, tempo+prometheus `:652-653`, minio `:654-665`. Five Modules (flower, grafana, mailpit, redis, redisinsight) get nothing from it and need their file written fresh.
- `AGENTS.md` -- inside a `bmad:context` managed block; its layout claims gain the three new per-Module files.
- `docs/adr/0001-modules-via-compose-include.md`, `0002-dependency-validation-is-compose-native.md`, `0004-volume-names-are-frozen.md` (its 2026-09-07 amendment is what eight Module headers point at for "the rules every Module holds"), `0007-catalog-admission-policy.md` -- read-only unless the Module contract needs recording; if it does, a new ADR, never an in-place edit.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- read-only. DW-30/DW-31 record that story 2-3's live-stack half never ran and that `depends_on`/`command` details are unpinned; both are the orchestrator's.

## Tasks & Acceptance

**Execution:**
- `<scratch>/before-*.yaml` -- capture `docker compose config` and `docker compose --profile admin --profile observability config`, plus `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort`, **outside the repository tree** before touching anything. The rendered diff is the primary verification and cannot be reconstructed afterwards. Expect exactly seven added `healthcheck:` blocks and nothing else.
- `services/<m>/compose.yaml` × 13 -- add a top-level `x-endpoints:` mapping, one entry per published-port variable the file uses, each naming the variable, a URL or DSN template and a one-line description. Add `x-requires:` to the six Modules that `depends_on` another: keycloak, pgadmin, flower, redisinsight, grafana, otel-collector — each mapping a provider Module name to the endpoint names it consumes. Verify Compose accepts an unknown top-level `x-` key in an included file before writing all thirteen.
- `services/{loki,tempo,prometheus,grafana,pgadmin,redisinsight,flower}/compose.yaml` -- add the verified healthcheck. `CMD-SHELL` with `wget -q -O /dev/null <url>` for all but tempo; tempo takes the exec form `["CMD","/busybox/wget","-q","-O","/dev/null","http://127.0.0.1:3200/ready"]` because it has no `/bin/sh`. Delete the two now-false "no healthcheck by design" comments. Keep `interval`/`timeout`/`retries`/`start_period` in the range the existing five use, so a full Selection still reaches all-healthy inside two minutes.
- `services/otel-collector/healthcheck.none` -- create with the justification: the image is distroless, `sh` is not on its `PATH`, it ships no HTTP client, and `smoke.sh` proves the pipeline from the host instead.
- `services/{flower,grafana,loki,mailpit,minio,otel-collector,pgadmin,prometheus,redis,redisinsight,tempo}/seed.none` -- create eleven markers, each with a one-line justification. minio's must say the buckets come from the `minio-init` helper's `command:`, grafana's that `conf/provisioning/` and `dashboards/` are configuration and a drop-zone rather than seed. `services/{postgres,keycloak}/` keep their `seed/` directories and get no marker.
- `services/<m>/gotchas.md` × 13 -- create, sourced from `README.md:628-665` where that section covers the Module and written fresh for flower, grafana, mailpit, redis and redisinsight. Record honestly where a Module's smoke check is liveness rather than real function (pgadmin, redisinsight) and where `urls.sh` omits the Module's endpoint (loki, tempo, keycloak's management port).
- `services/<m>/smoke.sh` × 13 -- create; move each section's checks **verbatim** from `scripts/smoke-test.sh`, keeping every label and expected substring. No `running` gate inside (the driver gates); no `set` line and no shebang execution path — these are sourced, and each says so in its header. `services/otel-collector/smoke.sh` keeps the whole emit-and-poll block including all three verdicts, and adds a silent readiness pre-wait on the backends it fans out to; `services/prometheus/smoke.sh` takes the scrape-target check and gains the `else skip` arm it never had; `services/grafana/smoke.sh` keeps both datasource loops and skips a datasource whose backing Module is not running rather than failing it.
- `scripts/smoke-test.sh` -- reduce to the driver: preflights, counters, helpers (including a silent `await_url` for the collector's pre-wait), then `for f in services/*/smoke.sh` — `section` the Module, `source` it when `running <dir>`, else `skip "<dir> not running"`. Then the unchanged summary. Core must gain no list of Module names.
- `scripts/assert_config.py` -- add `module_contract(paths)` beside `identifier_only()`, returning a diagnostic list and called from `main()` under the same `try/except RuntimeError`, with its own `OK` line in `passed`. It asserts, per Module: the primary service is named for the directory and every other service is `<dir>-<role>`; the primary declares `healthcheck:` or the directory holds a non-empty `healthcheck.none`; `smoke.sh` and `gotchas.md` exist; `seed/` exists or a non-empty `seed.none` does; a non-empty top-level `x-endpoints:` exists and names every `*_PORT` variable the file publishes; and every `x-requires:` entry names an existing provider Module, only endpoints that provider declares, and a provider whose primary service the requirer already lists in `depends_on`. Extend the docstring at `:1-57`. Do not touch the AD-17 duplicate-port rule at `:530-538` beyond confirming it.
- `scripts/lint_selftest.py` -- make the `zz-selftest-defect` fixture contract-complete (sibling files plus a shared body prefix carrying `x-endpoints:` and `healthcheck:`) so the exit-0 cases still pass; add one negative case per contract leg and per `x-requires` rule, each asserting a non-zero exit, that the diagnostic **names** the module and the missing thing, and that `"OK" not in stdout`; widen the `lint-shell` `empties` entry at `:842` to every `services/**/*.sh`; add a `lint-shell` `defects` fixture under `services/redisinsight/`; and pin the carved suite — that `services/*/smoke.sh` count equals the module count, and that the driver holds no hard-coded module name.
- `README.md` -- add `smoke.sh`, `gotchas.md` and `seed.none` to the layout block, drop the two "no healthcheck by design" annotations, and describe what `assert_config.py` now asserts.
- `AGENTS.md` -- correct the layout claims inside the managed block.
- `docs/adr/0012-*.md` -- add only if the Module contract's shape (marker filenames, `x-endpoints:`/`x-requires:` placement and reconciliation rule) is not already decided by an existing ADR. Never amend a decided one in place.

**Acceptance Criteria:**
- Given the full stack with `.env.example` defaults, when `pixi run smoke` runs, then it reports `47 passed, 0 failed, 0 skipped` — the same pass count as before the carve — and every label printed is one that existed before.
- Given no container running, when `pixi run smoke` runs, then it exits 0 and prints exactly thirteen `SKIP` lines, one naming each Module including `keycloak not running`; and when `SMOKE_STRICT=1` is set, it exits non-zero, prints no `SKIP` line, and still names `keycloak not running`.
- Given only `postgres` and `redis` running, when `pixi run smoke` runs, then their thirteen checks run and pass, the other eleven Modules skip, and nothing fails.
- Given each of the thirteen Modules, when `pixi run lint-config` runs, then it reports every Module satisfying all five contract legs, and removing any one of them from any Module makes it exit 1 naming that Module and that leg.
- Given a Compose service that no Module directory owns, when `pixi run lint-config` runs, then it exits 1 — the check runs in both directions, and the root `compose.yaml` declaring no `services:` key stays pinned so a module file is the only place a service can come from.
- Given the baseline rendered output captured before the change, when `docker compose config` and `docker compose --profile admin --profile observability config` are rendered after it, then the only difference is the seven added `healthcheck:` blocks — no service, image, port, environment value, command, volume identifier, restart policy, logging option, profile, network attachment or `depends_on` edge differs.
- Given `pixi run ci`, when it is run, then it exits 0 with every `lint-*` task and the self-test passing, and `lint-config` reports thirteen Module files scanned.
- Given the running stack recreated with both profiles active, when `./scripts/wait-healthy.sh` runs, then all thirteen containers reach `healthy` — including the seven that had no healthcheck before — inside two minutes, and `docker volume ls --filter name=devinfra` is byte-identical to the captured inventory.

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- verdicts: 40 findings — high 0, medium 6, low 20, false 5, maybe-false 0
- findings:
  - `[medium]` `[patch]` `healthcheck: {disable: true}` satisfies the healthcheck leg — confirmed: `not primary.get("healthcheck")` is False for any non-empty mapping, and a truthy `{"disable": True}` was verified in the interpreter, so Compose's own way of turning a probe off passed the check that exists to forbid exactly that. Fixed by `healthcheck_declared()`, which rejects `disable: true` and `test: NONE`/`["NONE"]` while still accepting a stanza that only tunes an inherited image HEALTHCHECK; a negative self-test case was added beside the missing-marker one.
  - `[medium]` `[patch]` The endpoint rule runs one direction only, so a stale `x-endpoints:` entry survives a deleted `ports:` line — confirmed by reading `module_contract()`: nothing iterated the endpoint keys. The reconciliation now runs both ways, with a self-test case for an entry naming a port the Module does not publish.
  - `[medium]` `[patch]` A literal published host port escapes the endpoints check — confirmed against the compiled regex: `127.0.0.1:5432:5432` yields an empty variable set, so the Module publishes a host port no declaration names and `lint-config` is silent. A `ports:` entry that interpolates no `*_PORT` variable is now its own diagnostic.
  - `[low]` `[patch]` Seventeen new `${VAR:-default}` port fallbacks in the `x-endpoints:` urls with nothing reconciling them against `.env.example` — real, but the class is pre-existing (the compose `ports:` fallbacks were already unchecked; `assert_pins.py` covers `*_VERSION` only). Recorded in `deferred` rather than patched, since the fix is a new assertion over a gap this story did not create.
  - `[low]` `[patch]` `x-requires:` names host-published endpoint keys while inter-Module traffic goes over the compose network on container ports — confirmed (Keycloak reaches Postgres at `postgres:5432`). The reconciliation is still sound as a capability-name check; ADR 0012 now states the distinction so nobody reads an endpoint key as a connection string.
  - `[low]` `[patch]` Compose discards top-level `x-` keys from included files and nothing records it — confirmed: the rendered document carries neither block. ADR 0012 now states it, so story 3-3 parses the raw module files rather than the rendered model.
  - `[false]` `[reject]` The committed spec contradicts the ADR and the code beside it (seven healthchecks claimed, five shipped; loki/tempo images named as 3.5.7/2.9.0). The mismatch is real and was verified — the pinned tags are 3.7.7/3.0.3 and both are genuinely distroless — but rejected under the standing rule that a finding whose fix is to edit this build's spec is not actionable. The correction is recorded in ADR 0012 and in this Auto Run Result instead.
  - `[low]` `[reject]` Section labels regressed from prose titles to bare directory names — confirmed. Rejected: every `PASS` label is byte-identical to the baseline (verified by diffing the label sets of both suites against the same live stack), the only loss is four section headings, and the fix is a per-Module display-name mechanism rather than a correction.
  - `[low]` `[patch]` A half-selected observability profile exits 0 where it used to exit non-zero, because `services/grafana/smoke.sh` gated the "datasource provisioned" loop on the backing Module running — confirmed, and it rewrote a check the carve was meant to move verbatim: that loop reads Grafana's own provisioning through Grafana's API and never reaches the backend. The first loop is un-gated again; only "datasource connects" keeps the gate.
  - `[low]` `[patch]` Sourced module scripts share the driver's variable namespace, so a module assigning `module=` would break enumeration for every later Module — real. The driver's own state is now prefixed (`driver_module`, `driver_module_smoke`, `local driver_http_code`).
  - `[low]` `[reject]` A Module with no `services:` key is checked for one leg and then abandoned, so the operator discovers the remaining legs one re-run at a time — confirmed at the `continue`. Rejected: a module file with no services is a broken file, one named diagnostic is the right first thing to say, and the fix reorders control flow for a cosmetic gain.
  - `[low]` `[patch]` `depends_on` is read from the primary only and matched exactly, so a Module whose helper holds the real edge — or one depending on a provider's `<provider>-<role>` helper — is falsely rejected. Real, and a false rejection blocks legitimate work rather than merely missing a defect. The union across every service the Module owns is now taken, and a `<provider>-<role>` edge is accepted.
  - `[low]` `[reject]` README's "Gotchas worth knowing" bullets are duplicated into the per-Module `gotchas.md` files with nothing keeping them in sync — real. Rejected: README's own new preamble says the per-Module file goes further than the section does, so the authority is stated, and the fix is either a trim that loses the overview or a new content check.
  - `[medium]` `[patch]` A module `smoke.sh` that fails to source leaves the Module contributing nothing while the suite exits 0 — confirmed in a shell: `source` on an unparseable file returns 1 and the loop simply continues. The driver now parses each script with `bash -n` first and records a failure naming the Module; a self-test plants an unparseable module script.
  - `[low]` `[reject]` A sourced module script hitting an unbound variable under `set -u` kills the driver mid-loop — confirmed, but this is a loud non-zero exit with the shell naming the variable, not a silent skip. Correct behaviour on a state never shown to be reachable.
  - `[low]` `[patch]` `await_url`'s 60s budget is half `assert_ready`'s 120s, so a backend ready between the two now fails Grafana's counted "datasource connects" check where the old ordering passed — confirmed at `seq 1 30` versus `seq 1 60`. The budgets now match.
  - `[low]` `[patch]` Grafana's counted datasource-health asserts run after only a bounded pre-wait — same root cause as the budget mismatch above; fixed by the same change.
  - `[medium]` `[patch]` A `healthcheck:` with `disable: true` or `test: ["NONE"]` satisfies the leg — same defect as the first row; fixed by the same change.
  - `[low]` `[patch]` `PORT_VARIABLE` misses the unbraced `$TEMPO_PORT` form, which Compose accepts — confirmed against the compiled regex. The pattern now accepts both forms, with a positive self-test case.
  - `[low]` `[patch]` An `x-endpoints:` key naming no published port passes — same defect as the one-direction row; fixed by the same change.
  - `[low]` `[reject]` An `x-requires:` provider value that is a mapping or `None` is reported as a missing endpoint name rather than as a shape defect — confirmed: `str(wanted)` produces a confusing name. Rejected: the check still exits non-zero naming the module and the offending value, so no defect ships; the fix adds a branch for a diagnostic nobody is likely to meet.
  - `[low]` `[patch]` A requirer depending on the provider's `<provider>-<role>` helper is reported as having no edge — same defect as the primary-only row; fixed by the same change.
  - `[low]` `[reject]` The self-test's module-name scan over the driver is case-insensitive across comments, so a future Module named with an ordinary word (`core`, `summary`) would fail it — confirmed. Rejected: no such Module exists or is planned, and the failure mode is a loud self-test failure naming the word, not a silent defect.
  - `[low]` `[patch]` The new partial-Selection case counts global `SKIP` lines, which breaks if the selected set ever includes a Module whose own script emits skips — real, and grafana and otel-collector both do. It now counts one `"<name> not running"` line per unselected Module.
  - `[low]` `[reject]` The driver prints bare directory names where the old script printed grouped prose headings, and the `Admin UIs` grouping is gone — same defect as the section-label row; rejected for the same reasons.
  - `[false]` `[reject]` The spec's task list says loki and tempo take real healthchecks including a `/busybox/wget` exec form, and none shipped. The premise is refuted: the spec's claim rested on probing the *running* containers, which are stale at `loki:3.5.7`/`tempo:2.9.0`; the pinned `loki:3.7.7` and `tempo:3.0.3` were exported and hold `usr/bin/loki` and `/tempo` respectively and no shell at all, so no such probe is possible. The implementation is right and the spec was wrong.
  - `[false]` `[reject]` The acceptance criterion "exactly seven added `healthcheck:` blocks" cannot be met — true, five shipped, for the reason refuted above. Rejected under the rule that a finding whose fix is to edit this build's spec is not actionable.
  - `[false]` `[reject]` The acceptance criterion "all thirteen containers reach healthy" cannot be met with three Modules exempt — true, and `wait-healthy.sh` passes over a container with an empty health field. Same rejection: the fix edits this build's spec.
  - `[false]` `[reject]` The acceptance criterion "every label printed is one that existed before" is broken by the section headings — the `PASS` labels were verified byte-identical against the baseline suite run on the same stack, so the criterion holds at the surface it was written about; only section headings changed, which is the rejected cosmetic row above.
  - `[false]` `[reject]` The spec says seven Modules ship no healthcheck when eight did. Confirmed off by one (keycloak, mailpit, minio, postgres, redis had them; `minio-init` is a helper the leg does not apply to). Rejected: no bad outcome follows, and the fix edits this build's spec.
  - `[medium]` `[patch]` The driver's empty-glob preflight is unverified — pre-verified by the verification-gap layer, which deleted the guard, hid all thirteen scripts and watched the suite report success having checked nothing while every existing case still passed. A `moved_aside` case now asserts a non-zero exit naming `services/*/smoke.sh`.
  - `[medium]` `[patch]` A `healthcheck.none` Module's only readiness gate is one unpinned line in its own `smoke.sh` — pre-verified: deleting `assert_ready` from `services/loki/smoke.sh` leaves lint-shell, lint-config and the self-test green while the stack declares Loki ready having never polled `/ready`. A case now requires every `healthcheck.none` Module's script to hold at least one counted assertion, matched generically so the collector's `assert_contains` round-trip qualifies.
  - `[low]` `[patch]` The `x-endpoints:` reconciliation is text-level and a non-port endpoint claim is checked for key existence only — real; every key in the tree is a `*_PORT` variable today, and the new reverse direction now pins that. The remaining generality is recorded in ADR 0012 rather than guarded.
  - `[low]` `[defer]` AC-2's expectation lives at the rendered model while the new check reads module-file text, so a service arriving from `compose.override.yaml` or an `include:` outside `services/` is not caught by this check — real, and closure rests on the separate pre-existing pin that the root file declares no `services:` key. Deferred: the trade is argued in this spec's Design Notes and reversing it breaks a dozen stub-document fixtures.
  - `[low]` `[reject]` AC-4 gained no new test; `identifier_only()` and the duplicate-published-port rule are pre-existing and untouched. Confirmed, and the existing rendered-document case still passes. Rejected: the spec's planned module-file fixture is impossible — a planted `services/zz-*/compose.yaml` never renders, because the root `include:` list is explicit and the self-test never edits the root file.
  - `[low]` `[defer]` AC-1 under its literal reading forbids a healthcheck marker at all, and three Modules take one — real as a reading, settled against the images: the exemption is a property of the pinned tag and ADR 0012 records that it must be re-verified on any version bump. Deferred as the residual risk.
  - `[medium]` `[defer]` AC-3's expectations live at a live stack while every added test lives at a stubbed surface — real. Settled here by running both suites against the live stack (47/0/0 each, label sets identical) but the live containers are stale relative to the pins, so the pinned images were never actually run. Deferred with what would settle it.
  - `[low]` `[reject]` AC-5 reconciles declaration against declaration rather than against effective configuration, so both sides can drift from the runtime together — confirmed. Rejected: the alternative needs a per-provider notion of what a database or bucket is, which is the special-casing the generic rule was chosen to avoid; ADR 0012 now states the narrowing.
  - `[low]` `[reject]` The spec was left at `in-review` with no `operator_actions:` key, where a reading that treats the live-stack criteria as operator-owed would finalize to `awaiting-operator`. Confirmed as a reading. Rejected: the story's acceptance criteria name no action only a human can perform outside the repo — no domain, DNS record, API key or vendor console — and the live-stack commands are ordinary repository verification, which is what the `deferred` entries are for.
  - `[low]` `[defer]` Nothing pins that the rendered document carries no top-level `x-` keys, so a future Compose that merged them would collide `x-requires.postgres`, declared by both keycloak and pgadmin with different lists — confirmed absent from the render today. Deferred: no bad outcome is reachable on any supported Compose, and ADR 0012 now records the behaviour.

### 2026-09-07 — Review pass (follow-up)
- verdicts: 38 findings — high 0, medium 7, low 25, false 6, maybe-false 0
- findings:
  - `[medium]` `[patch]` ADR 0012 miscounts its own evidence: "seven Modules shipped no healthcheck at all" and "Five of the seven" — confirmed against `git show 30fdb19:services/*/compose.yaml`: keycloak, mailpit, minio, postgres and redis carried probes, the other eight did not (loki's pre-change `healthcheck:` is inside a `# No healthcheck:` comment), and the diff itself resolves 5 probes + 3 markers = 8. Both figures corrected to eight in `docs/adr/0012`.
  - `[medium]` `[defer]` The `x-requires:` reconciliation runs one direction only, so an undeclared consumption is invisible; grafana provisions a `postgres` datasource, reads `POSTGRES_*` and asserts `datasource 'postgres' connects`, while declaring neither `x-requires: postgres` nor a `depends_on` edge — confirmed, and pre-existing at 30fdb19. Deferred: the mirror rule is a new check, and the smallest grafana-specific fix is a runtime-model change this story forbids.
  - `[low]` `[reject]` A sourced module script shares the driver's namespace and could assign `FAIL=0` or capture a helper name, zeroing the verdict — real as a mechanism (the counters are globals by design, argued in the spec's "Sourcing, not executing"), but no Module does it and the fix is the counting protocol the design deliberately rejected.
  - `[medium]` `[patch]` `smoke.sh` is the one contract leg with no content rule: `MODULE_FILES` asks `is_file()`, so a Module's verification is deletable from inside that Module with lint-config, lint-shell, the self-test and `pixi run smoke` all green — pre-verified by the verification-gap layer and re-confirmed here by emptying `services/redis/smoke.sh`. The `counted` assertion at `lint_selftest.py:2976` was widened from the three `healthcheck.none` Modules to all thirteen; with redis emptied the case fails naming it.
  - `[low]` `[patch]` Three branches of the new checker have no case: `test: NONE` (both spellings), the non-mapping `x-requires` guard and the "declares no services" guard — all three confirmed absent from `contract_cases`. The `test: NONE` case was added (see below); the other two were rejected — both fail loudly with a non-zero exit rather than admitting a bad Module, so the exposure is a worse diagnostic, not a missed defect.
  - `[low]` `[reject]` `published_ports()` drops a long-syntax `ports:` entry with no `published:` key: `str(entry.get("published", ""))` yields `""`, so it contributes to neither side of the endpoint rule and is not reported as a literal either — confirmed against the code. Rejected: all thirteen Modules use short syntax, no ephemeral-port entry exists, and the fix adds a branch.
  - `[low]` `[patch]` `expect("there are healthcheck-exempt Modules to check", bool(exempted), ...)` makes `healthcheck.none` permanently mandatory, so a Loki/Tempo bump that restored a shell — the good outcome ADR 0012 asks version bumps to look for — turns the self-test red. Confirmed. The guard was deleted with the widening above, which no longer needs a non-empty exempt set.
  - `[low]` `[patch]` The unparseable-script fixture overwrites tracked `services/redisinsight/smoke.sh` in place under a bare `try/finally`, holding the good body only in process memory, where every neighbouring fixture uses `planted()`/`moved_aside()` — confirmed. Rewritten as `with moved_aside([broken]), planted(broken, ...)`, so the good body survives on disk under the `.moved` name.
  - `[low]` `[reject]` The seventeen `x-endpoints:` urls re-duplicate `.env.example` defaults, and `services/redis` bakes `${REDIS_PASSWORD:-devinfra}` into one — real, and the same class already recorded in `deferred`. Rejected: the value is a dev default `.env.example`, `scripts/urls.sh:32` and `services/flower/compose.yaml:37` all carry identically, and the duplication itself is the already-deferred entry.
  - `[low]` `[reject]` Nothing ties an `x-endpoints:` entry's `url` to the key it is filed under, so `MINIO_CONSOLE_PORT` could carry the API port's URL — confirmed as a gap; all seventeen entries were checked and every url does interpolate its own key. Rejected: nothing reads the `url` field today (story 3-3 will), so no bad outcome is reachable, and the fix adds a rule.
  - `[low]` `[patch]` The partial-Selection case still asserts `r.stdout.count("SKIP") == len(unselected)` — the global total the comment twenty lines above it argues against, because grafana's and the collector's scripts emit skips of their own. Confirmed at `lint_selftest.py:3038`: the previous pass's per-Module fix landed on the all-absent case only. Rewritten to count one `"<name> not running"` line per unselected Module.
  - `[low]` `[reject]` `named_modules` matches a Module name anywhere in the driver, case-insensitively, so a future Module called `core` or `summary` would fail on prose — carried from the 2026-09-07 pass: confirmed, no such Module exists or is planned, and the failure is a loud self-test failure naming the word.
  - `[low]` `[reject]` `await_url` counts nothing and prints nothing, and grafana plus otel-collector call it six times, so a running-but-never-ready backend gives up to twelve minutes of silence where the old script printed dots — confirmed (`set -uo pipefail`, no `-e`, so the calls are safe). Rejected: each call is gated on `running`, the eventual failure is loud, and printing would add output the "no runtime-behaviour change" constraint disfavours.
  - `[low]` `[reject]` `services/prometheus/smoke.sh` runs the scrape-target check un-gated on its targets, so a partial Selection fails Prometheus for another Module's absence — confirmed against the old script, which nested it inside `running otel-collector`. Rejected: prometheus and otel-collector share the `observability` profile, so only manually stopping one container reaches the changed case, and gating on six targets would convert real failures into skips.
  - `[low]` `[reject]` `flower` is first in glob order and `check_http` is one curl with no retry, so a still-starting container fails where the old ordering passed — real as a mechanism. Rejected: `ci-stack` runs `wait` (→ `wait-healthy.sh`) before `smoke-strict`, and flower gained a healthcheck in this very change, so CI gates on it; `pixi run wait` is the answer for a hand-run.
  - `[low]` `[reject]` A sourced module script calling `exit`, or tripping `set -u`, kills the driver mid-loop with no summary — carried from the 2026-09-07 pass: a loud non-zero exit naming the variable, on a state never shown reachable.
  - `[low]` `[reject]` A Module publishing no host port at all cannot satisfy the endpoints leg, which demands a non-empty `x-endpoints:` unconditionally — confirmed at `assert_config.py:641-646`. Rejected: no such Module exists, and the intent's own matrix makes an empty block a failure, so the escape hatch would contradict the spec.
  - `[low]` `[reject]` A `ports:` entry interpolating a `*_PORT` variable in the container-side position would be read as published — confirmed against the regex. Rejected: every one of the thirteen Modules writes `${VAR}:<literal>`, so the form is unreached, and the fix parameterises the match.
  - `[low]` `[reject]` An `x-requires:` provider value that is a mapping or `None` is reported as a missing endpoint name rather than as a shape defect — carried from the 2026-09-07 pass: still exits non-zero naming the module and the value, so no defect ships.
  - `[false]` `[reject]` A healthcheck supplied through `extends: common/base.yaml` would be invisible to `healthcheck_declared()`, forcing a false `healthcheck.none`. Refuted: `common/base.yaml` is the only extends source any Module names (all fourteen `extends:` blocks point at it) and it states and holds that only `restart`, `logging` and `networks` may appear there — no healthcheck can arrive that way.
  - `[low]` `[patch]` The unparseable-script fixture mutates a tracked file without the file's own safety helper — same defect as the `planted()`/`moved_aside()` row above; fixed by the same change.
  - `[low]` `[reject]` The carve lost the central script's fixed ordering, so a never-ready Tempo now reads as the collector's lost trace — carried from the 2026-09-07 pass, where the compensating `await_url` pre-wait and its budget were the patch.
  - `[false]` `[reject]` Seven healthchecks were claimed and five shipped; loki/tempo named as 3.5.7/2.9.0 — carried from the 2026-09-07 pass: the premise is refuted against the pinned 3.7.7/3.0.3 images, and the fix would edit this build's spec.
  - `[false]` `[reject]` "All thirteen containers reach healthy" cannot be met with three Modules exempt — carried from the 2026-09-07 pass: true, and the fix edits this build's spec.
  - `[false]` `[reject]` The spec says `services/prometheus/smoke.sh` "gains the `else skip` arm it never had" when the file has only pass/fail. Confirmed as a wording defect — the `else` arm is the driver's Module gate, which the file's own header says. Rejected: the fix edits this build's spec, and no bad outcome follows.
  - `[false]` `[reject]` "Every label printed is one that existed before" is broken by the section headings and grafana's new skip labels — carried from the 2026-09-07 pass: every `PASS` label is byte-identical, only headings and skip strings differ.
  - `[medium]` `[patch]` `smoke.sh` can be emptied to comments with every gate green — pre-verified by the verification-gap layer; same root cause as the `MODULE_FILES` row above and fixed by the same widening.
  - `[medium]` `[patch]` `healthcheck: {test: NONE}` — the second of Compose's two off switches — has no self-test case, so deleting both `NONE` branches leaves every case green while a Module shipping a cancelled probe passes the leg. Pre-verified by the verification-gap layer and re-proved here: with the branches replaced by `return True` the suite still passed; with the new case added it fails three assertions. A `test: ["NONE"]` entry was added to `contract_cases`.
  - `[medium]` `[defer]` `scripts/urls.sh` is not reconciled against the `x-endpoints:` blocks that supersede it, and already omits `LOKI_PORT`, `TEMPO_PORT` and `KEYCLOAK_MGMT_PORT` — pre-verified and re-confirmed. Deferred: ADR 0012 states the decision and assigns generation to story 3-3.
  - `[low]` `[reject]` Prometheus's scrape-target check is un-gated on its targets — same finding as the edge-case row above; rejected for the same reasons, and the layer that filed it noted no realistic Selection reaches it.
  - `[low]` `[reject]` `module_contract()`'s non-mapping `x-requires` guard and its "declares no services" guard have no case — same root cause as the three-branches row; both fail loudly rather than admitting a bad Module.
  - `[false]` `[reject]` The healthcheck inventory is 5/3 where the intent says 7/1 — carried from the 2026-09-07 pass: settled against the pinned images, and the fix edits this build's spec.
  - `[low]` `[reject]` The Prometheus scrape check changed gate under partial Selection, which the intent's "never rewrite a check while moving it" forbids — same finding as the edge-case row; rejected for the same reasons.
  - `[low]` `[reject]` Grafana's `datasource '<uid>' connects` gained a skip label that did not exist before — carried from the 2026-09-07 pass, where the gating was deliberately settled: the first provisioning loop was un-gated again and only the health call keeps the gate, which FR-5 requires.
  - `[low]` `[reject]` `await_url` and the `bash -n` failure label are new runtime behaviour where the intent sanctions only healthchecks — confirmed as literally true. Rejected: `await_url` counts nothing and prints nothing so no label or count changes, and the `bash -n` label replaces a silent skip, which is strictly better; both were deliberate patches from the previous pass.
  - `[medium]` `[defer]` Every lint expectation is exercised at its production surface while every runtime expectation is exercised at a stub or not at all — carried from the 2026-09-07 pass, where it is already recorded in `deferred` (the live stack runs stale images relative to the pins).
  - `[low]` `[reject]` Section headings regressed from curated prose to bare directory names and the `Admin UIs` grouping is gone — carried from the 2026-09-07 pass: cosmetic, every `PASS` label byte-identical, and the fix is a per-Module display-name mechanism.

## Design Notes

**Why the reverse direction is asserted statically.** "A Compose service with no corresponding Module directory must fail" reads like a rendered-model rule, but asserting it there breaks a dozen existing self-tests: the stub documents name services like `pgbouncer` and `svc` that own no directory, and several of those cases expect exit 0. Statically it is stronger and free: a service can only be declared in a module file or in the root, the root is already pinned to declare no `services:` key (`lint_selftest.py:646-654`), so requiring every service key in `services/<dir>/compose.yaml` to be `<dir>` or `<dir>-<role>` closes the loop with no runtime dependency at all.

**Why `x-requires:` is reconciled against `x-endpoints:`.** The epic says a Module declares what it needs from another and CI reconciles it against the provider. Naming the provider's own endpoint keys makes that reconciliation generic — no per-provider special case, no checker that knows what a database is — and it composes with the endpoint contract that story 3-3 generates documentation from. Requiring the matching `depends_on` alongside is what keeps the declaration from drifting away from the runtime edge; ADR 0002 already says a dependency not expressed as `depends_on` does not exist.

**Why the healthchecks land here rather than being marked absent.** Seven Modules were assumed unable to carry one, and the committed comments in loki and tempo state it as fact. Probing the live containers refutes it: `grafana/loki:3.5.7` and `grafana/tempo:2.9.0` both ship `/busybox/busybox`, and every one of the seven answered a real readiness URL from inside its own container. Writing seven `healthcheck.none` markers would have satisfied the contract check while leaving `wait-healthy.sh` blind — the exact silent skip this repository keeps removing. `otel-collector` is the one genuine exemption, and it is the one Module whose pipeline `smoke.sh` proves end to end anyway.

**Sourcing, not executing.** The counters are shell globals and the checks read `.env` values the driver loaded. A module `smoke.sh` run as a subprocess would need a counting protocol over stdout or exit codes; sourced, the carve is a move rather than a redesign, which is what "the suite's pass count is unchanged" asks for. Each file says in its header that it is sourced and not run directly, and carries the one `shellcheck` directive that makes an unassigned `.env` value legal there.

**Ordering after the carve.** The driver runs Modules in glob order, so `otel-collector` now precedes `tempo` where the central script put both readiness polls first. The ingest poll already retries for 60s, so the round-trip still succeeds — but a Tempo that never came up would read as a lost trace instead of as itself. A silent readiness pre-wait inside the collector's own script restores that diagnostic without a Core-side ordering table and without adding a counted check.

## Verification

**Commands:**
- `docker compose config` and `docker compose --profile admin --profile observability config`, diffed against the pre-change captures -- expected: seven added `healthcheck:` blocks, nothing else. Confirm no `x-endpoints:`/`x-requires:` block leaked into a rendered *service* body.
- `docker compose config -q` for every profile subset, via `pixi run lint-compose` -- expected: OK, four combinations, proving Compose accepts the new top-level `x-` keys in included files on this runtime.
- `pixi run lint-config` -- expected: OK over thirteen module files, with the new contract line naming thirteen.
- `pixi run lint-shell` -- expected: OK across the thirteen new `smoke.sh` files; then delete `services/**/*.sh` from the task in `pixi.toml` and re-run the self-test — expected: the widened `empties` case fails by name. Restore and confirm `git diff -- pixi.toml` is empty.
- `pixi run test` -- expected: every self-test case passes, including the new per-leg negatives and the contract-complete fixture module.
- `pixi run ci` -- expected: exit 0.
- `pixi run smoke` against the full stack -- expected: `47 passed, 0 failed, 0 skipped`. Then `pixi run smoke` with nothing running -- expected: exit 0, thirteen skips. Then `SMOKE_STRICT=1 ./scripts/smoke-test.sh` with nothing running -- expected: non-zero, no `SKIP` line.
- `docker compose --profile admin --profile observability up -d && ./scripts/wait-healthy.sh` -- expected: thirteen containers healthy within two minutes.
- `docker volume ls --filter name=devinfra --format '{{.Name}}' | sort | diff <before> -` -- expected: no output.

**Manual checks (if no CLI):**
- The live stack runs from the main checkout and every Module pins a fixed `container_name`, so recreating a container from this worktree repoints its bind mounts at a directory that disappears when the run ends. Healthcheck probes can still be proved without recreating anything: `docker exec <container> <probe>` runs the exact command the healthcheck would. If the recreate-and-wait criteria cannot be settled from here, say so explicitly rather than reporting them as met.
- The local Compose is v5.3.0 and merges what CI's v2 line rejects. `lint-config` is the only local check that sees a Module redeclaring the keyed network; a green `lint-compose` proves nothing about it.



## Auto Run Result

Status: done

**Implemented change.** Every Module now carries its own contract and CI refuses an incomplete
one. Each of the thirteen `services/<name>/` directories ships a `smoke.sh`, a `gotchas.md`, a
top-level `x-endpoints:` block naming every host port it publishes, and either a `seed/`
directory or a justified `seed.none` marker; ten declare a `healthcheck:` and the three whose
pinned images are distroless carry a justified `healthcheck.none` instead. `scripts/smoke-test.sh`
lost its 436 lines of checks and became a driver: preflights, counters, helpers, then a glob over
`services/*/smoke.sh` that sources a Module's script when it is running and skips it by name when
it is not. Core names no Module anywhere, and the self-test fails if it ever does.

The check runs both directions. `assert_config.py` gained `module_contract()`, which asserts the
five legs per Module and requires every service key in a module file to be `<dir>` or
`<dir>-<role>` — with the root file already pinned to declare no `services:` key, that makes a
Compose service no Module directory owns impossible. It also reconciles `x-endpoints:` against the
ports each file publishes in both directions, and every `x-requires:` entry against the provider's
own endpoint keys plus a real `depends_on` edge.

**Correction to this spec's plan.** The plan called for real healthchecks on loki and tempo,
citing `/busybox/wget` in their images. That was probed against the *running* containers, which are
stale at `loki:3.5.7` and `tempo:2.9.0`. The pinned tags are 3.7.7 and 3.0.3; exporting both image
filesystems shows `usr/bin/loki` and `/tempo` and no shell at all. Five healthchecks were added,
not seven, and loki and tempo take justified markers alongside otel-collector. Before this change
eight Modules shipped no healthcheck (not seven — keycloak, mailpit, minio, postgres and redis
carried one); ADR 0012 now states eight. The acceptance criteria naming "seven added
`healthcheck:` blocks" and "all thirteen containers reach healthy" are unmeetable for that reason;
ten reach healthy and three are exempt.

**Files changed.**
- `scripts/smoke-test.sh` — reduced to the glob-driven driver; adds `await_url`, a `bash -n` guard on each sourced script and an empty-catalog preflight.
- `services/*/smoke.sh` (13 new) — the 47 checks, carved verbatim; labels byte-identical to the baseline suite.
- `services/*/gotchas.md` (13 new) — per-Module gotchas, including where a check is liveness and where `urls.sh` omits the endpoint.
- `services/*/seed.none` (11 new), `services/{loki,tempo,otel-collector}/healthcheck.none` (3 new) — justified exemption markers.
- `services/*/compose.yaml` (13) — `x-endpoints:` everywhere, `x-requires:` on the six with a dependency, `healthcheck:` on flower, grafana, pgadmin, prometheus and redisinsight.
- `scripts/assert_config.py` — `module_contract()`, `healthcheck_declared()`, `justified()`, `published_ports()`, `depends_on_names()`.
- `scripts/lint_selftest.py` — the contract's negative and positive cases, the carve's pins, a contract-complete fixture module, and the `lint-shell` `empties` entry widened to every `services/**/*.sh`.
- `docs/adr/0012-every-module-carries-its-own-contract.md` (new) + `docs/adr/README.md`, `README.md`, `AGENTS.md`.

**Review findings — first pass (2026-09-07).** 40 findings across four layers — high 0, medium 6,
low 20, false 5, maybe-false 0. Twelve patch entries applied; five items deferred; the five
spec-contradiction findings rejected under the rule that a finding whose fix edits this build's
spec is not actionable.

**Review findings — follow-up pass (2026-09-07).** 38 findings across the same four layers —
high 0, medium 7, low 25, false 6, maybe-false 0. Six entries patched (3 medium, 3 low):

- `docs/adr/0012` claimed "seven Modules shipped no healthcheck" and "Five of the seven"; the pre-change count is eight (verified against `30fdb19`). Both corrected.
- `smoke.sh` was the one contract leg with no content rule — `MODULE_FILES` asks `is_file()` only, so a Module's entire verification could be deleted from inside that Module with every gate green. `lint_selftest.py`'s `counted` assertion was widened from the three `healthcheck.none` Modules to all thirteen; proved load-bearing by emptying `services/redis/smoke.sh` (the case fails naming redis).
- The same widening removed `expect("there are healthcheck-exempt Modules to check", ...)`, which made `healthcheck.none` permanently mandatory and would have turned a Loki/Tempo bump that restored a shell — the good outcome ADR 0012 asks for — into a red self-test.
- `healthcheck: {test: NONE}`, the second of Compose's two off switches, had no case: `healthcheck_declared()` returns at `disable` before reading `test:`, so the existing `disable: true` case never reaches those branches. A `test: ["NONE"]` entry was added to `contract_cases`; proved load-bearing by replacing both `NONE` branches with `return True` (suite still green before the case, three assertions fail after).
- The partial-Selection case still asserted the global `SKIP` total the comment twenty lines above it argues against; the previous pass's per-Module fix had landed on the all-absent case only. Rewritten to count one `"<name> not running"` line per unselected Module.
- The unparseable-script fixture overwrote tracked `services/redisinsight/smoke.sh` in place, holding the good body only in process memory. Rewritten as `with moved_aside([broken]), planted(broken, ...)`, matching every neighbouring fixture.

Two items deferred (both `medium`, both recorded in frontmatter): the `x-requires:` reconciliation
runs one direction only, so grafana's undeclared Postgres consumption passes in silence; and
`scripts/urls.sh` is not reconciled against the `x-endpoints:` blocks that supersede it and
already omits three of their ports. Rejected findings and their reasons are recorded row by row in
the `## Review Triage Log` entry for this pass.

**Follow-up review recommendation: false.** This pass patched no `high`, so the work has
converged; on a follow-up pass patch volume is not grounds for another round. Patched counts by
verdict: medium 3, low 3.

**Verification performed** (this pass, from this worktree):
- `pixi run lint-config` — OK, fourteen module files carry the Module contract (thirteen Modules plus the planted fixture during the self-test), four profile combinations.
- `pixi run lint-shell` — OK across every `scripts/**/*.sh` and `services/**/*.sh`.
- `pixi run test` — 872 PASS, exit 0, including the two new cases (`every Module asserts something in its own smoke.sh`, `lint-config rejects a healthcheck whose test cancels the probe`).
- Both new cases proved load-bearing by removing what they guard (see above) and restoring afterwards; `git diff` confirms both files back to their patched state.
- `pixi run ci` — exit 0.
- `pixi run smoke` against the live stack — `47 passed, 0 failed, 0 skipped`, unchanged.

**Residual risks.** Unchanged from the first pass and carried in `deferred`: the live stack this
worktree can reach runs `grafana/loki:3.5.7` and `grafana/tempo:2.9.0` while `.env.example` pins
3.7.7 and 3.0.3, so `docker compose --profile admin --profile observability up -d` followed by
`./scripts/wait-healthy.sh` and `pixi run smoke` has never been run on the pinned images from the
checkout that owns the stack. The healthcheck exemptions remain a property of the pinned tag with
nothing enforcing re-verification on a bump — ADR 0012 states the obligation. This pass's patches
are test-only and documentation-only, so they carry no runtime risk of their own.

# Baseline before the image-bump wave — story 1.5

Captured 2026-09-07 at commit `a99e27d`, before any pin moved. A bump can only be
blamed for a check that was passing before it, so this is what "passing before it"
meant.

Runtime: Docker Engine 29.6.1, `linux/arm64` workstation.
Profiles: `COMPOSE_PROFILES=admin,observability` (every service in the stack).

## `pixi run ci`

Exit 0. `lint-compose`, `lint-config`, `lint-shell`, `lint-yaml`, `lint-json`,
`lint-python`, then the self-test — every case PASS.

## `pixi run ci-stack`

`init` → `start` → `wait` → `smoke-strict`. Health wait reported *All containers
running, all healthchecks passing*. Strict smoke summary: **45 passed, 0 failed,
0 skipped**.

Every check that passed at the baseline, by section:

| Section | Checks |
|---|---|
| PostgreSQL | server is PostgreSQL 17 · pgvector distance operator · `pg_stat_statements` loaded · custom `postgresql.conf` in effect · data checksums enabled · extra database `keycloak` · extra database `app_test` · write/read round-trip |
| Redis | authenticated PING · rejects unauthenticated clients · AOF persistence on · `maxmemory-policy` noeviction · broker db 1 round-trip |
| Keycloak | OIDC discovery served · issuer pinned · PKCE S256 advertised · `client_credentials` grant · password grant for `dev` · refresh token issued · **audience mapper puts `devinfra-api` in `aud`** · **realm roles present in token** · health endpoint UP |
| MinIO (Silo) | buckets `uploads`/`artifacts`/`backups` provisioned · object put/get round-trip · versioning enabled on `uploads` |
| Mailpit | SMTP message accepted and stored |
| Observability | collector accepts OTLP traces/logs/metrics · **trace retrievable from Tempo by ID** · **log queryable in Loki by label** · **metric scraped into Prometheus** · all Prometheus scrape targets healthy |
| Grafana | datasources `prometheus`/`loki`/`tempo`/`postgres` provisioned · `prometheus`/`loki`/`postgres` connect |
| Admin UIs | pgadmin `/misc/ping` 200 · redisinsight `/` 200 · flower `/api/workers` 200 |

## Pins at the baseline

| Variable | Tag |
|---|---|
| `POSTGRES_VERSION` | `0.8.1-pg17` |
| `REDIS_VERSION` | `8-alpine` |
| `KEYCLOAK_VERSION` | `26.4.0` |
| `SILO_VERSION` | `RELEASE.2026-09-03T13-18-01Z` |
| `MAILPIT_VERSION` | `v1.28` |
| `PGADMIN_VERSION` | `9.9` |
| `REDISINSIGHT_VERSION` | `2.70` |
| `FLOWER_VERSION` | `2.0` |
| `OTEL_COLLECTOR_VERSION` | `0.140.0` |
| `PROMETHEUS_VERSION` | `v3.7.3` |
| `LOKI_VERSION` | `3.5.7` |
| `TEMPO_VERSION` | `2.9.0` |
| `GRAFANA_VERSION` | `12.2.0` |

## Named volumes before the wave

`docker volume ls --filter name=devinfra`:

```
devinfra_flower-data
devinfra_grafana-data
devinfra_keycloak-data
devinfra_loki-data
devinfra_mailpit-data
devinfra_minio-data
devinfra_pgadmin-data
devinfra_postgres-data
devinfra_prometheus-data
devinfra_redis-data
devinfra_redisinsight-data
devinfra_tempo-data
```

No bump may add, remove or rename an entry in this list.

## Upstream currency, re-resolved 2026-09-07

Every target tag below was confirmed present for both `linux/amd64` and `linux/arm64`
by reading the registry manifest index directly, and confirmed to be upstream's newest
release by reading the project's own release feed.

| Variable | Target | Registry | arm64 + amd64 | Newest upstream |
|---|---|---|---|---|
| `POSTGRES_VERSION` | `0.8.6-pg17` | Docker Hub | yes | yes (newest pg17 tag) |
| `REDIS_VERSION` | `8.10.1-alpine` | Docker Hub | yes | yes |
| `MAILPIT_VERSION` | `v1.31.1` | Docker Hub | yes | yes |
| `PGADMIN_VERSION` | `9.17` | Docker Hub | yes | yes |
| `FLOWER_VERSION` | `2.1.0` | Docker Hub | yes | yes |
| `OTEL_COLLECTOR_VERSION` | `0.160.0` | Docker Hub | yes | yes |
| `PROMETHEUS_VERSION` | `v3.14.0` | Docker Hub | yes | yes |
| `KEYCLOAK_VERSION` | `26.7.3` | quay.io | yes | yes |
| `REDISINSIGHT_VERSION` | `3.8.0` | Docker Hub | yes | yes |
| `LOKI_VERSION` | `3.7.7` | Docker Hub | yes | yes |
| `TEMPO_VERSION` | `3.0.3` | Docker Hub | yes | yes |
| `GRAFANA_VERSION` | `13.2.1` | Docker Hub | yes | yes |
| `SILO_VERSION` | `RELEASE.2026-09-03T13-18-01Z` | Docker Hub | yes | yes — unchanged, already current |

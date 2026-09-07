# 8. Object storage replacement

Date: 2026-09-06 · Status: **Accepted** · Spine: Deferred (now resolved)

## Context

MinIO was archived on 2026-04-25.

The newest **pullable** image is `RELEASE.2025-09-07T16-13-09Z`, which is what this
repository pins. The final upstream release, `RELEASE.2025-10-15T17-29-55Z`, fixed
CVE-2025-62506 — privilege escalation, CVSS 8.1 — and was published with zero assets. It is
absent from both Docker Hub and quay. Six further post-archive advisories have no community
fix.

Separately, `RELEASE.2025-05-24T17-08-30Z` gutted the embedded console: admin features and
LDAP/OIDC login were removed to the commercial AIStor product, leaving only an object
browser. The pinned image already has that hollow console.

This is not a version bump. It touches seeded buckets, the `mc` round-trip in the smoke
test, the `AWS_ENDPOINT_URL` contract variable, and the console URL in the README.

## Options

| Candidate | License | Assessment |
|---|---|---|
| **`pgsty/silo`** | AGPL-3.0 | MinIO fork with the console restored and post-archive CVEs patched. Preserves `MINIO_*` variables and the on-disk format, so migration is close to an image-name swap. Bus factor 1. |
| **SeaweedFS 4.45** | Apache-2.0 | Real console, S3 on :8333, versioning and object lock supported. `S3_BUCKET=a,b,c` seeds at startup and would retire the `minio-init` helper. `mc admin *` does not work. Volume server defaults to 8080 — collides with `KEYCLOAK_PORT`. |
| **Garage v2.4.0** | AGPL-3.0 | **Ruled out.** No versioning (returns 501), no bucket policies, no object lock. `minio-init` runs `mc version enable` and the smoke test asserts versioning is on. |
| **RustFS 1.0.0-rc.5** | Apache-2.0 | Revisit at GA. Prerelease — do not pin. |
| *Status quo* | AGPL-3.0 | Documented acceptance of a pinned archived image with seven unpatched advisories, permitted by ADR 0007 only with a dated written acceptance. |

## Decision

**Adopt `pgsty/silo`**, pinned to `RELEASE.2026-09-03T13-18-01Z`.

The same image replaces `minio/mc` for the init container — it ships `mc` at
`/usr/bin/mc`, so overriding the entrypoint runs the client. That removes the
second archived dependency, which was easy to miss: `minio/mc` is archived too.

`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` and `MINIO_BUCKETS` keep their names,
because Silo genuinely consumes them — that is the compatibility surface, not an
oversight. Only the image pin was renamed, `MINIO_VERSION` → `SILO_VERSION`, since
it no longer points at a MinIO release. The volume stays `minio-data` and the
config directory stays `docker/minio/` per ADR 0004: renaming a volume orphans its
data, and no amount of tidiness is worth that.

## Verification

Tested against a copy of the live `minio-data` volume before any change was made to
the running stack:

- Buckets created by MinIO are read by Silo, with per-bucket versioning state
  intact — 6/6 checks.
- **Compatibility is bidirectional.** Silo reads objects MinIO wrote, and MinIO
  reads objects Silo wrote (6/6 checks). The migration is therefore reversible:
  rolling back is an image-pin revert, not a restore.
- The repository's own `scripts/smoke-test.sh` passes in full against the live
  stack running Silo — **45 passed, 0 failed, 0 skipped**.
- The image ships `mc` (`RELEASE.2026-09-03T07-13-05Z`), so the smoke test's
  `mc alias/ls/cp/cat/version` calls work unchanged, and `mc admin info` responds.

Not verified: whether Silo's console restores the admin features MinIO removed in
`RELEASE.2025-05-24`. Both serve a console over HTTP; comparing their UI features
needs a browser session. The decision does not rest on it.

## Consequences

The stack no longer depends on any archived image. Object storage is maintained
again, and the CVE gap is closed.

**Bus factor 1** — Silo is a small fork. That risk is materially reduced by the
verified reverse compatibility: if it is abandoned, the fallback is any
MinIO-format-compatible implementation, and no data migration is required to get
back. Re-evaluate if RustFS reaches GA or if SeaweedFS's ergonomics improve;
neither is urgent while the format stays portable.

Silo passes the ADR 0007 admission policy: AGPL-3.0, no account or token, no
privileged host access, actively maintained (published 2026-09-04).

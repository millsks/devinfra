# 8. Object storage replacement

Date: 2026-09-06 · Status: **Proposed** — awaiting decision · Spine: Deferred

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

**Not yet made.** Recorded so the choice is deliberate rather than inherited.

The module is named `object-storage` rather than `minio` so the eventual swap does not
collide with the frozen volume name `minio-data` (ADR 0004). Settle this before adding new
catalog services, so the module contract is exercised on a replacement that is already
understood.

## Consequences of deferring

Every day the stack runs a pinned archived image with a known unpatched privilege-escalation
CVE. Mitigated by loopback-only binding and the stack's local-development-only scope, but it
is real and it does not improve on its own.

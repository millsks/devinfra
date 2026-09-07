# 7. Catalog admission policy

Date: 2026-09-06 · Status: Accepted · Spine: AD-20

## Context

The catalog is expanding — message broker, search, secrets, AWS emulation. Two module
authors could easily disagree about what is acceptable to depend on, and by the time it is
noticed the service is wired in. Two concrete cases forced the question:

- **LocalStack**: Community Edition discontinued 2026-03-23, repository archived, the current
  image requires an auth token, and the free tier is licensed for non-commercial use only.
  It also requires mounting the host Docker socket — root-equivalent access.
- **HashiCorp Vault**: relicensed to BUSL. Excluded by the maintainer; OpenBao is the
  replacement.

## Decision

A service is admitted only if all hold: an OSI-approved license; no account, token or
license key required to start; no privileged host access (no `/var/run/docker.sock`, no
`privileged: true`); and actively maintained upstream — an archived project may be retained
only with a written, dated acceptance in its `gotchas.md` naming the unpatched advisories.

Separately, the security posture is **fixed, not improved**. Trivial credentials, no TLS,
`start-dev` Keycloak, anonymous Grafana admin and `synchronous_commit = off` are correct for
the stated purpose. Hardening requests are rejected on principle: a stack that is *almost*
safe to deploy is more dangerous than one that obviously is not.

## Rejected

**Judging case by case.** That is how a socket mount gets in.

**Partial hardening.** Creates a stack that looks deployable without being so.

## Consequences

LocalStack is out. AWS emulation beyond S3 uses `motoserver` plus ElasticMQ, both Apache-2.0,
no token, no socket mount, with MinIO's successor retaining the S3 endpoint.

MinIO's archival becomes a decision that must be made rather than a status quo that can
drift — see ADR 0008.

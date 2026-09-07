# 1. Modules via Compose `include`, sharing config through `extends`

Date: 2026-09-06 · Status: Accepted · Spine: AD-1, AD-2, AD-6

## Context

`compose.yaml` is 424 lines defining thirteen services. The granularity of choice is one of
four whole-profile combinations, so a project needing only Postgres and Redis still boots
Keycloak. Splitting it into one file per service requires a mechanism.

The existing file shares configuration through YAML anchors:

```yaml
x-defaults: &defaults
  <<: [*restart, *logging]
  networks: [devinfra]
services:
  postgres:
    <<: *defaults
```

## Decision

Split into `services/<name>/compose.yaml`, reassembled by `include` in the root file.
Shared configuration moves to `common/base.yaml`, consumed via
`extends: {file: ../../common/base.yaml, service: defaults}`. That file is never itself
included. Relative paths are rewritten against each module's own directory;
`project_directory` is not used.

## Rejected

**Keeping YAML anchors.** They do not work. Anchors are document-scoped (YAML 1.2.2 §7.1)
and `include` is applied after YAML parsing, so an alias cannot cross a file boundary.
Verified: `yaml: line 3, column 10: unknown anchor 'defaults' referenced`. Upstream closed
this "won't fix" (docker/compose#10912) and points to `extends` (docker/compose#12636).

**Multiple `-f` files instead of `include`.** Does not rescue anchors either — verified —
and merges by service name, so there is no way to broadcast defaults.

**Duplicating the fragment in every file.** Works, but N copies drift independently.

**`project_directory` to keep paths root-relative.** Silently re-bases *every* relative
path including `env_file`, `build.context` and `extends` targets, and breaks standalone use.

## Consequences

`common/base.yaml` may declare only `restart`, `logging`, `networks`. It must never declare
`depends_on`, `links`, `volumes_from` or `network_mode: service:*` — `extends` inherits
those keys but does not import the resources they reference, so every consumer would fail
with `depends on undefined service`.

One-shot helpers must override `restart: "no"`, or they inherit `unless-stopped` and
restart-loop forever.

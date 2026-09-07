# 6. Keep the five-container observability bundle

Date: 2026-09-06 · Status: Accepted · Spine: AD-13

## Context

The observability profile is five containers and roughly a gigabyte — the heaviest thing in
the stack. `grafana/otel-lgtm` packs the OTel Collector, Prometheus, Loki, Tempo and Grafana
into a single image built for exactly this dev/demo case.

## Decision

Keep the five separate modules with their existing configs. Do not adopt `otel-lgtm`.
Weight is addressed by Selection — not starting the bundle — rather than by replacing it.

## Rejected

**Adopting `otel-lgtm`.** It would discard two gotchas already solved here: Loki 3.x needs
`allow_structured_metadata: true` to accept OTLP at all, and Tempo's span metrics need
`--web.enable-remote-write-receiver` on Prometheus or Grafana's service map stays
permanently empty. Both are captured in the current configs. Adopting the bundle also
reduces configurability and moves further from production topology.

**Offering it as a lighter alternative bundle.** Tempting, but it duplicates every component
the stack already runs, the two bundles cannot run together without port collisions, and
catalog growth is an explicit counter-metric.

## Consequences

The observability bundle stays heavy. That is acceptable because modularity makes not
starting it a first-class choice, which was the real complaint.

Note: `otel-lgtm:0.32.1` currently bundles *older* pins than this stack runs (Grafana 13.2.0
vs 13.2.1, Collector 0.159.0 vs 0.160.0), but that gap is a few days of release timing on a
weekly-cadence repo. It is not a durable argument and should not be cited as one — the
solved gotchas are.

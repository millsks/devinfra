# 9. Podman is verified through the Docker-compatible socket

Date: 2026-09-07 · Status: Accepted · Spine: AD-9, NFR-7

## Context

"Portability is proven, not documented": the stack must actually start and pass the smoke
suite on a supported non-Docker-Desktop runtime. Three things could satisfy that, and they
are not equivalent.

`podman-compose` is a separate Python implementation of the Compose specification. It
reimplements `depends_on` ordering, `condition: service_healthy` gating and `ps --format`
output, so a run against it exercises *that* implementation's behaviour, not this stack's.
When it disagrees with Compose v2 — and it does, most sharply around health conditions and
one-shot containers — the result says nothing about whether the stack works under Podman.

`podman compose` is not a third thing. It is a wrapper that locates an installed
`docker-compose`, sets `DOCKER_HOST` at Podman's socket, and execs it (verified in
`cmd/podman/compose.go`). It is the option below, with one more layer between the decision
and the thing decided.

Rootless Podman keeps its socket in a user systemd manager, whose lifetime is tied to the
user's session. Healthchecks are implemented as systemd transient timers, so on a runner
where lingering is not enabled the health gating the whole verification rests on can be
absent for reasons unrelated to the stack.

## Decision

The Podman job runs the stock Compose v2 client with `DOCKER_HOST` pointing at **rootful**
`podman.socket`, and every task it invokes is the same `pixi run` task the Docker job
invokes. The runtime is selected by *where the Docker API lives*, never by swapping in a
different compose implementation or a second set of task bodies.

Because Compose's dependency graph and health gating run client-side, both jobs execute
byte-identical logic and differ only in which API answers. That is what makes the Podman
job a comparison rather than a different test.

The claim is then gated rather than assumed. `scripts/assert-podman.sh` reads the container
names Compose says this run created and asks Podman's own API what it is running; a name
Podman does not report fails the job. Asserting on Podman's view — not on a version string,
not on a binary's presence — is what makes a silent fallback to Docker impossible.
`scripts/podman-socket.sh` stops `docker.socket` and `docker.service` first, for the same
reason: with no Docker daemon left, a stack that comes up has nowhere else to have come up.

## Rejected

**`podman-compose`.** A different implementation of the orchestration under test. A green
run would prove that `podman-compose` can start something, not that this stack runs on
Podman.

**`podman compose`.** Equivalent to the decision with an extra indirection, and it hides
which `DOCKER_HOST` was used behind a wrapper's own lookup rules.

**Rootless Podman.** Closer to how a developer runs it, but its healthcheck machinery
depends on a user systemd manager that a CI runner does not reliably provide. A verification
that can fail for reasons outside the stack is not a verification. A workstation using
rootless Podman still works — the README documents it — it is simply not what CI gates on.

**Asserting the runtime by name** (`podman --version`, or `docker version` reporting
`podman`). Both are strings a Docker daemon could still be serving alongside. Only the
container list distinguishes "Podman is installed" from "Podman ran this".

## Consequences

`podman.socket` is created `root:root` mode `0660`, which a non-root runner cannot open, so
the job installs a `SocketGroup=docker` drop-in under `/etc/systemd/system/podman.socket.d`.
That is a machine-level change, so `scripts/podman-socket.sh` refuses to run unless `CI` is
set or `DEVINFRA_ALLOW_RUNTIME_SETUP=1` states the intent — it must never happen by accident
on a workstation.

The job pins `ubuntu-24.04` rather than `ubuntu-latest`. The label moves to a new release
with a different Podman and a different Compose major, silently changing what the job proves.

`compose.yaml`'s `x-logging` sets `max-file: "3"`. Podman's compat API stores unknown log
options without rejecting them and reads only `path`, `max-size` and `tag`, so **`max-file`
is inert under Podman** — log rotation keeps one file rather than three. Nothing fails and
nothing warns; it is a documented deviation, not a defect, and it is not worth changing the
Docker behaviour to erase.

`restart: unless-stopped` is honoured while Podman is running but is not restored after a
reboot unless `podman-restart.service` is enabled. Same class: stated in the README rather
than worked around.

Closing the `DEVINFRA_COMPOSE` seam was part of this decision, not a separate cleanup. Five
pixi tasks named `docker compose` directly, and `pixi.toml` has no shell expansion, so those
five ignored the seam entirely: without repointing them at `scripts/compose.sh`, setting
`DEVINFRA_COMPOSE="podman compose"` was a setting that appeared to work and did not.

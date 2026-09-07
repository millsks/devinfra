#!/usr/bin/env bash
# Put the Docker API at Podman's compatibility socket, and stop Docker.
#
#   CI=true ./scripts/podman-socket.sh
#   DEVINFRA_ALLOW_RUNTIME_SETUP=1 ./scripts/podman-socket.sh
#
# This reconfigures the machine it runs on: it stops the Docker daemon and writes
# a unit drop-in under /etc/systemd/system. That is correct on a throwaway hosted
# runner and wrong on a workstation, so it refuses to run unless CI says it is one
# or DEVINFRA_ALLOW_RUNTIME_SETUP=1 says the caller meant it. The refusal happens
# before anything is stopped or written.
#
# Stopping Docker is part of the proof, not tidiness. `docker compose` talks to
# whatever DOCKER_HOST points at; with no Docker daemon left running, a stack that
# comes up and passes the smoke suite can only have come up under Podman.
#
# The drop-in exists because podman.socket is created root:root mode 0660, which a
# non-root runner cannot open. SocketGroup=docker hands it to the group the runner
# user is already in — the same group Docker's own socket used, now vacated.
#
# Overridable seams, following the DEVINFRA_<TOOL> convention scripts/token.sh set:
#   DEVINFRA_SUDO             the privilege escalation command  (default "sudo")
#   DEVINFRA_PODMAN_SOCKET    the socket path to verify         (default /run/podman/podman.sock)
#   DEVINFRA_PODMAN_DROPIN_DIR  where the drop-in is written    (default /etc/systemd/system/podman.socket.d)
#
# The drop-in directory is a seam for the same reason the others are: the self-test
# drives the consenting path, and a real /etc/systemd path there would reconfigure
# the developer's own machine the moment one variable was wrong.
set -euo pipefail

socket_path="${DEVINFRA_PODMAN_SOCKET:-/run/podman/podman.sock}"
dropin_dir="${DEVINFRA_PODMAN_DROPIN_DIR:-/etc/systemd/system/podman.socket.d}"
dropin_file="${dropin_dir}/devinfra-socket-group.conf"

# Only a truthy CI counts. `CI=false` is a value people export on workstations to
# turn CI-ish behaviour off, and reading "set" as "yes" would take that as consent
# to stop their Docker and write under /etc/systemd — the exact opposite of what it
# says. An unrecognised value is not consent either.
consent=0
case "${CI:-}" in
    true | True | TRUE | 1 | yes | YES) consent=1 ;;
esac
if [[ "${DEVINFRA_ALLOW_RUNTIME_SETUP:-}" == "1" ]]; then
    consent=1
fi

if ((consent == 0)); then
    printf 'podman-socket: refusing to reconfigure this machine.\n' >&2
    printf '  It stops docker.socket and docker.service and writes %s.\n' "$dropin_file" >&2
    printf '  CI=%s is not consent.\n' "${CI:-<unset>}" >&2
    printf '  Set DEVINFRA_ALLOW_RUNTIME_SETUP=1 if that is genuinely what you want.\n' >&2
    exit 1
fi

# Word-split once, deliberately: the seam may name a multi-word command, exactly
# as DEVINFRA_COMPOSE does in scripts/lib/common.sh.
read -r -a sudo_argv <<<"${DEVINFRA_SUDO:-sudo}"

# The socket first, then the service: systemd would restart a stopped
# docker.service through a still-listening docker.socket.
"${sudo_argv[@]}" systemctl stop docker.socket docker.service
"${sudo_argv[@]}" mkdir -p "$dropin_dir"
# DirectoryMode matters as much as SocketMode. podman.socket's runtime directory
# is created root:root 0700, and a socket inside a directory the caller cannot
# traverse is unreachable no matter how permissive the socket itself is — worse,
# `test -e` on such a path returns false, so the socket reads as absent when it is
# merely walled off. That is exactly how the first hosted run failed.
printf '[Socket]\nSocketGroup=docker\nSocketMode=0660\nDirectoryMode=0755\n' | "${sudo_argv[@]}" tee "$dropin_file" >/dev/null
"${sudo_argv[@]}" systemctl daemon-reload
"${sudo_argv[@]}" systemctl enable podman.socket
# `enable --now` starts an inactive unit and leaves an already-active one exactly
# as it was, drop-in and all — and podman.socket is active by default on some
# images. `restart` both starts it and makes it re-read the drop-in, so the group
# change actually applies instead of taking effect at the next reboot.
"${sudo_argv[@]}" systemctl restart podman.socket

# DirectoryMode only governs a directory systemd creates. When the runtime
# directory already exists — which it does whenever podman.socket was active
# before this script ran — systemd leaves its mode alone, so widen it directly.
socket_dir="$(dirname "$socket_path")"
if [[ -d "$socket_dir" ]]; then
    "${sudo_argv[@]}" chmod 0755 "$socket_dir"
fi

# Verified, not assumed. `systemctl restart` reports success for a unit whose
# ListenStream it never managed to bind, and every later step would then fail with
# a connection error naming nothing.
#
# Bounded wait first: `systemctl restart` returns once systemd has accepted the
# job, not once the listener is bound, so checking the path on the very next line
# races a socket that is about to appear. Ten tries at 0.5s is far longer than
# binding a unix socket takes and still fails fast when the unit is genuinely not
# going to produce one.
for _ in $(seq 1 10); do
    [[ -e "$socket_path" ]] && break
    sleep 0.5
done

if [[ ! -e "$socket_path" ]]; then
    # Distinguish the two causes, because they read identically through `test -e`
    # and point at completely different fixes.
    if [[ ! -x "$socket_dir" ]]; then
        printf 'podman-socket: %s is not traversable by %s, so %s cannot be reached.\n' \
            "$socket_dir" "$(id -un)" "$socket_path" >&2
        printf '  The socket may well exist; a directory mode of 0700 makes test -e report false.\n' >&2
    else
        printf 'podman-socket: %s does not exist after enabling podman.socket.\n' "$socket_path" >&2
    fi
    # A bare "it is not there" names nothing actionable, which is the failure
    # mode this script's own checks exist to prevent. Say what systemd thinks
    # the unit is and where it was actually told to listen.
    #
    # `set +e` rather than `|| true` per command: the outcome is already decided
    # — this block ends in `exit 1` no matter what — so nothing here can mask a
    # failure. `|| true` would read as the swallow-the-error construct the lint
    # surface bans, and it is banned for good reason; this is not that.
    set +e
    printf '  --- systemctl status podman.socket ---\n' >&2
    "${sudo_argv[@]}" systemctl status --no-pager --full podman.socket >&2 2>&1
    printf '  --- ListenStream as configured ---\n' >&2
    "${sudo_argv[@]}" systemctl show podman.socket -p Listen -p ListenStream -p FragmentPath >&2 2>&1
    printf '  --- sockets systemd is actually listening on ---\n' >&2
    "${sudo_argv[@]}" systemctl list-sockets --no-pager >&2 2>&1
    printf '  --- podman socket paths present on this host ---\n' >&2
    ls -la /run/podman/ "/run/user/$(id -u)/podman/" >&2 2>&1
    set -e

    exit 1
fi

# Existing is not the same as usable: podman.socket is created root:root 0660, so
# without the drop-in above it is present and unopenable, which surfaces later as
# a permission error naming nothing. Connecting to a unix socket needs write
# access, so that is what is checked.
if [[ ! -w "$socket_path" ]]; then
    printf 'podman-socket: %s exists but is not writable by %s.\n' "$socket_path" "$(id -un)" >&2
    printf '  The %s drop-in did not take effect.\n' "$dropin_file" >&2
    exit 1
fi

printf 'podman-socket: OK — the Docker API is now %s and Docker is stopped\n' "$socket_path"

#!/usr/bin/env python3
"""Assert the resolved Compose configuration holds this stack's isolation and pinning rules.

Everything here reads `docker compose config --format json` — the *resolved* model — and
never `compose.yaml` or `.env` as text. That is the point of the check: `env_file` values
take no part in Compose interpolation, so a port that reads as `127.0.0.1:5432:5432` in the
source file can still render bound to every interface. Only the rendered output says what
the runtime will actually do.

Three properties, one pass per profile combination:

* every published port binds to the configured bind address, never to `0.0.0.0` or to an
  empty host address (NFR-2);
* no two services publish the same host address and port (AD-17) — `config -q` is blind to
  this, so `up` half-starts and reports `port is already allocated` instead;
* every image resolves to an explicit tag that is not `latest` (NFR-4).

A combination that renders no services is a failure, not a pass: a check that walked an
empty set has verified nothing, which is the silent skip this repository keeps removing.

Written in Python for the same reason as `lint_json.py`: it runs identically on every
platform `pixi.toml` declares. Diagnostics go to stderr because they are human-readable
tool output, not application logging.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Host addresses that publish a port on every interface. Never acceptable here.
WILDCARD_ADDRESSES = ("", "0.0.0.0", "::", "[::]", "*")

#: The default `compose.yaml` interpolates for BIND_ADDRESS.
DEFAULT_BIND_ADDRESS = "127.0.0.1"


def compose_argv() -> list[str]:
    """Return the container-runtime command, honouring the same seam the shell scripts use.

    Returns:
        The command as an argument vector, defaulting to `docker compose`.
    """
    # `or`, not a default: an exported-but-empty DEVINFRA_COMPOSE must read as
    # unset, exactly as `${DEVINFRA_COMPOSE:-docker compose}` does in
    # scripts/lib/common.sh. With a plain default it would split to an empty argv
    # and the first real argument would be executed as the command.
    return shlex.split(os.environ.get("DEVINFRA_COMPOSE") or "docker compose")


def dotenv_value(name: str) -> str | None:
    """Read one variable from the repository's `.env`, without exporting anything.

    The environment wins over `.env`, matching `scripts/lib/common.sh`; this is only
    consulted when the caller's environment is silent.

    The reading must agree with what sourcing the file produces, or the check fails
    every port for a value the runtime never saw. A leading `export ` and a trailing
    unquoted `# comment` are both invisible to the shell, so they are stripped here
    too; a quoted value keeps everything inside its quotes.

    Args:
        name: Variable to look for.

    Returns:
        The declared value, or None when `.env` is absent or does not declare it.
    """
    path = REPO / ".env"
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) > 1 and value[0] in "'\"" and value[-1] == value[0]:
            return value[1:-1]
        # An unquoted value ends at the first whitespace-preceded `#`.
        return re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return None


def bind_address() -> str:
    """Resolve the address every published port is expected to bind to.

    Returns:
        The environment's BIND_ADDRESS, else `.env`'s, else the compose default.
    """
    return os.environ.get("BIND_ADDRESS") or dotenv_value("BIND_ADDRESS") or DEFAULT_BIND_ADDRESS


def run_compose(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the container runtime with COMPOSE_PROFILES cleared.

    Compose unions COMPOSE_PROFILES with `--profile`, so leaving the variable set would
    make every combination resolve to the same superset and the enumeration would prove
    nothing.

    Args:
        args: Arguments to append to the compose command.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    env = dict(os.environ)
    env["COMPOSE_PROFILES"] = ""
    return subprocess.run(
        [*compose_argv(), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
        env=env,
    )


def declared_profiles() -> list[str]:
    """List the profiles the model declares.

    Returns:
        One profile name per line of `config --profiles` output, in declaration order.

    Raises:
        RuntimeError: If the runtime could not enumerate the profiles.
    """
    result = run_compose(["config", "--profiles"])
    if result.returncode != 0:
        raise RuntimeError(f"could not read the declared profiles: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def combinations(profiles: list[str]) -> list[list[str]]:
    """Enumerate every subset of the declared profiles, smallest mask first.

    Args:
        profiles: Declared profile names.

    Returns:
        Every subset, starting with the empty one.
    """
    subsets: list[list[str]] = []
    for mask in range(1 << len(profiles)):
        subsets.append([name for index, name in enumerate(profiles) if mask & (1 << index)])
    return subsets


def label(profiles: list[str]) -> str:
    """Name a profile combination for a diagnostic.

    Args:
        profiles: The combination's profile names.

    Returns:
        A comma-joined name, or `(none)` for the empty combination.
    """
    return ",".join(profiles) if profiles else "(none)"


def render(profiles: list[str]) -> dict[str, object]:
    """Render one profile combination to its resolved JSON model.

    Args:
        profiles: The combination's profile names.

    Returns:
        The parsed `config --format json` document.

    Raises:
        RuntimeError: If the runtime failed or produced output that is not JSON.
    """
    args: list[str] = []
    for name in profiles:
        args += ["--profile", name]
    args += ["config", "--format", "json"]
    result = run_compose(args)
    if result.returncode != 0:
        raise RuntimeError(f"{label(profiles)}: compose config failed: {result.stderr.strip()}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label(profiles)}: compose config did not return JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"{label(profiles)}: compose config returned {type(document).__name__}, not an object")
    return document


def tag_problem(image: str) -> str | None:
    """Judge whether an image reference is pinned.

    Args:
        image: A fully resolved image reference.

    Returns:
        A description of the problem, or None when the reference is explicitly pinned.
    """
    if not image:
        return "no image"
    if "@" in image:
        # A digest is a stricter pin than a tag.
        return None
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return "no explicit tag"
    tag = last.rsplit(":", 1)[1]
    if not tag:
        return "empty tag"
    if tag == "latest":
        return "floating tag 'latest'"
    return None


def check(profiles: list[str], document: dict[str, object], expected_bind: str) -> list[str]:
    """Assert every rule against one rendered combination.

    Args:
        profiles: The combination's profile names.
        document: The parsed `config --format json` document.
        expected_bind: The address every published port must bind to.

    Returns:
        One diagnostic per violation, empty when the combination is clean.
    """
    where = label(profiles)
    problems: list[str] = []

    services = document.get("services")
    if not isinstance(services, dict) or not services:
        return [f"{where}: rendered no services — a pass over an empty set verifies nothing"]

    published: dict[tuple[str, str, str], str] = {}
    for name in sorted(services):
        service = services[name]
        if not isinstance(service, dict):
            problems.append(f"{where}: service '{name}' did not render as an object")
            continue

        image = service.get("image")
        problem = tag_problem(image if isinstance(image, str) else "")
        if problem is not None:
            problems.append(f"{where}: service '{name}' image '{image}': {problem}")

        ports = service.get("ports")
        if not isinstance(ports, list):
            continue
        for entry in ports:
            if not isinstance(entry, dict):
                continue
            host_port = entry.get("published")
            if host_port is None or host_port == "":
                # Not published on the host, so neither rule applies.
                continue
            host_port = str(host_port)
            protocol = str(entry.get("protocol") or "tcp")
            host_ip = str(entry.get("host_ip") or "")

            if host_ip in WILDCARD_ADDRESSES:
                problems.append(
                    f"{where}: service '{name}' publishes port {host_port}/{protocol} on "
                    f"'{host_ip or '(empty)'}' — every interface, not {expected_bind}"
                )
            elif host_ip != expected_bind:
                problems.append(
                    f"{where}: service '{name}' publishes port {host_port}/{protocol} on "
                    f"'{host_ip}', not the configured bind address {expected_bind}"
                )

            key = (host_ip, host_port, protocol)
            owner = published.get(key)
            if owner is not None:
                problems.append(
                    f"{where}: services '{owner}' and '{name}' both publish "
                    f"{host_ip}:{host_port}/{protocol} — the second would fail to start"
                )
            else:
                published[key] = name

    return problems


def main() -> int:
    """Assert every rule for every profile combination.

    Returns:
        Process exit status: 0 when every combination is clean, 1 otherwise.
    """
    expected_bind = bind_address()
    if expected_bind in WILDCARD_ADDRESSES:
        sys.stderr.write(
            f"assert-config: BIND_ADDRESS is '{expected_bind}', which publishes every port on "
            "every interface — the stack must stay off the LAN\n"
        )
        return 1

    try:
        subsets = combinations(declared_profiles())
    except RuntimeError as exc:
        sys.stderr.write(f"assert-config: {exc}\n")
        return 1

    problems: list[str] = []
    for profiles in subsets:
        try:
            document = render(profiles)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        found = check(profiles, document, expected_bind)
        if found:
            problems += found
        else:
            sys.stdout.write(f"assert-config: OK {label(profiles)}\n")

    if problems:
        for problem in problems:
            sys.stderr.write(f"assert-config: {problem}\n")
        return 1

    sys.stdout.write(f"assert-config: OK — {len(subsets)} profile combination(s), bind address {expected_bind}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

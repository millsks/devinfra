#!/usr/bin/env python3
"""Assert the resolved Compose configuration holds this stack's isolation and pinning rules.

Everything here reads `docker compose config --format json` — the *resolved* model — and
never `compose.yaml` or `.env` as text. That is the point of the check: `env_file` values
take no part in Compose interpolation, so a port that reads as `127.0.0.1:5432:5432` in the
source file can still render bound to every interface. Only the rendered output says what
the runtime will actually do.

Four properties, one pass per profile combination:

* every published port binds to the configured bind address, never to `0.0.0.0` or to an
  empty host address (NFR-2);
* no two services publish the same host address and port (AD-17) — `config -q` is blind to
  this, so `up` half-starts and reports `port is already allocated` instead;
* every image resolves to an explicit tag that is not `latest` (NFR-4);
* every service carries the logging options `common/base.yaml` declares — the inlined
  services reach them through the `x-logging` anchor and a module through `extends`, and a
  module that lost its `extends` block renders valid, passes `config -q` and silently ships
  with unbounded logs. Logging is the only one of the fragment's three keys asserted here:
  `restart` cannot be, because overriding it is sanctioned — a one-shot helper such as
  `minio-init` must set `restart: "no"` — so telling a legitimate override from a lost
  inheritance needs a way to declare the exception, which no story has settled yet.

One property is read from the module files' own text instead, because the rendered model
cannot express it: every `volumes:`, `networks:`, `configs:` and `secrets:` entry in a
`services/*/compose.yaml` names an identifier the root file already declares, and carries
nothing else (AD-5). A module key the root file does not set *wins* — `docker compose
config` reports the module's own `name:` or `driver:` and `config -q` accepts it — so a
module that added one, or that named an identifier of its own, would silently mount a
different Docker volume and orphan the real data with no error anywhere.

A combination that renders no services is a failure, not a pass, and so is a module scan
that found no module file: a check that walked an empty set has verified nothing, which is
the silent skip this repository keeps removing.

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

import yaml

REPO = Path(__file__).resolve().parent.parent

#: Host addresses that publish a port on every interface. Never acceptable here.
WILDCARD_ADDRESSES = ("", "0.0.0.0", "::", "[::]", "*")

#: The default `compose.yaml` interpolates for BIND_ADDRESS.
DEFAULT_BIND_ADDRESS = "127.0.0.1"

#: The top-level stanzas a module may declare, and may declare nothing but an identifier in.
#: `configs` and `secrets` merge exactly as `volumes` and `networks` do, so a module body
#: under either wins the same way even though nothing declares one today.
MODULE_STANZAS = ("volumes", "networks", "configs", "secrets")

#: The shared fragment every module service pulls in through `extends`.
BASE_FRAGMENT = REPO / "common" / "base.yaml"


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


def module_composes() -> list[Path]:
    """List the Compose fragments the modules contribute.

    Returns:
        Every `services/<name>/compose.yaml`, in directory order.
    """
    return sorted((REPO / "services").glob("*/compose.yaml"))


def read_model(path: Path) -> dict[str, object]:
    """Parse a compose file as text, failing loudly on anything that is not a model.

    Args:
        path: The file to read.

    Returns:
        The parsed mapping.

    Raises:
        RuntimeError: If the file cannot be read or does not parse as a compose model.
    """
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        # Named, not an interpreter traceback: the same contract lint_json.py and
        # assert_renovate.py hold, and the one the self-test asserts for both.
        raise RuntimeError(f"{path}: not valid UTF-8 at byte {exc.start}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"{path}: unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"{path}: does not parse as YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{path}: parsed as {type(parsed).__name__}, not a compose model")
    return parsed


def root_identifiers(stanza: str) -> set[str]:
    """List the identifiers the root file declares in one top-level stanza.

    Args:
        stanza: The stanza name, one of `MODULE_STANZAS`.

    Returns:
        Every identifier the root `compose.yaml` declares there.

    Raises:
        RuntimeError: If the root file cannot be read or does not parse as a compose model.
    """
    declared = read_model(REPO / "compose.yaml").get(stanza)
    return set(declared) if isinstance(declared, dict) else set()


def shared_logging() -> dict[str, object]:
    """Read the logging options every service inherits from the shared fragment.

    Returns:
        The `logging` mapping `common/base.yaml` declares on its `defaults` service.

    Raises:
        RuntimeError: If the fragment is unreadable, unparseable, or declares no logging.
    """
    defaults = read_model(BASE_FRAGMENT).get("services")
    defaults = defaults.get("defaults") if isinstance(defaults, dict) else None
    logging = defaults.get("logging") if isinstance(defaults, dict) else None
    if not isinstance(logging, dict) or not logging:
        raise RuntimeError(f"{BASE_FRAGMENT}: the 'defaults' service declares no logging options")
    return logging


def identifier_only(paths: list[Path]) -> list[str]:
    """Assert every module stanza names its resource and declares nothing about it.

    A renamed or re-driven named volume is a *new* volume: the old one is orphaned and
    the service starts empty, with no error anywhere (AD-5). The rendered model cannot
    catch that, because the root file wins only on keys it sets explicitly and every key
    it omits is last-include-wins — a module's own `name:` or `driver_opts:` renders
    straight through and `config -q` accepts it. So the rule is asserted where it is
    stated: in the module's own text.

    Args:
        paths: The module compose files to read.

    Returns:
        One diagnostic per entry carrying anything but its identifier.

    Raises:
        RuntimeError: If a file cannot be read or does not parse as a compose model.
    """
    problems: list[str] = []
    known = {stanza: root_identifiers(stanza) for stanza in MODULE_STANZAS}
    for path in paths:
        where = path.relative_to(REPO).as_posix()
        parsed = read_model(path)
        for stanza in MODULE_STANZAS:
            declared = parsed.get(stanza)
            if declared is None:
                continue
            if not isinstance(declared, dict):
                problems.append(
                    f"{where}: '{stanza}:' parsed as {type(declared).__name__}, not a mapping of identifiers"
                )
                continue
            for name, body in declared.items():
                if str(name) not in known[stanza]:
                    problems.append(
                        f"{where}: {stanza}.{name} is not declared in the root compose.yaml — "
                        f"an identifier only this module names is a *new* resource, so the one the "
                        f"root declares is orphaned and the service starts empty (AD-5). Declare it "
                        f"in the root {stanza}: list, or fix the spelling"
                    )
                if body is None or body == {}:
                    continue
                keys = sorted(str(key) for key in body) if isinstance(body, dict) else [repr(body)]
                problems.append(
                    f"{where}: {stanza}.{name} declares {keys} — a module names the identifier and "
                    f"nothing else. Driver keys belong in the root compose.yaml; a key the root does "
                    f"not set wins from here, silently repointing the resource (AD-5)"
                )
    return problems


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


def check(
    profiles: list[str], document: dict[str, object], expected_bind: str, logging: dict[str, object]
) -> list[str]:
    """Assert every rule against one rendered combination.

    Args:
        profiles: The combination's profile names.
        document: The parsed `config --format json` document.
        expected_bind: The address every published port must bind to.
        logging: The logging options every service must carry, from the shared fragment.

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

        # An inlined service reads the `x-logging` anchor; a module reads the same three
        # keys out of common/base.yaml through `extends`. Drop the `extends` block from a
        # module and nothing else notices: `config -q` still passes, the ports and the
        # image are unchanged, and the service quietly ships with unbounded logs. The
        # rendered model is where the two paths converge, so it is where they are
        # compared. Only `logging` is compared: `restart` has a sanctioned override
        # (`minio-init` sets `restart: "no"`), so an equality check would reject it.
        if service.get("logging") != logging:
            problems.append(
                f"{where}: service '{name}' logging is {service.get('logging')!r}, not the "
                f"{logging!r} every service inherits — an inlined service takes it from the "
                f"x-logging anchor and a module from common/base.yaml through extends, so this "
                f"one reaches neither"
            )

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

    modules = module_composes()
    if not modules:
        sys.stderr.write(
            "assert-config: found no services/*/compose.yaml — a pass over an empty module set "
            "verifies nothing about the identifier-only rule\n"
        )
        return 1

    try:
        problems: list[str] = identifier_only(modules)
    except RuntimeError as exc:
        sys.stderr.write(f"assert-config: {exc}\n")
        return 1
    # Held, not written as they are earned. stdout and stderr interleave by buffering, so
    # a run that found a data-loss violation would otherwise end on a run of `OK` lines
    # and read as a pass to anyone who trusts the output over the exit status.
    passed: list[str] = [f"OK {len(modules)} module file(s) declare identifiers only"]

    try:
        logging = shared_logging()
        subsets = combinations(declared_profiles())
    except RuntimeError as exc:
        # Whatever the module scan already found is a finding in its own right: losing it
        # behind an unrelated runtime failure is how a data-loss diagnostic goes unread.
        problems.append(str(exc))
        for problem in problems:
            sys.stderr.write(f"assert-config: {problem}\n")
        return 1

    for profiles in subsets:
        try:
            document = render(profiles)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        found = check(profiles, document, expected_bind, logging)
        if found:
            problems += found
        else:
            passed.append(f"OK {label(profiles)}")

    if problems:
        for problem in problems:
            sys.stderr.write(f"assert-config: {problem}\n")
        return 1

    for line in passed:
        sys.stdout.write(f"assert-config: {line}\n")
    sys.stdout.write(f"assert-config: OK — {len(subsets)} profile combination(s), bind address {expected_bind}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

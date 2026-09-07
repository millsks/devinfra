#!/usr/bin/env python3
"""Assert the resolved Compose configuration holds this stack's isolation and pinning rules.

Everything here reads `docker compose config --format json` — the *resolved* model — and
never `compose.yaml` or `.env` as text. That is the point of the check: `env_file` values
take no part in Compose interpolation, so a port that reads as `127.0.0.1:5432:5432` in the
source file can still render bound to every interface. Only the rendered output says what
the runtime will actually do.

Five properties, one pass per profile combination:

* every published port binds to the configured bind address, never to `0.0.0.0` or to an
  empty host address (NFR-2);
* no two services publish the same host address and port (AD-17) — `config -q` is blind to
  this, so `up` half-starts and reports `port is already allocated` instead;
* every image resolves to an explicit tag that is not `latest` (NFR-4);
* every service carries the logging options `common/base.yaml` declares — every service is
  a module service and reaches them through `extends`, and a module that lost its `extends`
  block renders valid, passes `config -q` and silently ships with unbounded logs. Logging
  is the only one of the fragment's three keys asserted here:
  `restart` cannot be, because overriding it is sanctioned — a one-shot helper such as
  `minio-init` must set `restart: "no"` — so telling a legitimate override from a lost
  inheritance needs a way to declare the exception, which no story has settled yet;
* every bind mount's rendered `source` exists on disk and is not an empty directory. A
  module writes its bind sources against its own directory, and a mistyped one is silent
  in the worst way: Docker creates the missing path as an empty *directory* and starts the
  container, so the service runs with its configuration absent rather than failing. pgAdmin
  is the case that motivated this — a wrong `./conf/servers.json` leaves the server
  registration missing while `/misc/ping` still returns 200 and the smoke suite passes.
  Empty directories are rejected too, because Docker's own repair for a missing source is
  what an existence-only check would then accept forever.

Three properties are read from the module files' own text instead, because the rendered model
cannot express them.

* Every `volumes:`, `networks:`, `configs:` and `secrets:` entry in a `services/*/compose.yaml`
  names an identifier the root file already declares, and carries nothing else (AD-5). A module
  key the root file does not set *wins* — `docker compose config` reports the module's own
  `name:` or `driver:` and `config -q` accepts it — so a module that added one, or that named an
  identifier of its own, would silently mount a different Docker volume and orphan the real data
  with no error anywhere.
* No module names a top-level resource the root file declares *with keys*. Compose v2 refuses
  that whole model — `networks.devinfra conflicts with imported resource`, exit 15 — while
  Compose v5 resolves it happily. The rule is asserted statically here precisely because the
  rendered model cannot state it: whether the defect is visible at all depends on which Compose
  the developer has installed, so `lint-compose` passes on one machine and every CI job fails.
  See the 2026-09-07 amendment in docs/adr/0004-volume-names-are-frozen.md.
* Every Module carries its own contract, and it is checked in both directions (ADR 0012). A
  Module owns the service named for its directory plus helpers named `<dir>-<role>`, and no
  module file may declare a service outside that shape — with the root pinned to declare no
  `services:` key of its own, that makes a Compose service with no owning Module impossible.
  The Module's primary service declares a `healthcheck:` that is not `disable: true` or
  `test: NONE` — Compose's own ways of cancelling a probe — or the directory holds a justified
  `healthcheck.none`; the directory holds a `smoke.sh` and a `gotchas.md`; it holds a `seed/`
  directory or a justified `seed.none`; it declares a non-empty top-level `x-endpoints:` whose
  keys and the `*_PORT` variables it publishes are the *same set*, in both directions; and
  every `x-requires:` entry names an existing provider Module, only endpoints that provider
  declares, and a provider some service the Module owns already lists in `depends_on`. The
  check is presence-based throughout: it asks whether the five things exist, never whether
  their content was warranted — but a marker with no justification is the silent skip in file
  form, so an empty one is refused, as is a probe that is declared only to be turned off.

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

#: The files every Module carries beside its compose.yaml, unconditionally.
MODULE_FILES = ("smoke.sh", "gotchas.md")

#: A published host port comes from a `SOMETHING_PORT` interpolation, which is what ties an
#: `x-endpoints:` key to the port it names. Both Compose spellings are matched: `${NAME}` and
#: the brace-less `$NAME`, which is equally legal and would otherwise slip the whole endpoint
#: reconciliation. The container-side port is a literal and BIND_ADDRESS does not end in
#: _PORT, so neither is matched.
PORT_VARIABLE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*_PORT)\b")


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


def root_declarations(stanza: str) -> dict[str, object]:
    """Read what the root file declares in one top-level stanza, bodies included.

    The bodies matter as much as the names: a module may redeclare a root resource only
    when the root's own declaration is bare, so the check cannot work from names alone.

    Args:
        stanza: The stanza name, one of `MODULE_STANZAS`.

    Returns:
        Every identifier the root `compose.yaml` declares there, mapped to its body.

    Raises:
        RuntimeError: If the root file cannot be read or does not parse as a compose model.
    """
    declared = read_model(REPO / "compose.yaml").get(stanza)
    return dict(declared) if isinstance(declared, dict) else {}


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
    """Assert every module stanza is one the root sanctions, and says nothing about it.

    Two rules, both read from the module's own text because the rendered model cannot
    state either one.

    A renamed or re-driven named volume is a *new* volume: the old one is orphaned and
    the service starts empty, with no error anywhere (AD-5). The rendered model cannot
    catch that, because the root file wins only on keys it sets explicitly and every key
    it omits is last-include-wins — a module's own `name:` or `driver_opts:` renders
    straight through and `config -q` accepts it. So the rule is asserted where it is
    stated: in the module's own text.

    The second rule is Compose's, not this repository's: across `include`, a module may
    name a top-level resource only when the root's declaration of it is bare. Name one the
    root declares with keys and Compose v2 rejects the entire model — `networks.devinfra
    conflicts with imported resource`, exit 15 — where Compose v5 resolves it without
    complaint. That version split is the whole reason this is a static check: `lint-compose`
    renders with whatever Compose the developer happens to have, so the defect can pass
    every local gate and fail every CI job. Redeclaring is never *needed* — a module service
    reaches the resource through the root either way — so the rule costs nothing to hold.

    Args:
        paths: The module compose files to read.

    Returns:
        One diagnostic per entry the root does not sanction, carries a body, or may not
        be named here at all.

    Raises:
        RuntimeError: If a file cannot be read or does not parse as a compose model.
    """
    problems: list[str] = []
    known = {stanza: root_declarations(stanza) for stanza in MODULE_STANZAS}
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
                root_body = known[stanza].get(str(name))
                if str(name) not in known[stanza]:
                    problems.append(
                        f"{where}: {stanza}.{name} is not declared in the root compose.yaml — "
                        f"an identifier only this module names is a *new* resource, so the one the "
                        f"root declares is orphaned and the service starts empty (AD-5). Declare it "
                        f"in the root {stanza}: list, or fix the spelling"
                    )
                elif root_body:
                    # Bare in the root means None or {}; anything else is a keyed declaration,
                    # and Compose v2 refuses to import a module that names it at all. Reported
                    # instead of the body rule below, not as well as it: this entry must go,
                    # so telling its author to strip it to an identifier would be wrong advice.
                    root_keys = (
                        sorted(str(key) for key in root_body) if isinstance(root_body, dict) else [repr(root_body)]
                    )
                    problems.append(
                        f"{where}: {stanza}.{name} is redeclared here, but the root compose.yaml "
                        f"declares it with {root_keys} — Compose v2 rejects the whole model with "
                        f"'{stanza}.{name} conflicts with imported resource' (exit 15), while newer "
                        f"Compose resolves it, so this passes locally and fails every CI job. A "
                        f"module may name a top-level resource only when the root's declaration is "
                        f"bare. Delete the entry: the service reaches the {stanza[:-1]} through the "
                        f"root regardless"
                    )
                    continue
                if body is None or body == {}:
                    continue
                keys = sorted(str(key) for key in body) if isinstance(body, dict) else [repr(body)]
                problems.append(
                    f"{where}: {stanza}.{name} declares {keys} — a module names the identifier and "
                    f"nothing else. Driver keys belong in the root compose.yaml; a key the root does "
                    f"not set wins from here, silently repointing the resource (AD-5)"
                )
    return problems


def justified(path: Path) -> bool:
    """Judge whether a marker file carries a justification rather than only a heading.

    A marker is how a Module declares that one leg of the contract does not apply to it.
    An empty one is the silent skip in file form — it satisfies the check while saying
    nothing — so a blank or comment-only marker does not count as present.

    Args:
        path: The marker file to read.

    Returns:
        True when the file holds at least one line that is neither blank nor a comment.

    Raises:
        RuntimeError: If the marker cannot be read as UTF-8 text.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{path}: not valid UTF-8 at byte {exc.start}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"{path}: unreadable: {exc}") from exc
    return any(line.strip() and not line.strip().startswith("#") for line in text.splitlines())


def healthcheck_declared(service: dict[str, object]) -> bool:
    """Judge whether a service actually carries a probe, rather than a stanza that cancels one.

    `healthcheck: {disable: true}` and `test: NONE` are Compose's own ways of turning a probe
    *off*, and both are truthy mappings: a presence check reading the key alone would accept a
    Module that ships no probe at all and no `healthcheck.none` either, which is precisely the
    silent skip this leg exists to remove.

    A stanza with no `test:` is accepted: it is tuning an inherited image `HEALTHCHECK`, which
    is a real probe.

    Args:
        service: One service body from a module file.

    Returns:
        True when the service declares a probe that will actually run.
    """
    declared = service.get("healthcheck")
    if not isinstance(declared, dict) or not declared:
        return False
    if declared.get("disable"):
        return False
    test = declared.get("test")
    if isinstance(test, str):
        return test.strip().upper() != "NONE"
    if isinstance(test, list):
        return [str(item).strip().upper() for item in test] != ["NONE"]
    return True


def published_ports(services: dict[str, object]) -> tuple[set[str], list[str]]:
    """Read what a module file publishes on the host, and how each entry names it.

    Read from the module's own text rather than the rendered model, because the rendered
    model has already substituted the values away — and the variable name is exactly what
    an `x-endpoints:` key has to agree with.

    Args:
        services: The module file's `services:` mapping.

    Returns:
        The `*_PORT` variables the file interpolates into a `ports:` entry, and the entries
        that name no variable at all — a literal host port cannot be reconciled against
        anything, so it is reported rather than silently contributing nothing.
    """
    found: set[str] = set()
    literal: list[str] = []
    for body in services.values():
        if not isinstance(body, dict):
            continue
        ports = body.get("ports")
        if not isinstance(ports, list):
            continue
        for entry in ports:
            text = (
                entry if isinstance(entry, str) else str(entry.get("published", "")) if isinstance(entry, dict) else ""
            )
            names = PORT_VARIABLE.findall(text)
            if names:
                found.update(names)
            elif str(text).strip():
                literal.append(str(text))
    return found, literal


def depends_on_names(service: dict[str, object]) -> set[str]:
    """Read the services one service declares a dependency edge on.

    Both Compose forms are accepted, because both are in use here: the list form waits for
    start, the mapping form waits for a condition, and `x-requires:` is about the edge
    existing rather than about which form expresses it.

    Args:
        service: One rendered-or-source service body.

    Returns:
        The service names named in `depends_on`, empty when there is none.
    """
    declared = service.get("depends_on")
    if isinstance(declared, dict):
        return {str(name) for name in declared}
    if isinstance(declared, list):
        return {str(name) for name in declared}
    return set()


def module_contract(paths: list[Path]) -> list[str]:
    """Assert every Module carries its own contract, and that no service escapes one.

    Five things per Module, and the check is presence-based: it asks whether each exists,
    never whether its content was warranted. What makes that worth having is the second
    direction. A Compose service can be declared in a module file or in the root, the root
    is pinned to declare no `services:` key at all, and every service key here must be
    `<dir>` or `<dir>-<role>` — so a service that no Module directory owns cannot exist.
    Asserting that against the *rendered* model instead would be weaker and would break a
    dozen self-test fixtures, which name services no directory owns on purpose.

    `x-requires:` is reconciled against the provider's own `x-endpoints:` keys rather than
    against anything this checker knows about databases or brokers, which is what keeps the
    rule generic. The matching `depends_on` is required alongside it because a dependency
    not expressed as `depends_on` does not exist (ADR 0002) — without that, the declaration
    would be free to drift away from the runtime edge it describes.

    Args:
        paths: The module compose files to read.

    Returns:
        One diagnostic per contract leg a Module fails to hold.

    Raises:
        RuntimeError: If a file cannot be read or does not parse as a compose model.
    """
    problems: list[str] = []
    parsed_by_module: dict[str, dict[str, object]] = {}
    endpoints_by_module: dict[str, list[str]] = {}
    for path in paths:
        parsed = read_model(path)
        module = path.parent.name
        parsed_by_module[module] = parsed
        block = parsed.get("x-endpoints")
        endpoints_by_module[module] = [str(name) for name in block] if isinstance(block, dict) else []

    for path in paths:
        module = path.parent.name
        directory = path.parent
        where = path.relative_to(REPO).as_posix()
        parsed = parsed_by_module[module]

        services = parsed.get("services")
        if not isinstance(services, dict) or not services:
            problems.append(
                f"{where}: module '{module}' declares no services — a module directory whose file "
                f"contributes nothing is a directory the catalog cannot admit (ADR 0007)"
            )
            continue

        # Both directions of the ownership rule, in one pass.
        for name in services:
            role = str(name)[len(module) + 1 :] if str(name).startswith(f"{module}-") else ""
            if str(name) == module or role:
                continue
            problems.append(
                f"{where}: declares the service '{name}', which module '{module}' does not own — a "
                f"module file may declare '{module}' and helpers named '{module}-<role>' and nothing "
                f"else. The root compose.yaml declares no services: key, so this is the only place a "
                f"service can come from, and a service no module directory owns must be impossible"
            )
        primary = services.get(module)
        if not isinstance(primary, dict):
            problems.append(
                f"{where}: module '{module}' declares no primary service named '{module}' — the "
                f"directory maps to no service, so nothing owns its healthcheck, its smoke checks or "
                f"its endpoints"
            )
            primary = {}

        # 1. A healthcheck on the primary, or a justified exemption. A one-shot helper has
        #    nothing to keep healthy, so the leg is asserted on the primary only.
        marker = directory / "healthcheck.none"
        if not healthcheck_declared(primary) and not (marker.is_file() and justified(marker)):
            problems.append(
                f"{where}: module '{module}' declares no healthcheck: on its primary service and "
                f"carries no justified services/{module}/healthcheck.none — without one "
                f"wait-healthy.sh can see only that the container is running, which is the silent "
                f"skip this repository keeps removing. A marker with no justification does not "
                f"count, and neither does a stanza that disables the probe"
            )

        # 2. and 3. The two files every Module carries, unconditionally.
        for filename in MODULE_FILES:
            if not (directory / filename).is_file():
                problems.append(
                    f"{where}: module '{module}' is missing services/{module}/{filename} — every "
                    f"Module proves it works and records what bites; neither is optional"
                )

        # 4. Seed data, or a justified statement that there is none to load.
        seed_marker = directory / "seed.none"
        if not (directory / "seed").is_dir() and not (seed_marker.is_file() and justified(seed_marker)):
            problems.append(
                f"{where}: module '{module}' has neither a services/{module}/seed/ directory nor a "
                f"justified services/{module}/seed.none — say what is loaded at first boot, or say "
                f"why nothing is. A marker with no justification does not count"
            )

        # 5. Endpoints, reconciled against the ports in both directions. This is the drift
        #    scripts/urls.sh has already accumulated, stated where it can be checked — and a
        #    one-way check would only relocate it: a port deleted from ports: would leave its
        #    endpoint declared forever, which is the same lie in the newer file.
        variables, literal_ports = published_ports(services)
        for entry in literal_ports:
            problems.append(
                f"{where}: module '{module}' publishes '{entry}', which interpolates no *_PORT "
                f"variable — a literal host port cannot be named by an x-endpoints: entry, so it is "
                f"a published port no declaration can ever be reconciled against"
            )
        endpoints = parsed.get("x-endpoints")
        if not isinstance(endpoints, dict) or not endpoints:
            problems.append(
                f"{where}: module '{module}' declares no non-empty top-level x-endpoints: — a Module "
                f"says what it publishes, or a developer has to read the ports: list to find it"
            )
        else:
            for variable in sorted(variables):
                if variable not in endpoints_by_module[module]:
                    problems.append(
                        f"{where}: module '{module}' publishes ${{{variable}}} but declares no "
                        f"x-endpoints: entry named {variable} — an endpoint that only the ports: list "
                        f"knows about is the drift this block exists to end"
                    )
            for name in sorted(endpoints_by_module[module]):
                if name not in variables:
                    problems.append(
                        f"{where}: module '{module}' declares the x-endpoints: entry {name} but "
                        f"publishes no port that names it — a declaration outliving the port it "
                        f"describes is the same drift, one file later"
                    )

        # 6. Requirements, reconciled against the provider's own declaration and its edge.
        requires = parsed.get("x-requires")
        if requires is None:
            continue
        if not isinstance(requires, dict):
            problems.append(
                f"{where}: module '{module}' declares x-requires: as "
                f"{type(requires).__name__}, not a mapping of provider module to endpoint names"
            )
            continue
        edges: set[str] = set()
        for body in services.values():
            if isinstance(body, dict):
                edges |= depends_on_names(body)
        for provider, wanted in requires.items():
            provider = str(provider)
            if provider not in parsed_by_module:
                problems.append(
                    f"{where}: module '{module}' requires '{provider}', which is not a Module — there "
                    f"is no services/{provider}/compose.yaml for it to be satisfied by"
                )
                continue
            names = [str(name) for name in wanted] if isinstance(wanted, list) else [str(wanted)]
            for name in names:
                if name not in endpoints_by_module[provider]:
                    problems.append(
                        f"{where}: module '{module}' requires endpoint {name} from '{provider}', which "
                        f"'{provider}' does not publish — its x-endpoints: declares "
                        f"{sorted(endpoints_by_module[provider])}"
                    )
            # The edge may be held by any service this Module owns — a helper is often the
            # one that actually talks to the provider — and it may name the provider's own
            # helper rather than its primary, which is still a genuine edge to that Module.
            if provider not in edges and not any(edge.startswith(f"{provider}-") for edge in edges):
                problems.append(
                    f"{where}: module '{module}' requires '{provider}' but no service it owns "
                    f"declares depends_on '{provider}' — a dependency not expressed as depends_on "
                    f"does not exist (ADR 0002), so the runtime would start them in either order"
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

        # Every service reads these three keys out of common/base.yaml through `extends`.
        # Drop the `extends` block from a module and nothing else notices: `config -q`
        # still passes, the ports and the image are unchanged, and the service quietly
        # ships with unbounded logs. The rendered model is the only place the inheritance
        # is visible, so it is where it is checked. Only `logging` is compared: `restart`
        # has a sanctioned override (`minio-init` sets `restart: "no"`), so an equality
        # check would reject it.
        if service.get("logging") != logging:
            problems.append(
                f"{where}: service '{name}' logging is {service.get('logging')!r}, not the "
                f"{logging!r} every service inherits from common/base.yaml through extends, "
                f"so this one reaches it through neither an extends block nor an override"
            )

        # A bind source that does not exist is not an error to Docker: it creates the
        # path as an empty directory and starts the container, so the service runs with
        # its configuration simply absent. Nothing else catches it — `config -q` renders
        # the path without touching the filesystem, and a service reading no config
        # usually still starts and still answers a health probe. The rendered model is
        # read rather than the module text because only the rendered value has been
        # resolved against the module's own directory, which is the step that goes wrong.
        for mount in service.get("volumes") or []:
            if not isinstance(mount, dict) or mount.get("type") != "bind":
                continue
            source = mount.get("source")
            if not isinstance(source, str) or not source:
                problems.append(f"{where}: service '{name}' has a bind mount with no source: {mount!r}")
            elif not Path(source).exists():
                problems.append(
                    f"{where}: service '{name}' bind-mounts '{source}' at "
                    f"'{mount.get('target')}', and that path does not exist — Docker would "
                    f"create it as an empty directory and start the service with its "
                    f"configuration missing rather than failing"
                )
            elif Path(source).is_dir() and not any(Path(source).iterdir()):
                # Existence alone stops being evidence the moment the stack has been
                # started once: Docker's repair for a missing source *is* an empty
                # directory, so the mistyped path now exists and `exists()` says yes
                # forever while the configuration is still absent. An empty directory
                # is therefore rejected in its own right. No source in this repository
                # is legitimately empty — the one directory that could be,
                # services/grafana/dashboards/, carries .gitkeep, which is also how git
                # tracks it at all.
                problems.append(
                    f"{where}: service '{name}' bind-mounts '{source}' at "
                    f"'{mount.get('target')}', and that path is an empty directory — "
                    f"either the source is mistyped and Docker already created it, or the "
                    f"directory needs a .gitkeep, as services/grafana/dashboards/ has"
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
        problems += module_contract(modules)
    except RuntimeError as exc:
        sys.stderr.write(f"assert-config: {exc}\n")
        return 1
    # Held, not written as they are earned. stdout and stderr interleave by buffering, so
    # a run that found a data-loss violation would otherwise end on a run of `OK` lines
    # and read as a pass to anyone who trusts the output over the exit status.
    passed: list[str] = [
        f"OK {len(modules)} module file(s) declare identifiers only, and redeclare no keyed root resource",
        f"OK {len(modules)} module file(s) carry the Module contract, and declare no service they do not own",
    ]

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

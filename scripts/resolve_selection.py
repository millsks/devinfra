#!/usr/bin/env python3
"""Expand a requested Selection into the Modules it needs, before Compose sees it.

This is the closure half of `scripts/select.sh` (AD-16). A Selection is a set of names a
developer asks for — Module names such as `keycloak`, or Bundle names such as `admin` — and
what Compose has to be given instead is **every Module in the transitive `depends_on`
closure of that request**. Selecting `keycloak` alone cannot work otherwise: Postgres does
not carry the `keycloak` profile, and it cannot be made to without Keycloak editing
Postgres's file, which AD-15 forbids.

Three properties define the output.

* **It is always a set of Module names, never a Bundle name.** Emitting `admin` would
  re-enter Compose's own profile semantics and select the three admin services without the
  Postgres and Redis they talk to — the exact failure AD-16 exists to prevent. Emitting the
  closure as Module names makes "exactly two containers" a property of the string, checkable
  without starting anything.
* **The closure is computed from the Module files, never hand-maintained** (AD-6, AD-16).
  A service's `depends_on` targets are mapped to the Module that declares them, which by
  AD-8 is the directory named `<dir>` for a service `<dir>` or `<dir>-<role>`. Both Compose
  spellings of `depends_on` are read: the mapping form with conditions and the bare list
  form.
* **It fails loudly, and prints nothing on stdout when it does** (AD-18, NFR-5). Three
  refusals, each exit 1 with a diagnostic on stderr: an empty request, a name no Module and
  no profile answers to, and a `depends_on` edge no Module owns. A resolver that silently
  dropped an edge would start a stack missing the service the request was about. There is
  no fourth "the Selection resolved to nothing" refusal, and there is deliberately no
  unreachable branch standing in for one: every accepted name maps to at least one Module
  by construction, so a non-empty request cannot resolve to an empty set. What AD-18 calls
  the empty Selection is the empty *request*, which is the first refusal.

Resolving an already-resolved Selection returns the same set, which is what lets a resolved
value survive being re-read from the environment by a script that re-sources `.env`.

Written in Python behind a shell entry point because the closure reads YAML — two
`depends_on` forms, service-to-Module ownership, a profile index — which bash parses badly
and which `mypy --strict` and the self-test can hold to account here. `scripts/select.sh` is
the entry point AD-16 names; this module is what it runs. It is deliberately not named
`select.py`: that shadows the stdlib `select` module `subprocess` imports.

The graph primitives `module_composes()`, `read_model()` and `depends_on_names()` live here
rather than in `scripts/assert_config.py` because the import arrow can only point one way and
`assert_config.py` needs this module's Selection list — the enumeration that replaced its
profile power set. One closure implementation, read by both.

Diagnostics go to stderr because they are human-readable tool output, not application logging.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - driven by lint_selftest.py in a subprocess
    # Named, in the same shape every other refusal here uses, rather than an interpreter
    # traceback. `scripts/select.sh` runs whatever `python3` is on PATH, and every
    # lifecycle script — `./scripts/ps.sh`, `./scripts/psql.sh`, `./scripts/up-core.sh` —
    # now resolves through it, so a bare `python3` without PyYAML would otherwise kill
    # scripts that never needed Python before.
    sys.stderr.write(
        f"select: PyYAML is not available to this interpreter ({sys.executable}), so the\n"
        f"  Module graph in services/*/compose.yaml cannot be read.\n"
        f"  Run this through pixi, which supplies the pinned PyYAML: `pixi run select <names>`,\n"
        f"  or any `pixi run` task. To point the resolver at another interpreter, set\n"
        f"  DEVINFRA_PYTHON to one that has PyYAML installed.\n"
        f"  ({exc})\n"
    )
    raise SystemExit(1) from exc

REPO = Path(__file__).resolve().parent.parent

#: The request that names every Module, for the lifecycle operations that must act on the
#: whole stack — `down`, `stop`, `pull`, `dump-logs`, `config`, `destroy`. Spelled as a flag
#: so it can never collide with a Module or Bundle name.
ALL_MODULES_REQUEST = "--all"

#: The flag that prints the Selections this repository validates, one request per line, for
#: the two enumerations that used to walk the profile power set.
SELECTIONS_REQUEST = "--selections"

#: The line a checkout whose `.env` predates Selection is told to add. Three Bundle names
#: that between them cover every Module, so pasting it starts exactly what a bare
#: `docker compose up` started before Selection existed (AD-18).
#:
#: A literal, and deliberately not read out of `.env.example` or the root `compose.yaml`:
#: this is the message a reader sees *because* their environment is not yet in a state this
#: resolver can trust, and reaching for another file to compose the advice would be a second
#: way for it to fail. `lint_selftest.py` pins it against `.env.example` instead, so the two
#: cannot drift without a named failure.
LEGACY_UPGRADE_SELECTION = "core,admin,observability"


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


def depends_on_names(service: dict[str, object]) -> set[str]:
    """Read the services one service declares a dependency edge on.

    Both Compose forms are accepted, because both are in use here: the list form waits for
    start, the mapping form waits for a condition, and the closure is about the edge
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


def service_profiles(service: dict[str, object]) -> list[str]:
    """Read the profiles one service declares.

    Args:
        service: One source service body.

    Returns:
        The profile names, in declaration order, empty when there is no `profiles:` key.
    """
    declared = service.get("profiles")
    return [str(name) for name in declared] if isinstance(declared, list) else []


@dataclass
class Graph:
    """The Module dependency graph, read from the module files.

    Attributes:
        modules: Every Module directory name, sorted.
        owners: Service name to the Module that declares it.
        profiles: Profile name to the Modules whose services declare it, sorted.
        edges: Module to the Modules it depends on, sorted.
    """

    modules: tuple[str, ...]
    owners: dict[str, str]
    profiles: dict[str, tuple[str, ...]]
    edges: dict[str, tuple[str, ...]]

    def names(self) -> tuple[str, ...]:
        """List every name a request may use.

        Returns:
            Every Module name and every profile any Module service declares, sorted.
        """
        return tuple(sorted(set(self.modules) | set(self.profiles)))


@dataclass(frozen=True)
class Selection:
    """One request and the Modules it resolves to.

    Attributes:
        request: The name asked for, or `--all` for the whole stack.
        modules: The resolved closure, sorted.
    """

    request: str
    modules: tuple[str, ...]

    def value(self) -> str:
        """Render the resolved Selection as a `COMPOSE_PROFILES` value.

        Returns:
            The Module names, sorted and comma-joined.
        """
        return ",".join(self.modules)


def build_graph(paths: list[Path]) -> Graph:
    """Read the Module dependency graph out of the module files.

    Args:
        paths: The module compose files to read.

    Returns:
        The graph: every Module, who owns which service, which Modules carry which profile,
        and which Modules depend on which.

    Raises:
        RuntimeError: If there are no module files, if one does not parse, or if a
            `depends_on` edge names a service no Module owns.
    """
    if not paths:
        raise RuntimeError(
            "found no services/*/compose.yaml — a closure computed over an empty catalog "
            "would resolve every request to nothing and start no stack at all"
        )

    modules: list[str] = []
    owners: dict[str, str] = {}
    bodies: dict[str, dict[str, dict[str, object]]] = {}
    profiles: dict[str, set[str]] = {}

    for path in paths:
        module = path.parent.name
        modules.append(module)
        # A Module is requestable by its own name whether or not its services spell that
        # name out in `profiles:`. The contract leg in assert_config.py is what makes the
        # two agree; seeding the index here keeps the request resolvable — and therefore
        # renderable, and therefore reportable — while that check names the defect.
        profiles.setdefault(module, set()).add(module)
        declared = read_model(path).get("services")
        services: dict[str, dict[str, object]] = {}
        if isinstance(declared, dict):
            services = {str(name): body for name, body in declared.items() if isinstance(body, dict)}
        bodies[module] = services
        for name, body in services.items():
            owners[name] = module
            for profile in service_profiles(body):
                profiles.setdefault(profile, set()).add(module)

    edges: dict[str, set[str]] = {module: set() for module in modules}
    for module in modules:
        for name, body in bodies[module].items():
            for target in sorted(depends_on_names(body)):
                owner = owners.get(target)
                if owner is None:
                    raise RuntimeError(
                        f"services/{module}/compose.yaml: service '{name}' declares "
                        f"depends_on '{target}', which no Module owns — the closure cannot "
                        f"include it, and silently dropping the edge would start a stack "
                        f"missing the service that edge exists for"
                    )
                if owner != module:
                    edges[module].add(owner)

    return Graph(
        modules=tuple(sorted(modules)),
        owners=owners,
        profiles={name: tuple(sorted(owning)) for name, owning in profiles.items()},
        edges={module: tuple(sorted(targets)) for module, targets in edges.items()},
    )


def parse_request(values: list[str]) -> list[str]:
    """Split requested names out of the arguments or the environment value.

    One argument may carry a whole `COMPOSE_PROFILES` value, so commas separate names just
    as whitespace does. That is what makes `select.sh "$(select.sh keycloak)"` idempotent.

    Args:
        values: Raw argument strings.

    Returns:
        The requested names, in the order given, with duplicates preserved.
    """
    return [part for value in values for part in value.replace(",", " ").split()]


def closure(graph: Graph, request: list[str]) -> tuple[str, ...]:
    """Expand a request into the Modules it transitively needs.

    Args:
        graph: The Module dependency graph.
        request: The names asked for, Module or Bundle.

    Returns:
        Every Module in the transitive `depends_on` closure, sorted.

    Raises:
        RuntimeError: If the request is empty, or names something no Module declares. It
            cannot raise for an empty result: every accepted name resolves through the
            profile index to at least one Module, so a non-empty request always yields
            one.
    """
    if not request:
        raise RuntimeError(
            "no Selection was requested — COMPOSE_PROFILES is unset or empty.\n"
            "  Set COMPOSE_PROFILES in .env to a comma-separated list of Module or Bundle "
            "names, or pass them as arguments.\n"
            # The concrete line, not only the variable. A .env predating Selection has no
            # COMPOSE_PROFILES at all, and its owner's question is "what do I write?", not
            # "which variable is missing?". This value is the whole stack — what the
            # checkout started before Selection existed — so pasting it is a no-op upgrade
            # and narrowing it afterwards is a deliberate choice (AD-18, ADR 0014).
            f"  Add this line to .env to start what this stack started before Selection "
            f"existed:\n"
            f"    COMPOSE_PROFILES={LEGACY_UPGRADE_SELECTION}\n"
            "  `pixi run init` ships that default; a .env that lost the variable must "
            "fail rather than start nothing and report success (AD-18).\n"
            f"  Valid names: {', '.join(graph.names())}"
        )

    seeds: set[str] = set()
    for name in request:
        owning = graph.profiles.get(name)
        if owning is None:
            raise RuntimeError(
                f"'{name}' is not a Module and is not a profile any Module declares.\n"
                f"  Valid names: {', '.join(graph.names())}"
            )
        seeds.update(owning)

    resolved: set[str] = set()
    pending = sorted(seeds)
    while pending:
        module = pending.pop()
        if module in resolved:
            continue
        resolved.add(module)
        pending.extend(target for target in graph.edges.get(module, ()) if target not in resolved)

    return tuple(sorted(resolved))


def selections(graph: Graph) -> list[Selection]:
    """List the Selections this repository actually validates.

    These replace the profile power set both `lint-compose.sh` and `assert_config.py` used
    to walk: seventeen declared profiles is 131 072 renders, and a cap would be arbitrary.
    The Selections that exist are the ones the resolver can name — one per Module (AD-6's
    closure-validity, which is what makes a Module liftable), one per Bundle, and the whole
    stack. Every duplicate-published-port collision is still caught, because the full
    Selection contains every Module.

    The non-Module profiles are derived, never listed, which is why the Bundles ADR 0014
    registers became Selections here with no change to this function. This does not read
    the registry: `assert_config.py` is what refuses a profile the registry does not
    register, so by the time a model passes lint-config the two sets are the same.

    Args:
        graph: The Module dependency graph.

    Returns:
        One Selection per Module, then one per non-Module profile, then every Module.

    Raises:
        RuntimeError: If any of those requests does not resolve.
    """
    resolved = [Selection(module, closure(graph, [module])) for module in graph.modules]
    bundles = sorted(set(graph.profiles) - set(graph.modules))
    resolved += [Selection(bundle, closure(graph, [bundle])) for bundle in bundles]
    resolved.append(Selection(ALL_MODULES_REQUEST, graph.modules))
    return resolved


def main(argv: list[str]) -> int:
    """Print the Modules a requested Selection resolves to.

    Args:
        argv: Arguments after the program name: `--all`, `--selections`, or the requested
            Module and Bundle names, which may arrive comma-joined in one argument.

    Returns:
        Process exit status: 0 with the Selection on stdout, 1 with a diagnostic on stderr
        and nothing on stdout.
    """
    try:
        graph = build_graph(module_composes())
        if SELECTIONS_REQUEST in argv:
            for selection in selections(graph):
                sys.stdout.write(f"{selection.request}\n")
            return 0
        if ALL_MODULES_REQUEST in argv:
            resolved = graph.modules
        else:
            resolved = closure(graph, parse_request(argv))
    except RuntimeError as exc:
        # Nothing is written to stdout before this point, which is the contract: a caller
        # substituting this command must get an empty value, never a partial Selection.
        sys.stderr.write(f"select: {exc}\n")
        return 1

    sys.stdout.write(",".join(resolved) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

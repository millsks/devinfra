#!/usr/bin/env python3
"""Render every connection detail this stack publishes, from the metadata that already declares it.

A connection string written down by hand is written down more than once. Before this script
there were three copies — the fourteen defaults and twelve `printf` lines in
`scripts/urls.sh`, the README's `## Contents` table, and the README's `## Connecting your
application` dotenv block — and the checked source of truth, each Module's own
`x-endpoints:` block, was read by nothing that produced documentation. A changed port
changed none of the three and no check noticed; `urls.sh` had already lost `LOKI_PORT`,
`TEMPO_PORT` and `KEYCLOAK_MGMT_PORT` while its own header promised completeness.

This is the only thing in this repository that states a connection string as documentation —
code that has to reach a service still builds its own address, and that is not what drifts.
It reads the raw `x-endpoints:` blocks of `services/*/compose.yaml`, the root `compose.yaml`'s
`x-app-variables:` registry — the application tier of ADR 0003's two-tier namespace — and a
dotenv, and renders two surfaces from them:

    python scripts/endpoints.py                                    # the ambient Selection, as text
    python scripts/endpoints.py postgres redis                     # an explicit Selection
    python scripts/endpoints.py --format markdown --write docs/ENDPOINTS.md
    python scripts/endpoints.py --check

The text listing resolves against the process environment, which `scripts/lib/common.sh`
has already loaded `.env` into, so a developer sees their own ports. The document resolves
against the tracked `.env.example` and nothing else, so it is a function of tracked inputs
alone: rendering it against a developer's `.env` would make `--check` fail for everyone who
changed a port locally, and a check that fails for everyone is a check that gets disabled.
`--check` re-renders the document from those tracked inputs and refuses on any divergence,
and pins three more things the document cannot see: `.env.example` must declare exactly the
fallback each compose `${VAR:-default}` names, every port literal in the README's
`## Contents` table must be a resolved endpoint, and no connection string may live anywhere
else in the README.

One `<subject>: OK …` line per Module and per application variable on stdout, every defect
as an `endpoints: …` line on stderr, and exit 0 only when every subject is clean. A
`Refusal` is reserved for "cannot check at all" — no Module files, no registry, no document
to compare against — because a check that walked nothing has verified nothing.

Offline: no container runtime and no network. `x-endpoints:` describes the *host* surface a
developer types on their own machine (ADR 0012); in-network addresses are not endpoints and
are not generated. PyYAML is read through `resolve_selection.read_model()`, which is the
required way to read a raw Module file — Compose discards top-level `x-` keys from included
files, so `docker compose config` cannot be the source (ADR 0012).
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from assert_pins import dotenv_declarations
from resolve_selection import (
    ALL_MODULES_REQUEST,
    build_graph,
    closure,
    module_composes,
    parse_request,
    read_model,
)

REPO = Path(__file__).resolve().parent.parent

#: The key every Module publishes its host surface under (ADR 0012). Keyed by the `*_PORT`
#: variable that publishes the port, which is what `lint-config` reconciles against `ports:`.
ENDPOINTS_KEY = "x-endpoints"

#: The root-file key registering the application tier of ADR 0003's namespace.
APP_VARIABLES_KEY = "x-app-variables"

#: What a registry entry declares, exactly — no more and no less. An unexpected key is a
#: failure rather than an extra, for the same reason `x-bundles` refuses one: a second field
#: nothing reads is a second thing free to drift.
APP_ENTRY_KEYS = ("module", "endpoint", "value", "description")

#: The tracked dotenv the committed document is rendered against.
TEMPLATE = ".env.example"

#: How many lines of a stale-document diff reach stderr before it is truncated, with the
#: remainder counted rather than dropped.
DIFF_LINES = 40

#: The generated document, and the task that rewrites it.
DOCUMENT = "docs/ENDPOINTS.md"
REGENERATE = "pixi run endpoints"
RECHECK = "pixi run lint-endpoints"

#: One Compose variable reference: `${NAME}`, `${NAME:-default}`, `${NAME-default}`, or the
#: brace-less `$NAME` Compose accepts just as readily. Copied in shape from
#: `assert_pins.PIN`, widened from `*_VERSION` to any name.
REFERENCE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>:?-)?(?P<default>[^}]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)

#: The README's `## Contents` header row, and the number of cells every row of it carries.
#: The table is the one place in that file a port literal is allowed to appear, and this is
#: where the parse starts.
CONTENTS_HEADER = "| Service | Version | Purpose | Endpoint |"
CONTENTS_COLUMNS = 4

#: A port literal inside a table cell: `:5432`, `localhost:5432`, `(SMTP \`:1025\`)`.
PORT_LITERAL = re.compile(r":(?P<port>\d{2,5})\b")

#: A connection string: a loopback host and a port. Deliberately wider than `://localhost:`
#: in both halves — a DSN carrying credentials reads `@localhost:5432`, and this repository
#: writes the same address as `127.0.0.1` as readily as `localhost` (`BIND_ADDRESS` is
#: `127.0.0.1`), so pinning one spelling would leave the `DATABASE_URL` and `REDIS_URL`
#: copies this story removes free to grow back under another. The README carries none of
#: these outside the Contents table; `docs/ENDPOINTS.md` carries them all.
CONNECTION_STRING = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):\d")

#: One `${NAME:-default}` fallback — the only form a compose file states a default in.
COMPOSE_FALLBACK = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*):-(?P<default>[^}]*)\}")

#: The shell spelling of the same statement, `: "${NAME:=default}"`, which is how
#: scripts/lib/common.sh and scripts/token.sh supply a default at shell level.
SHELL_FALLBACK = re.compile(r':\s*"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*):=(?P<default>[^}]*)\}"')


class Refusal(Exception):
    """Raised when the render or the check cannot be run at all, rather than run and pass."""


@dataclass(frozen=True)
class Endpoint:
    """One entry of one Module's `x-endpoints:` block.

    Attributes:
        module: The Module directory that publishes it.
        variable: The `*_PORT` variable the entry is keyed by.
        url: The host URL as written, before interpolation.
        description: What answers there, as written.
    """

    module: str
    variable: str
    url: str
    description: str


@dataclass(frozen=True)
class AppVariable:
    """One entry of the root `x-app-variables:` registry.

    Attributes:
        name: The externally-dictated variable name an SDK reads.
        module: The one Module that owns it.
        endpoint: The owning Module's endpoint key it reaches.
        value: The value as written, before interpolation.
        description: What an application uses it for, as written.
    """

    name: str
    module: str
    endpoint: str
    value: str
    description: str


@dataclass(frozen=True)
class Catalog:
    """Everything the two registries declare, before any of it is interpolated.

    Attributes:
        endpoints: Every Module's entries, keyed by Module then by variable, in file order.
        variables: The registry entries, in registration order.
    """

    endpoints: dict[str, dict[str, Endpoint]]
    variables: dict[str, AppVariable]


def read_endpoints(paths: list[Path]) -> tuple[dict[str, dict[str, Endpoint]], list[str]]:
    """Read every Module's published host surface out of the module files.

    Args:
        paths: The `services/*/compose.yaml` files to read.

    Returns:
        Each Module's entries keyed by Module then by variable, and one message per
        malformed entry.

    Raises:
        Refusal: If the glob matched nothing.
        RuntimeError: If a module file cannot be read or does not parse.
    """
    if not paths:
        raise Refusal(
            "services/*/compose.yaml matched nothing — there is no catalog to generate from, "
            "and a document rendered over an empty walk would document nothing and say so at "
            "exit 0"
        )
    found: dict[str, dict[str, Endpoint]] = {}
    problems: list[str] = []
    for path in paths:
        module = path.parent.name
        where = path.relative_to(REPO).as_posix()
        block = read_model(path).get(ENDPOINTS_KEY)
        entries: dict[str, Endpoint] = {}
        if not isinstance(block, dict) or not block:
            # `lint-config` is what refuses a Module for carrying no block at all (ADR 0012);
            # this says the same thing in the one place that would otherwise silently render
            # a Module with no endpoints and sign off on it.
            problems.append(
                f"{where}: declares no `{ENDPOINTS_KEY}:` mapping, so this Module publishes "
                f"nothing a developer could be told about"
            )
            found[module] = entries
            continue
        for raw_name, body in block.items():
            name = str(raw_name)
            if not isinstance(body, dict):
                problems.append(f"{where}: {ENDPOINTS_KEY}.{name} is {type(body).__name__}, not a mapping")
                continue
            url, description = body.get("url"), body.get("description")
            if not isinstance(url, str) or not url.strip():
                problems.append(f"{where}: {ENDPOINTS_KEY}.{name} declares no non-empty `url:` — it is {url!r}")
                continue
            if not isinstance(description, str) or not description.strip():
                problems.append(
                    f"{where}: {ENDPOINTS_KEY}.{name} declares no non-empty `description:` — it is {description!r}"
                )
                continue
            entries[name] = Endpoint(module=module, variable=name, url=url.strip(), description=description.strip())
        found[module] = entries
    return found, problems


def read_registry(root: Path, endpoints: dict[str, dict[str, Endpoint]]) -> tuple[dict[str, AppVariable], list[str]]:
    """Read the application-variable registry, reconciling every entry against the catalog.

    Absent, malformed and empty are all refused rather than read as "no application
    variables": read that way, every check below would iterate an empty set and the tier ADR
    0003 defines would silently cease to exist. A single bad *entry* is a per-subject
    failure instead, so one typo names itself rather than hiding the other thirteen.

    Args:
        root: The root `compose.yaml`.
        endpoints: Every Module's endpoints, from `read_endpoints()`.

    Returns:
        The registry in registration order, and one message per defective entry.

    Raises:
        Refusal: If the root file declares no registry, or declares one that is not a
            non-empty mapping.
        RuntimeError: If the root file cannot be read or does not parse.
    """
    where = f"{root.name}: {APP_VARIABLES_KEY}"
    declared = read_model(root).get(APP_VARIABLES_KEY)
    if declared is None:
        raise Refusal(
            f"{where}: is not declared — it is the only place an unprefixed, externally "
            f"dictated variable name becomes legal (ADR 0003), so without it there is no "
            f"application tier to document and this check would sign off on its absence"
        )
    if not isinstance(declared, dict) or not declared:
        kind = "empty" if isinstance(declared, dict) else f"{type(declared).__name__}, not a mapping"
        raise Refusal(
            f"{where}: parsed as {kind} of variable name to entry — read as 'no application "
            f"variables' this would render a document with none and exit 0"
        )

    variables: dict[str, AppVariable] = {}
    problems: list[str] = []
    for raw_name, entry in declared.items():
        name = str(raw_name)
        if not isinstance(entry, dict):
            problems.append(f"{where}.{name}: is {type(entry).__name__}, not a mapping of {list(APP_ENTRY_KEYS)}")
            continue
        unexpected = sorted(str(key) for key in entry if str(key) not in APP_ENTRY_KEYS)
        if unexpected:
            problems.append(
                f"{where}.{name}: declares {unexpected}, which a registry entry may not carry — "
                f"an entry names one owning Module, one of that Module's endpoint keys, the "
                f"value and what it is for, and nothing else"
            )
            continue
        fields: dict[str, str] = {}
        missing = False
        for key in APP_ENTRY_KEYS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}.{name}: declares no non-empty '{key}:' — it is {value!r}")
                missing = True
                break
            fields[key] = value.strip()
        if missing:
            continue
        module, endpoint = fields["module"], fields["endpoint"]
        if module not in endpoints:
            problems.append(
                f"{where}.{name}: names the Module '{module}', which no services/*/compose.yaml "
                f"declares — the Modules are {', '.join(sorted(endpoints))}"
            )
            continue
        if endpoint not in endpoints[module]:
            published = ", ".join(sorted(endpoints[module])) or "nothing at all"
            problems.append(
                f"{where}.{name}: names the endpoint '{endpoint}', which Module '{module}' does "
                f"not publish — its {ENDPOINTS_KEY}: keys are {published}"
            )
            continue
        claimed = module_tier_owner(name, module, sorted(endpoints))
        if claimed is not None:
            problems.append(
                f"{where}.{name}: carries the prefix of Module '{claimed}' while naming "
                f"'{module}' as its owner — a `<MODULE>_<CONCERN>` name belongs to the Module "
                f"tier and is owned by the Module it is named for (ADR 0003). Either rename it "
                f"or give it to '{claimed}'"
            )
            continue
        variables[name] = AppVariable(
            name=name,
            module=module,
            endpoint=endpoint,
            value=fields["value"],
            description=fields["description"],
        )
    return variables, problems


def module_tier_owner(name: str, owner: str, modules: list[str]) -> str | None:
    """Say whether a registry name is really a Module variable wearing a contract name.

    ADR 0003 splits the namespace in two: `<MODULE>_<CONCERN>` belongs to the Module it is
    named for, and the registry is only for names an external contract dictates. A name
    carrying a Module's prefix while another Module owns the entry is the collision the
    split exists to prevent — the name says one service and the registry says another. A
    name carrying *its own* Module's prefix is not that collision and is allowed: `REDIS_URL`
    is dictated by the same SDK convention as `DATABASE_URL` and does reach Redis.

    Args:
        name: The registry key.
        owner: The Module the entry names as owner.
        modules: Every Module directory name.

    Returns:
        The Module whose prefix the name carries, or None when there is no such collision.
    """
    for module in modules:
        if module == owner:
            continue
        if name.startswith(f"{module.upper().replace('-', '_')}_"):
            return module
    return None


def interpolate(text: str, environment: dict[str, str], where: str) -> tuple[str, list[str]]:
    """Resolve `${VAR}` and `${VAR:-default}` exactly as Compose resolves them.

    `${VAR:-default}` falls back when the variable is unset *or* empty; `${VAR-default}`
    only when it is unset. A reference with no default and no declaration is a defect rather
    than an empty string: Compose substitutes nothing and warns, which in a generated
    document is a connection string with a hole in it that nobody notices.

    Args:
        text: The value as written.
        environment: Variable name to value — a dotenv's declarations, or `os.environ`.
        where: The file the text came from, for the diagnostic.

    Returns:
        The resolved text, and one message per reference that could not be resolved.
    """
    problems: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare")
        declared = environment.get(name)
        if match.group("braced") is not None and match.group("sep"):
            fallback = match.group("default")
            empty_counts = match.group("sep") == ":-"
            if declared is None or (empty_counts and not declared):
                return fallback
            return declared
        if declared is None:
            problems.append(
                f"{where}: references ${{{name}}}, which has no default and is declared nowhere — "
                f"Compose would substitute nothing and leave a hole in the value"
            )
            return match.group(0)
        return declared

    return REFERENCE.sub(replace, text), problems


def referenced_defaults(text: str) -> dict[str, str]:
    """Collect the fallback each `${VAR:-default}` in a value names.

    Args:
        text: The value as written.

    Returns:
        Variable name to the fallback it declares, empty when the text names no fallback.
    """
    found: dict[str, str] = {}
    for match in REFERENCE.finditer(text):
        name = match.group("braced")
        if name is not None and match.group("sep"):
            found[name] = match.group("default")
    return found


def select(catalog: Catalog, request: list[str]) -> tuple[str, ...]:
    """Expand a Selection request into the Modules the listing covers.

    Args:
        catalog: The catalog, for the Module names an empty request falls back to.
        request: The requested Module and Bundle names, empty for every Module.

    Returns:
        The Modules to render, sorted.

    Raises:
        RuntimeError: If a requested name is not a Module and not a profile any Module
            declares.
    """
    if not request or ALL_MODULES_REQUEST in request:
        return tuple(sorted(catalog.endpoints))
    return closure(build_graph(module_composes()), request)


def render_text(catalog: Catalog, modules: tuple[str, ...], environment: dict[str, str]) -> tuple[str, list[str]]:
    """Render the terminal listing for one Selection.

    Args:
        catalog: The whole catalog.
        modules: The Modules the Selection resolved to.
        environment: The values to interpolate against.

    Returns:
        The listing, and one message per value that could not be resolved.
    """
    problems: list[str] = []
    lines: list[str] = ["", "Endpoints", ""]
    entries = [entry for module in modules for entry in catalog.endpoints.get(module, {}).values()]
    variables = [entry for entry in catalog.variables.values() if entry.module in modules]
    width = max((len(entry.variable) for entry in entries), default=0)
    for module in modules:
        published = catalog.endpoints.get(module, {})
        if not published:
            continue
        lines.append(f"  {module}")
        for entry in published.values():
            url, trouble = interpolate(entry.url, environment, f"services/{module}/compose.yaml")
            problems.extend(trouble)
            lines.append(f"    {entry.variable.ljust(width)}  {url}")
            lines.append(f"    {' ' * width}  {entry.description}")
        lines.append("")
    if variables:
        lines.extend(["Application variables", ""])
        for variable in variables:
            value, trouble = interpolate(variable.value, environment, f"compose.yaml: {APP_VARIABLES_KEY}")
            problems.extend(trouble)
            lines.append(f"  {variable.name}={value}")
        lines.append("")
    return "\n".join(lines) + "\n", problems


def cell(text: str) -> str:
    """Escape the three characters a generated Markdown table cell cannot carry raw.

    A `|` ends the cell wherever it appears, backticks included, and a bare `<…>` — which
    Keycloak's `/realms/<realm>/…` and Tempo's `/api/traces/<id>` both write — is read as an
    unknown HTML tag and disappears from the rendered page.

    Args:
        text: The cell's content, as the metadata states it.

    Returns:
        The same text, safe to place between two pipes.
    """
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def render_markdown(catalog: Catalog, modules: tuple[str, ...], environment: dict[str, str]) -> tuple[str, list[str]]:
    """Render the committed document for one Selection.

    Args:
        catalog: The whole catalog.
        modules: The Modules the Selection resolved to.
        environment: The values to interpolate against.

    Returns:
        The document, and one message per value that could not be resolved.
    """
    problems: list[str] = []
    lines: list[str] = [
        f"<!-- Generated by `{REGENERATE}` from every Module's `{ENDPOINTS_KEY}:` block and the",
        f"     root compose.yaml's `{APP_VARIABLES_KEY}:` registry, resolved against `{TEMPLATE}`.",
        "     Do not edit this file by hand — the next run overwrites it, and",
        f"     `{RECHECK}` fails the build while it disagrees with those inputs. -->",
        "",
        "# Endpoints",
        "",
        "Every address below is the **host** surface: what you type on your own machine.",
        "In-network traffic between containers does not use any of it — see each Module's",
        f"`{ENDPOINTS_KEY}:` description. Values are the defaults `{TEMPLATE}` ships.",
        "",
        "To change one: edit the `${VAR:-default}` in the owning Module's compose file **and**",
        f"the declaration in `{TEMPLATE}` — `{RECHECK}` pins those two to each other and",
        f"fails while they disagree — then run `{REGENERATE}` and commit this file. Your own",
        "`.env` overrides the value at runtime and is what `pixi run urls` prints, scoped to",
        "the Selection you are running.",
        "",
    ]
    # Suppressed entirely when the Selection owns none, rather than rendered as a header row
    # over nothing: a table with no rows reads as "this stack has no application variables",
    # which is a different statement from "the Modules you asked for own none of them". This
    # is the same rule render_text applies to the same section.
    scoped = [variable for variable in catalog.variables.values() if variable.module in modules]
    if scoped:
        lines.extend(
            [
                "## Application variables",
                "",
                "The names an application's own SDK reads. Each is owned by exactly one Module and",
                "reaches exactly one of that Module's endpoints, which is what tells `REDIS_URL`,",
                "`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` apart.",
                "",
                "| Variable | Module | Endpoint | Value | Purpose |",
                "|---|---|---|---|---|",
            ]
        )
    for variable in scoped:
        value, trouble = interpolate(variable.value, environment, f"compose.yaml: {APP_VARIABLES_KEY}")
        problems.extend(trouble)
        lines.append(
            f"| `{cell(variable.name)}` | `{cell(variable.module)}` | `{cell(variable.endpoint)}` "
            f"| `{cell(value)}` | {cell(variable.description)} |"
        )
    # One blank line before the next heading whether or not the section above rendered:
    # a suppressed section would otherwise leave the separator it was going to be preceded by.
    while lines and not lines[-1]:
        lines.pop()
    lines.extend(["", "## Service endpoints", ""])
    for module in modules:
        published = catalog.endpoints.get(module, {})
        if not published:
            continue
        lines.extend([f"### {module}", "", "| Variable | URL | Description |", "|---|---|---|"])
        for entry in published.values():
            url, trouble = interpolate(entry.url, environment, f"services/{module}/compose.yaml")
            problems.extend(trouble)
            lines.append(f"| `{cell(entry.variable)}` | `{cell(url)}` | {cell(entry.description)} |")
        lines.append("")
    # The separator after the last table is a trailing blank line, not a separator. A
    # generated file that ends in one is a file every editor and formatter wants to change,
    # which is a diff `lint-endpoints` would then blame on the metadata.
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n", problems


def catalog_scope(catalog: Catalog) -> set[str]:
    """List the variables the catalog itself states a default for.

    The pin below reaches outside the catalog, so it needs a boundary that is derived rather
    than listed: a variable is in scope exactly when an endpoint URL or a registry value
    interpolates it with a fallback. That is what keeps `POSTGRES_EXTRA_DATABASES` and the
    `*_VERSION` pins — which `assert_pins.py` already owns — out of it.

    Args:
        catalog: The whole catalog.

    Returns:
        Every variable name the catalog interpolates with a `:-default`.
    """
    scope: set[str] = set()
    for module in catalog.endpoints:
        for entry in catalog.endpoints[module].values():
            scope |= set(referenced_defaults(entry.url))
    for variable in catalog.variables.values():
        scope |= set(referenced_defaults(variable.value))
    return scope


def fallback_occurrences(catalog: Catalog) -> list[tuple[str, str, str]]:
    """Find every place this repository states a default for a variable the catalog documents.

    The catalog's own strings are not the only place a default is written down. The same
    port and the same password are stated again in a module's `environment:`, in
    `scripts/lib/common.sh`'s four `: "${VAR:=default}"` lines and in `scripts/token.sh`.
    Pinning only the strings the document is rendered from would leave every one of those
    free to drift from `.env.example` with the build green.

    Args:
        catalog: The whole catalog.

    Returns:
        One `(where, name, fallback)` triple per occurrence, `where` naming the file and,
        for the file scans, the line.
    """
    scope = catalog_scope(catalog)
    found: list[tuple[str, str, str]] = [
        (f"compose.yaml: {APP_VARIABLES_KEY}.{variable.name}", name, fallback)
        for variable in catalog.variables.values()
        for name, fallback in referenced_defaults(variable.value).items()
    ]
    # The shell scan covers what `pixi run lint-shell` covers, not scripts/ alone: a module's
    # smoke.sh and a git hook state a default in exactly the same spelling, and one that only
    # scripts/ was walked for is one free to drift from .env.example with the build green.
    shell_files = sorted(
        {
            *(REPO / "scripts").rglob("*.sh"),
            *(REPO / "services").rglob("*.sh"),
            *(path for path in (REPO / ".githooks").glob("*") if path.is_file()),
        }
    )
    scans: list[tuple[list[Path], re.Pattern[str]]] = [
        (sorted((REPO / "services").glob("*/compose.yaml")), COMPOSE_FALLBACK),
        (shell_files, SHELL_FALLBACK),
    ]
    for paths, pattern in scans:
        for path in paths:
            where = path.relative_to(REPO).as_posix()
            # A commented-out line is not part of the model Compose renders and not part of
            # what the shell executes, so a reference inside one is not a statement of a
            # default — the same rule `assert_pins.py` applies to its own scan.
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.lstrip().startswith("#"):
                    continue
                for match in pattern.finditer(line):
                    if match.group("name") in scope:
                        found.append((f"{where}:{number}", match.group("name"), match.group("default")))
    return found


def check_template(catalog: Catalog, template: Path, declared: dict[str, str]) -> list[str]:
    """Pin every stated default to the declaration `.env.example` ships.

    Required rather than extra. The document is rendered from the template, so changing only
    a compose `${VAR:-default}` would leave the document unchanged and the build green —
    exactly the silent divergence this story exists to close. Same shape `assert_pins.py`
    already applies to `*_VERSION`.

    Args:
        catalog: The whole catalog.
        template: The tracked dotenv, for the diagnostic.
        declared: What that dotenv declares.

    Returns:
        One message per variable the two disagree about, empty when they agree.
    """
    occurrences = fallback_occurrences(catalog)
    # The walk, before anything is asserted with it: a scan that found no occurrence would
    # sign off on every file it was supposed to have read.
    if not occurrences:
        return [
            f"no `${{VAR:-default}}` was found for any variable the catalog documents, so the "
            f"{template.name} pin walked nothing — the scan over the module files and "
            f"scripts/ has stopped matching"
        ]
    problems: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for where, name, fallback in occurrences:
        if (where, name, fallback) in seen:
            continue
        seen.add((where, name, fallback))
        if name not in declared:
            problems.append(
                f"{where}: states ${{{name}:-{fallback}}}, which {template.name} declares "
                f"nowhere — a clone that copies the template gets no value for it, so the "
                f"documented default and the running default are two different numbers"
            )
            continue
        if declared[name] != fallback:
            problems.append(
                f"{name}: {template.name} says {declared[name]!r} and {where} falls back to "
                f"{fallback!r} — the document is rendered from the template, so changing only "
                f"one of the two leaves it green and wrong"
            )
    return problems


def contents_rows(readme: str) -> tuple[list[tuple[int, list[str]]], set[int]]:
    """Parse the README's `## Contents` table into its rows.

    Every cell of every row is returned, not only the Endpoint one: a port stated in the
    Service, Version or Purpose cell is the same claim about the same service, and returning
    one column would leave it unpinned while the whole table stayed exempt from the
    connection-string scan.

    Args:
        readme: The README's full text.

    Returns:
        One `(1-based line number, cells)` pair per data row — the header and the `|---|`
        separator excluded — and the 0-based line numbers the table occupies.
    """
    rows: list[tuple[int, list[str]]] = []
    occupied: set[int] = set()
    lines = readme.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != CONTENTS_HEADER:
            continue
        occupied.add(index)
        for offset, row in enumerate(lines[index + 1 :], start=index + 1):
            if not row.strip().startswith("|"):
                break
            occupied.add(offset)
            columns = [column.strip() for column in row.strip().strip("|").split("|")]
            if all(set(column) <= {"-", ":"} for column in columns):
                continue
            rows.append((offset + 1, columns))
        break
    return rows, occupied


def check_readme(readme_path: Path, port_owner: dict[str, str]) -> list[str]:
    """Pin the README's port literals to the catalog, and its prose to no connection string.

    Each row is one service, so every port literal anywhere in it must belong to one Module,
    and no two rows may claim the same Module — which is what catches a port moved from the
    row that publishes it into a row that does not. Tying a row to the Module it is *named*
    for would need a hand-written service-name map (`Silo` is `minio`, `PostgreSQL` is
    `postgres`), which is the second hand-maintained list ADR 0017 refuses; a mutual swap
    that leaves every Module claimed exactly once is therefore out of reach here and is left
    to review.

    Args:
        readme_path: The README to read.
        port_owner: Each resolved endpoint port, mapped to the Module that publishes it.

    Returns:
        One message per defect, empty when the README states nothing the catalog does not.
    """
    problems: list[str] = []
    readme = readme_path.read_text(encoding="utf-8")
    rows, occupied = contents_rows(readme)
    # The parse found nothing, so nothing below would be reporting on anything. Separated
    # from the equality it guards, exactly as the Bundle-footprint pin separates the two:
    # a broken parse must blame the parse, not the content.
    if not rows:
        return [
            f"{readme_path.name}: parsed no rows out of the `## Contents` table — the header "
            f"{CONTENTS_HEADER!r} moved or changed shape, so the port pin below would be "
            f"reporting on nothing"
        ]
    literals = 0
    claimed: dict[str, int] = {}
    for number, columns in rows:
        if len(columns) != CONTENTS_COLUMNS:
            problems.append(
                f"{readme_path.name}:{number}: the `## Contents` row carries {len(columns)} "
                f"cells, not {CONTENTS_COLUMNS} — the table has been reshaped, and a row this "
                f"parse cannot read is a row the port pin silently skips"
            )
            continue
        owners: set[str] = set()
        for column in columns:
            for match in PORT_LITERAL.finditer(column):
                literals += 1
                port = match.group("port")
                owner = port_owner.get(port)
                if owner is None:
                    problems.append(
                        f"{readme_path.name}:{number}: states port {port}, which no Module "
                        f"publishes — the endpoints resolve to {', '.join(sorted(port_owner))}"
                    )
                    continue
                owners.add(owner)
        if len(owners) > 1:
            problems.append(
                f"{readme_path.name}:{number}: states ports belonging to {', '.join(sorted(owners))} "
                f"— one row is one service, so a row naming two Modules' ports is a port that "
                f"has moved into the wrong row"
            )
            continue
        for owner in owners:
            if owner in claimed:
                problems.append(
                    f"{readme_path.name}:{number}: states {owner}'s port, which line "
                    f"{claimed[owner]} already states — two rows cannot be the same service, so "
                    f"one of them is now documenting something it does not run"
                )
                continue
            claimed[owner] = number
    if not literals:
        problems.append(
            f"{readme_path.name}: the `## Contents` table carries no port literal at all — "
            f"the pin found nothing to check"
        )
    # The reverse direction, without which the pin is one-way: every port the table states
    # is checked, but a Module the table never mentions is checked by nothing, so adding a
    # Module or deleting a row silently drops it out of the at-a-glance index of what the
    # stack runs. Derived from the catalog rather than from a second list of names.
    unclaimed = sorted(set(port_owner.values()) - set(claimed))
    if unclaimed:
        problems.append(
            f"{readme_path.name}: the `## Contents` table states no port for "
            f"{', '.join(unclaimed)} — every Module the catalog publishes gets a row, or the "
            f"table stops being the index of what this stack runs"
        )
    for index, line in enumerate(readme.splitlines()):
        if index in occupied or CONNECTION_STRING.search(line) is None:
            continue
        problems.append(
            f"{readme_path.name}:{index + 1}: carries a connection string outside the "
            f"`## Contents` table — {line.strip()!r}. Connection details live in {DOCUMENT}, "
            f"which is generated; a copy here is free to drift and nobody editing a Module "
            f"file ever sees it"
        )
    return problems


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Returns:
        The parser, with every default stated so the task bodies pass nothing they need not.
    """
    parser = argparse.ArgumentParser(
        prog="endpoints.py",
        description="Render every connection detail from the Modules' x-endpoints: and the x-app-variables: registry.",
    )
    parser.add_argument(
        "selection",
        nargs="*",
        metavar="SELECTION",
        help=(
            "Module and Bundle names to render. Defaults to COMPOSE_PROFILES when it is "
            "non-empty and to every Module otherwise, so a fresh clone still gets a listing"
        ),
    )
    # Declared as a flag rather than left to the positional list: argparse would reject a
    # leading `--all` as an unknown option, and the lifecycle scripts spell the whole stack
    # exactly this way (resolve_selection.ALL_MODULES_REQUEST).
    parser.add_argument(
        ALL_MODULES_REQUEST,
        action="store_true",
        dest="all_modules",
        help="render every Module, whatever COMPOSE_PROFILES asks for",
    )
    # No `default=`: main() has to be able to tell "not given" from "given as text", because
    # --check fixes the format and a --check that silently overrode an explicit --format
    # would be doing something other than what it was asked for.
    parser.add_argument(
        "--format",
        choices=("text", "markdown"),
        default=None,
        help="text for the terminal listing, markdown for the committed document (default: text)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help=(
            f"dotenv to resolve values against; defaults to the process environment for text "
            f"and to {TEMPLATE} for markdown, so the document is a function of tracked inputs alone"
        ),
    )
    parser.add_argument("--write", default=None, metavar="PATH", help="write the render to PATH instead of stdout")
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            f"re-render {DOCUMENT} from the tracked inputs and refuse on any divergence, and pin "
            f"{TEMPLATE} and the README against the catalog. Fixes --format markdown, the whole "
            f"catalog and --env-file {TEMPLATE}, so it takes none of those and refuses them"
        ),
    )
    return parser


def settle(args: argparse.Namespace) -> None:
    """Reconcile the options against each other, refusing a combination with two meanings.

    `--check` fixes the format, the Selection and the dotenv, because a check is only a check
    when both sides of the comparison are functions of tracked inputs. Silently overriding an
    option the caller passed would make it do something other than what it was asked for, so
    the combination is refused instead.

    The second refusal is the one pixi makes reachable by accident: task arguments are
    appended to the task body, so `pixi run endpoints postgres` would otherwise overwrite the
    committed document with a one-Module render that `pixi run lint-endpoints` then rejects.

    Args:
        args: The parsed command line, settled in place.

    Raises:
        Refusal: If two options ask for different things.
    """
    if args.check:
        conflicting = [
            name
            for name, given in (
                ("--format", args.format is not None),
                ("--env-file", args.env_file is not None),
                ("--write", args.write is not None),
                ("a SELECTION", bool(args.selection)),
            )
            if given
        ]
        if conflicting:
            raise Refusal(
                f"--check takes none of {', '.join(conflicting)}: it fixes --format markdown, "
                f"the whole catalog and --env-file {TEMPLATE}, because a check is only a check "
                f"when both sides of the comparison are functions of tracked inputs"
            )
        args.format, args.env_file, args.all_modules = "markdown", str(REPO / TEMPLATE), True
        return
    args.format = args.format or "text"
    if args.write is None:
        return
    if Path(args.write).resolve() != (REPO / DOCUMENT).resolve():
        return
    # Every option pixi can append to the task body, not the Selection alone: a trailing
    # `--format text` or `--env-file .env` reaches argparse after the task body's own flags
    # and wins, so either one would overwrite the committed document with something
    # `lint-endpoints` immediately rejects — a narrowed render, a banner-less listing, or
    # this machine's values in a file that must be a function of tracked inputs.
    narrowing = [
        name
        for name, given in (
            (f"the Selection {' '.join(args.selection)}", bool(args.selection)),
            (f"--format {args.format}", args.format != "markdown"),
            (f"--env-file {args.env_file}", args.env_file is not None),
        )
        if given
    ]
    if narrowing:
        raise Refusal(
            f"refusing to write {DOCUMENT} with {', '.join(narrowing)}: the committed document "
            f"is the whole catalog in markdown, resolved against {TEMPLATE}, and anything else "
            f"is a document `{RECHECK}` immediately rejects. Drop it, or --write elsewhere"
        )


def environment_for(args: argparse.Namespace) -> dict[str, str]:
    """Choose the values a render resolves against.

    Args:
        args: The parsed command line.

    Returns:
        Variable name to value.

    Raises:
        RuntimeError: If an explicitly named dotenv cannot be read.
        Refusal: If the template the document needs is not there to read.
    """
    if args.env_file is not None:
        return dotenv_declarations(Path(args.env_file))
    if args.format == "markdown":
        template = REPO / TEMPLATE
        if not template.is_file():
            raise Refusal(
                f"{TEMPLATE} is not there to render against — the committed document is a "
                f"function of tracked inputs alone, and rendering it against this machine's "
                f"environment instead would make it differ for every developer"
            )
        return dotenv_declarations(template)
    return dict(os.environ)


def report(problems: list[str]) -> int:
    """Write every problem to stderr in the house shape.

    Args:
        problems: The messages, in the order they were found.

    Returns:
        Process exit status: 1 when anything was reported, 0 otherwise.
    """
    for problem in problems:
        sys.stderr.write(f"endpoints: {problem}\n")
    return 1 if problems else 0


def run_check(catalog: Catalog, environment: dict[str, str]) -> int:
    """Re-render the document from the tracked inputs and pin everything it cannot see.

    Args:
        catalog: The whole catalog.
        environment: The template's declarations.

    Returns:
        Process exit status: 0 only when every subject is clean.

    Raises:
        Refusal: If the document is not there to compare against.
    """
    document = REPO / DOCUMENT
    if not document.is_file():
        raise Refusal(
            f"{DOCUMENT} does not exist, so there is nothing to compare a regeneration "
            f"against — run `{REGENERATE}` and commit it"
        )
    modules = tuple(sorted(catalog.endpoints))
    rendered, problems = render_markdown(catalog, modules, environment)
    committed = document.read_text(encoding="utf-8")
    if committed != rendered:
        diff = list(
            difflib.unified_diff(
                committed.splitlines(),
                rendered.splitlines(),
                fromfile=f"{DOCUMENT} (committed)",
                tofile=f"{DOCUMENT} (regenerated)",
                lineterm="",
                n=1,
            )
        )
        # Truncated, but never silently: a difference cut off with no marker reads as the
        # whole difference, and a maintainer who fixes what they can see would then find the
        # check still red for a reason they were never shown.
        shown = diff[:DIFF_LINES]
        if len(diff) > len(shown):
            shown.append(f"… {len(diff) - len(shown)} more line(s); `{REGENERATE}` writes all of them")
        problems.append(
            f"{DOCUMENT} is stale — it no longer matches what the module metadata renders. "
            f"Run `{REGENERATE}` and commit the result.\n  " + "\n  ".join(shown)
        )
    problems += check_template(catalog, REPO / TEMPLATE, environment)
    # Keyed by port and mapped back to the Module that publishes it, which is what lets the
    # README pin ask whether a row states *its own* service's port rather than merely some
    # port this stack happens to publish.
    port_owner: dict[str, str] = {}
    for module in modules:
        for entry in catalog.endpoints[module].values():
            resolved, trouble = interpolate(f"${{{entry.variable}}}", environment, f"services/{module}/compose.yaml")
            problems.extend(trouble)
            if trouble:
                continue
            # Guarded rather than last-wins, like every other derived structure here: two
            # Modules on one port would leave the README pin naming whichever Module the
            # walk reached last, so it would blame the wrong row for a defect neither has.
            if port_owner.setdefault(resolved, module) != module:
                problems.append(
                    f"{port_owner[resolved]} and {module} both publish port {resolved} — the "
                    f"stack cannot bind it twice, and the README pin below cannot say which "
                    f"Module a row stating it means"
                )
    if not port_owner:
        problems.append(
            f"no endpoint resolved to a port at all, so the README pin below would be reporting "
            f"on nothing — every {ENDPOINTS_KEY}: key is missing from {TEMPLATE}"
        )
    else:
        problems += check_readme(REPO / "README.md", port_owner)
    if problems:
        return report(problems)
    for module in modules:
        sys.stdout.write(f"{module}: OK {len(catalog.endpoints[module])} endpoint(s)\n")
    for variable in catalog.variables.values():
        sys.stdout.write(f"{variable.name}: OK {variable.module}/{variable.endpoint}\n")
    sys.stdout.write(f"{DOCUMENT}: OK current against {TEMPLATE} and the README\n")
    return 0


def main(argv: list[str]) -> int:
    """Render or check the endpoint catalog.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        Process exit status: 0 only when every subject is clean.
    """
    args = build_parser().parse_args(argv)
    try:
        settle(args)
        endpoints, problems = read_endpoints(module_composes())
        variables, registry_problems = read_registry(REPO / "compose.yaml", endpoints)
        catalog = Catalog(endpoints=endpoints, variables=variables)
        problems += registry_problems
        # Reported before the environment is resolved: an absent .env.example is a Refusal,
        # and raising it first would hide a real catalog defect behind a message about a
        # different file.
        if problems:
            return report(problems)
        environment = environment_for(args)
        if args.check:
            return run_check(catalog, environment)
        request = [ALL_MODULES_REQUEST] if args.all_modules else parse_request(args.selection)
        if not request and args.format == "text":
            # The ambient Selection, and every Module when there is none. A listing that
            # refuses to document is worse than one that documents everything, and
            # `pixi run urls` has to keep working on a fresh clone that has no .env at all.
            request = parse_request([os.environ.get("COMPOSE_PROFILES", "")])
        modules = select(catalog, request)
        render = render_markdown if args.format == "markdown" else render_text
        text, problems = render(catalog, modules, environment)
    except (Refusal, RuntimeError) as exc:
        sys.stderr.write(f"endpoints: {exc}\n")
        return 1
    # The README, the template and the document are read after an `is_file()` check, which
    # says nothing about permissions or encoding. A traceback out of one of those reads is
    # the same silent-shape failure a named refusal exists to replace.
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"endpoints: unreadable input: {exc}\n")
        return 1
    if problems:
        return report(problems)
    if args.write is None:
        sys.stdout.write(text)
        return 0
    # Inside its own guard for the same reason the reads are: an undeletable target or an
    # unwritable parent is a named refusal, not a traceback out of the one command the
    # do-not-edit banner tells every contributor to run.
    try:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        sys.stderr.write(f"endpoints: cannot write {args.write}: {exc}\n")
        return 1
    sys.stdout.write(f"{args.write}: written, {len(text.splitlines())} lines\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

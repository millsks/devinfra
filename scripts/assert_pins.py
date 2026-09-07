#!/usr/bin/env python3
"""Assert every image pin agrees between the compose files and `.env.example`.

Every pinned tag in this repository lives in two places that must move together: the
`<NAME>_VERSION=` declaration in `.env.example`, which `pixi run init` copies into a
contributor's `.env`, and the `${<NAME>_VERSION:-<tag>}` fallback in a compose file,
which is what a clone with no `.env` actually runs. Editing one and forgetting the
other gives two developers on the same commit two different images — the exact
reproducibility failure the pinning rule exists to prevent.

The compose half is no longer one file. Every service was extracted into
`services/<name>/compose.yaml`, reassembled by the root file's `include:` list, so the
check reads the root file *and* every module file and takes the union of what they
reference. A per-file pass would report every pin as "declared but never referenced",
because the root file now declares no services at all. Module files are
all named `compose.yaml`, so diagnostics name the path as it was given rather than the
basename — `services/postgres/compose.yaml`, never a bare `compose.yaml` that could be
any of thirteen files.

`assert_config.py` cannot see this. It reads the *rendered* model, where `.env` has
already supplied a value, so a stale `compose.yaml` fallback is invisible to it: the
tag it inspects is the one that was interpolated, never the one that was written down.
This check therefore reads both files as text and parses, rather than evaluating.

Five properties, over every `${*_VERSION...}` occurrence in every compose file:

* every reference carries a `:-` fallback — without one, a clone with no `.env`
  renders an empty tag;
* every referenced variable is declared in `.env.example` — an undeclared pin is
  invisible to `pixi run init` and so has no value to fall back from;
* neither side is empty — `${X_VERSION:-}` against `X_VERSION=` agrees perfectly and
  still renders `image: repo:`, which is the same untagged image the fallback rule
  exists to prevent;
* the fallback tag and the declared value are identical, at every occurrence — a pin
  may appear more than once (`SILO_VERSION` names both `minio` and `minio-init`), so
  every occurrence is compared, not just the first;
* every `*_VERSION` the template declares is referenced at least once, by *some*
  compose file — a declaration nothing reads is what a misspelled variable name and a
  literal tag written over the interpolation both look like, and comparing only in the
  compose-to-dotenv direction is blind to either.

A run that matched no pins is a failure, not a pass: a check that walked an empty set
has verified nothing, which is the silent skip this repository keeps removing.

Written in Python for the same reason as `lint_json.py`: it runs identically on every
platform `pixi.toml` declares. Diagnostics go to stderr because they are human-readable
tool output, not application logging.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: One `${NAME_VERSION}` or `${NAME_VERSION:-tag}` reference. `tag` is None for the
#: first form, which is itself a defect: the fallback is what a clone without .env runs.
PIN = re.compile(r"\$\{(?P<name>[A-Z0-9_]*[A-Z0-9]_VERSION)(?::-(?P<tag>[^}]*))?\}")

#: One `NAME=value` declaration in a dotenv file, ignoring blanks and comments.
DECLARATION = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")


def dotenv_declarations(path: Path) -> dict[str, str]:
    """Read every variable a dotenv file declares, as the shell would read it.

    A leading `export ` and a trailing `# comment` are both invisible to `source`,
    so they are stripped here too. A quoted value ends at its own closing quote, and
    everything after that quote is discarded — which is what makes `X="8-alpine"
    # pinned` read as `8-alpine` rather than as a tag with quotes still attached,
    while `X="a # b"` keeps the `#` the quotes protect. A later declaration wins,
    which is also what sourcing produces.

    Args:
        path: The dotenv file to read.

    Returns:
        Declared name to declared value, empty when the file declares nothing.

    Raises:
        RuntimeError: If the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"{path}: unreadable: {exc}") from exc
    declared: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = DECLARATION.match(line)
        if match is None:
            continue
        value = match.group("value").strip()
        # A quoted value ends at its closing quote, whatever follows it. Unquoting
        # must therefore happen before any comment is stripped, or a value that is
        # both quoted and commented keeps the quotes the shell would have removed.
        if value[:1] in ("'", '"'):
            closing = value.find(value[0], 1)
            if closing != -1:
                declared[match.group("name")] = value[1:closing]
                continue
        # An unquoted value ends at the first whitespace-preceded `#`.
        declared[match.group("name")] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return declared


def occurrences(path: Path) -> list[tuple[int, str, str | None]]:
    """Find every version-pin reference in a compose file.

    A commented-out line is not part of the model Compose renders, so a reference
    inside one is not a pin. Counting it would let the non-zero-match guard below be
    satisfied entirely by commented text — a file whose every real `image:` had lost
    its interpolation would still report pins compared.

    Args:
        path: The compose file to read.

    Returns:
        One `(line number, variable name, fallback tag or None)` per reference, in
        file order. The same variable appears once per occurrence.

    Raises:
        RuntimeError: If the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"{path}: unreadable: {exc}") from exc
    found: list[tuple[int, str, str | None]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for match in PIN.finditer(line):
            found.append((number, match.group("name"), match.group("tag")))
    return found


def label(path: Path) -> str:
    """Name a file the way a reader can act on it.

    Every module compose file is called `compose.yaml`, so a basename no longer
    identifies one: `compose.yaml:31` could be the root file or any of thirteen
    modules. The repo-relative path is used where the file sits inside the
    repository, and the path exactly as it was given otherwise — which is what
    keeps a diagnostic over a throwaway fixture pointing at that fixture.

    Args:
        path: The file to name.

    Returns:
        The repo-relative path, or the path as given when it lies outside.
    """
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def check(composes: list[Path], dotenv: Path) -> tuple[int, list[str]]:
    """Compare every pin reference in every compose file against its declaration.

    Agreement is not enough on its own. Two pins that agree on the empty string
    still render `image: repo:`, so an empty tag is rejected on either side rather
    than compared. And a variable the template declares but no compose file
    references is reported too: that is what a misspelled variable name, or a
    literal tag written where the interpolation used to be, both look like — the
    declaration keeps the pin looking maintained while nothing reads it.

    That last scan runs over the *union* of every file's references, never one file
    at a time. A service extracted into `services/<name>/compose.yaml` takes its pin
    with it, so a per-file pass would report the pin missing from the root file and
    the twelve declarations missing from the module — the check would go red on a
    correct repository, which trains the habit of ignoring it.

    Args:
        composes: The compose files holding the `${NAME_VERSION:-tag}` fallbacks.
        dotenv: The template holding the `NAME_VERSION=tag` declarations.

    Returns:
        The number of references compared, and one diagnostic per violation.

    Raises:
        RuntimeError: If any file cannot be read.
    """
    declared = dotenv_declarations(dotenv)
    found: list[tuple[Path, int, str, str | None]] = [
        (compose, number, name, tag) for compose in composes for number, name, tag in occurrences(compose)
    ]
    problems: list[str] = []
    for compose, number, name, tag in found:
        where = f"{label(compose)}:{number}"
        if tag is None:
            problems.append(f"{where}: '{name}' has no ':-' fallback — a clone with no .env would render an empty tag")
            continue
        if name not in declared:
            problems.append(
                f"{where}: '{name}' is not declared in {dotenv.name} — "
                f"an undeclared pin never reaches a contributor's .env"
            )
            continue
        if not tag or not declared[name]:
            problems.append(
                f"{where}: '{name}' resolves to an empty tag "
                f"({dotenv.name} says '{declared[name]}', the fallback is '{tag}') — "
                f"the image would render as 'repo:' with no tag at all"
            )
            continue
        if declared[name] != tag:
            problems.append(
                f"'{name}' disagrees: {dotenv.name} says '{declared[name]}', "
                f"{where} falls back to '{tag}' — both must move together"
            )

    referenced = {name for _, _, name, _ in found}
    named = ", ".join(label(compose) for compose in composes)
    for name in sorted(declared):
        if name.endswith("_VERSION") and name not in referenced:
            problems.append(
                f"{dotenv.name}: '{name}' is declared but no compose file references it "
                f"({named}) — a pin nothing reads is either a misspelled name or a tag "
                f"written literally where the interpolation belongs"
            )
    return len(found), problems


def main(argv: list[str]) -> int:
    """Assert every pin in every compose file agrees with `.env.example`.

    The file set defaults to this repository's own — the root compose file plus every
    `services/*/compose.yaml` a module contributes — which is what the task runs. A
    caller may name a different set, dotenv last, and the self-test drives the tool
    over throwaway fixtures that way, so proving it fails on drift never involves
    editing a tracked file in place.

    Args:
        argv: Either empty, or one or more compose files followed by the dotenv
            template. The dotenv template is always the last argument.

    Returns:
        Process exit status: 0 when every pin agrees, 1 otherwise.
    """
    if len(argv) == 1:
        sys.stderr.write("assert-pins: usage: assert_pins.py [<compose file>... <dotenv file>]\n")
        return 1
    if argv:
        composes = [Path(arg) for arg in argv[:-1]]
        dotenv = Path(argv[-1])
    else:
        composes = [REPO / "compose.yaml", *sorted((REPO / "services").glob("*/compose.yaml"))]
        dotenv = REPO / ".env.example"
    for path in (*composes, dotenv):
        if not path.is_file():
            sys.stderr.write(f"assert-pins: no such file: {path}\n")
            return 1

    try:
        counted, problems = check(composes, dotenv)
    except RuntimeError as exc:
        sys.stderr.write(f"assert-pins: {exc}\n")
        return 1

    if counted == 0:
        sys.stderr.write(
            f"assert-pins: checked no pins — {', '.join(label(path) for path in composes)} "
            "reference no *_VERSION variable, so this pass verified nothing\n"
        )
        return 1

    if problems:
        for problem in problems:
            sys.stderr.write(f"assert-pins: {problem}\n")
        return 1

    sys.stdout.write(f"assert-pins: OK {counted} pin references agree with {dotenv.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

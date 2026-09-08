#!/usr/bin/env python3
"""Assert every Module's gotchas register carries entries in the checked four-field shape.

A gotcha written as free prose is a gotcha nobody can act on: the bullet says what went
wrong and leaves out what you would have seen, or names the fix and never says which
versions it applies to. Presence alone — which is all `lint-config` asks of a
`gotchas.md`, deliberately, under ADR 0012 — cannot tell those apart from a complete
entry.

This checks the shape and nothing else. Each entry is a `### ` heading followed by
`- **Symptom:**`, `- **Cause:**`, `- **Fix:**` and `- **Affected versions:**`, in that
order, each populated, optionally followed by `- **Verified by:**` naming the check that
catches a regression. Whether the sentence after `**Fix:**` is any good is still a
review's job — a field is present or it is not, which is the difference between this and
the minimum-length rule ADR 0012 rejected as unfalsifiable. See
docs/adr/0016-gotcha-entries-carry-a-checked-shape.md.

    python scripts/check_gotchas.py

One `<module>: OK <n> entries` line per Module on stdout, every defect as a
`check-gotchas: …` line on stderr, and exit 0 only when every file is clean. The Modules
are found with a glob over `services/*/gotchas.md` and an empty set is refused, exactly as
`assert_config.py` and `smoke-test.sh` refuse one: a check that walked nothing has
verified nothing, and Core gains no list of Modules either way.

Stdlib only, and no container runtime: this reads Markdown, so it runs in the pre-commit
hook where `lint-config` cannot.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: The fields every entry carries, in the order they must appear.
REQUIRED_FIELDS: tuple[str, ...] = ("Symptom", "Cause", "Fix", "Affected versions")

#: The one optional field, and it may only follow the four required ones.
OPTIONAL_FIELD = "Verified by"

#: The exact phrase that stands in for a version expression when a failure mode is not
#: version-bound. Spelled out rather than left to any wording, so "all versions", "any"
#: and "every release" cannot each mean the same thing in a different file.
NOT_VERSION_SPECIFIC = "Not version-specific"

#: Values that fill a field without populating it. Compared case-insensitively against the
#: whole value with surrounding punctuation stripped, so `TBD` and `(tbd)` are both caught
#: while a sentence that merely contains the word "unknown" is not.
PLACEHOLDERS: frozenset[str] = frozenset({"tbd", "todo", "unknown", "n/a", "na", "none", "?", "-", "…", "..."})

#: A field bullet: `- **Name:** value`, with the value running to the end of the line and
#: continuing on any indented line below it.
BULLET = re.compile(r"^- \*\*(?P<name>[^*]+?):\*\*(?P<value>.*)$")

#: An entry heading. The title is the claim, so the file still skims like the prose it
#: replaces.
ENTRY_HEADING = re.compile(r"^###\s+(?P<title>.*\S)\s*$")

#: A `Verified by:` value opens with the check's path in backticks. Anything after it is
#: prose for a reader and is not inspected.
VERIFIED_PATH = re.compile(r"^`(?P<path>[^`]+)`")


@dataclass(frozen=True)
class Field:
    """One `- **Name:** value` bullet under an entry.

    Attributes:
        name: The bolded field name, without the colon.
        value: Everything after the colon, with continuation lines joined by spaces.
        line: 1-based line number the bullet opened on.
    """

    name: str
    value: str
    line: int


@dataclass(frozen=True)
class Entry:
    """One `### ` heading and the field bullets beneath it.

    Attributes:
        title: The heading text, as a reader sees it.
        line: 1-based line number of the heading.
        fields: The field bullets, in the order they appear.
    """

    title: str
    line: int
    fields: tuple[Field, ...]


class Refusal(Exception):
    """Raised when the check cannot be run at all, rather than run and pass."""


def is_placeholder(value: str) -> bool:
    """Say whether a field value fills the field without populating it.

    Args:
        value: The value as written, already stripped.

    Returns:
        True when the whole value is one of the placeholder tokens.
    """
    return value.strip(" .,:;()[]<>`\"'").casefold() in PLACEHOLDERS


def parse(text: str) -> tuple[list[Entry], list[str], str | None]:
    """Split a register into its heading, its entries and whatever sits outside them.

    Args:
        text: The file's full contents.

    Returns:
        The entries in file order, one message per structural defect found while
        splitting, and the H1 line as written (None when the file opens on something
        else).
    """
    lines = text.splitlines()
    # A field's value is its bullet line plus every indented line under it — which is how
    # every entry in the register wraps — so the joining is done once, up front, and the
    # walk below only has to recognise where a bullet starts.
    values = _values(lines)
    problems: list[str] = []
    heading: str | None = None
    entries: list[Entry] = []
    title: str | None = None
    title_line = 0
    fields: list[Field] = []

    def close_entry() -> None:
        if title is not None:
            entries.append(Entry(title=title, line=title_line, fields=tuple(fields)))

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("#"):
            if heading is None and title is None:
                heading = stripped
                continue
            match = ENTRY_HEADING.match(line)
            if match is None:
                problems.append(f"line {number}: {stripped!r} is neither the H1 nor a `### ` entry heading")
                continue
            close_entry()
            title, title_line, fields = match.group("title"), number, []
            continue
        bullet = BULLET.match(line)
        if bullet is None:
            continue
        if title is None:
            problems.append(f"line {number}: {stripped!r} is a field bullet outside any entry")
            continue
        fields.append(Field(name=bullet.group("name").strip(), value=values[number], line=number))
    close_entry()
    return entries, problems, heading


def _values(lines: list[str]) -> dict[int, str]:
    """Join every field bullet with the indented lines that continue it.

    Args:
        lines: The file's lines, without their endings.

    Returns:
        The joined value for each bullet, keyed by the bullet's 1-based line number.
    """
    values: dict[int, str] = {}
    current: int | None = None
    for number, line in enumerate(lines, start=1):
        bullet = BULLET.match(line)
        if bullet is not None:
            current = number
            values[number] = bullet.group("value").strip()
            continue
        if current is not None and line.strip() and line.startswith(" ") and not line.lstrip().startswith("#"):
            values[current] = f"{values[current]} {line.strip()}".strip()
            continue
        current = None
    return values


def check_field_order(entry: Entry) -> list[str]:
    """Assert an entry carries exactly the four fields, in order, plus the optional fifth.

    Args:
        entry: The entry to inspect.

    Returns:
        One message per defect, empty when the entry's field sequence is the shape.
    """
    names = [field.name for field in entry.fields]
    problems: list[str] = []
    missing = [name for name in REQUIRED_FIELDS if name not in names]
    if missing:
        problems.append(f"entry {entry.title!r} (line {entry.line}) has no {', '.join(missing)} field")
    unknown = sorted({name for name in names if name not in REQUIRED_FIELDS and name != OPTIONAL_FIELD})
    if unknown:
        problems.append(f"entry {entry.title!r} (line {entry.line}) carries unknown field(s) {', '.join(unknown)}")
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        problems.append(f"entry {entry.title!r} (line {entry.line}) repeats field(s) {', '.join(repeated)}")
    if problems:
        return problems
    expected = list(REQUIRED_FIELDS) + ([OPTIONAL_FIELD] if OPTIONAL_FIELD in names else [])
    if names != expected:
        problems.append(
            f"entry {entry.title!r} (line {entry.line}) lists its fields as {', '.join(names)}; "
            f"the expected order is {', '.join(expected)}"
        )
    return problems


def check_values(entry: Entry, repo_root: Path) -> list[str]:
    """Assert every field of an entry is populated, and that a named check exists.

    Args:
        entry: The entry to inspect; its field sequence is already known to be the shape.
        repo_root: Directory a `Verified by:` path is resolved against.

    Returns:
        One message per defect, empty when every value is populated and resolvable.
    """
    problems: list[str] = []
    for field in entry.fields:
        where = f"entry {entry.title!r} (line {field.line}) field {field.name}"
        if not field.value:
            problems.append(f"{where} is empty")
            continue
        if is_placeholder(field.value):
            problems.append(f"{where} is the placeholder {field.value!r}")
            continue
        if field.name == "Affected versions" and field.value != NOT_VERSION_SPECIFIC:
            if not any(character.isdigit() for character in field.value):
                problems.append(
                    f"{where} is {field.value!r}, which names no version — write a version "
                    f"expression or the exact phrase {NOT_VERSION_SPECIFIC!r}"
                )
        if field.name == OPTIONAL_FIELD:
            match = VERIFIED_PATH.match(field.value)
            if match is None:
                problems.append(f"{where} must open with the check's repo-relative path in backticks")
            elif not (repo_root / match.group("path")).is_file():
                problems.append(f"{where} names {match.group('path')}, which is not a file that exists")
    return problems


def check_file(path: Path, repo_root: Path) -> tuple[int, list[str]]:
    """Check one Module's register.

    Args:
        path: The `services/<module>/gotchas.md` file.
        repo_root: Directory a `Verified by:` path is resolved against.

    Returns:
        The number of entries found, and one message per defect.
    """
    module = path.parent.name
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, [f"{path}: unreadable: {exc}"]
    entries, problems, heading = parse(text)
    expected_heading = f"# {module} — gotchas"
    if heading != expected_heading:
        problems.insert(0, f"opens on {heading!r}; the H1 must be {expected_heading!r}")
    if not entries:
        problems.append("carries no `### ` entry — an empty register is the silent skip in file form")
    for entry in entries:
        order = check_field_order(entry)
        problems.extend(order)
        if not order:
            problems.extend(check_values(entry, repo_root))
    return len(entries), [f"{path}: {problem}" for problem in problems]


def registers(services_dir: Path) -> list[Path]:
    """Find every Module's register.

    Args:
        services_dir: The directory holding one subdirectory per Module.

    Returns:
        The `gotchas.md` files, in Module order.

    Raises:
        Refusal: If the glob matched nothing.
    """
    found = sorted(services_dir.glob("*/gotchas.md"))
    if not found:
        raise Refusal(
            f"{services_dir}/*/gotchas.md matched nothing — the walk was empty, and a check "
            f"that walked nothing has verified nothing"
        )
    return found


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Returns:
        The parser, with every default stated so the task body passes nothing.
    """
    parser = argparse.ArgumentParser(
        prog="check_gotchas.py",
        description="Assert every Module's gotchas.md carries entries in the checked four-field shape.",
    )
    parser.add_argument("--services-dir", default="services", help="directory holding one subdirectory per Module")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="directory a `Verified by:` path is resolved against; defaults to the services directory's parent",
    )
    return parser


def main(argv: list[str]) -> int:
    """Check every Module's register and report one line each.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        Process exit status: 0 only when every register is in shape.
    """
    args = build_parser().parse_args(argv)
    services_dir = Path(args.services_dir)
    repo_root = Path(args.repo_root) if args.repo_root is not None else services_dir.parent
    try:
        paths = registers(services_dir)
    except Refusal as exc:
        sys.stderr.write(f"check-gotchas: {exc}\n")
        return 1
    status = 0
    for path in paths:
        count, problems = check_file(path, repo_root)
        if problems:
            status = 1
            for problem in problems:
                sys.stderr.write(f"check-gotchas: {problem}\n")
            continue
        sys.stdout.write(f"{path.parent.name}: OK {count} entries\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

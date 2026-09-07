#!/usr/bin/env python3
"""Assert the Renovate configuration actually detects this repository's image pins.

Renovate proposes image updates here through a `customManagers` regex over the annotated
`<NAME>_VERSION=` declarations in `.env.example` and the matching `${<NAME>_VERSION:-tag}`
fallbacks in the compose files — the root one and every `services/<name>/compose.yaml` a
module contributes. Neither of Renovate's stock managers can do it: the
`docker-compose` manager skips the `repo:${VAR:-tag}` form entirely, and Dependabot cannot
read a dotenv file at all. See
`docs/adr/0010-image-updates-are-proposed-by-regex-over-the-dotenv-template.md`.

A regex manager fails silently. A pattern that matches nothing produces no dependencies, no
pull requests and no error — the bot reports a clean run having proposed nothing, which is
indistinguishable from "everything is current". That is exactly the silent skip this
repository keeps removing, so the configuration is checked here rather than trusted.

The check applies the configuration's **own** `matchStrings` to the files its own
`managerFilePatterns` select. A check carrying a private copy of the regexes would keep
passing after the configuration's regexes were broken, which is the failure it exists to
catch.

Renovate runs those patterns under RE2 with JavaScript syntax; this check runs them under
Python's `re`. The two agree on everything these patterns use, and the only rewrite is
`(?<name>` to Python's `(?P<name>`. They do **not** agree everywhere: RE2 has no
lookaround and no backreferences, so a pattern using either would match here and be
rejected by Renovate — a green check over a bot that extracts nothing. Those constructs
are therefore refused outright rather than translated. Differences beyond them (Unicode
class semantics, the exact backtracking a pathological pattern provokes) are not covered,
which is why the README still documents `--dry-run=extract` as the authority.

The properties asserted:

* the configuration parses as strict JSON — Renovate accepts JSON5, but a file this check
  cannot read is a file whose regexes cannot be proved to match anything;
* every enabled manager is `custom.regex` — enabling `docker-compose` or `github-actions`
  alongside would make the detected-dependency count unattributable to these patterns;
* every `matchStrings` pattern matches at least once, resolves a `docker` datasource and a
  versioning, and yields no match with an empty `depName`, variable or `currentValue`;
* `matchStringsStrategy` is `any` — `combination` and `recursive` compose the patterns
  differently, so this check would be reproducing an extraction Renovate does not perform;
* every `*_VERSION` declaration in `.env.example` is immediately preceded by its
  `# renovate:` annotation, and is detected in the dotenv *and* in some compose file — a
  pin whose service moved into a module and whose module the patterns do not select is
  detected in one half only, which is the one-file pull request `lint-pins` rejects;
* the annotated `depName` is the image repository the compose half names for the same
  variable, and an annotation carrying `versioning=` has a `packageRules` entry restating
  it byte-for-byte — the compose half has no annotation to read, so without the
  restatement the two halves compute different updates and the bot edits one file alone;
* nothing re-enables grouping: no `extends`, and no `packageRules` entry naming a
  `groupName` — one image per pull request is what keeps a red run attributable;
* `ci.yml`'s `pull_request` trigger targets `main` and carries no `paths`, `paths-ignore`,
  `types` or `branches-ignore` filter — a bot pull request that fell through any of them
  would be merged having run nothing.

Offline by construction: no network, no Node, no platform token. `npx renovate
--platform=local --dry-run=extract` is the real thing and is documented in the README, but
it needs all three, so it is evidence a maintainer gathers rather than a task CI reaches.

Diagnostics go to stderr because they are human-readable tool output, not application
logging.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

import yaml

REPO = Path(__file__).resolve().parent.parent

#: A JavaScript-style named group, as Renovate writes them. Python needs the `P`.
JS_GROUP = re.compile(r"\(\?<(?P<name>[A-Za-z_][A-Za-z0-9_]*)>")

#: One `NAME=value` declaration in a dotenv file. Mirrors `assert_pins.py`, which is the
#: dotenv-reading precedent in this repository.
DECLARATION = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")

#: The annotation line a declaration must be preceded by.
ANNOTATION = re.compile(r"^#\s*renovate:\s")

#: Constructs Python's `re` accepts and RE2 does not. A pattern using one of these matches
#: here and is rejected by the bot, which is a green check over an extraction that never
#: happens — the exact silent skip this file exists to catch.
RE2_UNSUPPORTED = (
    ("(?=", "lookahead"),
    ("(?!", "negative lookahead"),
    ("(?<=", "lookbehind"),
    ("(?<!", "negative lookbehind"),
)

#: A backreference, which RE2 also rejects. Matched separately because it is not a literal.
BACKREFERENCE = re.compile(r"(?<!\\)\\[1-9]")

#: The one datasource this repository's pins can have. Anything else means the manager is
#: resolving something other than a container image.
DATASOURCE = "docker"


class Detection(NamedTuple):
    """One dependency reference a manager's pattern matched.

    Attributes:
        source: Name of the file the match came from.
        variable: The `*_VERSION` variable the reference belongs to.
        dep_name: The image repository the match names.
        current_value: The tag the match names.
        versioning: The versioning the match captured, empty when it captured none.
    """

    source: str
    variable: str
    dep_name: str
    current_value: str
    versioning: str


def translate(pattern: str) -> re.Pattern[str]:
    """Compile a Renovate `matchStrings` pattern with Python's regex engine.

    One rewrite only: Renovate writes a named group JavaScript-style, `(?<name>...)`, and
    Python spells the same construct `(?P<name>...)`. Nothing else is touched, because the
    point is to run the configuration's regex rather than a paraphrase of it.

    That equivalence has a boundary. Renovate compiles under RE2, which supports neither
    lookaround nor backreferences; Python supports both. A pattern using one would match
    here and be refused there, leaving this check green over a bot that extracts nothing —
    so such a pattern is refused rather than translated. Remaining differences between the
    two engines (Unicode class semantics, backtracking behaviour) are not modelled, which
    is why `--dry-run=extract` stays the documented authority.

    Args:
        pattern: The pattern exactly as `renovate.json` declares it.

    Returns:
        The compiled pattern.

    Raises:
        RuntimeError: If the pattern uses a construct RE2 rejects, or does not compile.
    """
    for construct, name in RE2_UNSUPPORTED:
        if construct in pattern:
            raise RuntimeError(
                f"matchStrings pattern uses {name} ('{construct}'), which RE2 rejects: {pattern!r} — "
                f"it would match here and extract nothing under the bot"
            )
    if BACKREFERENCE.search(pattern):
        raise RuntimeError(
            f"matchStrings pattern uses a backreference, which RE2 rejects: {pattern!r} — "
            f"it would match here and extract nothing under the bot"
        )
    try:
        return re.compile(JS_GROUP.sub(r"(?P<\g<name>>", pattern))
    except re.error as exc:
        raise RuntimeError(f"matchStrings pattern does not compile: {pattern!r}: {exc}") from exc


def candidate_names(path: Path, base: Path) -> list[str]:
    r"""Give the names a `managerFilePatterns` entry could be written against.

    Renovate matches these patterns against the repository-relative path and nothing
    else, so that is what is offered here: the path computed against the directory
    holding the configuration, which stands in for the repository root. The basename is
    a fallback for a file that lies outside that directory and therefore has no relative
    path at all.

    Offering the basename as well would be laxer than the bot. Every module compose file
    is called `compose.yaml`, so `/^compose\.yaml$/` would appear to select
    `services/postgres/compose.yaml` here while Renovate — anchoring on the relative path
    — selects only the root file. The configuration would look complete while the bot saw
    one half of every module's pin, which is the one-file pull request lint-pins rejects.

    Args:
        path: The candidate file.
        base: The directory the configuration sits in, standing in for the repository root.

    Returns:
        The names to try: the relative path, or the basename when there is none.
    """
    try:
        return [path.resolve().relative_to(base.resolve()).as_posix()]
    except ValueError:
        return [path.name]


def source_name(path: Path, base: Path) -> str:
    """Name a file the way `managerFilePatterns` and a reader both see it.

    Every module compose file is called `compose.yaml`, so the basename identifies
    none of them: `Detection.source` has to carry the repo-relative path or the
    dotenv/compose split below cannot tell the root file from a module's.

    Args:
        path: The file to name.
        base: The directory the configuration sits in.

    Returns:
        The repo-relative path, or the basename for a file outside the repository.
    """
    return candidate_names(path, base)[0]


def selects(pattern: str, path: Path, base: Path) -> bool:
    """Decide whether one `managerFilePatterns` entry selects one file.

    Renovate accepts two forms: a regex delimited by slashes, and a glob.

    Args:
        pattern: One `managerFilePatterns` entry.
        path: The candidate file.
        base: The directory the configuration sits in.

    Returns:
        True when the pattern selects the file.

    Raises:
        RuntimeError: If a slash-delimited pattern does not compile.
    """
    names = candidate_names(path, base)
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        body = pattern[1:-1]
        try:
            return any(re.search(body, name) is not None for name in names)
        except re.error as exc:
            raise RuntimeError(f"managerFilePatterns entry does not compile: {pattern!r}: {exc}") from exc
    return any(fnmatch.fnmatch(name, pattern) for name in names)


def read_text(path: Path) -> str:
    """Read a file, turning every decoding failure into a named diagnostic.

    Args:
        path: The file to read.

    Returns:
        The file's text.

    Raises:
        RuntimeError: If the file cannot be read or is not UTF-8.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{path}: not valid UTF-8 at byte {exc.start}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"{path}: unreadable: {exc}") from exc


def live_text(path: Path) -> str:
    r"""Read a file with every commented-out line blanked, keeping line positions.

    A commented-out `image:` line is not part of the model Compose renders, so a pin inside
    one is not a pin — `assert_pins.occurrences()` excludes them for the same reason, and
    without this a compose file whose every real interpolation had been replaced by a
    literal tag would still report a full match set. The `# renovate:` annotations are the
    one exception: in `.env.example` the comment *is* the declaration's metadata, and
    blanking it would delete the thing being checked.

    Lines are emptied rather than removed so line numbering and the `\n` a two-line pattern
    matches across both survive.

    Args:
        path: The file to read.

    Returns:
        The text, with non-annotation comment lines emptied.

    Raises:
        RuntimeError: If the file cannot be read or is not UTF-8.
    """
    kept = [
        line if not line.lstrip().startswith("#") or ANNOTATION.match(line.lstrip()) else ""
        for line in read_text(path).splitlines()
    ]
    return "\n".join(kept) + "\n"


def load_config(path: Path) -> dict[str, Any]:
    """Parse the Renovate configuration.

    Args:
        path: The configuration file.

    Returns:
        The parsed configuration.

    Raises:
        RuntimeError: If the file cannot be read, is not UTF-8, is not valid JSON, or is
            valid JSON that is not an object.
    """
    text = read_text(path)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{path}: parsed as {type(parsed).__name__}, not a configuration object")
    return parsed


def declarations(dotenv: Path) -> list[tuple[int, str, bool]]:
    """Find every `*_VERSION` declaration and say whether it is annotated.

    Adjacency is the contract, not mere presence: an annotation two lines above a
    declaration is read by Renovate as belonging to whatever sits between them, so the
    preceding line is what is inspected.

    Args:
        dotenv: The dotenv template to read.

    Returns:
        One `(line number, variable name, annotated)` per declaration, in file order.

    Raises:
        RuntimeError: If the file cannot be read.
    """
    lines = read_text(dotenv).splitlines()
    found: list[tuple[int, str, bool]] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = DECLARATION.match(line)
        if match is None or not match.group("name").endswith("_VERSION"):
            continue
        previous = lines[index - 1].strip() if index else ""
        found.append((index + 1, match.group("name"), bool(ANNOTATION.match(previous))))
    return found


def detect(config: dict[str, Any], candidates: list[Path], base: Path) -> tuple[list[Detection], list[str]]:
    """Run every enabled manager's own patterns over the files its own patterns select.

    Args:
        config: The parsed Renovate configuration.
        candidates: The files a manager may select from.
        base: The directory the configuration sits in, for resolving file patterns.

    Returns:
        Every reference matched, and one diagnostic per configuration defect.

    Raises:
        RuntimeError: If a pattern cannot be compiled or a selected file cannot be read.
    """
    problems: list[str] = []
    detections: list[Detection] = []

    enabled = config.get("enabledManagers")
    if not isinstance(enabled, list) or not enabled:
        problems.append(
            "enabledManagers is missing or empty — every stock manager would run and the count would be unattributable"
        )
    else:
        for name in enabled:
            if name != "custom.regex":
                problems.append(
                    f"enabledManagers names '{name}' — only 'custom.regex' may be enabled, or the "
                    f"detected-dependency count stops being attributable to these patterns"
                )

    # Grouping is what makes a red run attributable to one image, so it is asserted off in
    # both places it could come back: a preset that batches updates, and a rule that names a
    # group directly. `groupName: null` is the explicit off switch and stays allowed.
    if "extends" in config:
        problems.append(
            f"the configuration extends {config['extends']!r} — a preset can restore grouping, "
            f"and a pull request carrying two images makes a red run unattributable"
        )
    rules = config.get("packageRules")
    for position, rule in enumerate(rules if isinstance(rules, list) else []):
        if isinstance(rule, dict) and rule.get("groupName") is not None:
            problems.append(
                f"packageRules[{position}] sets groupName {rule['groupName']!r} — one image per "
                f"pull request is what this repository promises and what keeps a break attributable"
            )

    managers = config.get("customManagers")
    if not isinstance(managers, list) or not managers:
        problems.append("customManagers is missing or empty — nothing would detect an image pin")
        return detections, problems

    for position, manager in enumerate(managers):
        label = f"customManagers[{position}]"
        if not isinstance(manager, dict):
            problems.append(f"{label} is not an object")
            continue
        if manager.get("customType") != "regex":
            problems.append(f"{label}: customType is {manager.get('customType')!r}, not 'regex'")

        # `combination` concatenates the patterns into one match and `recursive` nests them,
        # so either would make this check reproduce an extraction the bot does not perform.
        strategy = manager.get("matchStringsStrategy", "any")
        if strategy != "any":
            problems.append(
                f"{label}: matchStringsStrategy is {strategy!r} — this check applies each pattern "
                f"independently, which reproduces 'any' only; under {strategy!r} the bot extracts a "
                f"different set and a green run here would say nothing about it"
            )

        file_patterns = manager.get("managerFilePatterns")
        if not isinstance(file_patterns, list) or not file_patterns:
            problems.append(f"{label}: managerFilePatterns is missing or empty — it would select no file")
            continue
        selected = [path for path in candidates if any(selects(str(entry), path, base) for entry in file_patterns)]
        if not selected:
            problems.append(
                f"{label}: managerFilePatterns {file_patterns} select none of "
                f"{[source_name(path, base) for path in candidates]} — the manager reads nothing"
            )
            continue

        match_strings = manager.get("matchStrings")
        if not isinstance(match_strings, list) or not match_strings:
            problems.append(f"{label}: matchStrings is missing or empty")
            continue

        # A datasource and a versioning are what turn a matched string into a dependency
        # Renovate can look up. Missing either, the manager extracts references it cannot
        # resolve — no proposal, no error, and this check green over both.
        datasource_template = manager.get("datasourceTemplate")
        if datasource_template is not None and datasource_template != DATASOURCE:
            problems.append(
                f"{label}: datasourceTemplate is {datasource_template!r}, not {DATASOURCE!r} — "
                f"these pins are container images and nothing else can resolve them"
            )
        versioning_template = manager.get("versioningTemplate")
        if not versioning_template:
            problems.append(
                f"{label}: no versioningTemplate — every reference would fall back to the default "
                f"versioning, and an annotation's 'versioning=' value would be inert"
            )
        captures_versioning = any("<versioning>" in str(pattern) for pattern in match_strings)
        if captures_versioning and "versioning" not in str(versioning_template or ""):
            problems.append(
                f"{label}: a matchStrings pattern captures 'versioning' but versioningTemplate "
                f"{versioning_template!r} never reads it — the annotation's value would be discarded"
            )

        for pattern in match_strings:
            text_pattern = str(pattern)
            if "<datasource>" not in text_pattern and datasource_template is None:
                problems.append(
                    f"{label}: pattern {text_pattern!r} captures no 'datasource' and the manager "
                    f"declares no datasourceTemplate — the reference resolves to no registry at all"
                )
            compiled = translate(text_pattern)
            hits = 0
            for path in selected:
                for match in compiled.finditer(live_text(path)):
                    groups = match.groupdict()
                    hits += 1
                    found = Detection(
                        source=source_name(path, base),
                        variable=str(groups.get("varName") or ""),
                        dep_name=str(groups.get("depName") or ""),
                        current_value=str(groups.get("currentValue") or ""),
                        versioning=str(groups.get("versioning") or ""),
                    )
                    # An empty capture is a match that carries nothing: Renovate would drop
                    # the reference, and counting it here would report coverage this
                    # configuration does not have.
                    empty = [
                        field
                        for field, value in (
                            ("variable", found.variable),
                            ("depName", found.dep_name),
                            ("currentValue", found.current_value),
                        )
                        if not value
                    ]
                    if empty:
                        problems.append(
                            f"{label}: pattern {text_pattern!r} matched in {source_name(path, base)} with no "
                            f"{', '.join(empty)} — the group is missing from the pattern or captured "
                            f"nothing, so the reference carries no dependency"
                        )
                        continue
                    captured = str(groups.get("datasource") or "") or str(datasource_template or "")
                    if captured != DATASOURCE:
                        problems.append(
                            f"{label}: pattern {text_pattern!r} matched in {source_name(path, base)} resolving "
                            f"datasource {captured!r}, not {DATASOURCE!r}"
                        )
                        continue
                    detections.append(found)
            if hits == 0:
                problems.append(
                    f"{label}: matchStrings pattern {text_pattern!r} matched nothing in "
                    f"{[source_name(path, base) for path in selected]} — a manager whose regex matches "
                    f"nothing proposes nothing and still reports a clean run"
                )
    return detections, problems


def pull_request_trigger(workflow: Path) -> list[str]:
    """Assert the CI gate still runs on every pull request a bot could open.

    A bot pull request is only validated because `ci.yml` has an unfiltered `pull_request`
    trigger on `main`. Every narrowing of that trigger is refused, because each one breaks
    the promise differently: `paths`/`paths-ignore` let a `.env.example`-only diff merge
    having run nothing; `branches-ignore` can exclude `main` while `branches` still names
    it; and `types` replaces the default set, so a run that dropped `synchronize` would
    never re-check the branch after Renovate rebases and force-pushes it — the gate would
    have passed on a commit that is no longer the head.

    Args:
        workflow: The CI workflow file.

    Returns:
        One diagnostic per defect, empty when the trigger is unfiltered.

    Raises:
        RuntimeError: If the file cannot be read or does not parse as YAML.
    """
    try:
        parsed: object = yaml.safe_load(read_text(workflow))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"{workflow.name}: does not parse as YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{workflow.name}: parsed as {type(parsed).__name__}, not a workflow")

    # `on` is a YAML 1.1 boolean, so PyYAML hands the key back as True unless it is quoted.
    triggers = parsed.get("on", parsed.get(True))
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        return [f"{workflow.name}: declares no 'pull_request' trigger — a bot pull request would run no checks at all"]
    event = triggers["pull_request"]
    if event is None:
        event = {}
    if not isinstance(event, dict):
        return [f"{workflow.name}: 'pull_request' is {event!r}, not a trigger block"]

    problems: list[str] = []
    for filter_name in ("paths", "paths-ignore", "types", "branches-ignore"):
        if filter_name in event:
            problems.append(
                f"{workflow.name}: 'pull_request' declares a '{filter_name}' filter "
                f"({event[filter_name]!r}) — a narrowed trigger cannot promise to run on every "
                f"bot pull request, and one that falls through it merges having run nothing"
            )
    branches = event.get("branches")
    if not isinstance(branches, list) or "main" not in branches:
        problems.append(
            f"{workflow.name}: 'pull_request' targets {branches!r}, not ['main'] — the bot opens "
            f"its pull requests against main"
        )
    return problems


def restated_versionings(config: dict[str, Any]) -> dict[str, set[str]]:
    """Collect the versioning each `packageRules` entry states for each package.

    Args:
        config: The parsed Renovate configuration.

    Returns:
        Package name to the set of versionings rules state for it.
    """
    stated: dict[str, set[str]] = {}
    rules = config.get("packageRules")
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict) or not rule.get("versioning"):
            continue
        names = rule.get("matchPackageNames")
        for name in names if isinstance(names, list) else []:
            stated.setdefault(str(name), set()).add(str(rule["versioning"]))
    return stated


def check(config_path: Path, composes: list[Path], dotenv: Path, workflow: Path) -> tuple[int, int, list[str]]:
    """Prove the configuration detects every pin, in the dotenv and in some compose file.

    The compose half is a list, not a file. Every service was extracted into
    `services/<name>/compose.yaml`, so a pin's compose half lives in one of them and
    `managerFilePatterns` has to select every one — a pattern set that reached only the
    root file would leave the bot editing `.env.example` alone for every service, which
    is the one-file pull request `lint-pins` rejects.

    Args:
        config_path: The Renovate configuration.
        composes: The compose files holding the `${NAME_VERSION:-tag}` fallbacks.
        dotenv: The template holding the annotated `NAME_VERSION=tag` declarations.
        workflow: The CI workflow whose `pull_request` trigger validates a bot pull request.

    Returns:
        The number of distinct variables detected, the number of references they were
        detected from, and one diagnostic per violation.

    Raises:
        RuntimeError: If a file cannot be read or a pattern cannot be compiled.
    """
    config = load_config(config_path)
    base = config_path.parent
    detections, problems = detect(config, [dotenv, *composes], base)
    stated = restated_versionings(config)

    dotenv_source = source_name(dotenv, base)
    compose_sources = {source_name(path, base) for path in composes}
    named = ", ".join(sorted(compose_sources))

    from_dotenv = {item.variable: item for item in detections if item.source == dotenv_source}
    # Keyed by (source, dep_name), not dep_name alone: every module file is called
    # compose.yaml, so a diagnostic that says "a compose file" names none of them.
    from_compose: dict[str, set[tuple[str, str]]] = {}
    for item in detections:
        if item.source in compose_sources:
            from_compose.setdefault(item.variable, set()).add((item.source, item.dep_name))

    for number, name, annotated in declarations(dotenv):
        if not annotated:
            problems.append(
                f"{dotenv.name}:{number}: '{name}' is not preceded by a "
                f"'# renovate: datasource=docker depName=<repo>' line — Renovate cannot see a pin "
                f"it is not told about, and would propose no update for it"
            )
        if name not in from_dotenv:
            problems.append(
                f"{dotenv.name}:{number}: '{name}' is declared but no manager pattern detects it — "
                f"the annotation is missing or malformed"
            )
            continue
        if name not in from_compose:
            problems.append(
                f"{dotenv.name}:{number}: '{name}' is detected in {dotenv.name} but in no "
                f"compose file ({named}) — a pull request would move one file alone, which "
                f"lint-pins rejects"
            )
            continue
        annotated_repo = from_dotenv[name].dep_name
        for compose_source, compose_repo in sorted(from_compose[name]):
            if compose_repo != annotated_repo:
                problems.append(
                    f"'{name}' disagrees on depName: {dotenv.name} annotates "
                    f"'{annotated_repo}', {compose_source} names '{compose_repo}' — the two halves "
                    f"of the pin would be tracked as two different images"
                )

        # The compose half of the pin has no annotation to read, so a `versioning=` stated
        # only in `.env.example` applies to one of the two extractions. They then compute
        # different updates and the bot edits one file alone, which lint-pins rejects.
        # Byte-identical, not merely present: a versioning that parses the tag differently
        # is the same divergence with a different spelling.
        wanted = from_dotenv[name].versioning
        if wanted and wanted not in stated.get(annotated_repo, set()):
            problems.append(
                f"'{name}' annotates versioning {wanted!r} but no packageRules entry restates it "
                f"for '{annotated_repo}' (rules state {sorted(stated.get(annotated_repo, set()))}) — "
                f"the compose half would use the default versioning and the two halves would "
                f"disagree about the update"
            )

    problems.extend(pull_request_trigger(workflow))
    # Distinct variables, not distinct repositories. The two are equal today only because
    # thirteen variables happen to name thirteen different images; the coverage assertion in
    # the self-test ties this to the number of `*_VERSION` declarations, and counting
    # variables makes that tie hold by construction rather than by coincidence.
    return len({item.variable for item in detections}), len(detections), problems


def main(argv: list[str]) -> int:
    """Assert `renovate.json` detects every image pin this repository declares.

    The file set defaults to this repository's own, which is what the task runs. A caller
    may name a different set — the self-test drives the tool over throwaway fixtures that
    way, so proving it fails on a planted defect never involves editing a tracked file.

    Args:
        argv: Either empty, or the Renovate configuration, the root compose file, the
            dotenv template and the CI workflow, optionally followed by further compose
            files. The first four keep their positions so the self-test's four-argument
            fixture contract is unchanged.

    Returns:
        Process exit status: 0 when every pin is detected in both halves, 1 otherwise.
    """
    if argv and len(argv) < 4:
        sys.stderr.write(
            "assert-renovate: usage: assert_renovate.py "
            "[<renovate config> <compose file> <dotenv file> <ci workflow> [<compose file>...]]\n"
        )
        return 1
    if argv:
        config_path, compose, dotenv, workflow = (Path(arg) for arg in argv[:4])
        composes = [compose, *(Path(arg) for arg in argv[4:])]
    else:
        config_path = REPO / "renovate.json"
        composes = [REPO / "compose.yaml", *sorted((REPO / "services").glob("*/compose.yaml"))]
        dotenv = REPO / ".env.example"
        workflow = REPO / ".github" / "workflows" / "ci.yml"
    for path in (config_path, *composes, dotenv, workflow):
        if not path.is_file():
            sys.stderr.write(f"assert-renovate: no such file: {path}\n")
            return 1

    try:
        dependencies, references, problems = check(config_path, composes, dotenv, workflow)
    except RuntimeError as exc:
        sys.stderr.write(f"assert-renovate: {exc}\n")
        return 1

    # Both are reported, not one or the other. A pattern that matches nothing produces the
    # empty detection set *and* the diagnostic naming the pattern; printing only the summary
    # would leave the reader knowing the bot is broken without knowing which regex broke it.
    for problem in problems:
        sys.stderr.write(f"assert-renovate: {problem}\n")
    if dependencies == 0:
        sys.stderr.write(
            f"assert-renovate: detected no dependencies — {config_path.name}'s custom managers "
            f"matched nothing, so the bot would open no pull request and still report a clean run\n"
        )
    if problems or dependencies == 0:
        return 1

    sys.stdout.write(
        f"assert-renovate: OK {dependencies} dependencies detected over {references} references "
        f"in {dotenv.name} and {', '.join(source_name(path, config_path.parent) for path in composes)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Assert a commit message follows the Conventional Commits contract.

The repository had no commit-message contract at all: `updated the readme` and
`feat(hooks)!: install via core.hooksPath` were equally acceptable, so the history
cannot be read by tooling and a breaking change is announced only in prose. This
check is what makes the contract real, and it lives in Python — rather than in the
hook that calls it — because a hook body cannot be tested and this can.

The shape enforced is `type(optional-scope)!: description`: a type from the set
below, an optional parenthesised scope, an optional `!` marking a breaking change,
a colon, a space, and a non-empty description.

Four things the naive form of this check gets wrong, all of which break real work:

* `git merge` runs `commit-msg` on the message git itself generated, and so does a
  `git revert` or `git commit --fixup` completed by hand after a conflict.
  Rejecting `Merge branch 'x' into main` would make every merge in this repository
  impossible, so git's own generated subjects are accepted as given rather than
  parsed. They are matched by the exact prefixes git writes, not by the word
  `Merge`, so ordinary prose starting with it is still judged;
* `git commit --verbose` appends the staged diff below a scissors line. Everything
  from that line down is git's, not the author's, and judging it would fail on any
  diff whose first line happened to look like a subject;
* every line beginning with the comment character is dropped before the message is
  read, so a message whose only content is the template's own comments is empty
  rather than valid. This is stricter than git itself in one case: `git commit -m`
  applies whitespace cleanup rather than stripping comments, so it records a
  leading `#142 fix the pin drift` verbatim where this check does not see it;
* the message file is written by an editor, so it can hold anything, including
  bytes that are not UTF-8. That is a diagnostic, not a traceback.

Diagnostics go to stderr because they are human-readable tool output, not
application logging, and they name what was wrong rather than restating the rule:
a developer who is told "invalid commit message" reaches for `--no-verify`.

Two known limitations. `core.commentChar` is assumed to be `#`, which is git's
default and what this repository uses; a clone that changed it would have its
comment lines judged as content. And only the subject is judged: the body and
footers are not read at all, so a `BREAKING CHANGE:` footer is neither required
alongside the `!` marker nor validated when it appears.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The types a subject may declare. The Conventional Commits recommended set:
#: anything outside it is a typo (`feature`) or a category nothing downstream can
#: group, which is what an open set degrades into.
TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

#: The shape a subject must have, quoted verbatim in every diagnostic so the
#: developer is told the contract rather than only that they broke it.
SHAPE = "type(optional-scope)!: description"

#: Subjects git generates itself and then runs this hook over. Matched by the exact
#: prefixes git writes rather than by a leading word: `Merge the two configs into
#: one` is prose an author chose and is judged, where `Merge branch 'x'` is not.
#: `Reapply "..."` is what a revert of a revert produces.
GENERATED = re.compile(
    r"^(?:"
    r"Merge (?:remote-tracking )?(?:branch|branches|tag|tags|commit|commits|pull request) "
    r'|(?:Revert|Reapply) "'
    r"|(?:fixup|squash|amend)! "
    r")"
)

#: The scissors line `git commit --verbose` writes. Everything below it is the
#: staged diff git appended, which is not part of the message.
SCISSORS = re.compile(r"^#\s*-+\s*>8\s*-+")

#: `type(scope)!: description`. The type is anything up to the first delimiter, so
#: a wrong type is reported as a wrong type rather than as a wrong shape. The `!`
#: is matched but not captured: nothing downstream reads it, because the footer it
#: would be cross-checked against is not part of what this judges.
SUBJECT = re.compile(r"^(?P<type>[^\s():!]+)(?:\((?P<scope>[^()]*)\))?(?:!)?:(?P<description>.*)$")


def subject_of(text: str) -> str | None:
    """Extract the subject line git would record from a raw message file.

    Args:
        text: The full contents of the message file.

    Returns:
        The first line that survives stripping comments and the scissors block and
        is not blank, or None when nothing does.
    """
    for raw in text.splitlines():
        if SCISSORS.match(raw):
            break
        if raw.startswith("#"):
            continue
        if raw.strip():
            return raw.rstrip()
    return None


def check(subject: str) -> list[str]:
    """Judge one subject line against the contract.

    Args:
        subject: The subject line, already stripped of comments and trailing space.

    Returns:
        One diagnostic per violation, empty when the subject is acceptable.
    """
    if GENERATED.match(subject):
        return []

    match = SUBJECT.match(subject)
    if match is None:
        return [f"'{subject}' is not '{SHAPE}' — there is no 'type:' before a description"]

    problems: list[str] = []
    kind = match.group("type")
    if kind not in TYPES:
        problems.append(f"'{kind}' is not a commit type — the subject was '{subject}'")

    scope = match.group("scope")
    if scope is not None and not scope.strip():
        problems.append(f"'{subject}' declares an empty scope — write 'type: description' or name the scope")

    description = match.group("description")
    if not description.strip():
        problems.append(f"'{subject}' has an empty description — '{SHAPE}' needs something after the colon")
    elif not description.startswith(" "):
        problems.append(f"'{subject}' has no space after the colon — the shape is '{SHAPE}'")

    return problems


def main(argv: list[str]) -> int:
    """Check the commit message file named on the command line.

    The path is a parameter rather than a constant so the self-test can drive every
    row of the contract over throwaway fixtures, which is also how the `commit-msg`
    hook passes git's `$1`.

    Args:
        argv: Exactly the path of the message file to check.

    Returns:
        Process exit status: 0 when the message is acceptable, 1 otherwise.
    """
    if len(argv) != 1 or not argv[0].strip():
        sys.stderr.write("commit-msg: usage: check_commit_msg.py <message file>\n")
        return 1

    path = Path(argv[0])
    if not path.is_file():
        sys.stderr.write(f"commit-msg: no such file: {path}\n")
        return 1

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        sys.stderr.write(f"commit-msg: {path}: is not valid UTF-8 — the message could not be read\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"commit-msg: {path}: unreadable: {exc}\n")
        return 1

    subject = subject_of(text)
    if subject is None:
        sys.stderr.write(f"commit-msg: {path}: the message is empty — every line is blank or a comment\n")
        return 1

    problems = check(subject)
    if problems:
        for problem in problems:
            sys.stderr.write(f"commit-msg: {problem}\n")
        sys.stderr.write(f"commit-msg: allowed types: {', '.join(TYPES)}\n")
        sys.stderr.write(
            "commit-msg: git's own Merge, Revert, Reapply, fixup!, squash! and amend! subjects are accepted\n"
        )
        return 1

    sys.stdout.write(f"commit-msg: OK 1 subject checked — {subject}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

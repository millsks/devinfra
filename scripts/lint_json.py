#!/usr/bin/env python3
"""Validate that every JSON file named on the command line parses.

Written in Python rather than shell so the check runs identically on every platform
`pixi.toml` declares; conda-forge publishes no `jq` for win-64, and a shell script is
not executable there either. Diagnostics go to stderr because they are human-readable
tool output, not application logging.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(path: Path) -> str | None:
    """Parse one JSON file.

    Args:
        path: File to parse.

    Returns:
        A diagnostic message, or None when the file parses.
    """
    if not path.is_file():
        return f"no such file: {path}"
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
    except UnicodeDecodeError as exc:
        # Not an OSError and not a JSONDecodeError, so without this arm a single
        # non-UTF-8 file aborts the run and every later file goes unchecked.
        return f"{path}: not valid UTF-8 at byte {exc.start}: {exc.reason}"
    except OSError as exc:
        return f"{path}: unreadable: {exc}"
    return None


def main(argv: list[str]) -> int:
    """Validate every path in argv.

    An empty argument list is a failure, not a pass — a lint target whose glob matched
    nothing has verified nothing.

    Args:
        argv: File paths to validate.

    Returns:
        Process exit status: 0 when every file parses, 1 otherwise.
    """
    if not argv:
        sys.stderr.write("lint-json: no files given — a lint target matched nothing\n")
        return 1
    status = 0
    for arg in argv:
        problem = check(Path(arg))
        if problem is None:
            sys.stdout.write(f"lint-json: OK {arg}\n")
        else:
            sys.stderr.write(f"lint-json: {problem}\n")
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

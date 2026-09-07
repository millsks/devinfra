#!/usr/bin/env python3
"""Self-test for this repository's lint surface.

Plain stdlib rather than pytest: this repository ships no Python package, so a test
runner and a coverage gate would be apparatus without a subject. The contract worth
pinning is narrow — every check must exit non-zero on a real defect and name the file,
and none may pass on an empty file set.

Two layers. The tool cases run against throwaway files in a temp directory, so a failure
there means the tool itself is broken rather than that a tracked file drifted. The task
cases run the declared `pixi run` tasks, because a task is what CI and a contributor
actually invoke — a case that only calls the tool binary would still pass if someone
appended `|| true` to the task that wraps it.

Task cases plant a fixture inside the repository or move a target aside, always undoing
it in a `finally`. Nothing tracked is edited in place.

There is no availability guard anywhere in this file: a missing tool must fail loudly,
which is the property the whole lint surface exists to hold.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

LINTER = Path(__file__).with_name("lint_json.py")
REPO = Path(__file__).resolve().parent.parent

#: Constructs that turn a missing tool into a pass. None may appear in a task body.
FORBIDDEN = ("command -v", "which ", "|| true", "skipping")

#: Tools a contributor might not have. The lint surface must supply all of them.
SUPPLIED_TOOLS = ("shellcheck", "yamllint", "python", "python3", "ruff", "mypy")

#: Suffix used to hide a file from a lint glob, then put it back.
MOVED = ".selftest-moved"


def pixi(task: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a declared pixi task, exactly as CI and a contributor would.

    Args:
        task: Task name from pixi.toml.
        env: Environment to run under, defaulting to this process's own.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(
        ["pixi", "run", task],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
        env=env,
    )


@contextlib.contextmanager
def planted(path: Path, body: str) -> Iterator[None]:
    """Create a fixture file inside the repository, then remove it.

    Args:
        path: File to create. Must not already exist, so a bug here can never
            destroy tracked content.
        body: File content, written with LF endings on every platform.

    Yields:
        None, while the fixture exists.

    Raises:
        FileExistsError: If the path is already occupied.
    """
    if path.exists():
        raise FileExistsError(f"selftest fixture would overwrite {path}")
    path.write_text(body, encoding="utf-8", newline="\n")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def moved_aside(paths: list[Path]) -> Iterator[None]:
    """Hide files from a lint glob by renaming them, then restore every one.

    Args:
        paths: Files to hide.

    Yields:
        None, while the files are hidden.
    """
    moved = []
    try:
        for path in paths:
            hidden = path.with_name(path.name + MOVED)
            path.rename(hidden)
            moved.append((hidden, path))
        yield
    finally:
        for hidden, original in moved:
            hidden.rename(original)


def write_stub(directory: Path, name: str) -> None:
    """Write an executable that always fails, to shadow a real tool on PATH.

    Args:
        directory: Directory to write into.
        name: Tool name to shadow.
    """
    if os.name == "nt":
        stub = directory / f"{name}.bat"
        stub.write_text("@echo off\r\nexit /b 127\r\n", encoding="utf-8", newline="")
        return
    stub = directory / name
    stub.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the linter.

    Args:
        args: Paths to pass through.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(
        [sys.executable, str(LINTER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def tool(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke a pinned lint tool.

    Args:
        args: Full command line.
        cwd: Working directory, defaulting to the process's own.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd)


def main() -> int:
    """Exercise every branch of the linter's contract.

    Returns:
        Process exit status: 0 when every case behaves as specified.
    """
    failures: list[str] = []

    def expect(name: str, condition: bool, detail: str) -> None:
        if condition:
            sys.stdout.write(f"lint-selftest: PASS {name}\n")
        else:
            failures.append(f"{name}: {detail}")

    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.json"
        good.write_text(json.dumps({"servers": {}}), encoding="utf-8", newline="\n")
        bad = Path(tmp) / "bad.json"
        bad.write_text('{\n  "a": 1,\n}\n', encoding="utf-8", newline="\n")
        missing = Path(tmp) / "absent.json"

        r = run(str(good))
        expect("valid file exits 0", r.returncode == 0, f"exit {r.returncode}")

        r = run(str(bad))
        expect("invalid file exits non-zero", r.returncode != 0, "exited 0")
        expect("invalid file is named", "bad.json" in r.stderr, f"stderr: {r.stderr!r}")
        # The offending comma is on line 2; the diagnostic must point at it, not at the
        # line where parsing gave up.
        expect("invalid file reports the offending line", ":2:" in r.stderr, f"stderr: {r.stderr!r}")

        r = run(str(missing))
        expect("missing file exits non-zero", r.returncode != 0, "exited 0")
        expect("missing file is named", "absent.json" in r.stderr, f"stderr: {r.stderr!r}")

        r = run()
        expect("empty argument list fails", r.returncode != 0, "an empty file set passed")

        r = run(str(good), str(bad))
        expect("one bad file fails the batch", r.returncode != 0, "exited 0")

        # A non-UTF-8 file must be a diagnostic, not an uncaught UnicodeDecodeError:
        # a traceback would abort the batch and leave every later file unchecked.
        binary = Path(tmp) / "binary.json"
        binary.write_bytes(b'{"a": "\xff\xfe"}')
        r = run(str(binary), str(good))
        expect("non-UTF-8 file exits non-zero", r.returncode != 0, "exited 0")
        expect("non-UTF-8 file is named", "binary.json" in r.stderr, f"stderr: {r.stderr!r}")
        expect("non-UTF-8 file does not abort the batch", "good.json" in r.stdout, f"stdout: {r.stdout!r}")

        # shellcheck: a real violation must fail and name the file and the rule.
        script = Path(tmp) / "violation.sh"
        script.write_text('#!/usr/bin/env bash\nv="$1"\necho $v\n', encoding="utf-8", newline="\n")
        r = tool(["shellcheck", str(script)])
        expect("shellcheck flags a real violation", r.returncode != 0, "exited 0")
        expect("shellcheck names the rule", "SC2086" in r.stdout, f"stdout: {r.stdout!r}")
        clean = Path(tmp) / "clean.sh"
        clean.write_text('#!/usr/bin/env bash\nset -euo pipefail\nv="$1"\necho "$v"\n', encoding="utf-8", newline="\n")
        r = tool(["shellcheck", str(clean)])
        expect("shellcheck passes a clean script", r.returncode == 0, f"exit {r.returncode}: {r.stdout!r}")

        # yamllint: broken indentation must fail under the repository's own config.
        doc = Path(tmp) / "broken.yaml"
        doc.write_text("root:\n  a: 1\n      b: 2\n", encoding="utf-8", newline="\n")
        config = str(REPO / ".yamllint.yaml")
        r = tool(["yamllint", "--strict", "-c", config, str(doc)])
        expect("yamllint flags broken indentation", r.returncode != 0, "exited 0")
        expect("yamllint names the file", "broken.yaml" in r.stdout, f"stdout: {r.stdout!r}")
        ok = Path(tmp) / "ok.yaml"
        ok.write_text("root:\n  a: 1\n  b: 2\n", encoding="utf-8", newline="\n")
        r = tool(["yamllint", "--strict", "-c", config, str(ok)])
        expect("yamllint passes a clean file", r.returncode == 0, f"exit {r.returncode}: {r.stdout!r}")

        # docker compose: a depends_on naming an undefined service must fail and name it.
        compose = Path(tmp) / "compose.yaml"
        compose.write_text(
            "services:\n"
            "  a:\n"
            "    image: alpine\n"
            "    depends_on:\n"
            "      absent-service:\n"
            "        condition: service_started\n",
            encoding="utf-8",
            newline="\n",
        )
        r = tool(["docker", "compose", "-f", str(compose), "config", "-q"])
        expect("compose flags an undefined depends_on", r.returncode != 0, "exited 0")
        expect(
            "compose names the undefined service",
            "absent-service" in (r.stderr + r.stdout),
            f"stderr: {r.stderr!r}",
        )

    # --- No task body may turn a missing tool into a pass. ---
    manifest = tomllib.loads((REPO / "pixi.toml").read_text(encoding="utf-8"))
    tasks: dict[str, object] = manifest.get("tasks", {})
    expect("pixi.toml declares tasks", bool(tasks), "no [tasks] table found")
    for name in sorted(tasks):
        body = tasks[name]
        cmd = body.get("cmd", "") if isinstance(body, dict) else str(body)
        hits = [token for token in FORBIDDEN if token in cmd]
        expect(f"{name} has no tool-presence branch", not hits, f"cmd contains {hits}")

    # --- Every task fails on a real defect. Planted fixtures, never edits in place. ---
    defects: list[tuple[str, Path, str]] = [
        ("lint-shell", REPO / "scripts" / "zz_selftest_defect.sh", '#!/usr/bin/env bash\nv="$1"\necho $v\n'),
        ("lint-yaml", REPO / "docker" / "loki" / "zz_selftest_defect.yaml", "root:\n  a: 1\n      b: 2\n"),
        ("lint-json", REPO / "docker" / "pgadmin" / "zz_selftest_defect.json", '{\n  "a": 1,\n}\n'),
        (
            "lint-compose",
            REPO / "compose.override.yaml",
            "services:\n  zz-selftest-defect:\n    image: alpine\n    depends_on:\n      - zz-absent-service\n",
        ),
    ]
    for task, fixture, body_text in defects:
        with planted(fixture, body_text):
            r = pixi(task)
            expect(f"{task} fails on a real defect", r.returncode != 0, "the task exited 0")
            expect(
                f"{task} names the defect",
                fixture.name in (r.stdout + r.stderr) or "zz-absent-service" in (r.stdout + r.stderr),
                f"output: {(r.stdout + r.stderr)!r}",
            )

    # --- Every glob-driven task fails when its pattern matches nothing. ---
    empties: list[tuple[str, list[Path]]] = [
        ("lint-shell", sorted((REPO / "docker" / "postgres" / "initdb").glob("*.sh"))),
        ("lint-yaml", sorted((REPO / "docker").rglob("*.yaml")) + sorted((REPO / "docker").rglob("*.yml"))),
        ("lint-json", sorted((REPO / "docker").rglob("*.json"))),
    ]
    for task, targets in empties:
        expect(f"{task} has a glob target to empty", bool(targets), "found no files to hide")
        with moved_aside(targets):
            r = pixi(task)
            expect(f"{task} fails on an empty file set", r.returncode != 0, "an empty file set passed")

    # --- The headline criterion: the tools need not be on the contributor's PATH. ---
    with tempfile.TemporaryDirectory() as stub_dir:
        stubs = Path(stub_dir)
        for name in SUPPLIED_TOOLS:
            write_stub(stubs, name)
        shadowed = dict(os.environ)
        shadowed["PATH"] = str(stubs) + os.pathsep + shadowed.get("PATH", "")
        r = pixi("lint", env=shadowed)
        expect(
            "lint passes with every tool shadowed by a failing stub",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )
        expect(
            "no check reported skipping",
            "skipping" not in (r.stdout + r.stderr).lower(),
            f"output: {(r.stdout + r.stderr)!r}",
        )

    for failure in failures:
        sys.stderr.write(f"lint-selftest: FAIL {failure}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

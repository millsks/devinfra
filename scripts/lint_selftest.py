#!/usr/bin/env python3
"""Self-test for this repository's lint surface and its lifecycle scripts.

Plain stdlib rather than pytest: this repository ships no Python package, so a test
runner and a coverage gate would be apparatus without a subject. The contract worth
pinning is narrow — every check must exit non-zero on a real defect and name the file,
none may pass on an empty file set, and every lifecycle script must hold its own
refusal and timeout contracts.

Three layers. The tool cases run against throwaway files in a temp directory, so a
failure there means the tool itself is broken rather than that a tracked file drifted.
The task cases run the declared `pixi run` tasks, because a task is what CI and a
contributor actually invoke — a case that only calls the tool binary would still pass
if someone appended `|| true` to the task that wraps it. The script cases drive
`scripts/*.sh` standalone and through their tasks, with the container runtime replaced
by a recording stub, so a health-wait timeout or a refused confirmation is provable
without breaking a real stack.

Task and script cases plant a fixture inside the repository or move a target aside,
always undoing it in a `finally`. Nothing tracked is edited in place, and no case
touches a real volume: every compose and curl invocation goes to a stub.

There is no availability guard anywhere in this file: a missing tool must fail loudly,
which is the property the whole lint surface exists to hold.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

LINTER = Path(__file__).with_name("lint_json.py")
PINNER = Path(__file__).with_name("assert_pins.py")
REPO = Path(__file__).resolve().parent.parent

#: Constructs that turn a missing tool into a pass. None may appear in a task body.
FORBIDDEN = ("command -v", "which ", "|| true", "skipping")

#: Tools a contributor might not have. The lint surface must supply all of them.
SUPPLIED_TOOLS = ("shellcheck", "yamllint", "python", "python3", "ruff", "mypy")

#: Suffix used to hide a file from a lint glob, then put it back.
MOVED = ".selftest-moved"

#: Every target the Makefile exposed before pixi, and the exact line each must
#: forward with. Written out rather than derived from the file it checks: a
#: derived expectation agrees with whatever the Makefile happens to say, so
#: rewiring `make destroy` to `pixi run down` would pass unnoticed.
MAKE_FORWARDS = {
    "help": "@pixi task list",
    "init": "@pixi run init",
    "up": "@pixi run up",
    "up-core": "@pixi run up-core",
    "down": "@pixi run down",
    "stop": "@pixi run stop",
    "restart": "@pixi run restart",
    "destroy": "@pixi run destroy",
    "pull": "@pixi run pull",
    "wait": "@pixi run wait",
    "ps": "@pixi run ps",
    "logs": "@pixi run logs $(S)",
    "smoke": "@pixi run smoke",
    "urls": "@pixi run urls",
    "psql": "@pixi run psql $(DB)",
    "redis-cli": "@pixi run redis-cli $(N)",
    "mc": "@pixi run mc",
    "backup": "@pixi run backup",
    "restore": "@pixi run restore $(F)",
    "keycloak-reimport": "@pixi run keycloak-reimport",
    "keycloak-export": "@pixi run keycloak-export",
    "token": "@pixi run token $(or $(U),dev) $(or $(P),dev)",
    "config": "@pixi run config",
    "lint": "@pixi run lint",
}


def pixi(
    task: str,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a declared pixi task, exactly as CI and a contributor would.

    Args:
        task: Task name from pixi.toml.
        args: Task arguments, passed through as a contributor would type them.
        env: Environment to run under, defaulting to this process's own.
        stdin: Text fed to the task's standard input.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(
        ["pixi", "run", task, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
        env=env,
        input=stdin,
    )


def run_script(
    name: str,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a lifecycle script standalone, as `./scripts/<name>` would.

    Args:
        name: File name under scripts/.
        args: Arguments to pass to the script.
        env: Environment to run under, defaulting to this process's own.
        stdin: Text fed to the script's standard input.
        cwd: Working directory, defaulting to the repository root.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(
        [str(REPO / "scripts" / name), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd or REPO,
        env=env,
        input=stdin,
    )


#: The recording stub, standing in for `docker compose` and for `curl`.
#:
#: The subcommand is found by walking the arguments rather than reading $1, because
#: the compose invocations under test are prefixed with global flags — `--profile
#: admin config -q` is a `config` call, and answering it with the `ps` reply would
#: make the profile enumeration untestable.
RECORDER = r"""#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >> "$STUB_RECORD"
printf 'COMPOSE_PROFILES=%s\n' "${COMPOSE_PROFILES-<unset>}" >> "$STUB_ENV_RECORD"
sub=""
skip=0
for a in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$a" in
    --profile|-f|--file|-p|--project-name) skip=1 ;;
    -*) ;;
    *) sub="$a"; break ;;
  esac
done
code="${STUB_EXIT:-0}"
case "$sub" in
  config)
    case " $* " in
      *" --profiles "*)
        printf '%s' "${STUB_PROFILES:-}"
        code="${STUB_PROFILES_EXIT:-${STUB_EXIT:-0}}" ;;
      *" --format "*) printf '%s' "${STUB_JSON:-}" ;;
      *) printf '%s' "${STUB_SERVICES:-}" ;;
    esac ;;
  ps)
    case " $* " in
      *" --all "*) printf '%s' "${STUB_ALL:-}" ;;
      *) printf '%s' "${STUB_STDOUT:-}" ;;
    esac ;;
  *) printf '%s' "${STUB_STDOUT:-}" ;;
esac
exit "$code"
"""


#: A stub standing in for `podman` and for `sudo`: it records and does nothing else.
#:
#: Separate from RECORDER because both are in play at once. assert-podman.sh asks the
#: compose seam what containers exist and then asks Podman what it is running, and the
#: point of the check is that the two answers can disagree — one stub answering both
#: from the same variable could never express that.
#: Only a `tee` invocation reads standard input, and only from the pipe the script
#: builds. Draining stdin unconditionally would block: every other invocation
#: inherits this process's stdin, which may be a terminal that never reaches EOF.
LISTER = r"""#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >> "$STUB_LIST_RECORD"
case "${1:-}" in
  tee) cat >> "${STUB_LIST_STDIN:-/dev/null}" ;;
esac
printf '%s' "${STUB_LIST_STDOUT:-}"
exit "${STUB_LIST_EXIT:-0}"
"""


def write_lister(directory: Path, name: str) -> Path:
    """Write a stub that records its arguments and prints a fixed answer.

    Args:
        directory: Directory to write the stub into.
        name: File name for the stub.

    Returns:
        Path to the stub, marked executable.
    """
    stub = directory / name
    stub.write_text(LISTER, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    return stub


def write_recorder(directory: Path, name: str) -> Path:
    """Write a stub that records what it was asked to do instead of doing it.

    It stands in for `docker compose` and for `curl`, so a script's contract can be
    asserted on the command it would have run. Every invocation appends one argument
    per line to STUB_RECORD and one environment observation to STUB_ENV_RECORD.
    `config --profiles` answers with STUB_PROFILES, `config --format` with STUB_JSON,
    any other `config` with STUB_SERVICES, `ps --all` with STUB_ALL and everything
    else with STUB_STDOUT — the health wait asks for the expected service set, the
    running containers and the exited ones in the same run and must be able to see
    all three disagree, and the compose lint asks for the profiles and then for each
    combination's resolved model.

    Args:
        directory: Directory to write the stub into.
        name: File name for the stub.

    Returns:
        Path to the stub, marked executable.
    """
    stub = directory / name
    stub.write_text(RECORDER, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    return stub


def stub_env(
    compose: Path,
    record: Path,
    stdout: str = "",
    services: str = "",
    exit_code: str = "0",
    profiles: str = "",
    document: str = "",
) -> dict[str, str]:
    """Build an environment whose container runtime and HTTP client are stubs.

    Args:
        compose: Recording stub standing in for `docker compose` and `curl`.
        record: File the stub appends its arguments to.
        stdout: Text the stub prints for `ps` and any subcommand but `config`.
        services: Text the stub prints for `config`, the expected service set.
        exit_code: Status the stub exits with, for driving a failure path.
        profiles: Text the stub prints for `config --profiles`.
        document: Text the stub prints for `config --format json`.

    Returns:
        A copy of this process's environment with the stub wiring added.
    """
    env = dict(os.environ)
    env["DEVINFRA_COMPOSE"] = str(compose)
    env["DEVINFRA_CURL"] = str(compose)
    env["STUB_RECORD"] = str(record)
    env["STUB_ENV_RECORD"] = str(record.with_name(record.name + ".env"))
    env["STUB_STDOUT"] = stdout
    # `ps --all` defaults to the same answer, so only a case that cares about
    # exited containers has to say otherwise.
    env["STUB_ALL"] = stdout
    env["STUB_SERVICES"] = services
    env["STUB_EXIT"] = exit_code
    env["STUB_PROFILES"] = profiles
    env["STUB_JSON"] = document
    # The enumeration succeeds by default even when the per-combination calls are
    # told to fail: a case that wants every combination to fail must still be able
    # to read the combinations.
    env["STUB_PROFILES_EXIT"] = "0"
    return env


def recorded(record: Path) -> list[str]:
    """Read back the arguments a stub recorded.

    Args:
        record: File the stub appended to.

    Returns:
        One entry per recorded argument, empty when the stub never ran.
    """
    if not record.exists():
        return []
    return record.read_text(encoding="utf-8").splitlines()


def recorded_env(record: Path) -> list[str]:
    """Read back the environment each stub invocation saw.

    Args:
        record: The argument record; the environment record sits beside it.

    Returns:
        One `COMPOSE_PROFILES=...` line per invocation.
    """
    path = record.with_name(record.name + ".env")
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def not_ready_names(stderr: str) -> set[str]:
    """Extract the set the health wait reported as not ready.

    Guarded rather than indexed: a change to the report's wording must fail one
    case, not raise IndexError and abandon every case after it.

    Args:
        stderr: The health wait's standard error.

    Returns:
        The names listed under the timeout header, empty when there is no header.
    """
    marker = "Timed out. Not ready:"
    if marker not in stderr:
        return set()
    names: set[str] = set()
    for line in stderr.split(marker, 1)[1].splitlines():
        match = re.match(r"^(\S+) \(.+\)$", line.strip())
        if match:
            names.add(match.group(1))
        elif names:
            break
    return names


def first_word(line: str) -> str:
    """Return a line's first word, or the empty string for a blank line.

    Args:
        line: Any text.

    Returns:
        The first whitespace-delimited word, never raising on a blank line.
    """
    parts = line.split()
    return parts[0] if parts else ""


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


def tool(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a pinned lint tool, or the real container runtime.

    Args:
        args: Full command line.
        cwd: Working directory, defaulting to the process's own.
        env: Environment to run under, defaulting to this process's own.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    return subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd, env=env)


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
        # A task body cannot expand ${DEVINFRA_COMPOSE:-docker compose} — pixi.toml
        # has no shell expansion — so one that names a runtime directly runs that
        # runtime whatever the environment selected, and DEVINFRA_COMPOSE becomes a
        # setting that appears to work and does not. Everything goes through
        # scripts/compose.sh instead.
        runtimes = [token for token in ("docker compose", "docker-compose", "podman compose") if token in cmd]
        expect(f"{name} names no container runtime directly", not runtimes, f"cmd contains {runtimes}")

    # The same scan over every shell script. This story moved essentially all the
    # logic out of task bodies and into scripts/, so a `|| true` or a `command -v`
    # branch there is the same defect one file removed. Full-line comments are
    # stripped: several scripts legitimately explain what they must not do.
    shell_sources = sorted((REPO / "scripts").rglob("*.sh"))
    expect("scripts/ contains shell scripts to scan", bool(shell_sources), "found none")
    for path in shell_sources:
        code = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
        hits = [token for token in FORBIDDEN if any(token in line for line in code)]
        expect(f"{path.name} has no tool-presence branch", not hits, f"contains {hits}")

    # lint-shell must reach every script, at any depth. Its patterns are checked
    # against a recursive walk rather than trusted, and a planted defect below
    # proves the shell agrees with this expansion.
    shell_task = tasks.get("lint-shell")
    shell_cmd = shell_task.get("cmd", "") if isinstance(shell_task, dict) else str(shell_task)
    covered: set[Path] = set()
    for pattern in shell_cmd.split()[1:]:
        covered.update(REPO.glob(pattern))
    uncovered = sorted(path for path in shell_sources if path not in covered)
    expect("lint-shell covers every script at any depth", not uncovered, f"unlinted: {uncovered}")

    # --- Every task fails on a real defect. Planted fixtures, never edits in place. ---
    defects: list[tuple[str, Path, str]] = [
        ("lint-shell", REPO / "scripts" / "zz_selftest_defect.sh", '#!/usr/bin/env bash\nv="$1"\necho $v\n'),
        (
            "lint-shell",
            REPO / "scripts" / "lib" / "zz_selftest_defect.sh",
            '#!/usr/bin/env bash\nv="$1"\necho $v\n',
        ),
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
            where = fixture.parent.name
            expect(f"{task} fails on a real defect in {where}/", r.returncode != 0, "the task exited 0")
            expect(
                f"{task} names the defect in {where}/",
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

    # --- Lifecycle scripts: each contract, driven against a recording stub. ---
    # No case touches a real volume, a real database or a real HTTP endpoint.
    env_files = [path for path in (REPO / ".env",) if path.exists()]
    with tempfile.TemporaryDirectory() as stub_dir, moved_aside(env_files):
        stubs = Path(stub_dir)
        record = stubs / "record.txt"
        compose_stub = write_recorder(stubs, "compose-stub")

        def fresh(
            stdout: str = "",
            services: str = "",
            exit_code: str = "0",
            profiles: str = "",
            document: str = "",
        ) -> dict[str, str]:
            record.unlink(missing_ok=True)
            record.with_name(record.name + ".env").unlink(missing_ok=True)
            return stub_env(compose_stub, record, stdout, services, exit_code, profiles, document)

        core = "postgres\nredis\n"
        healthy = "postgres|devinfra-postgres|running|healthy|0\nredis|devinfra-redis|running|healthy|0\n"

        # wait-healthy: every expected service up and healthy.
        env = fresh(healthy, core)
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy passes when every container is healthy", r.returncode == 0, f"exit {r.returncode}")
        expect("wait-healthy reports readiness", "All containers running" in r.stdout, f"stdout: {r.stdout!r}")

        # wait-healthy: one container never becomes healthy. NFR-3 — the wait must
        # never return optimistically, and must name exactly what held it up.
        env = fresh(healthy + "keycloak|devinfra-keycloak|running|starting|0\n", core + "keycloak\n")
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy fails on timeout", r.returncode != 0, "exited 0 with a container unhealthy")
        expect(
            "wait-healthy names exactly the container that was not ready",
            not_ready_names(r.stderr) == {"devinfra-keycloak"},
            f"reported {not_ready_names(r.stderr)}; stderr: {r.stderr!r}",
        )

        # wait-healthy: a crashed service is simply absent from `compose ps`, so
        # judging readiness by what is running would call this a success.
        env = fresh(healthy, core + "keycloak\n")
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy fails when an expected service has no container", r.returncode != 0, "exited 0")
        expect(
            "wait-healthy names the absent service",
            not_ready_names(r.stderr) == {"keycloak"},
            f"reported {not_ready_names(r.stderr)}; stderr: {r.stderr!r}",
        )

        # ...but a one-shot init container that finished is not a failure. It is
        # visible only to `ps --all`: a plain `ps` hides it, so the wait would spin
        # to timeout on a perfectly healthy stack.
        env = fresh(healthy, core + "minio-init\n")
        env["STUB_ALL"] = healthy + "minio-init|devinfra-minio-init|exited||0\n"
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy accepts a one-shot container that exited 0", r.returncode == 0, f"stderr: {r.stderr!r}")

        env = fresh(healthy, core + "minio-init\n")
        env["STUB_ALL"] = healthy + "minio-init|devinfra-minio-init|exited||1\n"
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy rejects a container that exited non-zero", r.returncode != 0, "exited 0")
        expect(
            "wait-healthy names the container that exited non-zero",
            not_ready_names(r.stderr) == {"devinfra-minio-init"},
            f"reported {not_ready_names(r.stderr)}; stderr: {r.stderr!r}",
        )

        # wait-healthy: nothing selected. An empty set is the one case the old
        # Makefile loop would have called success.
        env = fresh("", "")
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "2", "0"
        r = run_script("wait-healthy.sh", env=env)
        expect("wait-healthy fails when no service is selected", r.returncode != 0, "an empty set passed")
        expect("wait-healthy says nothing is selected", "No services" in r.stderr, f"stderr: {r.stderr!r}")

        # Confirmations: the two irreversible operations. Anything but the exact
        # word must abort before the runtime is touched.
        for name, word in (("destroy.sh", "destroy"), ("keycloak-reimport.sh", "reimport")):
            for answer in ("", "no\n", "yes\n", f"{word}x\n", f" {word}\n", f"{word.upper()}\n"):
                r = run_script(name, env=fresh(), stdin=answer)
                expect(
                    f"{name} refuses {answer!r}",
                    r.returncode != 0 and not recorded(record),
                    f"exit {r.returncode}, recorded {recorded(record)}",
                )
                expect(f"{name} says it aborted for {answer!r}", "Aborted." in r.stderr, f"stderr: {r.stderr!r}")

        # ...and the exact word proceeds, reaching the command the Makefile built.
        r = run_script("destroy.sh", env=fresh(), stdin="destroy\n")
        expect("destroy.sh accepts its exact word", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "destroy.sh removes volumes for both profiles",
            recorded(record) == ["--profile", "admin", "--profile", "observability", "down", "-v"],
            f"recorded {recorded(record)}",
        )

        env = fresh(healthy, core)
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "1", "0"
        r = run_script("keycloak-reimport.sh", env=env, stdin="reimport\n")
        args = recorded(record)
        expect("keycloak-reimport.sh accepts its exact word", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect("keycloak-reimport.sh stops keycloak first", args[:2] == ["stop", "keycloak"], f"recorded {args}")
        expect(
            "keycloak-reimport.sh drops and recreates the keycloak database",
            any("DROP DATABASE IF EXISTS keycloak" in a for a in args)
            and any("CREATE DATABASE keycloak" in a for a in args),
            f"recorded {args}",
        )

        # restore: refuses before psql is ever invoked.
        r = run_script("restore.sh", env=fresh())
        expect("restore.sh refuses a missing argument", r.returncode != 0, "exited 0 with no file given")
        expect("restore.sh prints usage", "Usage:" in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore.sh never invoked the runtime", not recorded(record), f"recorded {recorded(record)}")

        absent = str(stubs / "no-such-backup.sql.gz")
        r = run_script("restore.sh", absent, env=fresh())
        expect("restore.sh refuses a nonexistent file", r.returncode != 0, "exited 0 for a path that does not exist")
        expect("restore.sh names the missing path", absent in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore.sh never invoked psql", not recorded(record), f"recorded {recorded(record)}")

        # restore: a relative path is read against the caller's directory, not the
        # repository root that common.sh cds to.
        elsewhere = stubs / "elsewhere"
        elsewhere.mkdir()
        with gzip.open(elsewhere / "dump.sql.gz", "wb") as archive:
            archive.write(b"-- empty\n")
        r = run_script("restore.sh", "./dump.sql.gz", env=fresh(), cwd=elsewhere)
        expect(
            "restore.sh resolves a relative path against the caller's directory",
            "psql" in recorded(record),
            f"exit {r.returncode}, recorded {recorded(record)}, stderr {r.stderr!r}",
        )

        # backup: the archive is named on success and never left truncated on failure.
        backups = REPO / "backups"
        before = set(backups.glob("postgres-*.sql.gz")) if backups.exists() else set()
        r = run_script("backup.sh", env=fresh())
        after = set(backups.glob("postgres-*.sql.gz"))
        created = sorted(after - before)
        expect("backup.sh exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "backup.sh dumps every database as POSTGRES_USER",
            recorded(record) == ["exec", "-T", "postgres", "pg_dumpall", "-U", "devinfra"],
            f"recorded {recorded(record)}",
        )
        expect("backup.sh writes exactly one archive", len(created) == 1, f"created {created}")
        for path in created:
            path.unlink()

        r = run_script("backup.sh", env=fresh(exit_code="1"))
        leftovers = sorted(set(backups.glob("postgres-*")) - before)
        expect("backup.sh fails when pg_dumpall fails", r.returncode != 0, "exited 0 on a failed dump")
        expect("backup.sh leaves no truncated archive behind", not leftovers, f"left {leftovers}")
        for path in leftovers:
            path.unlink()

        # keycloak-export: the copy runs container-side source first, repo path second.
        r = run_script("keycloak-export.sh", env=fresh())
        args = recorded(record)
        expect("keycloak-export.sh exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "keycloak-export.sh exports the realm to a temp dir",
            args[:9]
            == [
                "exec",
                "keycloak",
                "/opt/keycloak/bin/kc.sh",
                "export",
                "--dir",
                "/tmp/kc-export",
                "--realm",
                "devinfra",
                "--users",
            ],
            f"recorded {args}",
        )
        expect(
            "keycloak-export.sh copies container path to repository path, in that order",
            args[-3:]
            == [
                "cp",
                "keycloak:/tmp/kc-export/devinfra-realm.json",
                "docker/keycloak/realms/devinfra-realm.json",
            ],
            f"recorded {args}",
        )

        # init-env: it runs on every `pixi run up` through the init dependency, so an
        # inverted guard would overwrite the developer's own credentials.
        example = (REPO / ".env.example").read_bytes()
        with planted(REPO / ".env", "POSTGRES_USER=zzexisting\n"):
            original = (REPO / ".env").read_bytes()
            r = run_script("init-env.sh", env=fresh())
            expect("init-env.sh exits 0 when .env exists", r.returncode == 0, f"exit {r.returncode}")
            expect(
                "init-env.sh leaves an existing .env byte-identical",
                (REPO / ".env").read_bytes() == original,
                "the existing .env was modified",
            )
        r = run_script("init-env.sh", env=fresh())
        try:
            expect("init-env.sh exits 0 when .env is absent", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            created_env = (REPO / ".env").read_bytes() if (REPO / ".env").exists() else None
            expect("init-env.sh creates .env", created_env is not None, "no .env was created")
            expect(
                "init-env.sh copies .env.example verbatim",
                created_env == example,
                "the created .env differs from the template",
            )
        finally:
            (REPO / ".env").unlink(missing_ok=True)

        # urls: complete without .env, using the defaults compose.yaml interpolates.
        r = pixi("urls", env=fresh())
        services = (
            "PostgreSQL",
            "Redis",
            "Keycloak",
            "OIDC discovery",
            "MinIO console",
            "Mailpit",
            "pgAdmin",
            "RedisInsight",
            "Flower",
            "Grafana",
            "Prometheus",
            "OTLP ingest",
        )
        expect("urls exits 0 without .env", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        for service in services:
            expect(f"urls prints {service} without .env", service in r.stdout, f"stdout: {r.stdout!r}")
        defaults = (
            "5432",
            "6379",
            "8080",
            "9101",
            "9100",
            "8025",
            "1025",
            "5050",
            "5540",
            "5555",
            "3000",
            "9090",
            "4317",
            "4318",
        )
        for default in defaults:
            expect(f"urls falls back to compose's {default}", default in r.stdout, f"stdout: {r.stdout!r}")

        # --- Task arguments reach the command, and omitting one applies the default. ---
        r = pixi("psql", "keycloak", env=fresh())
        expect(
            "psql passes its argument through",
            recorded(record) == ["exec", "postgres", "psql", "-U", "devinfra", "-d", "keycloak"],
            f"recorded {recorded(record)}",
        )
        r = pixi("psql", env=fresh())
        expect(
            "psql defaults to POSTGRES_DB",
            recorded(record) == ["exec", "postgres", "psql", "-U", "devinfra", "-d", "devinfra"],
            f"recorded {recorded(record)}",
        )

        r = pixi("redis-cli", "1", env=fresh())
        expect(
            "redis-cli passes its database through",
            recorded(record)[-2:] == ["-n", "1"],
            f"recorded {recorded(record)}",
        )
        r = pixi("redis-cli", env=fresh())
        expect(
            "redis-cli selects no database by default",
            recorded(record) == ["exec", "redis", "redis-cli", "-a", "devinfra", "--no-auth-warning"],
            f"recorded {recorded(record)}",
        )

        r = pixi("logs", "keycloak", env=fresh())
        expect(
            "logs passes its service through",
            recorded(record) == ["logs", "-f", "--tail=100", "keycloak"],
            f"recorded {recorded(record)}",
        )
        r = pixi("logs", env=fresh())
        expect(
            "logs tails everything by default",
            recorded(record) == ["logs", "-f", "--tail=100"],
            f"recorded {recorded(record)}",
        )

        r = pixi("restore", absent, env=fresh())
        expect("restore passes its path through", absent in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore fails on a nonexistent path", r.returncode != 0, "exited 0")
        r = pixi("restore", env=fresh())
        expect("restore with no argument fails loudly", r.returncode != 0, "exited 0 with no file given")

        # An argument containing a space must arrive as one argument, not two.
        spaced_dir = stubs / "old dumps"
        spaced_dir.mkdir()
        spaced = spaced_dir / "x.sql.gz"
        with gzip.open(spaced, "wb") as archive:
            archive.write(b"-- empty\n")
        r = pixi("restore", str(spaced), env=fresh())
        expect(
            "restore keeps an argument containing a space intact",
            "psql" in recorded(record),
            f"exit {r.returncode}, recorded {recorded(record)}, stderr {r.stderr!r}",
        )

        r = pixi("token", "alice", "s3cr3t", env=fresh())
        args = recorded(record)
        expect("token passes its user through", "username=alice" in args, f"recorded {args}")
        expect("token passes its password through", "password=s3cr3t" in args, f"recorded {args}")
        r = pixi("token", env=fresh())
        args = recorded(record)
        expect("token defaults to the dev user", "username=dev" in args, f"recorded {args}")
        expect("token defaults to the dev password", "password=dev" in args, f"recorded {args}")

        # --- With a .env present, its values must actually reach the scripts. ---
        # Every case above runs without .env, so nothing there would notice if
        # scripts/lib/common.sh stopped loading it at all.
        dotenv = (
            "POSTGRES_USER=zzuser\n"
            "POSTGRES_DB=zzdb\n"
            "REDIS_PASSWORD=zzpass\n"
            "KEYCLOAK_REALM=zzrealm\n"
            "GRAFANA_PORT=31337\n"
            "COMPOSE_PROFILES=admin,observability\n"
        )
        with planted(REPO / ".env", dotenv):
            r = pixi("psql", env=fresh())
            expect(
                "psql uses the user and database .env declares",
                recorded(record) == ["exec", "postgres", "psql", "-U", "zzuser", "-d", "zzdb"],
                f"recorded {recorded(record)}",
            )
            r = pixi("redis-cli", env=fresh())
            expect(
                "redis-cli uses the password .env declares",
                recorded(record) == ["exec", "redis", "redis-cli", "-a", "zzpass", "--no-auth-warning"],
                f"recorded {recorded(record)}",
            )
            r = pixi("urls", env=fresh())
            expect("urls prints the port .env declares", "31337" in r.stdout, f"stdout: {r.stdout!r}")
            expect("urls prints the user .env declares", "zzuser" in r.stdout, f"stdout: {r.stdout!r}")
            expect("urls prints the realm .env declares", "zzrealm" in r.stdout, f"stdout: {r.stdout!r}")

            # The stub is a child process, so what it observes is what .env
            # actually exported. Without `set -a` around the source these values
            # would stay shell-local and every child would see nothing.
            r = pixi("ps", env=fresh())
            expect(
                "values from .env are exported to child processes",
                recorded_env(record) == ["COMPOSE_PROFILES=admin,observability"],
                f"observed {recorded_env(record)}",
            )

            # up-core clears COMPOSE_PROFILES and then execs the health wait, which
            # re-reads .env. If .env won, the wait would cover the very containers
            # up-core excluded.
            env = fresh(healthy, core)
            env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "1", "0"
            r = run_script("up-core.sh", env=env)
            profiles = recorded_env(record)
            expect("up-core.sh exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect("up-core.sh starts the stack", recorded(record)[:2] == ["up", "-d"], f"recorded {recorded(record)}")
            expect("up-core.sh made more than one runtime call", len(profiles) > 1, f"observed {profiles}")
            expect(
                "every up-core.sh runtime call sees COMPOSE_PROFILES empty",
                set(profiles) == {"COMPOSE_PROFILES="},
                f"observed {profiles}",
            )

        # --- Every lifecycle task reaches the runtime through the DEVINFRA_COMPOSE seam. ---
        # Five task bodies used to name `docker compose` directly. pixi.toml has no
        # shell expansion, so those five ignored DEVINFRA_COMPOSE entirely and a
        # contributor who set it started the stack under Docker regardless — the
        # setting appeared to work and did not. These cases run the tasks themselves,
        # so a body repointed back at a runtime fails here rather than at review.
        seam_tasks = {
            "start": ["up", "-d"],
            "down": ["--profile", "admin", "--profile", "observability", "down"],
            "stop": ["--profile", "admin", "--profile", "observability", "stop"],
            "pull": ["--profile", "admin", "--profile", "observability", "pull"],
            "config": ["--profile", "admin", "--profile", "observability", "config"],
            "dump-logs": [
                "--profile",
                "admin",
                "--profile",
                "observability",
                "logs",
                # Uncoloured because the destination is a CI log, and bounded because
                # an unbounded dump of fourteen services buries the failure it exists
                # to explain. `logs.sh` cannot be reused: it hard-codes -f and hangs.
                "--no-color",
                "--tail=200",
            ],
        }
        # `start` depends on init, which would otherwise create a .env and leave it.
        with planted(REPO / ".env", (REPO / ".env.example").read_text(encoding="utf-8")):
            for task_name, expected_argv in seam_tasks.items():
                r = pixi(task_name, env=fresh())
                expect(
                    f"{task_name} exits 0 through the seam",
                    r.returncode == 0,
                    f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
                )
                expect(
                    f"{task_name} invokes DEVINFRA_COMPOSE, not docker",
                    recorded(record) == expected_argv,
                    f"recorded {recorded(record)}",
                )

        # The seam's default, against the real runtime: only it can say what an
        # unset DEVINFRA_COMPOSE actually reaches. An exported-but-empty value must
        # read as unset too, exactly as ${DEVINFRA_COMPOSE:-docker compose} does —
        # taken as a value it splits to an empty argv and the first real argument
        # is executed as the command. Docker stays the default; the Podman job
        # changes where the API lives, not what this falls back to.
        for value in (None, ""):
            env = dict(os.environ)
            if value is None:
                env.pop("DEVINFRA_COMPOSE", None)
            else:
                env["DEVINFRA_COMPOSE"] = value
            r = run_script("compose.sh", "version", env=env)
            expect(
                f"compose.sh defaults to docker compose when DEVINFRA_COMPOSE is {value!r}",
                r.returncode == 0 and "Docker Compose" in r.stdout,
                f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
            )

        # --- assert-podman: the gate that makes a silent fallback to Docker impossible. ---
        # A job that sets DOCKER_HOST and reaches a Docker daemon anyway starts the
        # stack, passes every healthcheck and passes the smoke suite. Nothing else in
        # this repository would notice. So the check asks Podman's own API what it is
        # running and compares that against what Compose says this run created.
        lister = write_lister(stubs, "podman-stub")
        list_record = stubs / "list-record.txt"
        list_stdin = stubs / "list-stdin.txt"
        podman_url = "unix:///run/podman/podman.sock"
        containers = "devinfra-postgres\ndevinfra-redis\n"

        def fresh_podman(names: str, url: str | None = podman_url, listing: str = containers) -> dict[str, str]:
            env = fresh(stdout=listing)
            list_record.unlink(missing_ok=True)
            env["DEVINFRA_PODMAN"] = str(lister)
            env["STUB_LIST_RECORD"] = str(list_record)
            env["STUB_LIST_STDOUT"] = names
            env.pop("DOCKER_HOST", None)
            env.pop("DEVINFRA_PODMAN_URL", None)
            if url is not None:
                env["DEVINFRA_PODMAN_URL"] = url
            return env

        r = pixi("assert-podman", env=fresh_podman(containers))
        expect("assert-podman passes when Podman reports every container", r.returncode == 0, f"stderr: {r.stderr!r}")
        expect("assert-podman names the endpoint it verified", podman_url in r.stdout, f"stdout: {r.stdout!r}")
        expect(
            "assert-podman asks Podman over the socket, in remote mode",
            recorded(list_record) == ["--url", podman_url, "ps", "--all", "--format", "{{.Names}}"],
            f"recorded {recorded(list_record)}",
        )

        # The failure this whole story exists to catch: the containers exist, but
        # they are not Podman's.
        r = pixi("assert-podman", env=fresh_podman("devinfra-postgres\n"))
        expect("assert-podman fails when Podman does not report a container", r.returncode != 0, "exited 0")
        expect(
            "assert-podman names the container Podman does not see",
            "devinfra-redis" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # The stack ran entirely under Docker: Podman reports none of them.
        r = pixi("assert-podman", env=fresh_podman(""))
        expect("assert-podman fails when Podman reports none of the containers", r.returncode != 0, "exited 0")
        for name in ("devinfra-postgres", "devinfra-redis"):
            expect(f"assert-podman names {name} as unseen by Podman", name in r.stderr, f"stderr: {r.stderr!r}")

        # An empty expected set is the silent skip this epic removes: with nothing to
        # look for, every comparison passes and the job reports a stack it never ran.
        r = pixi("assert-podman", env=fresh_podman(containers, listing=""))
        expect("assert-podman refuses an empty container set", r.returncode != 0, "an empty set passed")
        expect(
            "assert-podman says there was nothing to verify",
            "nothing to verify" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        env = fresh_podman(containers, url=None)
        r = pixi("assert-podman", env=env)
        expect("assert-podman fails when no endpoint is configured", r.returncode != 0, "exited 0")
        for variable in ("DEVINFRA_PODMAN_URL", "DOCKER_HOST"):
            expect(f"assert-podman names {variable}", variable in r.stderr, f"stderr: {r.stderr!r}")
        expect("assert-podman never reached Podman with no endpoint", not recorded(list_record), "the stub ran")

        # DOCKER_HOST alone must work: it is what the CI job sets, and what the
        # Compose client in that same job talks to.
        env = fresh_podman(containers, url=None)
        env["DOCKER_HOST"] = podman_url
        r = pixi("assert-podman", env=env)
        expect("assert-podman falls back to DOCKER_HOST", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        # --- podman-socket: it reconfigures the machine, so it must refuse by default. ---
        sudo_stub = write_lister(stubs, "sudo-stub")
        socket_file = stubs / "podman.sock"
        # Never the real /etc/systemd path. Every case below drives the *consenting*
        # branch with sudo stubbed; if the drop-in directory were the production one,
        # a single wrong variable would reconfigure the developer's own machine and
        # stop their Docker before any assertion had a chance to fail.
        dropin_dir = stubs / "systemd" / "podman.socket.d"
        dropin_file = dropin_dir / "devinfra-socket-group.conf"
        privileged = [
            # The socket before the service: systemd restarts a stopped
            # docker.service through a still-listening docker.socket.
            "systemctl",
            "stop",
            "docker.socket",
            "docker.service",
            "mkdir",
            "-p",
            str(dropin_dir),
            "tee",
            str(dropin_file),
            "systemctl",
            "daemon-reload",
            "systemctl",
            "enable",
            "podman.socket",
            # `enable --now` leaves an already-active socket untouched, so on an image
            # where podman.socket is running the new SocketGroup would not apply until
            # the next reboot and every Compose call would get permission denied.
            "systemctl",
            "restart",
            "podman.socket",
            # DirectoryMode only governs a directory systemd creates; an existing
            # one keeps its mode, so the script widens it directly. Without this
            # the socket is present and listening yet `test -e` reports false,
            # which is exactly how the first hosted stack-podman run failed.
            "chmod",
            "0755",
            str(socket_file.parent),
        ]
        # The whole unit body, not just the line that matters today: any other change
        # to what is written into /etc/systemd should be a deliberate edit here too.
        dropin_body = "[Socket]\nSocketGroup=docker\nSocketMode=0660\nDirectoryMode=0755\n"

        def fresh_socket(ci: str | None, allow: str | None, socket: Path) -> dict[str, str]:
            env = fresh()
            list_record.unlink(missing_ok=True)
            list_stdin.unlink(missing_ok=True)
            env["DEVINFRA_SUDO"] = str(sudo_stub)
            env["STUB_LIST_RECORD"] = str(list_record)
            env["STUB_LIST_STDIN"] = str(list_stdin)
            env["STUB_LIST_STDOUT"] = ""
            env["DEVINFRA_PODMAN_SOCKET"] = str(socket)
            env["DEVINFRA_PODMAN_DROPIN_DIR"] = str(dropin_dir)
            env.pop("CI", None)
            env.pop("DEVINFRA_ALLOW_RUNTIME_SETUP", None)
            if ci is not None:
                env["CI"] = ci
            if allow is not None:
                env["DEVINFRA_ALLOW_RUNTIME_SETUP"] = allow
            return env

        socket_file.write_text("", encoding="utf-8")
        # `CI=false` is what a workstation exports to turn CI-ish behaviour off.
        # Reading "set" as "yes" would take it as permission to stop that machine's
        # Docker and write under /etc/systemd — the opposite of what it says.
        for refusing_ci, label in ((None, "unset"), ("false", "false"), ("", "empty")):
            r = pixi("ci-podman-socket", env=fresh_socket(refusing_ci, None, socket_file))
            expect(f"podman-socket refuses with CI {label}", r.returncode != 0, "exited 0")
            expect(
                f"podman-socket names the opt-in variable with CI {label}",
                "DEVINFRA_ALLOW_RUNTIME_SETUP" in r.stderr,
                f"stderr: {r.stderr!r}",
            )
            expect(
                f"podman-socket refuses before it stops or writes anything with CI {label}",
                not recorded(list_record),
                f"recorded {recorded(list_record)}",
            )

        r = pixi("ci-podman-socket", env=fresh_socket("true", None, socket_file))
        expect("podman-socket runs under CI", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")
        expect(
            "podman-socket stops Docker, installs the drop-in and restarts podman.socket",
            recorded(list_record) == privileged,
            f"recorded {recorded(list_record)}",
        )
        # podman.socket is created root:root 0660 inside a root:root 0700 directory.
        # SocketGroup/SocketMode make the socket openable; DirectoryMode makes the
        # directory holding it traversable. Both are needed — a socket behind an
        # untraversable directory is unreachable however permissive it is itself.
        expect(
            "podman-socket writes exactly the SocketGroup drop-in",
            (list_stdin.read_text(encoding="utf-8") if list_stdin.exists() else "") == dropin_body,
            f"wrote {(list_stdin.read_text(encoding='utf-8') if list_stdin.exists() else '')!r}",
        )

        r = pixi("ci-podman-socket", env=fresh_socket(None, "1", socket_file))
        expect("podman-socket runs on an explicit opt-in", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        absent_socket = stubs / "no-such.sock"
        r = pixi("ci-podman-socket", env=fresh_socket("true", None, absent_socket))
        expect("podman-socket fails when the socket never appeared", r.returncode != 0, "exited 0")
        expect("podman-socket names the socket path", str(absent_socket) in r.stderr, f"stderr: {r.stderr!r}")

        # A socket that exists but cannot be opened is the failure the drop-in exists
        # to prevent, and it is exactly what an ineffective drop-in leaves behind.
        # Reporting OK there hands the job an unexplained connection error later.
        unreadable = stubs / "locked.sock"
        unreadable.write_text("", encoding="utf-8")
        unreadable.chmod(0o000)
        try:
            r = pixi("ci-podman-socket", env=fresh_socket("true", None, unreadable))
            expect("podman-socket fails when the socket is not usable by this user", r.returncode != 0, "exited 0")
            expect(
                "podman-socket says the socket is not writable",
                "not writable" in r.stderr and str(unreadable) in r.stderr,
                f"stderr: {r.stderr!r}",
            )
        finally:
            unreadable.chmod(0o644)

        # --- lint-compose: one `config -q` per combination of declared profiles. ---
        # The profiles are read back from the model, so a profile added to
        # compose.yaml later is validated without anyone remembering to add it here.
        two = "admin\nobservability\n"
        every_combination = [
            "config",
            "--profiles",
            "config",
            "-q",
            "--profile",
            "admin",
            "config",
            "-q",
            "--profile",
            "observability",
            "config",
            "-q",
            "--profile",
            "admin",
            "--profile",
            "observability",
            "config",
            "-q",
        ]
        r = pixi("lint-compose", env=fresh(profiles=two))
        expect("lint-compose exits 0 when every combination validates", r.returncode == 0, f"exit {r.returncode}")
        expect(
            "lint-compose validates every combination of the declared profiles",
            recorded(record) == every_combination,
            f"recorded {recorded(record)}",
        )
        expect(
            "lint-compose clears COMPOSE_PROFILES so each combination is exactly its flags",
            set(recorded_env(record)) == {"COMPOSE_PROFILES="},
            f"observed {recorded_env(record)}",
        )

        r = pixi("lint-compose", env=fresh(profiles=""))
        expect(
            "lint-compose still validates one combination when no profile is declared",
            recorded(record) == ["config", "--profiles", "config", "-q"],
            f"recorded {recorded(record)}",
        )

        # A failure in one combination must not hide the others.
        r = pixi("lint-compose", env=fresh(profiles=two, exit_code="1"))
        listed = {line.strip() for line in r.stderr.splitlines() if line.startswith("  ")}
        expect("lint-compose fails when a combination fails", r.returncode != 0, "exited 0")
        expect(
            "lint-compose names every failing combination",
            listed == {"(none)", "admin", "observability", "admin,observability"},
            f"listed {listed}; stderr: {r.stderr!r}",
        )

        # An enumeration that could not be read must never be treated as "no
        # profiles": that would validate one combination and report success.
        env = fresh(profiles=two)
        env["STUB_PROFILES_EXIT"] = "1"
        r = pixi("lint-compose", env=env)
        expect("lint-compose fails when the profiles cannot be enumerated", r.returncode != 0, "exited 0")

        # --- assert_config: the rules NFR-2, NFR-4 and AD-17 state, read from the
        # rendered model. Each fixture is what `config --format json` would return.
        def rendered(services: dict[str, object]) -> str:
            return json.dumps({"name": "devinfra", "services": services})

        def port(host_ip: str, published: str, target: int) -> dict[str, object]:
            return {
                "mode": "ingress",
                "host_ip": host_ip,
                "target": target,
                "published": published,
                "protocol": "tcp",
            }

        clean_doc = rendered(
            {
                "postgres": {"image": "pgvector/pgvector:0.8.1-pg17", "ports": [port("127.0.0.1", "5432", 5432)]},
                "redis": {"image": "redis:8-alpine", "ports": [port("127.0.0.1", "6379", 6379)]},
            }
        )
        r = pixi("lint-config", env=fresh(document=clean_doc))
        expect("lint-config accepts a clean rendered config", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        env = fresh(profiles=two, document=clean_doc)
        env["COMPOSE_PROFILES"] = "admin,observability"
        r = pixi("lint-config", env=env)
        expect(
            "lint-config renders every profile combination",
            recorded(record).count("--format") == 4,
            f"recorded {recorded(record)}",
        )
        expect(
            "lint-config clears COMPOSE_PROFILES so each combination is exactly its flags",
            set(recorded_env(record)) == {"COMPOSE_PROFILES="},
            f"observed {recorded_env(record)}",
        )

        # An enumeration that could not be read must never be treated as "no
        # profiles": that would assert against one combination and report success.
        env = fresh(profiles=two, document=clean_doc)
        env["STUB_PROFILES_EXIT"] = "1"
        r = pixi("lint-config", env=env)
        expect("lint-config fails when the profiles cannot be enumerated", r.returncode != 0, "exited 0")

        # A digest is a stricter pin than a tag, so it is accepted.
        digest = rendered({"redis": {"image": "redis@sha256:" + "a" * 64, "ports": []}})
        r = pixi("lint-config", env=fresh(document=digest))
        expect("lint-config accepts a digest-pinned image", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        # A wildcard bind address is refused before a single port is looked at:
        # every port would match it, so the port check alone would pass.
        env = fresh(document=clean_doc)
        env["BIND_ADDRESS"] = "0.0.0.0"
        r = pixi("lint-config", env=env)
        expect("lint-config refuses a wildcard BIND_ADDRESS", r.returncode != 0, "exited 0")
        expect("lint-config names the wildcard address", "0.0.0.0" in r.stderr, f"stderr: {r.stderr!r}")

        # .env must be read as the shell reads it. `export` and a trailing comment
        # are both invisible to `source`. The declared address is deliberately not
        # the default: taking `export ` literally would silently fall back to
        # 127.0.0.1, and keeping the comment would compare every rendered host_ip
        # against "10.1.2.3   # lab" and fail every port.
        lab = rendered({"postgres": {"image": "redis:8-alpine", "ports": [port("10.1.2.3", "5432", 5432)]}})
        with planted(REPO / ".env", "export BIND_ADDRESS=10.1.2.3   # lab\n"):
            env = fresh(document=lab)
            env.pop("BIND_ADDRESS", None)
            r = pixi("lint-config", env=env)
            expect(
                "lint-config reads .env the way sourcing it would",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # An exported-but-empty DEVINFRA_COMPOSE means "unset", as it does in
        # common.sh. Read as a value it would split to an empty argv and execute
        # the first real argument as the command.
        env = fresh()
        env["DEVINFRA_COMPOSE"] = ""
        r = pixi("lint-config", env=env)
        expect(
            "lint-config falls back to docker compose when DEVINFRA_COMPOSE is empty",
            r.returncode == 0 and "Traceback" not in r.stderr,
            f"output: {(r.stdout + r.stderr)!r}",
        )

        wildcard = rendered({"postgres": {"image": "redis:8-alpine", "ports": [port("0.0.0.0", "5432", 5432)]}})
        r = pixi("lint-config", env=fresh(document=wildcard))
        expect("lint-config rejects a port published on every interface", r.returncode != 0, "exited 0")
        expect(
            "lint-config names the service, the port and the address",
            "postgres" in r.stderr and "5432" in r.stderr and "0.0.0.0" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        empty_host = rendered({"postgres": {"image": "redis:8-alpine", "ports": [port("", "5432", 5432)]}})
        r = pixi("lint-config", env=fresh(document=empty_host))
        expect("lint-config rejects a port with no host address", r.returncode != 0, "exited 0")

        collision = rendered(
            {
                "postgres": {"image": "redis:8-alpine", "ports": [port("127.0.0.1", "5432", 5432)]},
                "pgbouncer": {"image": "redis:8-alpine", "ports": [port("127.0.0.1", "5432", 6432)]},
            }
        )
        r = pixi("lint-config", env=fresh(document=collision))
        expect("lint-config rejects two services on one host port", r.returncode != 0, "exited 0")
        expect(
            "lint-config names both services and the port",
            "postgres" in r.stderr and "pgbouncer" in r.stderr and "5432" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        for image in ("redis", "redis:latest"):
            floating = rendered({"redis": {"image": image, "ports": []}})
            r = pixi("lint-config", env=fresh(document=floating))
            expect(f"lint-config rejects the image '{image}'", r.returncode != 0, "exited 0")
            expect(
                f"lint-config names the service and image for '{image}'",
                "redis" in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        r = pixi("lint-config", env=fresh(document=rendered({})))
        expect("lint-config refuses an empty service set", r.returncode != 0, "a pass over zero services")

        # --- assert_pins: the two places every tag lives must agree. ---
        # Driven over throwaway fixtures rather than the tracked pair, because the
        # defect being proved is a tracked file edited alone — planting that inside
        # the repository is the very state the check exists to reject.
        pins_dir = stubs / "pins"
        pins_dir.mkdir(exist_ok=True)

        def pins(compose_body: str, dotenv_body: str) -> subprocess.CompletedProcess[str]:
            compose_fixture = pins_dir / "compose.yaml"
            dotenv_fixture = pins_dir / ".env.example"
            compose_fixture.write_text(compose_body, encoding="utf-8", newline="\n")
            dotenv_fixture.write_text(dotenv_body, encoding="utf-8", newline="\n")
            return tool([sys.executable, str(PINNER), str(compose_fixture), str(dotenv_fixture)])

        # The real shape, including a variable referenced twice: the object-storage
        # image and its init helper share SILO_VERSION, so a check that compared only
        # the first occurrence would miss a second one left behind.
        clean_compose = (
            "services:\n"
            "  redis:\n"
            "    image: redis:${REDIS_VERSION:-8-alpine}\n"
            "  minio:\n"
            "    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026-09-03T13-18-01Z}\n"
            "  minio-init:\n"
            "    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026-09-03T13-18-01Z}\n"
        )
        clean_env = "REDIS_VERSION=8-alpine\nSILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n"

        r = pins(clean_compose, clean_env)
        expect("lint-pins accepts a pair that agrees", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")
        # The exact phrase, not a bare "3": a substring check passes for 13, 23 or 30,
        # so it would survive the very miscount it is supposed to catch.
        expect(
            "lint-pins reports how many pins it compared",
            "OK 3 pin references agree" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        drifted = clean_compose.replace("${REDIS_VERSION:-8-alpine}", "${REDIS_VERSION:-8.9.0-alpine}")
        r = pins(drifted, clean_env)
        expect("lint-pins rejects a drifted tag", r.returncode != 0, "exited 0")
        expect(
            "lint-pins names the variable and both values",
            "REDIS_VERSION" in r.stderr and "8-alpine" in r.stderr and "8.9.0-alpine" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # The second occurrence of a twice-referenced pin must be compared too.
        half_moved = clean_compose.replace(
            "  minio-init:\n    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026-09-03T13-18-01Z}\n",
            "  minio-init:\n    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026-08-01T00-00-00Z}\n",
        )
        r = pins(half_moved, clean_env)
        expect("lint-pins compares every occurrence, not just the first", r.returncode != 0, "exited 0")
        expect("lint-pins names the stale occurrence", "SILO_VERSION" in r.stderr, f"stderr: {r.stderr!r}")

        no_fallback = clean_compose.replace("${REDIS_VERSION:-8-alpine}", "${REDIS_VERSION}")
        r = pins(no_fallback, clean_env)
        expect("lint-pins rejects a reference with no fallback", r.returncode != 0, "exited 0")
        expect("lint-pins names the unguarded variable", "REDIS_VERSION" in r.stderr, f"stderr: {r.stderr!r}")

        r = pins(clean_compose, "SILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n")
        expect("lint-pins rejects a variable no dotenv declares", r.returncode != 0, "exited 0")
        expect("lint-pins names the undeclared variable", "REDIS_VERSION" in r.stderr, f"stderr: {r.stderr!r}")

        # Two sides that agree on nothing at all still render `image: redis:`, which
        # is the untagged image the fallback rule exists to prevent — so equality is
        # not on its own a pass.
        empty_tag = clean_compose.replace("${REDIS_VERSION:-8-alpine}", "${REDIS_VERSION:-}")
        r = pins(empty_tag, "REDIS_VERSION=\nSILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n")
        expect("lint-pins rejects an empty tag both sides agree on", r.returncode != 0, "exited 0")
        expect("lint-pins names the emptily-pinned variable", "REDIS_VERSION" in r.stderr, f"stderr: {r.stderr!r}")

        # The compose-to-dotenv direction alone is blind to a variable nothing reads,
        # which is what a misspelled name and a literal tag written over the
        # interpolation both leave behind.
        r = pins(clean_compose, clean_env + "LOKI_VERSION=3.5.7\n")
        expect("lint-pins rejects a declared pin compose never references", r.returncode != 0, "exited 0")
        expect("lint-pins names the unreferenced variable", "LOKI_VERSION" in r.stderr, f"stderr: {r.stderr!r}")

        r = pins("services:\n  redis:\n    image: redis:8-alpine\n", clean_env)
        expect("lint-pins refuses an empty match set", r.returncode != 0, "a pass over zero pins")

        # A commented-out reference is not in the model Compose renders, so it must
        # not count towards the non-zero-match guard: a file whose every real image
        # had lost its interpolation would otherwise still report pins compared.
        commented_out = (
            "services:\n  redis:\n    image: redis:8-alpine\n    # image: redis:${REDIS_VERSION:-8-alpine}\n"
        )
        r = pins(commented_out, "REDIS_VERSION=8-alpine\n")
        expect("lint-pins does not count a commented-out reference", r.returncode != 0, "a pass over commented text")
        expect("lint-pins says it checked no pins", "checked no pins" in r.stderr, f"stderr: {r.stderr!r}")

        # A commented-out declaration is invisible to `source`, so it must be
        # invisible here: reading it would let a disabled pin satisfy the check.
        r = pins(clean_compose, "# REDIS_VERSION=8-alpine\nSILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n")
        expect("lint-pins ignores a commented-out declaration", r.returncode != 0, "exited 0")

        # …and `export ` plus a trailing comment must be stripped, for the same
        # reason: both are invisible to the shell, so keeping either would fail a
        # pin whose declaration the runtime reads as correct.
        r = pins(clean_compose, "export REDIS_VERSION=8-alpine   # pinned\nSILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n")
        expect("lint-pins reads a declaration the way sourcing it would", r.returncode == 0, f"stderr: {r.stderr!r}")

        # Quoted *and* commented at once: unquoting has to happen before the comment
        # is stripped, or the closing quote lands inside the retained value and the
        # tag reads as `8-alpine"` — false drift on a declaration the shell reads fine.
        r = pins(clean_compose, 'REDIS_VERSION="8-alpine"  # pinned\nSILO_VERSION=RELEASE.2026-09-03T13-18-01Z\n')
        expect(
            "lint-pins unquotes a value that is both quoted and commented", r.returncode == 0, f"stderr: {r.stderr!r}"
        )

        # The task itself, over the tracked pair — the tool passing on fixtures says
        # nothing about whether what CI runs is wired to the real files.
        r = pixi("lint-pins")
        expect("lint-pins passes on the tracked pair", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        # …and passing says nothing about *coverage*. Every fixture case above would
        # still pass if the PIN regex narrowed to match fewer images, and a service
        # added with a hardcoded tag leaves the check reporting OK over the pins it
        # does see. Tying the reported count to the number of `image:` keys is what
        # makes either regression go red.
        image_keys = sum(
            1
            for line in (REPO / "compose.yaml").read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("image:")
        )
        expect(
            "lint-pins compares one pin for every image in the tracked compose file",
            f"OK {image_keys} pin references agree" in r.stdout,
            f"{image_keys} 'image:' keys, stdout: {r.stdout!r}",
        )

        # --- smoke-test: FR-5 by default, FR-16 under SMOKE_STRICT. ---
        # The stub answers `ps` with nothing, so no service is running. .env is
        # planted because the suite interpolates ports at shell level.
        with planted(REPO / ".env", (REPO / ".env.example").read_text(encoding="utf-8")):
            r = run_script("smoke-test.sh", env=fresh())
            expect("smoke-test exits 0 when a service is absent", r.returncode == 0, f"exit {r.returncode}")
            expect("smoke-test reports SKIP by default", "SKIP" in r.stdout, f"stdout: {r.stdout!r}")
            expect("smoke-test skips the absent keycloak", "keycloak not running" in r.stdout, f"stdout: {r.stdout!r}")

            env = fresh()
            env["SMOKE_STRICT"] = "1"
            r = run_script("smoke-test.sh", env=env)
            expect("smoke-test fails under SMOKE_STRICT when a service is absent", r.returncode != 0, "exited 0")
            expect("strict smoke-test prints no SKIP line", "SKIP" not in r.stdout, f"stdout: {r.stdout!r}")
            expect(
                "strict smoke-test names the absent service as a failure",
                "keycloak not running" in r.stdout,
                f"stdout: {r.stdout!r}",
            )

            # The runtime must go through the compose seam, or the strict CI run
            # would talk to a real daemon regardless of what it was pointed at.
            expect(
                "smoke-test drives the runtime through the compose seam",
                bool(recorded(record)),
                "the stub never ran",
            )

            # Preflight: a missing required tool fails before any check runs. PATH
            # is replaced with a directory holding only what the script needs to
            # reach the preflight at all, so `curl` is genuinely absent.
            minimal = stubs / "minimal-bin"
            minimal.mkdir()
            for needed in ("bash", "sh", "dirname"):
                resolved = shutil.which(needed)
                if resolved is not None:
                    os.symlink(resolved, minimal / needed)
            env = fresh()
            env["SMOKE_STRICT"] = "1"
            env["PATH"] = str(minimal)
            r = run_script("smoke-test.sh", env=env)
            expect("smoke-test fails when a required tool is missing", r.returncode != 0, "exited 0")
            for needed in ("curl", "openssl", "base64"):
                expect(f"smoke-test names {needed} as missing", needed in r.stderr, f"stderr: {r.stderr!r}")
            expect(
                "smoke-test preflight runs before any check",
                "PASS" not in r.stdout and "FAIL" not in r.stdout,
                f"stdout: {r.stdout!r}",
            )
            expect("smoke-test never reached the runtime", not recorded(record), f"recorded {recorded(record)}")

            # An unreachable runtime makes every service look absent. Without this
            # preflight the default suite exits 0 having checked nothing, and the
            # strict suite fails a dozen checks without once naming the cause.
            env = fresh(exit_code="1")
            r = run_script("smoke-test.sh", env=env)
            expect("smoke-test fails when the runtime is unreachable", r.returncode != 0, "exited 0 having checked 0")
            expect(
                "smoke-test names the unreachable runtime",
                "not reachable" in r.stderr and str(compose_stub) in r.stderr,
                f"stderr: {r.stderr!r}",
            )
            expect(
                "smoke-test stops before any check when the runtime is unreachable",
                "SKIP" not in r.stdout and "PASS" not in r.stdout,
                f"stdout: {r.stdout!r}",
            )

            # The strict TASK, not just the script with the variable injected by
            # hand: deleting `env = { SMOKE_STRICT = "1" }` from pixi.toml would
            # otherwise leave CI silently running the skip-tolerant suite.
            r = pixi("smoke-strict", env=fresh())
            expect("the smoke-strict task fails on an absent service", r.returncode != 0, "exited 0")
            expect("the smoke-strict task prints no SKIP line", "SKIP" not in r.stdout, f"stdout: {r.stdout!r}")

            # --- Profile precedence, against the real runtime. ---
            # Everything above proves lint-compose *passes* COMPOSE_PROFILES=""; the
            # stub has no precedence rules, so only Compose itself can show that the
            # empty value beats the .env selecting both profiles. Without that, every
            # combination would resolve to the same superset.
            real = dict(os.environ)
            real["COMPOSE_PROFILES"] = ""
            empty_combination = tool(["docker", "compose", "config", "--services"], cwd=REPO, env=real)
            full_combination = tool(
                ["docker", "compose", "--profile", "admin", "--profile", "observability", "config", "--services"],
                cwd=REPO,
                env=real,
            )
            core_services = {line.strip() for line in empty_combination.stdout.splitlines() if line.strip()}
            all_services = {line.strip() for line in full_combination.stdout.splitlines() if line.strip()}
            expect("the real runtime rendered the core selection", bool(core_services), "no services")
            expect(
                "an empty COMPOSE_PROFILES beats the .env that selects every profile",
                core_services < all_services,
                f"core {sorted(core_services)} vs all {sorted(all_services)}",
            )

    # --- An enumeration that cannot be read is a failure, never "no profiles". ---
    # `config --profiles` resolves the whole model, so a real structural defect
    # surfaces there rather than in the per-combination loop. Treating that as an
    # empty profile list would check one combination and report success — the exact
    # silent pass this repository keeps removing.
    with planted(
        REPO / "compose.override.yaml",
        "services:\n  zz-selftest-defect:\n    image: alpine:3\n    depends_on:\n      - zz-absent-service\n",
    ):
        r = pixi("lint-compose")
        output = r.stdout + r.stderr
        expect("lint-compose fails when the model does not resolve", r.returncode != 0, "exited 0")
        expect("lint-compose names the undefined service", "zz-absent-service" in output, f"output: {output!r}")
        expect(
            "lint-compose says the profiles could not be read",
            "could not read the declared profiles" in output,
            f"output: {output!r}",
        )
        expect(
            "lint-compose validated no combination it could not enumerate",
            "profile combination(s) validated" not in output,
            f"output: {output!r}",
        )

    # --- Every script the tasks invoke must survive a fresh clone. ---
    # The stock Python .gitignore excludes `lib/`, which silently swallowed
    # scripts/lib/common.sh. A helper that lints locally and is absent from a
    # clone is the same silent failure this repository keeps removing.
    sources = sorted((REPO / "scripts").rglob("*.sh")) + sorted((REPO / "scripts").rglob("*.py"))
    expect("scripts/ has files to check for tracking", bool(sources), "no scripts found")
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(str(path.relative_to(REPO)) for path in sources),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    expect(
        "no script is excluded by .gitignore",
        not ignored.stdout.strip(),
        f"ignored: {ignored.stdout.split()}",
    )

    # --- The CI workflow is a gate, not a suggestion. ---
    # Checked here rather than trusted, because nothing else in this repository can
    # tell whether the hosted run would have failed: the file is the only artefact.
    # Every workflow is scanned, not just ci.yml, so one added later cannot arrive
    # with a `continue-on-error` nothing looks at.
    workflow_dir = REPO / ".github" / "workflows"
    workflow_files = sorted(workflow_dir.glob("*.y*ml"))
    expect("the repository ships at least one CI workflow", bool(workflow_files), f"nothing in {workflow_dir}")

    workflows: dict[str, Any] = {}
    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        softeners = ("continue-on-error", "|| true", "command -v", "if: always")
        hits = [token for token in softeners if token in text]
        expect(f"no step in {path.name} can report success having checked nothing", not hits, f"contains {hits}")
        installers = ("apt-get", "apt install", "pip install", "brew install", "npm install")
        hits = [token for token in installers if token in text]
        expect(f"{path.name} installs no tool of its own", not hits, f"contains {hits}")

        parsed = yaml.safe_load(text)
        expect(f"{path.name} parses as a workflow", isinstance(parsed, dict), f"parsed as {type(parsed).__name__}")
        if not isinstance(parsed, dict):
            continue
        workflows[path.name] = parsed

        # `on` is a YAML 1.1 boolean, so PyYAML hands it back as True unless the
        # file quotes the key. Both spellings mean the same trigger block.
        triggers = parsed.get("on", parsed.get(True))
        expect(f"{path.name} declares triggers", isinstance(triggers, dict), f"on: {triggers!r}")
        if isinstance(triggers, dict):
            for event in ("push", "pull_request"):
                expect(f"{path.name} runs on {event}", event in triggers, f"triggers: {sorted(triggers)}")

        jobs = parsed.get("jobs")
        expect(f"{path.name} declares jobs", isinstance(jobs, dict) and bool(jobs), "no jobs found")
        if not isinstance(jobs, dict):
            continue
        for job_name in sorted(jobs):
            job = jobs[job_name]
            bound = job.get("timeout-minutes")
            expect(
                f"{path.name} job {job_name} is bounded at 15 minutes or less",
                isinstance(bound, int) and 0 < bound <= 15,
                f"timeout-minutes: {bound!r}",
            )
            for step in job.get("steps") or []:
                # Parsed, not grepped: `if: ${{ always() }}` and
                # `if: success() || failure()` both defeat a substring scan, and
                # both would let a red job report green.
                guard = step.get("if")
                expect(
                    f"{path.name} job {job_name} step guard {guard!r} is a failure-only diagnostic",
                    guard is None or str(guard).strip() == "failure()",
                    f"if: {guard!r}",
                )
                command = step.get("run")
                if command is None or (guard is not None and str(guard).strip() == "failure()"):
                    # A diagnostic that runs only once the job has already failed
                    # is not the job's work, and cannot turn a red run green.
                    continue
                expect(
                    f"{path.name} job {job_name} runs {command.split(chr(10))[0]!r} as a pixi task",
                    command.strip().startswith("pixi run "),
                    f"run: {command!r}",
                )

    # Which task each job runs, pinned by equality. Without this, rewiring the stack
    # job to `pixi run ps` would satisfy every assertion above while CI stopped
    # starting the stack at all.
    ci_jobs = workflows.get("ci.yml", {}).get("jobs", {})
    expected_work = {
        "validate": "pixi run ci",
        "stack": "pixi run ci-stack",
        "stack-podman": "pixi run ci-stack-podman",
    }
    expect("ci.yml declares exactly the three CI jobs", set(ci_jobs) == set(expected_work), f"{set(ci_jobs)}")
    for job_name, expected_command in expected_work.items():
        job = ci_jobs.get(job_name, {})
        work = [
            str(step.get("run")).strip()
            for step in job.get("steps") or []
            if step.get("run") is not None and str(step.get("if", "")).strip() != "failure()"
        ]
        expect(f"CI job {job_name} runs exactly ['{expected_command}']", work == [expected_command], f"runs {work}")

    # Both stack jobs must start every profile the model declares. Hard-coding is fine
    # only while it agrees with the model: a third profile would otherwise be linted
    # by lint-compose and never started, which is the gap this whole story removes.
    # Asserted on the Podman job too, so "no service is silently excluded under
    # Podman" is a gate rather than a sentence in the README.
    declared = tool(["docker", "compose", "config", "--profiles"], cwd=REPO)
    model_profiles = {line.strip() for line in declared.stdout.splitlines() if line.strip()}
    expect("the model declares profiles to compare against", bool(model_profiles), f"stderr: {declared.stderr!r}")
    for job_name in ("stack", "stack-podman"):
        job_profiles = str(ci_jobs.get(job_name, {}).get("env", {}).get("COMPOSE_PROFILES", ""))
        expect(
            f"the {job_name} job starts exactly the profiles the model declares",
            {name.strip() for name in job_profiles.split(",") if name.strip()} == model_profiles,
            f"workflow: {job_profiles!r}; model: {sorted(model_profiles)}",
        )

    # The Podman job's runtime switch is one environment variable. Without it the job
    # would start the stack on the runner's Docker daemon and pass every check in it,
    # so the value is pinned here rather than left to review.
    podman_job = ci_jobs.get("stack-podman", {})
    docker_host = str(podman_job.get("env", {}).get("DOCKER_HOST", ""))
    expect(
        "the stack-podman job points the Docker API at Podman's socket",
        docker_host.startswith("unix://") and "podman" in docker_host,
        f"DOCKER_HOST: {docker_host!r}",
    )
    # The workflow says where the API is; podman-socket.sh verifies the socket it
    # created. Two independent literals for one path: change either alone and the
    # gate stays green while the hosted job fails on a connection error that names
    # neither file.
    socket_default = re.search(
        r"DEVINFRA_PODMAN_SOCKET:-([^}]+)}",
        (REPO / "scripts" / "podman-socket.sh").read_text(encoding="utf-8"),
    )
    expect("podman-socket.sh declares a default socket path", socket_default is not None, "no default found")
    if socket_default is not None:
        expect(
            "the stack-podman job's DOCKER_HOST is the socket podman-socket.sh verifies",
            docker_host == f"unix://{socket_default.group(1)}",
            f"workflow: {docker_host!r}; script default: {socket_default.group(1)!r}",
        )
    # ubuntu-latest moves to a new release with a different Podman and a different
    # Compose major, silently changing what this job proves.
    expect(
        "the stack-podman job pins its runner image",
        str(podman_job.get("runs-on", "")).startswith("ubuntu-") and podman_job.get("runs-on") != "ubuntu-latest",
        f"runs-on: {podman_job.get('runs-on')!r}",
    )

    # Every task CI invokes must exist, or the workflow fails on the runner for a
    # reason no local check would have surfaced.
    for task_name in ("ci", "ci-stack", "ci-stack-podman", "ps", "dump-logs"):
        expect(f"pixi declares the {task_name} task CI invokes", task_name in tasks, "no such task")

    # --- The gate must actually reach every check. ---
    # A task with cases of its own that no chain depends on is a check that runs
    # only in the self-test: `pixi run ci` would stop running it and stay green.
    def chain(task_name: str) -> list[str]:
        body = tasks.get(task_name)
        depends = body.get("depends-on", []) if isinstance(body, dict) else []
        return [str(item) for item in depends] if isinstance(depends, list) else []

    lint_tasks = {name for name in tasks if name.startswith("lint-")}
    orphans = sorted(lint_tasks - set(chain("lint")))
    expect("every lint-* task is reachable from `pixi run lint`", not orphans, f"orphaned: {orphans}")
    expect("`pixi run ci` chains lint and test", {"lint", "test"} <= set(chain("ci")), f"ci depends on {chain('ci')}")
    expect(
        "`pixi run ci-stack` starts the stack, waits and runs the strict suite",
        {"start", "wait", "smoke-strict"} <= set(chain("ci-stack")),
        f"ci-stack depends on {chain('ci-stack')}",
    )
    # The same tasks, in the same order, plus the socket setup that puts the Docker
    # API at Podman and the gate that proves the containers ended up there. Order is
    # part of the contract: asserting the socket first and the proof last is what
    # stops the chain degenerating into a Docker run with a Podman-shaped name.
    podman_chain = chain("ci-stack-podman")
    expect(
        "`pixi run ci-stack-podman` runs the same stack tasks the Docker job runs",
        [name for name in podman_chain if name in {"init", "start", "wait", "smoke-strict"}]
        == [name for name in chain("ci-stack") if name in {"init", "start", "wait", "smoke-strict"}],
        f"ci-stack-podman depends on {podman_chain}",
    )
    expect(
        "`pixi run ci-stack-podman` configures the socket first and proves Podman last",
        podman_chain[:1] == ["ci-podman-socket"] and podman_chain[-1:] == ["assert-podman"],
        f"ci-stack-podman depends on {podman_chain}",
    )

    # --- The Makefile is a shim and nothing more. ---
    # Parsed rather than executed: `make` is not a pixi dependency, and the property
    # that matters is structural. This is what stops a recipe regressing to a no-op
    # (a deleted forward line, or `@-pixi run lint`) while the gate stays green.
    makefile = (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
    recipes: dict[str, list[str]] = {}
    current: str | None = None
    for line in makefile:
        declaration = re.match(r"^([A-Za-z0-9_-]+):.*?## ", line)
        if declaration:
            current = declaration.group(1)
            recipes[current] = []
        elif line.startswith("\t") and current is not None:
            recipes[current].append(line[1:])
        elif line and not line.startswith("\t"):
            current = None

    expect(
        "Makefile exposes exactly the targets it did before pixi",
        set(recipes) == set(MAKE_FORWARDS),
        f"differs by {sorted(set(recipes) ^ set(MAKE_FORWARDS))}",
    )

    # The notice itself: without >&2 a target's stdout is no longer clean, and no
    # case above would notice.
    notice = next((line for line in makefile if first_word(line) == "NOTICE"), "")
    expect("the Makefile defines a deprecation notice", bool(notice), "no NOTICE definition found")
    expect("the deprecation notice goes to stderr", notice.rstrip().endswith(">&2"), f"NOTICE: {notice!r}")
    expect("the deprecation notice names the target", "$@" in notice, f"NOTICE: {notice!r}")

    logic = re.compile(r"\bif\b|\bfor\b|\bwhile\b|\bread\b|&&|\|\|")
    for target, forward in sorted(MAKE_FORWARDS.items()):
        body = recipes.get(target, [])
        # Equality, not membership: a `make destroy` rewired to `pixi run down`
        # would preserve data where the developer asked for deletion, and the
        # reverse would delete every volume. Argument variables are part of the
        # comparison, so dropping $(S) or $(F) fails here too.
        expect(
            f"make {target} forwards to exactly `{forward}`",
            body == ["@$(NOTICE)", forward],
            f"recipe: {body}",
        )
        expect(
            f"make {target} recipe contains no logic",
            not any(logic.search(line) for line in body),
            f"recipe: {body}",
        )

    # Every task the Makefile forwards to must exist: parity, target for target.
    for target, forward in sorted(MAKE_FORWARDS.items()):
        words = forward.split()
        if words[1:2] != ["run"]:
            continue
        expect(f"pixi declares the {words[2]} task make {target} forwards to", words[2] in tasks, "no such task")

    for failure in failures:
        sys.stderr.write(f"lint-selftest: FAIL {failure}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

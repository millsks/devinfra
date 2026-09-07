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


def write_recorder(directory: Path, name: str) -> Path:
    """Write a stub that records what it was asked to do instead of doing it.

    It stands in for `docker compose` and for `curl`, so a script's contract can be
    asserted on the command it would have run. Every invocation appends one argument
    per line to STUB_RECORD and one environment observation to STUB_ENV_RECORD.
    `config` answers with STUB_SERVICES, `ps --all` with STUB_ALL and everything else
    with STUB_STDOUT, because the health wait asks for the expected service set, the
    running containers and the exited ones in the same run and must be able to see all
    three disagree.

    Args:
        directory: Directory to write the stub into.
        name: File name for the stub.

    Returns:
        Path to the stub, marked executable.
    """
    stub = directory / name
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do printf \'%s\\n\' "$a"; done >> "$STUB_RECORD"\n'
        'printf \'COMPOSE_PROFILES=%s\\n\' "${COMPOSE_PROFILES-<unset>}" >> "$STUB_ENV_RECORD"\n'
        'case "$1" in\n'
        "  config) printf '%s' \"${STUB_SERVICES:-}\" ;;\n"
        '  ps) case "${2:-}" in\n'
        "        --all) printf '%s' \"${STUB_ALL:-}\" ;;\n"
        "        *) printf '%s' \"${STUB_STDOUT:-}\" ;;\n"
        "      esac ;;\n"
        "  *) printf '%s' \"${STUB_STDOUT:-}\" ;;\n"
        "esac\n"
        'exit "${STUB_EXIT:-0}"\n',
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    return stub


def stub_env(
    compose: Path,
    record: Path,
    stdout: str = "",
    services: str = "",
    exit_code: str = "0",
) -> dict[str, str]:
    """Build an environment whose container runtime and HTTP client are stubs.

    Args:
        compose: Recording stub standing in for `docker compose` and `curl`.
        record: File the stub appends its arguments to.
        stdout: Text the stub prints for `ps` and any subcommand but `config`.
        services: Text the stub prints for `config`, the expected service set.
        exit_code: Status the stub exits with, for driving a failure path.

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

        def fresh(stdout: str = "", services: str = "", exit_code: str = "0") -> dict[str, str]:
            record.unlink(missing_ok=True)
            record.with_name(record.name + ".env").unlink(missing_ok=True)
            return stub_env(compose_stub, record, stdout, services, exit_code)

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

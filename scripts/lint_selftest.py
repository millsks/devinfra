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
RENOVATOR = Path(__file__).with_name("assert_renovate.py")
REPO = Path(__file__).resolve().parent.parent

#: Constructs that turn a missing tool into a pass. None may appear in a task body.
FORBIDDEN = ("command -v", "which ", "|| true", "skipping")

#: Tools a contributor might not have. The lint surface must supply all of them.
SUPPLIED_TOOLS = ("shellcheck", "yamllint", "python", "python3", "ruff", "mypy")

#: Suffix used to hide a file from a lint glob, then put it back.
MOVED = ".selftest-moved"

#: The tracked directory `pixi run bootstrap` points core.hooksPath at. Its files
#: are extensionless, so every walk that reaches them names the directory.
HOOKS_DIR = ".githooks"

#: The checks that resolve the compose model through a container runtime, and so
#: must stay out of the pre-commit hook: a hook that cannot run while Docker is
#: down trains the `--no-verify` reflex that makes hooks worthless. Written out
#: rather than derived, because a derived expectation agrees with whatever the
#: tasks happen to say — including a `lint-compose` that quietly joined the hook.
RUNTIME_BOUND = ("lint-compose", "lint-config")

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


@contextlib.contextmanager
def throwaway_repo() -> Iterator[Path]:
    """Build a throwaway git repository holding a copy of this repository's hooks.

    Created *inside* this repository rather than beside it, which is the one thing
    that makes an end-to-end hook case possible: the pre-commit hook execs
    `pixi run precommit`, and pixi finds a manifest by walking up from its working
    directory, so a repository under the system temp directory would reach no tasks
    at all. The checks therefore run over this repository's own working tree, which
    is what lets a planted defect here be rejected by a commit made there.

    It is never this repository's own `.git`. `pixi run bootstrap` writes
    `core.hooksPath` into the config every linked worktree of this repository
    shares, so installing here would change the environment of the run doing the
    installing.

    Yields:
        The work tree root of a new repository with `.githooks/` copied in, an
        identity configured and no commits yet.
    """
    with tempfile.TemporaryDirectory(dir=REPO, prefix="zz-selftest-repo-") as tmp:
        work = Path(tmp)
        shutil.copytree(REPO / HOOKS_DIR, work / HOOKS_DIR)
        tool(["git", "init", "--quiet", "--initial-branch=main"], cwd=work)
        tool(["git", "config", "--local", "user.name", "devinfra selftest"], cwd=work)
        tool(["git", "config", "--local", "user.email", "selftest@example.invalid"], cwd=work)
        yield work


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
    # .githooks/ joins the walk. A hook is shell that runs on every commit, so one
    # that is unlinted and unscanned is the same silent skip one directory over —
    # and the files are extensionless, so no `*.sh` glob would ever reach them.
    hook_sources = sorted(REPO.glob(f"{HOOKS_DIR}/*"))
    expect(f"{HOOKS_DIR}/ contains hooks to scan", bool(hook_sources), f"nothing in {HOOKS_DIR}/")
    shell_sources = sorted((REPO / "scripts").rglob("*.sh")) + hook_sources
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

    # common/base.yaml and compose.yaml's anchors are two sources of one truth until
    # every service is extracted: the twelve inlined services read `x-defaults`, the
    # module reads `defaults`. A logging or restart change applied to one and not the
    # other lands on some services and silently skips the rest, and nothing renders
    # both in a way a diff would show. So they are compared here, resolved.
    base_model = yaml.safe_load((REPO / "common" / "base.yaml").read_text(encoding="utf-8"))
    root_model = yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))
    shared = base_model.get("services", {}).get("defaults")
    anchored = root_model.get("x-defaults")
    expect("common/base.yaml declares a 'defaults' service", isinstance(shared, dict), f"got {shared!r}")
    expect("compose.yaml still declares the x-defaults anchor", isinstance(anchored, dict), f"got {anchored!r}")
    expect(
        "the shared fragment and the root anchors resolve to the same configuration",
        shared == anchored,
        f"common/base.yaml defaults {shared!r} != compose.yaml x-defaults {anchored!r}",
    )
    expect(
        "the shared fragment declares only restart, logging and networks",
        isinstance(shared, dict) and set(shared) == {"restart", "logging", "networks"},
        f"common/base.yaml defaults declares {sorted(shared) if isinstance(shared, dict) else shared!r}",
    )

    # --- Every task fails on a real defect. Planted fixtures, never edits in place. ---
    defects: list[tuple[str, Path, str]] = [
        ("lint-shell", REPO / "scripts" / "zz_selftest_defect.sh", '#!/usr/bin/env bash\nv="$1"\necho $v\n'),
        (
            "lint-shell",
            REPO / "scripts" / "lib" / "zz_selftest_defect.sh",
            '#!/usr/bin/env bash\nv="$1"\necho $v\n',
        ),
        # Extensionless, in a dotted directory, reached by a literal `.githooks/*`
        # in the task body. Nothing else here proves pixi's shell expands that
        # pattern: the coverage assertion above compares one Python glob against
        # another and can only ever agree with itself.
        (
            "lint-shell",
            REPO / HOOKS_DIR / "zz_selftest_defect",
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
        ("lint-shell", sorted((REPO / "services" / "postgres" / "seed").glob("*.sh"))),
        ("lint-yaml", sorted((REPO / "docker").rglob("*.yaml")) + sorted((REPO / "docker").rglob("*.yml"))),
        # The two module halves of the lint-yaml glob, pinned one term per entry. Hiding
        # both at once would let either `common/*.y*ml` or `services/**/*.y*ml` be deleted
        # from pixi.toml with every case here still passing, because the other term would
        # empty the file set on its own.
        ("lint-yaml", sorted((REPO / "common").rglob("*.y*ml"))),
        ("lint-yaml", sorted((REPO / "services").glob("*/compose.yaml"))),
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
        base_logging = yaml.safe_load((REPO / "common" / "base.yaml").read_text(encoding="utf-8"))["services"][
            "defaults"
        ]["logging"]

        def rendered(services: dict[str, object]) -> str:
            # Every real service renders with the shared logging options, whether it reads
            # them from the `x-logging` anchor or from common/base.yaml through `extends`,
            # so the stub document carries them too. A service written here without them is
            # stating that defect deliberately.
            filled = {
                name: ({"logging": base_logging, **body} if isinstance(body, dict) else body)
                for name, body in services.items()
            }
            return json.dumps({"name": "devinfra", "services": filled})

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
        expect(
            "lint-config passes the tracked modules' volume and network stanzas",
            "module file(s) declare identifiers only" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # AD-5, and the one rule the rendered model cannot express: the root file wins
        # only on keys it sets, so a module's own `name:` renders straight through and
        # `config -q` accepts it — Postgres would mount a different Docker volume and
        # orphan devinfra_postgres-data with no error anywhere. Planted as a second
        # module rather than an edit to services/postgres/, which is never touched.
        defect_module = REPO / "services" / "zz-selftest-defect"
        # A hard kill skips both `finally` blocks below, so the directory can outlive a
        # run. It must then neither abort the next one nor need removing by hand: it is
        # reused, and torn down whole rather than by an rmdir that a surviving fixture
        # file would defeat.
        defect_module.mkdir(exist_ok=True)
        try:
            for stanza, body in (
                ("volumes", "volumes:\n  postgres-data:\n    name: somewhere-else\n"),
                ("volumes", "volumes:\n  postgres-data:\n    driver_opts:\n      type: tmpfs\n"),
                ("networks", "networks:\n  devinfra:\n    driver: host\n"),
                # `configs:` and `secrets:` merge exactly as the other two do, so a body
                # under either wins from the module in the same way.
                ("configs", "configs:\n  zz-selftest:\n    file: ./elsewhere.conf\n"),
                # The other half of AD-5: an identifier the root never declares is a *new*
                # resource. Body-less, so only the membership rule can reject it.
                ("volumes", "volumes:\n  postgres-dataa:\n"),
            ):
                fixture = defect_module / "compose.yaml"
                with planted(fixture, "services:\n  zz-selftest-defect:\n    image: alpine:3.22\n" + body):
                    r = pixi("lint-config", env=fresh(document=clean_doc))
                    expect(
                        f"lint-config rejects a module {stanza} entry the root does not sanction",
                        r.returncode != 0,
                        "exited 0",
                    )
                    expect(
                        f"lint-config names the module file and the {stanza} stanza",
                        "services/zz-selftest-defect/compose.yaml" in r.stderr and stanza in r.stderr,
                        f"stderr: {r.stderr!r}",
                    )
                    # stdout and stderr interleave by buffering, so a run that emitted its
                    # per-combination OK lines as it earned them would end on a wall of
                    # `OK` with the data-loss diagnostic scrolled off above it.
                    expect(
                        f"lint-config signs off on nothing when the {stanza} stanza is bad",
                        "OK" not in r.stdout,
                        f"stdout: {r.stdout!r}",
                    )

            # A module file that is not UTF-8 must be a named diagnostic, not an
            # interpreter traceback — the contract lint-json, commit-msg and lint-renovate
            # already hold. `read_model` reads text, so it is where the byte is met.
            undecodable = defect_module / "compose.yaml"
            undecodable.write_bytes(b"services:\n  zz: {image: alpine:3.22}\n# caf\xe9\n")
            try:
                r = pixi("lint-config", env=fresh(document=clean_doc))
                expect("lint-config rejects a module file that is not UTF-8", r.returncode != 0, "exited 0")
                expect(
                    "lint-config names the undecodable module without a traceback",
                    # `read_model` raises with the path as constructed, so the separators
                    # are the platform's — compare on the directory name, not a spelling.
                    "zz-selftest-defect" in r.stderr and "not valid UTF-8" in r.stderr and "Traceback" not in r.stderr,
                    f"stderr: {r.stderr!r}",
                )
            finally:
                undecodable.unlink(missing_ok=True)
        finally:
            shutil.rmtree(defect_module, ignore_errors=True)

        # The module scan's own guard. Without a case, the `if not modules` return could be
        # deleted and every assertion above would still pass: the clean case looks for
        # "module file(s) declare identifiers only", which "OK 0 module file(s) ..." also
        # satisfies. A pass over an empty module set verifies nothing about AD-5.
        module_files = sorted((REPO / "services").glob("*/compose.yaml"))
        expect("there are module files to hide", bool(module_files), "found no services/*/compose.yaml")
        with moved_aside(module_files):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect("lint-config refuses an empty module set", r.returncode != 0, "a pass over zero modules")
            expect(
                "lint-config names the empty module set",
                "services/*/compose.yaml" in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        # The rendered half of the same contract: a module that lost its `extends` block
        # renders valid, keeps its image and its ports, and quietly drops the shared
        # logging options and `restart: unless-stopped` with it.
        unshared = rendered(
            {
                "postgres": {
                    "image": "pgvector/pgvector:0.8.1-pg17",
                    "ports": [port("127.0.0.1", "5432", 5432)],
                    "logging": {},
                }
            }
        )
        r = pixi("lint-config", env=fresh(document=unshared))
        expect("lint-config rejects a service that reaches neither logging source", r.returncode != 0, "exited 0")
        expect(
            "lint-config names the service whose logging is not the shared one",
            "postgres" in r.stderr and "logging" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

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

        # --- The compose half is more than one file. ---
        # A service extracted into services/<name>/compose.yaml takes its pin with it,
        # so the pin is declared once in .env.example and referenced only in the module.
        # The reverse scan therefore has to run over the *union* of every file's
        # references: file by file it would report the module's pin missing from the
        # root and the root's twelve missing from the module, and go red on a correct
        # repository — which is how a check stops being read.
        module_dir = pins_dir / "services" / "postgres"
        module_dir.mkdir(parents=True, exist_ok=True)
        module_fixture = module_dir / "compose.yaml"

        def pins_pair(root_body: str, module_body: str, dotenv_body: str) -> subprocess.CompletedProcess[str]:
            root_fixture = pins_dir / "compose.yaml"
            dotenv_fixture = pins_dir / ".env.example"
            for fixture, body in (
                (root_fixture, root_body),
                (module_fixture, module_body),
                (dotenv_fixture, dotenv_body),
            ):
                fixture.write_text(body, encoding="utf-8", newline="\n")
            return tool([sys.executable, str(PINNER), str(root_fixture), str(module_fixture), str(dotenv_fixture)])

        module_compose = "services:\n  postgres:\n    image: pgvector/pgvector:${POSTGRES_VERSION:-0.8.6-pg17}\n"
        module_env = clean_env + "POSTGRES_VERSION=0.8.6-pg17\n"

        # The contrast that gives the case its meaning: the same declaration against the
        # root file alone is exactly the "declared but never referenced" defect above.
        r = pins(clean_compose, module_env)
        expect("lint-pins rejects a module's pin when the module is not read", r.returncode != 0, "exited 0")
        expect("lint-pins names the pin no file it read references", "POSTGRES_VERSION" in r.stderr, f"{r.stderr!r}")

        r = pins_pair(clean_compose, module_compose, module_env)
        expect(
            "lint-pins accepts a pin referenced only in a module file",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )
        expect(
            "lint-pins counts references across every compose file it was given",
            "OK 4 pin references agree" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # Every module file is called compose.yaml, so a diagnostic naming the basename
        # names none of them. The path as given is what a reader can act on.
        r = pins_pair(clean_compose, module_compose.replace(":-0.8.6-pg17}", ":-0.8.6-pg18}"), module_env)
        expect("lint-pins rejects drift inside a module file", r.returncode != 0, "exited 0")
        expect(
            "lint-pins identifies the module by path, not by a bare compose.yaml",
            # The fixture is outside the repository, so the diagnostic carries the path
            # exactly as it was given — separators and all. Comparing against a hardcoded
            # POSIX spelling would fail spuriously on win-64, a platform pixi.toml declares.
            str(module_fixture) in r.stderr and "POSTGRES_VERSION" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # …and that assertion rides the fallback branch, because the fixture is outside
        # the repository. The branch `pixi run lint-pins` actually takes is the other one,
        # so it needs a module inside the repository to name. Without this case `label()`
        # could return `path.name` again and every case above would still pass — the
        # regression the multi-file rewrite exists to prevent.
        repo_module = REPO / "services" / "zz-selftest-defect"
        repo_module.mkdir(exist_ok=True)
        try:
            with planted(
                repo_module / "compose.yaml",
                "services:\n  zz-selftest-defect:\n    image: pgvector/pgvector:${POSTGRES_VERSION:-0.0.0-selftest}\n",
            ):
                r = pixi("lint-pins")
                expect("lint-pins rejects drift in a tracked module file", r.returncode != 0, "exited 0")
                expect(
                    "lint-pins names a tracked module by its repo-relative path",
                    "services/zz-selftest-defect/compose.yaml" in r.stderr,
                    f"stderr: {r.stderr!r}",
                )
        finally:
            shutil.rmtree(repo_module, ignore_errors=True)

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
        #
        # Every compose file, not just the root one: a service extracted into
        # services/<name>/compose.yaml takes its `image:` key with it, so counting the
        # root file alone would let the total fall by one per extraction and call it
        # correct — the check would go green on a module whose pin nothing reads.
        tracked_composes = [REPO / "compose.yaml", *sorted((REPO / "services").glob("*/compose.yaml"))]
        expect(
            "the module compose files are found where the tasks look for them",
            len(tracked_composes) > 1,
            f"only {[str(path) for path in tracked_composes]} — services/*/compose.yaml matched nothing",
        )
        image_keys = sum(
            1
            for path in tracked_composes
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("image:")
        )
        expect(
            "lint-pins compares one pin for every image across the tracked compose files",
            f"OK {image_keys} pin references agree" in r.stdout,
            f"{image_keys} 'image:' keys, stdout: {r.stdout!r}",
        )

        # --- assert_renovate: the update bot's regexes must still match something. ---
        # Every fixture case runs the *tracked* renovate.json against planted files, so a
        # defect proved here is proved against the configuration CI actually ships. A copy
        # of the regexes in the fixture would pass forever after the real ones broke.
        renovate_dir = stubs / "renovate"
        renovate_dir.mkdir(exist_ok=True)
        tracked_renovate = (REPO / "renovate.json").read_text(encoding="utf-8")
        tracked_ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        def write_renovate_fixtures(
            config_body: str,
            dotenv_body: str,
            compose_body: str,
            workflow_body: str = tracked_ci,
        ) -> None:
            for name, body in (
                ("renovate.json", config_body),
                (".env.example", dotenv_body),
                ("compose.yaml", compose_body),
                ("ci.yml", workflow_body),
            ):
                (renovate_dir / name).write_text(body, encoding="utf-8", newline="\n")

        def renovate(
            config_body: str,
            dotenv_body: str,
            compose_body: str,
            workflow_body: str = tracked_ci,
        ) -> subprocess.CompletedProcess[str]:
            write_renovate_fixtures(config_body, dotenv_body, compose_body, workflow_body)
            return tool(
                [
                    sys.executable,
                    str(RENOVATOR),
                    str(renovate_dir / "renovate.json"),
                    str(renovate_dir / "compose.yaml"),
                    str(renovate_dir / ".env.example"),
                    str(renovate_dir / "ci.yml"),
                ]
            )

        # The tracked configuration with one field of its first custom manager replaced —
        # a planted defect in the real config, rather than a fixture config of its own.
        def remanaged(**overrides: object) -> str:
            edited = json.loads(tracked_renovate)
            edited["customManagers"][0].update(overrides)
            return json.dumps(edited, indent=2)

        # Two variables, one of them referenced twice, and one carrying the `versioning=`
        # suffix the annotation grammar allows — the same shape the tracked pair has.
        # The versioning is read out of the tracked configuration rather than written again
        # here: the property under test is that the annotation and the packageRules entry
        # agree, and a second literal would let the fixture agree with itself while the
        # tracked pair drifted apart.
        silo_versioning = next(
            str(rule["versioning"])
            for rule in json.loads(tracked_renovate)["packageRules"]
            if "pgsty/silo" in rule.get("matchPackageNames", [])
        )
        annotated_env = (
            "# renovate: datasource=docker depName=redis\n"
            "REDIS_VERSION=8-alpine\n"
            f"# renovate: datasource=docker depName=pgsty/silo versioning={silo_versioning}\n"
            "SILO_VERSION=RELEASE.2026\n"
        )
        annotated_compose = (
            "services:\n"
            "  redis:\n"
            "    image: redis:${REDIS_VERSION:-8-alpine}\n"
            "  minio:\n"
            "    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026}\n"
            "  minio-init:\n"
            "    image: pgsty/silo:${SILO_VERSION:-RELEASE.2026}\n"
        )

        r = renovate(tracked_renovate, annotated_env, annotated_compose)
        expect(
            "lint-renovate accepts an annotated pair",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )
        # The exact phrase, for the same reason lint-pins uses one: a bare "2" is a
        # substring of the count it is supposed to catch a regression in.
        expect(
            "lint-renovate reports the dependency and reference counts",
            "OK 2 dependencies detected over 5 references" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # An annotation two lines up belongs to whatever sits between it and the
        # declaration, so adjacency — not presence — is the contract.
        r = renovate(
            tracked_renovate,
            annotated_env.replace("# renovate: datasource=docker depName=redis\n", ""),
            annotated_compose,
        )
        expect("lint-renovate rejects an unannotated declaration", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the unannotated variable",
            "REDIS_VERSION" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # The annotation is what Renovate tracks; the compose line is what it edits. If they
        # name different repositories the bot tracks two images and updates one file alone.
        r = renovate(
            tracked_renovate,
            annotated_env.replace("depName=redis\n", "depName=redis/redisinsight\n"),
            annotated_compose,
        )
        expect("lint-renovate rejects a depName compose disagrees with", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the variable and both repositories",
            "REDIS_VERSION" in r.stderr and "'redis/redisinsight'" in r.stderr and "names 'redis'" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # A pattern that matches nothing is the silent skip in its purest form: the bot runs,
        # proposes nothing, and reports success.
        dead_pattern = "NOTHING_IN_THIS_REPOSITORY_MATCHES_THIS"
        tracked_patterns = list(json.loads(tracked_renovate)["customManagers"][0]["matchStrings"])
        r = renovate(remanaged(matchStrings=[*tracked_patterns, dead_pattern]), annotated_env, annotated_compose)
        expect("lint-renovate rejects a pattern that matches nothing", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the manager and the dead pattern",
            "customManagers[0]" in r.stderr and dead_pattern in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        r = renovate(remanaged(matchStrings=[dead_pattern]), annotated_env, annotated_compose)
        expect("lint-renovate rejects a configuration that detects nothing", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate says it detected no dependencies",
            "detected no dependencies" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # managerFilePatterns that select neither file leave every pattern unapplied.
        r = renovate(remanaged(managerFilePatterns=["/^nothing$/"]), annotated_env, annotated_compose)
        expect("lint-renovate rejects file patterns that select nothing", r.returncode != 0, "exited 0")

        # The tracked configuration with one top-level field replaced or removed.
        def reconfigured(remove: tuple[str, ...] = (), **overrides: object) -> str:
            edited = json.loads(tracked_renovate)
            for key in remove:
                edited.pop(key, None)
            edited.update(overrides)
            return json.dumps(edited, indent=2)

        # The manager's own shape. Each of these leaves a configuration that parses, looks
        # maintained, and detects nothing.
        r = renovate(reconfigured(remove=("customManagers",)), annotated_env, annotated_compose)
        expect("lint-renovate rejects a configuration with no custom manager", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate says nothing would detect a pin",
            "customManagers is missing or empty" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        r = renovate(remanaged(customType="jsonata"), annotated_env, annotated_compose)
        expect("lint-renovate rejects a manager that is not a regex manager", r.returncode != 0, "exited 0")
        expect("lint-renovate names the customType", "jsonata" in r.stderr, f"stderr: {r.stderr!r}")

        r = renovate(remanaged(matchStrings=[]), annotated_env, annotated_compose)
        expect("lint-renovate rejects a manager with no matchStrings", r.returncode != 0, "exited 0")

        # `combination` and `recursive` compose the patterns rather than applying each
        # independently, so this check would be reproducing an extraction the bot never runs.
        for strategy in ("combination", "recursive"):
            r = renovate(remanaged(matchStringsStrategy=strategy), annotated_env, annotated_compose)
            expect(f"lint-renovate rejects matchStringsStrategy {strategy}", r.returncode != 0, "exited 0")
            expect(
                f"lint-renovate names the {strategy} strategy",
                strategy in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        # A pattern that still matches but captures nothing. Renovate drops such a reference;
        # counting it here would report coverage the configuration does not have.
        nameless = [pattern.replace("(?<depName>", "(?:") for pattern in tracked_patterns]
        r = renovate(remanaged(matchStrings=nameless), annotated_env, annotated_compose)
        expect("lint-renovate rejects a match with no depName", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the empty capture and the file",
            "depName" in r.stderr and (".env.example" in r.stderr or "compose.yaml" in r.stderr),
            f"stderr: {r.stderr!r}",
        )

        # Without a datasource a reference resolves to no registry; without a versioning the
        # annotation's `versioning=` is inert and the two halves compute different updates.
        r = renovate(
            json.dumps(
                {**json.loads(tracked_renovate)},
                indent=2,
            ).replace('"datasourceTemplate": "docker",\n', ""),
            annotated_env,
            annotated_compose,
        )
        expect("lint-renovate rejects a manager with no datasourceTemplate", r.returncode != 0, "exited 0")
        expect("lint-renovate names the unresolvable datasource", "datasource" in r.stderr, f"stderr: {r.stderr!r}")

        r = renovate(remanaged(datasourceTemplate="npm"), annotated_env, annotated_compose)
        expect("lint-renovate rejects a datasource that is not docker", r.returncode != 0, "exited 0")

        no_versioning = json.loads(tracked_renovate)
        del no_versioning["customManagers"][0]["versioningTemplate"]
        r = renovate(json.dumps(no_versioning, indent=2), annotated_env, annotated_compose)
        expect("lint-renovate rejects a manager with no versioningTemplate", r.returncode != 0, "exited 0")

        # A template that never reads the capture discards the annotation's value silently.
        r = renovate(remanaged(versioningTemplate="docker"), annotated_env, annotated_compose)
        expect("lint-renovate rejects a versioningTemplate that ignores the capture", r.returncode != 0, "exited 0")

        # The compose half has no annotation to read, so a `versioning=` stated only in
        # .env.example applies to one of the two extractions and the bot edits one file alone.
        unrestated = json.loads(tracked_renovate)
        unrestated["packageRules"] = [
            rule for rule in unrestated["packageRules"] if "pgsty/silo" not in rule.get("matchPackageNames", [])
        ]
        r = renovate(json.dumps(unrestated, indent=2), annotated_env, annotated_compose)
        expect("lint-renovate rejects an unrestated annotation versioning", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the variable whose versioning is not restated",
            "SILO_VERSION" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # Grouping is what makes a red run attributable to one image, so both ways it could
        # come back are refused.
        r = renovate(reconfigured(extends=["config:recommended"]), annotated_env, annotated_compose)
        expect("lint-renovate rejects an extends preset", r.returncode != 0, "exited 0")

        grouped = json.loads(tracked_renovate)
        grouped["packageRules"][0]["groupName"] = "all images"
        r = renovate(json.dumps(grouped, indent=2), annotated_env, annotated_compose)
        expect("lint-renovate rejects a packageRules groupName", r.returncode != 0, "exited 0")
        expect("lint-renovate names the group", "all images" in r.stderr, f"stderr: {r.stderr!r}")

        # RE2 has neither lookaround nor backreferences, so a pattern using one matches here
        # and extracts nothing under the bot — green check, silent bot.
        for construct, pattern in (
            ("lookahead", "(?=x)" + tracked_patterns[1]),
            ("backreference", tracked_patterns[1] + r"\1"),
        ):
            r = renovate(remanaged(matchStrings=[tracked_patterns[0], pattern]), annotated_env, annotated_compose)
            expect(f"lint-renovate rejects a pattern using a {construct}", r.returncode != 0, "exited 0")
            expect(
                f"lint-renovate names the {construct} RE2 rejects",
                "RE2" in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        # A pin detected in one file only is exactly the one-file pull request lint-pins
        # rejects, so it must be rejected before the bot ever opens one.
        r = renovate(
            tracked_renovate,
            annotated_env,
            "services:\n  redis:\n    image: redis:${REDIS_VERSION:-8-alpine}\n",
        )
        expect("lint-renovate rejects a pin compose never references", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the pin missing from compose.yaml",
            "SILO_VERSION" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # --- The compose half is more than one file. ---
        # A service extracted into services/<name>/compose.yaml holds its own half of the
        # pin, so managerFilePatterns has to select the module file too. The module is
        # passed as a fifth path, which is what the task does with every
        # services/*/compose.yaml; the first four keep their positions.
        module_renovate_dir = renovate_dir / "services" / "postgres"
        module_renovate_dir.mkdir(parents=True, exist_ok=True)

        def renovate_module(config_body: str, dotenv_body: str, module_body: str) -> subprocess.CompletedProcess[str]:
            module_fixture = module_renovate_dir / "compose.yaml"
            module_fixture.write_text(module_body, encoding="utf-8", newline="\n")
            # The fixtures only; running the four-path form here as well would spend a
            # second subprocess whose result nothing reads.
            write_renovate_fixtures(config_body, dotenv_body, annotated_compose)
            return tool(
                [
                    sys.executable,
                    str(RENOVATOR),
                    str(renovate_dir / "renovate.json"),
                    str(renovate_dir / "compose.yaml"),
                    str(renovate_dir / ".env.example"),
                    str(renovate_dir / "ci.yml"),
                    str(module_fixture),
                ]
            )

        module_annotated_env = annotated_env + (
            "# renovate: datasource=docker depName=pgvector/pgvector\nPOSTGRES_VERSION=0.8.6-pg17\n"
        )
        module_annotated_compose = (
            "services:\n  postgres:\n    image: pgvector/pgvector:${POSTGRES_VERSION:-0.8.6-pg17}\n"
        )

        r = renovate_module(tracked_renovate, module_annotated_env, module_annotated_compose)
        expect(
            "lint-renovate accepts a pin whose compose half lives in a module",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )
        # 3 = the two variables annotated_env declares plus POSTGRES_VERSION. 7 = the six
        # references those first two carry across .env.example and the root fixture, plus
        # the module's one. Both must move when either fixture body changes: a count that
        # tracked the fixtures automatically could not tell a lost module reference from a
        # smaller fixture.
        expect(
            "lint-renovate counts the module's reference too",
            "OK 3 dependencies detected over 7 references" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # Thirteen module files will all be called compose.yaml, so the depName
        # disagreement above has to say *which* one names the other repository. Without
        # this case the diagnostic could go back to "a compose file" and the case above,
        # whose fixture is the root file, would not notice.
        r = renovate_module(
            tracked_renovate,
            module_annotated_env,
            module_annotated_compose.replace("pgvector/pgvector:", "pgvector/elsewhere:"),
        )
        expect("lint-renovate rejects a depName a module disagrees with", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the module that disagrees, not 'a compose file'",
            "services/postgres/compose.yaml names 'pgvector/elsewhere'" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # …and the module pattern is load-bearing, not decoration. Every module file is
        # named compose.yaml, so the root file's own pattern must not reach one: with only
        # the two original patterns the bot sees the .env.example half of the module's pin
        # and nothing else, and would open the one-file pull request lint-pins rejects.
        root_only = [
            entry
            for entry in json.loads(tracked_renovate)["customManagers"][0]["managerFilePatterns"]
            if "services" not in entry
        ]
        expect("the tracked patterns name the module directory", len(root_only) == 2, f"patterns: {root_only}")
        r = renovate_module(remanaged(managerFilePatterns=root_only), module_annotated_env, module_annotated_compose)
        expect("lint-renovate rejects file patterns blind to a module", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the pin detected in one half only",
            "POSTGRES_VERSION" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # A commented-out interpolation is not in the model Compose renders, so it must not
        # satisfy the match-set guard — the same exclusion assert_pins.occurrences() makes.
        r = renovate(
            tracked_renovate,
            annotated_env,
            annotated_compose.replace("    image: redis:", "    # image: redis:"),
        )
        expect("lint-renovate does not count a commented-out image line", r.returncode != 0, "exited 0")

        # A file that is not UTF-8 must be a named diagnostic, not an interpreter traceback.
        for target in (".env.example", "compose.yaml", "ci.yml"):
            renovate(tracked_renovate, annotated_env, annotated_compose)
            (renovate_dir / target).write_bytes(b"\xff\xfe not text\n")
            r = tool(
                [
                    sys.executable,
                    str(RENOVATOR),
                    str(renovate_dir / "renovate.json"),
                    str(renovate_dir / "compose.yaml"),
                    str(renovate_dir / ".env.example"),
                    str(renovate_dir / "ci.yml"),
                ]
            )
            expect(f"lint-renovate rejects a {target} that is not UTF-8", r.returncode != 0, "exited 0")
            expect(
                f"lint-renovate names the undecodable {target} without a traceback",
                target in r.stderr and "not valid UTF-8" in r.stderr and "Traceback" not in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        # A stock manager enabled alongside makes the detected count unattributable to these
        # patterns, so a regex that had stopped matching would hide behind its dependencies.
        stock = json.loads(tracked_renovate)
        stock["enabledManagers"] = ["custom.regex", "docker-compose"]
        r = renovate(json.dumps(stock, indent=2), annotated_env, annotated_compose)
        expect("lint-renovate rejects a stock manager enabled alongside", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the stock manager",
            "docker-compose" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        r = renovate('{"enabledManagers": ["custom.regex",}\n', annotated_env, annotated_compose)
        expect("lint-renovate rejects a configuration that is not JSON", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate names the file and the parse position",
            "renovate.json:1:" in r.stderr and "invalid JSON" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect(
            "lint-renovate reports a parse failure without a traceback",
            "Traceback" not in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # A filtered gate cannot promise to run on a bot pull request. `paths`/`paths-ignore`
        # let a .env.example-only diff through; `branches-ignore` can exclude main while
        # `branches` still names it; and `types` replaces the default set, so a run that
        # dropped `synchronize` would never re-check the branch after Renovate rebases and
        # force-pushes it — the gate would have passed on a commit that is no longer head.
        for filter_name in ("paths", "paths-ignore", "types", "branches-ignore"):
            filtered = tracked_ci.replace(
                "  pull_request:\n    branches: [main]\n",
                f"  pull_request:\n    branches: [main]\n    {filter_name}: ['compose.yaml']\n",
            )
            expect(
                f"the ci.yml fixture actually gained a {filter_name} filter",
                filtered != tracked_ci,
                "the pull_request trigger no longer has the expected shape",
            )
            r = renovate(tracked_renovate, annotated_env, annotated_compose, filtered)
            expect(f"lint-renovate rejects a ci.yml filtered by {filter_name}", r.returncode != 0, "exited 0")
            expect(
                f"lint-renovate names the {filter_name} filter",
                filter_name in r.stderr,
                f"stderr: {r.stderr!r}",
            )

        # The trigger has to exist at all, and it has to target the branch the bot opens
        # against — a gate on a branch nothing merges to is a gate that never runs.
        r = renovate(
            tracked_renovate,
            annotated_env,
            annotated_compose,
            tracked_ci.replace("  pull_request:\n    branches: [main]\n", ""),
        )
        expect("lint-renovate rejects a ci.yml with no pull_request trigger", r.returncode != 0, "exited 0")
        expect(
            "lint-renovate says the gate would run no checks",
            "pull_request" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        r = renovate(
            tracked_renovate,
            annotated_env,
            annotated_compose,
            tracked_ci.replace("  pull_request:\n    branches: [main]\n", "  pull_request:\n    branches: [develop]\n"),
        )
        expect("lint-renovate rejects a pull_request trigger that misses main", r.returncode != 0, "exited 0")
        expect("lint-renovate names the branch it targets", "develop" in r.stderr, f"stderr: {r.stderr!r}")

        # The task itself, over the tracked files.
        r = pixi("lint-renovate")
        expect("lint-renovate passes on the tracked files", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")

        # …and coverage, for the same reason lint-pins ties its count to the `image:` keys.
        # A regex narrowed to fewer images, or a service added without an annotation, leaves
        # the check reporting OK over the pins it still happens to see.
        declared_versions = sum(
            1
            for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
            if re.match(r"^[A-Z][A-Z0-9_]*_VERSION=", line)
        )
        expect(
            "lint-renovate detects one dependency for every *_VERSION declaration",
            f"OK {declared_versions} dependencies detected" in r.stdout,
            f"{declared_versions} declarations, stdout: {r.stdout!r}",
        )
        expect(
            "lint-renovate detects every declaration and every compose reference",
            f"over {declared_versions + image_keys} references" in r.stdout,
            f"{declared_versions} declarations + {image_keys} 'image:' keys, stdout: {r.stdout!r}",
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
    # The hooks are checked the same way: a clone whose .githooks/ arrived ignored
    # has no hooks at all, and `pixi run bootstrap` would point core.hooksPath at
    # a directory that is not there.
    sources = sorted((REPO / "scripts").rglob("*.sh")) + sorted((REPO / "scripts").rglob("*.py")) + hook_sources
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
            if path.name == "ci.yml":
                # The gate, and the only workflow a change is checked by. It must run on
                # both events a change can arrive as. The `pull_request` half is what the
                # hosted Renovate App depends on: `renovate[bot]` is a separate installation,
                # so its pull requests trigger this workflow like anyone's, and every image
                # bump it proposes is checked before it can be merged.
                for event in ("push", "pull_request"):
                    expect(f"{path.name} runs on {event}", event in triggers, f"triggers: {sorted(triggers)}")
            else:
                # Every other workflow is scheduled or hand-started, and must be
                # unreachable by a change. A second workflow on push or pull_request would
                # be a second, weaker definition of what a change is checked by — and the
                # weaker one is the one a reader would trust, because it is green sooner.
                expect(
                    f"{path.name} is scheduled or hand-started",
                    bool({"schedule", "workflow_dispatch"} & set(triggers)),
                    f"triggers: {sorted(triggers)}",
                )
                reachable = sorted({"push", "pull_request"} & set(triggers))
                expect(
                    f"{path.name} is not reachable by a change",
                    not reachable,
                    f"also runs on {reachable}",
                )

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

    # --- Commit-time checks run the same tasks the gate runs. ---
    # The hooks carry no logic, so what is asserted here is the wiring: the mode
    # git needs before it will run them at all, that each reaches one declared task
    # and nothing else, and that the task it reaches is a subset of the gate.
    def body_of(task_name: str) -> str:
        entry = tasks.get(task_name)
        return str(entry.get("cmd", "")) if isinstance(entry, dict) else str(entry)

    for task_name in ("bootstrap", "precommit", "commit-msg"):
        expect(f"pixi declares the {task_name} task", task_name in tasks, "no such task")

    # git ignores a hook file it cannot execute and says nothing about it: the
    # commit lands unchecked and the developer is never told. The mode git records
    # is what decides that, so the index is read rather than the working tree.
    tracked_modes: dict[str, str] = {}
    for line in tool(["git", "ls-files", "--stage", "--", HOOKS_DIR], cwd=REPO).stdout.splitlines():
        meta, _, tracked_name = line.partition("\t")
        fields = meta.split()
        if len(fields) == 3:
            tracked_modes[tracked_name] = fields[0]
    # Driven by what git tracks rather than by what the directory holds: an editor
    # backup or a merge .orig left in .githooks/ is not a hook git will ever run,
    # and failing on it would say nothing a developer could act on.
    expect(
        f"git tracks the files in {HOOKS_DIR}/", bool(tracked_modes), f"git ls-files reports nothing under {HOOKS_DIR}/"
    )
    for relative, mode in sorted(tracked_modes.items()):
        expect(
            f"{relative} is tracked with the mode git needs to run it",
            mode == "100755",
            f"git ls-files reports mode {mode!r}; `git add --chmod=+x {relative}` fixes it",
        )

    hook_tasks: dict[str, str] = {}
    for hook in hook_sources:
        hook_body = hook.read_text(encoding="utf-8")
        code = [line for line in hook_body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        handoffs = [line for line in code if line.startswith("exec ")]
        expect(f"{hook.name} hands off exactly once", len(handoffs) == 1, f"exec lines: {handoffs}")
        if len(handoffs) != 1:
            continue
        expect(f"{hook.name} hands off as its last act", code[-1] == handoffs[0], f"last line: {code[-1]!r}")
        words = handoffs[0].split()
        expect(f"{hook.name} hands off to pixi", words[:3] == ["exec", "pixi", "run"], f"line: {handoffs[0]!r}")
        named = words[3] if len(words) > 3 else ""
        expect(f"{hook.name} names a task pixi.toml declares", named in tasks, f"names {named!r}")
        hook_tasks[hook.name] = named
        # The headline criterion: a hook that named a tool or a version would be a
        # second declaration of what runs, drifting from pixi.lock the moment one
        # of the two moved. Everything it reaches comes from the task it names.
        # Comments are stripped first, as the FORBIDDEN walk above strips them: a
        # hook that explains in prose which tool the task it names will reach is
        # accurate, not a second declaration of anything.
        executable = "\n".join(code)
        named_tools = [name for name in SUPPLIED_TOOLS if name in executable]
        expect(f"{hook.name} names no tool", not named_tools, f"names {named_tools}")
        expect(f"{hook.name} pins no version", not re.search(r"\d+\.\d+", executable), f"body: {executable!r}")

    # pre-merge-commit is not a duplicate of pre-commit: git runs it, and never
    # pre-commit, when `git merge` creates a commit. Without it every merge commit
    # lands with the offline checks not run.
    for hook_name, task_run in (
        ("pre-commit", "precommit"),
        ("pre-merge-commit", "precommit"),
        ("commit-msg", "commit-msg"),
    ):
        expect(f"the {hook_name} hook runs the {task_run} task", hook_tasks.get(hook_name) == task_run, f"{hook_tasks}")

    # `chain()` reads one level of depends-on. Containment has to hold over the
    # whole reachable set, or a member of precommit that later grew a depends-on
    # naming lint-compose would put a container runtime back in the hook with every
    # assertion below still green.
    def closure(task_name: str) -> set[str]:
        seen: set[str] = set()
        pending = list(chain(task_name))
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(chain(current))
        return seen

    precommit_members = closure("precommit")
    lint_members = closure("lint")
    expect("`pixi run precommit` runs checks at all", bool(precommit_members), "precommit depends on nothing")
    expect(
        "every check the pre-commit hook runs is one `pixi run lint` runs",
        precommit_members <= lint_members,
        f"outside lint: {sorted(precommit_members - lint_members)}",
    )
    expect(
        "the checks that need a container runtime stay in the gate",
        set(RUNTIME_BOUND) <= lint_members,
        f"lint runs {sorted(lint_members)}",
    )
    expect(
        "the pre-commit hook reaches no check that needs a container runtime",
        not (precommit_members & set(RUNTIME_BOUND)),
        f"runtime-bound members: {sorted(precommit_members & set(RUNTIME_BOUND))}",
    )
    reaches_runtime = sorted(
        name for name in precommit_members if "assert_config" in body_of(name) or "scripts/compose.sh" in body_of(name)
    )
    expect("no pre-commit check reaches the compose seam", not reaches_runtime, f"reaches it: {reaches_runtime}")

    # And proved by running it, not only by reading it: a member that grew a call to
    # a container runtime would pass every scan above and still make a commit
    # impossible on a machine where nothing is running.
    with tempfile.TemporaryDirectory() as stub_dir:
        runtime_stubs = Path(stub_dir)
        for name in ("docker", "podman", "docker-compose"):
            write_stub(runtime_stubs, name)
        no_runtime = dict(os.environ)
        no_runtime["PATH"] = str(runtime_stubs) + os.pathsep + no_runtime.get("PATH", "")
        r = pixi("precommit", env=no_runtime)
        expect(
            "precommit passes with every container runtime shadowed by a failing stub",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )

    # --- The message contract, row by row, driven through the task the hook runs. ---
    with tempfile.TemporaryDirectory() as tmp:
        messages = Path(tmp)

        def judge(label: str, body_text: str) -> subprocess.CompletedProcess[str]:
            path = messages / f"{label.replace(' ', '-')}.txt"
            path.write_text(body_text, encoding="utf-8", newline="\n")
            return pixi("commit-msg", str(path))

        # git generates four of these itself and then runs the hook over them, so a
        # contract that rejected any of them would break every merge and every
        # `git commit --fixup` in this repository.
        accepted = {
            "a conventional subject": "feat: add the commit-time hooks\n",
            "a scoped breaking subject": "feat(api)!: drop the v1 endpoint\n",
            "a subject with a body": "fix: name the offending file\n\nThe diagnostic said nothing useful before.\n",
            "a merge subject": "Merge branch 'topic' into main\n",
            "a revert subject": 'Revert "feat: add the commit-time hooks"\n',
            # git writes this one for a revert of a revert.
            "a reapply subject": 'Reapply "feat: add the commit-time hooks"\n',
            "a fixup subject": "fixup! feat: add the commit-time hooks\n",
            "a squash subject": "squash! feat: add the commit-time hooks\n",
            "an amend subject": "amend! feat: add the commit-time hooks\n",
            # Everything below the scissors is the diff git appended, not the
            # author's message. Judging it would fail on any diff whose first line
            # happened to look like a subject — as this fixture's does.
            "a verbose commit scissors block": (
                "docs: explain the hook\n"
                "\n"
                "# Please enter the commit message for your changes.\n"
                "# ------------------------ >8 ------------------------\n"
                "diff --git a/x b/x\n"
                "updated the readme\n"
            ),
        }
        for label, body_text in accepted.items():
            r = judge(label, body_text)
            expect(f"commit-msg accepts {label}", r.returncode == 0, f"exit {r.returncode}: {(r.stdout + r.stderr)!r}")

        rejected = [
            ("a subject with no type", "updated the readme\n", "type(optional-scope)!: description"),
            # The generated forms are matched by the prefixes git writes, not by a
            # leading word. Without these three rows, dropping the rest of the
            # prefix would leave every accepted row above still green.
            ("prose beginning Merged", "Merged the two configs\n", "type(optional-scope)!: description"),
            ("prose beginning Merge", "Merge the two configs into one\n", "type(optional-scope)!: description"),
            ("prose beginning Reapplying", "Reapplying the change\n", "type(optional-scope)!: description"),
            ("a subject with no colon", "feat add hooks\n", "type(optional-scope)!: description"),
            ("an unknown type", "feature: add hooks\n", "'feature' is not a commit type"),
            ("an empty description", "fix:\n", "empty description"),
            ("a whitespace-only description", "fix:   \n", "empty description"),
            ("no space after the colon", "fix:name the file\n", "no space after the colon"),
            ("an empty scope", "feat(): add hooks\n", "empty scope"),
            ("a message that is only comments", "\n# Please enter the commit message.\n\n", "the message is empty"),
        ]
        outputs: dict[str, str] = {}
        for label, body_text, needle in rejected:
            r = judge(label, body_text)
            outputs[label] = r.stdout + r.stderr
            expect(f"commit-msg rejects {label}", r.returncode != 0, "exited 0")
            expect(
                f"commit-msg says what was wrong with {label}", needle in outputs[label], f"said: {outputs[label]!r}"
            )
        # A developer told only that the message is invalid reaches for --no-verify.
        expect(
            "commit-msg lists the types it would have accepted",
            "allowed types:" in outputs["an unknown type"] and "feat" in outputs["an unknown type"],
            f"said: {outputs['an unknown type']!r}",
        )

        # Argument defects are diagnostics, never tracebacks: the hook's output is
        # the only thing the developer sees when a commit is refused.
        r = pixi("commit-msg")
        expect("commit-msg with no path exits non-zero", r.returncode != 0, "exited 0")
        expect("commit-msg with no path prints usage", "usage" in (r.stdout + r.stderr), f"said: {r.stderr!r}")

        absent_message = messages / "absent.txt"
        r = pixi("commit-msg", str(absent_message))
        expect("commit-msg on a missing file exits non-zero", r.returncode != 0, "exited 0")
        expect("commit-msg names the missing file", "absent.txt" in (r.stdout + r.stderr), f"said: {r.stderr!r}")

        binary = messages / "binary.txt"
        binary.write_bytes(b"feat: \xff\xfe not utf-8\n")
        r = pixi("commit-msg", str(binary))
        expect("commit-msg on a non-UTF-8 file exits non-zero", r.returncode != 0, "exited 0")
        expect("commit-msg names the non-UTF-8 file", "binary.txt" in (r.stdout + r.stderr), f"said: {r.stderr!r}")
        expect("commit-msg does not traceback on a non-UTF-8 file", "Traceback" not in r.stderr, f"said: {r.stderr!r}")

    # --- The installer, and a real commit through the hooks it installs. ---
    installer = str(REPO / "scripts" / "bootstrap.sh")
    with tempfile.TemporaryDirectory() as outside:
        r = tool([installer], cwd=Path(outside))
        expect("bootstrap refuses outside a git work tree", r.returncode != 0, "exited 0")
        expect(
            "bootstrap names the condition it refused on",
            "work tree" in (r.stdout + r.stderr),
            f"said: {(r.stdout + r.stderr)!r}",
        )

    with throwaway_repo() as clone:
        reads_back = ["git", "config", "--get", "core.hooksPath"]
        local_reads_back = ["git", "config", "--local", "--get", "core.hooksPath"]

        # An installer with nothing to install must say so rather than point
        # core.hooksPath at a directory that is not there.
        hidden = clone / f"{HOOKS_DIR}-hidden"
        (clone / HOOKS_DIR).rename(hidden)
        r = tool([installer], cwd=clone)
        expect("bootstrap refuses a clone with no hooks directory", r.returncode != 0, "exited 0")
        expect(
            "bootstrap names the directory it could not find",
            HOOKS_DIR in (r.stdout + r.stderr),
            f"said: {(r.stdout + r.stderr)!r}",
        )
        (clone / HOOKS_DIR).mkdir()
        r = tool([installer], cwd=clone)
        expect("bootstrap refuses an empty hooks directory", r.returncode != 0, "exited 0")
        expect("bootstrap says the directory is empty", "empty" in (r.stdout + r.stderr), f"said: {r.stderr!r}")
        (clone / HOOKS_DIR).rmdir()
        hidden.rename(clone / HOOKS_DIR)

        # The bad-mode refusal is checked while there is still nothing to overwrite:
        # an installer that writes the config and then refuses has left the clone
        # pointing at hooks git will ignore.
        (clone / HOOKS_DIR / "pre-commit").chmod(0o644)
        r = tool([installer], cwd=clone)
        expect("bootstrap refuses a hook it cannot execute", r.returncode != 0, "exited 0")
        expect(
            "bootstrap names the hook that is not executable",
            "pre-commit" in (r.stdout + r.stderr),
            f"said: {(r.stdout + r.stderr)!r}",
        )
        expect(
            "a refused install writes no core.hooksPath",
            tool(local_reads_back, cwd=clone).returncode != 0,
            "core.hooksPath was set anyway",
        )
        (clone / HOOKS_DIR / "pre-commit").chmod(0o755)

        r = tool([installer], cwd=clone)
        expect("bootstrap installs the hooks", r.returncode == 0, f"exit {r.returncode}: {(r.stdout + r.stderr)!r}")
        expect(
            "core.hooksPath reads back as the tracked directory",
            tool(reads_back, cwd=clone).stdout.strip() == HOOKS_DIR,
            f"reads back {tool(reads_back, cwd=clone).stdout.strip()!r}",
        )

        r = tool([installer], cwd=clone)
        expect("bootstrap is idempotent", r.returncode == 0, f"exit {r.returncode}: {(r.stdout + r.stderr)!r}")
        expect("a rerun says the value was already set", "already" in r.stdout, f"said: {r.stdout!r}")

        tool(["git", "config", "--local", "core.hooksPath", ".git/hooks"], cwd=clone)
        r = tool([installer], cwd=clone)
        expect(
            "bootstrap overwrites another value", r.returncode == 0, f"exit {r.returncode}: {(r.stdout + r.stderr)!r}"
        )
        expect("bootstrap prints the value it overwrote", ".git/hooks" in r.stdout, f"said: {r.stdout!r}")
        expect(
            "core.hooksPath reads back as the tracked directory after an overwrite",
            tool(reads_back, cwd=clone).stdout.strip() == HOOKS_DIR,
            f"reads back {tool(reads_back, cwd=clone).stdout.strip()!r}",
        )

        # git hands commit-msg a path relative to the work tree root, and in a real
        # clone that is also the pixi manifest root, so the task resolves it. Here
        # the two are different directories by construction — the repository has to
        # sit inside this one for pixi to find any tasks at all — so the fixture
        # asks git for absolute paths instead. It changes nothing the hooks do.
        clone_env = dict(os.environ)
        clone_env["GIT_DIR"] = str(clone / ".git")
        clone_env["GIT_WORK_TREE"] = str(clone)

        def commit(
            name: str,
            message: str,
            *flags: str,
            env: dict[str, str] | None = None,
            cwd: Path | None = None,
        ) -> subprocess.CompletedProcess[str]:
            (clone / name).write_text(f"{name}\n", encoding="utf-8", newline="\n")
            tool(["git", "add", "--", name], cwd=clone, env=clone_env)
            return tool(["git", "commit", *flags, "-m", message], cwd=cwd or clone, env=env or clone_env)

        r = commit("clean.txt", "feat: prove a clean commit still lands")
        expect(
            "a clean tree and a conventional message commit",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )

        # githooks(5) chdirs to the work tree root before running a hook, which is
        # the whole reason the hooks need no path handling. Proved from a
        # subdirectory, because that is where it would stop being true.
        (clone / "sub").mkdir()
        r = commit("sub/nested.txt", "feat: commit from a subdirectory", cwd=clone / "sub")
        expect(
            "a commit issued from a subdirectory is checked the same way",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )

        # The defects are planted in *this* repository, because that is the tree the
        # hook's `pixi run precommit` walks. What is proved is the whole path: git
        # runs the hook, the hook runs the task, the task finds the defect, and the
        # commit does not happen.
        shell_defect = REPO / "scripts" / "zz_selftest_hook_defect.sh"
        with planted(shell_defect, '#!/usr/bin/env bash\nv="$1"\necho $v\n'):
            r = commit("shell.txt", "feat: this commit must not land")
            output = r.stdout + r.stderr
            expect("the pre-commit hook rejects a shell defect", r.returncode != 0, "the commit landed")
            expect("the rejection names the offending script", shell_defect.name in output, f"said: {output!r}")
            expect("the rejection names the rule that caught it", "SC2086" in output, f"said: {output!r}")

        yaml_defect = REPO / "docker" / "loki" / "zz_selftest_hook_defect.yaml"
        with planted(yaml_defect, "root:\n  a: 1\n      b: 2\n"):
            r = commit("yaml.txt", "feat: this commit must not land either")
            output = r.stdout + r.stderr
            expect("the pre-commit hook rejects a YAML defect", r.returncode != 0, "the commit landed")
            expect("the rejection names the offending document", yaml_defect.name in output, f"said: {output!r}")
            expect("the rejection names the yamllint rule", "(syntax)" in output, f"said: {output!r}")

        r = commit("message.txt", "updated the readme")
        output = r.stdout + r.stderr
        expect("the commit-msg hook rejects a non-conventional message", r.returncode != 0, "the commit landed")
        expect("the rejection names the offending subject", "updated the readme" in output, f"said: {output!r}")

        # The documented escape. It is not a defect: CI is the authoritative gate,
        # and a hook a developer cannot get past is one they disable permanently.
        r = commit("bypass.txt", "nope, not conventional at all", "--no-verify")
        expect(
            "--no-verify lands the commit anyway", r.returncode == 0, f"exit {r.returncode}: {(r.stdout + r.stderr)!r}"
        )

        # A merge runs pre-merge-commit and then commit-msg — never pre-commit — so
        # both halves are proved with a real merge rather than with fixtures that
        # look like one: the checks must still run, and the message git generated
        # for itself must still be accepted.
        branch = tool(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=clone, env=clone_env).stdout.strip()
        tool(["git", "checkout", "--quiet", "-b", "zz-selftest-merge"], cwd=clone, env=clone_env)
        commit("branch.txt", "feat: work done on a branch", "--no-verify")
        tool(["git", "checkout", "--quiet", branch], cwd=clone, env=clone_env)

        with planted(shell_defect, '#!/usr/bin/env bash\nv="$1"\necho $v\n'):
            r = tool(["git", "merge", "--no-ff", "--no-edit", "zz-selftest-merge"], cwd=clone, env=clone_env)
            output = r.stdout + r.stderr
            expect("the pre-merge-commit hook rejects a merge over a shell defect", r.returncode != 0, "it merged")
            expect("the refused merge names the offending script", shell_defect.name in output, f"said: {output!r}")
            tool(["git", "merge", "--abort"], cwd=clone, env=clone_env)

        r = tool(["git", "merge", "--no-ff", "--no-edit", "zz-selftest-merge"], cwd=clone, env=clone_env)
        expect(
            "a clean merge passes both hooks on the message git generated",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )

        # A hook that cannot find pixi must reject the commit. Falling through to a
        # successful commit is the silent skip this repository keeps removing, one
        # layer further out than any lint task can see.
        lean = os.pathsep.join(
            entry
            for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and not (Path(entry) / "pixi").exists() and not (Path(entry) / "pixi.exe").exists()
        )
        expect("the pixi-free PATH really has no pixi", shutil.which("pixi", path=lean) is None, "pixi still resolves")
        expect("the pixi-free PATH still reaches git", shutil.which("git", path=lean) is not None, "git went with it")
        stripped = dict(clone_env)
        stripped["PATH"] = lean
        r = commit("nopixi.txt", "feat: this must not land unchecked", env=stripped)
        output = r.stdout + r.stderr
        expect("a hook whose pixi is missing rejects the commit", r.returncode != 0, "the commit landed unchecked")
        expect("the refusal names what could not be found", "pixi" in output, f"said: {output!r}")

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

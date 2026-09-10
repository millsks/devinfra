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

import base64
import contextlib
import gzip
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.parse
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

#: Every name the root compose.yaml's `x-app-variables:` registry declares (ADR 0003's
#: application tier, ADR 0017). Stated here rather than derived from the registry for the
#: same reason MAKE_FORWARDS is stated rather than read out of the Makefile: every other
#: assertion about the registry iterates whatever it happens to contain, so a *deleted*
#: entry — a name an application's SDK actually reads, dropped by a bad merge — would
#: satisfy all of them and vanish from docs/ENDPOINTS.md, `pixi run urls` and the
#: repository with the whole gate green. Adding or removing one is a deliberate edit here.
APP_VARIABLES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_ENDPOINT_URL",
    "AWS_SECRET_ACCESS_KEY",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "DATABASE_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_DISCOVERY_URL",
    "OIDC_ISSUER",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "REDIS_URL",
    "SMTP_HOST",
    "SMTP_PORT",
}

#: Exactly the application variables the worked example reads, written out name by name for
#: the same reason APP_VARIABLES is written out rather than derived: every other assertion
#: about the example iterates whatever its own table happens to hold, so a *deleted* read —
#: the object-storage integration quietly dropped by a bad merge — would satisfy all of them
#: while the contract it exists to prove went untested. Deriving this from APP_VARIABLES
#: would reintroduce exactly that: the two sets would move together and a name lost from
#: both would still compare equal.
#:
#: This set must stay a subset of APP_VARIABLES, which is the property that makes a renamed
#: registry key fail `pixi run test` with no container runtime anywhere near it (ADR 0019).
#: CELERY_BROKER_URL and CELERY_RESULT_BACKEND are the two registry names deliberately
#: absent: the example ships no Celery worker, and reading a variable it does not use would
#: make its refusal fire for a Module it does not need.
EXAMPLE_VARIABLES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_ENDPOINT_URL",
    "AWS_SECRET_ACCESS_KEY",
    "DATABASE_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_DISCOVERY_URL",
    "OIDC_ISSUER",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "REDIS_URL",
    "SMTP_HOST",
    "SMTP_PORT",
}

#: The example's own directory, and the one module the name-set pin names. The directory is
#: asserted to hold exactly that module, so a second one cannot arrive reading whatever it
#: likes with the pin still agreeing with itself.
EXAMPLE_DIR = "examples"
EXAMPLE_APP = "examples/worked-example/main.py"

#: One `"NAME": "integration",` row of the example's REQUIRED table — a four-space-indented
#: dict key. Matched rather than imported: importing the module would need every client
#: library resolvable at self-test time and would run its imports, and what is being pinned
#: is the text a reviewer reads. Applied to every Python file under `examples/`, not to the
#: one module alone, so the union of what the directory declares is what the pin compares.
EXAMPLE_READ = re.compile(r'^ {4}"([A-Z][A-Z0-9_]*)": ', re.MULTILINE)

#: An environment read that names its variable inline, in all four spellings Python offers:
#: `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`, and either of the bare forms
#: reached through `from os import environ, getenv`. Every read under `examples/` must go
#: through the REQUIRED table instead, or the table stops being the whole list and the set
#: equality above stops meaning anything. A read whose argument is a *variable* —
#: `os.environ.get(name, "")`, which is how the table is walked — is not one of these.
INLINE_ENV_READ = re.compile(r"(?:os\.)?(?:environ(?:\.get)?[(\[]|getenv\()\s*[\"'][A-Za-z_]")

#: An address literal: a `host:port` pair, in every spelling one can be written. Four
#: alternatives, because a DSN, an endpoint URL and a bare container address are the same
#: defect wearing different clothes:
#:
#:   scheme://[user[:password]@]host:port   `postgresql://devinfra:devinfra@postgres:5432/db`
#:   [user[:password]@]host:port            the same DSN with the scheme cut off
#:   <loopback>:port                        `localhost:5432`, `127.0.0.1:9100`, `[::1]:6379`
#:   <single-label host>:port               `minio:9000`, `postgres:5432`
#:
#: The last one is deliberately restricted to a host with no dot in it, and to one not
#: preceded by a word character, a dot or a slash. This repository cites code as
#: `scripts/endpoints.py:714`, and a rule that read a dotted label as a hostname would fail
#: every file that follows the house citation convention — which is a check nobody could
#: keep, and the surest way to have this one deleted. A quote or a backtick before the host
#: is emphatically *not* excluded: `"minio:9000"` in Python and a markdown code span in a
#: README are the two most likely places for an address to be written down, and excluding
#: them to dodge a hypothetical minified `{"timeout":30}` would gut the rule to protect a
#: file that does not exist.
#:
#: What that leaves unenforced, and what the prose in ADR 0019, CHANGELOG.md and the
#: example's own README therefore says: a bare hostname with no port, and a bare credential,
#: are not detectable as literals and are not claimed to be. The enforced promise is that no
#: `host:port` pair appears under `examples/` in any form.
CONNECTION_STRING = re.compile(
    r"[a-z][a-z0-9+.\-]*://[^\s\"'`/]*?[A-Za-z0-9._\-\[\]]+:\d{1,5}(?![\d.])"
    r"|@[A-Za-z0-9._\-\[\]]+:\d{1,5}(?![\d.])"
    r"|(?<![\w.])(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):\d"
    r"|(?<![\w./\-])[a-z][a-z0-9\-]*:\d{2,5}(?![\d.])"
)

#: One line of `endpoints.py --format env` output, and the only shape it may emit: no
#: heading, no indent, no blank line, no endpoint row. The format is read by `export`, where
#: any of those would become part of a name or a value.
ENV_LINE = re.compile(r"^[A-Z][A-Z0-9_]*=.+$")

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
# One named argument fails, everything else succeeds. STUB_EXIT is all-or-nothing, and a
# script whose contract is "this step failed, and the recovery still ran" cannot be driven
# by it: a stub that fails the whole run fails the recovery too, so the recording proves
# nothing about ordering.
if [ -n "${STUB_FAIL_ON:-}" ]; then
  for a in "$@"; do
    if [ "$a" = "$STUB_FAIL_ON" ]; then exit 1; fi
  done
fi
# A second answer, for the one Module whose capture reads a listing that the shared
# STUB_STDOUT cannot also be: a database list and an `mc ls --json` listing are parsed
# differently and cannot be the same text.
if [ -n "${STUB_MINIO:-}" ]; then
  for a in "$@"; do
    if [ "$a" = "minio" ]; then
      printf '%s' "$STUB_MINIO"
      exit "${STUB_EXIT:-0}"
    fi
  done
fi
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
    code="${STUB_PS_EXIT:-${STUB_EXIT:-0}}"
    case " $* " in
      *" --all "*) printf '%s' "${STUB_ALL:-}" ;;
      *) printf '%s' "${STUB_STDOUT:-}" ;;
    esac ;;
  *) printf '%s' "${STUB_STDOUT:-}" ;;
esac
exit "$code"
"""


#: What each backend's native API returns through Grafana's datasource proxy when the
#: panel's query matches something. Written out rather than captured from a live stack,
#: so the shapes the checker reads — Prometheus `data.result`, Loki `data.result`, Tempo
#: `traces` — are stated here and a checker that started reading a different key fails.
PROXY_ROWS: dict[str, dict[str, Any]] = {
    "prometheus": {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"__name__": "devinfra_smoke_counter_total"}, "value": [1, "1"]}],
        },
    },
    "loki": {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [{"stream": {"service_name": "zz-selftest"}, "values": [["1", "smoke-ok"]]}],
        },
    },
    "tempo": {"traces": [{"traceID": "0123456789abcdef", "rootServiceName": "zz-selftest"}]},
}

#: The same shapes with nothing in them — a backend that answered, and had no data. This
#: is the state a "No data" panel is in, and the one the whole check exists to fail on.
#:
#: Tempo's empty answer omits `traces` entirely rather than sending `[]`: it is a repeated
#: protobuf field, so a build that renders an empty search result as `{}` is answering
#: correctly, and a checker that read that as a malformed body would report ERROR and skip
#: the retry budget the lagging search index is the reason for. The pinned build sends the
#: explicit empty list, which the live run covers; this is the other half.
PROXY_EMPTY: dict[str, dict[str, Any]] = {
    "prometheus": {"status": "success", "data": {"resultType": "vector", "result": []}},
    "loki": {"status": "success", "data": {"resultType": "streams", "result": []}},
    "tempo": {},
}

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
    else with STUB_STDOUT. `ps` exits with STUB_PS_EXIT when that is set, which is
    how a project that fails to load is expressed: `ps` ignores active profiles but
    still has to resolve the model, so an unresolved Selection fails there while
    `version` succeeds — the health wait asks for the expected service set, the
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
    minio: str = "",
    fail_on: str = "",
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
        minio: Text the stub prints for any call naming the `minio` service, whose
            listing cannot be the same text as a database list.
        fail_on: One argument that makes the stub exit 1, leaving every other call
            succeeding, so a recovery path can be recorded alongside the failure.

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
    # `ps` follows STUB_EXIT unless a case says otherwise. Defined-but-empty rather
    # than absent, so a value in this process's own environment cannot leak in.
    env["STUB_PS_EXIT"] = ""
    # Both defined-but-empty for the same reason: an inherited value would silently
    # change what every other case's stub answers and what it exits with.
    env["STUB_MINIO"] = minio
    env["STUB_FAIL_ON"] = fail_on
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
    # A fixture may need a directory of its own — a planted Module is a directory holding a
    # compose.yaml — so the missing parents are created here and exactly the ones this call
    # created are removed again, deepest first. `rmdir` refuses a directory that is not
    # empty, so a parent that turned out to hold anything else survives untouched.
    created: list[Path] = []
    parent = path.parent
    while not parent.exists():
        created.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
        for directory in created:
            with contextlib.suppress(OSError):
                directory.rmdir()


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


#: The native endpoint each datasource answers on behind Grafana's proxy, and the name of
#: the parameter that carries the panel's query. The stub 404s anything else, so a checker
#: that started asking Prometheus for `api/v1/query_range`, or spelling Tempo's parameter
#: `query` instead of `q`, fails the offline cases instead of staying green against a stub
#: that answers every path the same way. Written out here because these three shapes are
#: the whole reason the proxy can be stubbed at all.
NATIVE_ENDPOINTS: dict[str, tuple[str, str]] = {
    "prometheus": ("api/v1/query", "query"),
    "loki": ("loki/api/v1/query_range", "query"),
    "tempo": ("api/search", "q"),
}


@contextlib.contextmanager
def stub_grafana(mode: str) -> Iterator[tuple[str, list[str]]]:
    """Serve a stand-in for Grafana's datasource proxy, yielding its base URL.

    A stub rather than a real Grafana because the property under test is the checker's
    own contract — what it does with a result, with nothing, and with a refusal — and
    that must be provable without a container runtime. The proxy speaks each backend's
    native API, which is exactly why it can be stubbed at all: three fixed JSON shapes,
    no query model to emulate.

    Args:
        mode: `results` answers every proxy call with one row, `empty` with none, and
            `error` with HTTP 400 and a body whose first line names the reason. The two
            `-once` modes answer the *first* call for each datasource that way and every
            call after it with a row, which is how a backend that has not ingested yet
            and a query that is simply malformed are told apart: the checker must retry
            the first and must not retry the second. `error-later` and `empty-later`
            are the mirrors of the two: the first call answers with a row and every one
            after it refuses or answers with nothing, which is what a signal whose
            *second* panel is broken looks like.

    Yields:
        The base URL to hand `--grafana-url`, and a list that accumulates the
        `Authorization` header of every request served — empty string when a request
        carried none — so the credential half of the URL can be asserted on.
    """
    seen: dict[str, int] = {}
    authorizations: list[str] = []
    counting = threading.Lock()

    def nth(uid: str) -> int:
        """Count this datasource's calls, returning how many came before this one.

        Args:
            uid: Datasource UID the proxy path named.

        Returns:
            The zero-based index of this call.
        """
        with counting:
            before = seen.get(uid, 0)
            seen[uid] = before + 1
        return before

    class Handler(http.server.BaseHTTPRequestHandler):
        """Answer any datasource-proxy path according to the enclosing mode."""

        def do_GET(self) -> None:
            """Reply to one proxied query."""
            with counting:
                authorizations.append(self.headers.get("Authorization", ""))
            found = re.match(r"^/api/datasources/proxy/uid/([^/]+)/([^?]*)(?:\?(.*))?$", self.path)
            if found is None or found.group(1) not in NATIVE_ENDPOINTS:
                self.send_error(404, "not a datasource proxy path")
                return
            native, parameter = NATIVE_ENDPOINTS[found.group(1)]
            if found.group(2) != native or parameter not in urllib.parse.parse_qs(found.group(3) or ""):
                # Not "no data": a request this datasource's real API would not recognise.
                self.send_error(404, f"expected {native} carrying '{parameter}'")
                return
            first = nth(found.group(1)) == 0
            if mode == "error" or (mode == "error-once" and first) or (mode == "error-later" and not first):
                body = b"parse error at 1:2: syntax error: unexpected }\nthe panel query is malformed\n"
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            empty_now = mode == "empty" or (mode == "empty-once" and first) or (mode == "empty-later" and not first)
            table = PROXY_EMPTY if empty_now else PROXY_ROWS
            payload = json.dumps(table[found.group(1)]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            """Swallow the default request log, which would bury the case output."""
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", authorizations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


#: The three paths the OpenTelemetry HTTP exporters post to, under the base
#: `OTEL_EXPORTER_OTLP_ENDPOINT` publishes. Written out rather than accepted from whatever
#: the exporter happens to ask for: the whole point of the collector stub is to say which
#: signals actually left the process, and a handler that answered 200 to any path at all
#: would report three arrivals for a run that flushed one.
OTLP_PATHS = ("/v1/traces", "/v1/logs", "/v1/metrics")

#: A `<integration>: OK` or `<integration>: FAIL <Type>: <detail>` line from the worked
#: example, which is the whole per-integration report it writes.
VERDICT = re.compile(r"^(?P<integration>[a-z-]+): (?P<outcome>OK|FAIL)(?: (?P<detail>.+))?$")

#: The shape a client's own diagnostic reaches a FAIL line in: the exception class it raised,
#: then its message. What this exists to reject is the example paraphrasing — one wording
#: written here and applied to all five clients, which would read the same whether the
#: address was refused, resolved to the wrong host or authenticated badly.
CLIENT_ERROR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*: \S.*$")

#: Loopback ports nothing ever listens on, one per integration the "a service is down" case
#: takes away. Ports 1-5 are chosen rather than an ephemeral port allocated and closed
#: again: allocating one leaves a window in which something else can bind it, and this case
#: must never be the flaky one. Distinct per integration deliberately, so each client's own
#: diagnostic names its own address and two of them cannot coincidentally match.
CLOSED_PORTS = {"keycloak": 1, "postgres": 2, "redis": 3, "object-storage": 4, "mailpit": 5}


@contextlib.contextmanager
def stub_collector() -> Iterator[tuple[str, set[str]]]:
    """Serve a stand-in for the collector's OTLP/HTTP ingest, yielding its base URL.

    A stub rather than a running collector for the same reason `stub_grafana` is one: the
    property under test is the example's own contract — that it reports every integration,
    that a failing one does not abandon the rest, and that all three signal providers are
    flushed before it exits — and that must be provable in the `validate` job, with no
    container runtime anywhere in the run.

    The whole protocol it has to speak is "200, empty body": the exporters read the status
    and nothing else on success, so there is no wire format to emulate.

    Yields:
        The base URL to hand `OTEL_EXPORTER_OTLP_ENDPOINT`, and the set of paths the
        exporters posted to, which is what says the flush really happened rather than
        merely being reported.
    """
    seen: set[str] = set()
    recording = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        """Accept any OTLP/HTTP export and record which signal it carried."""

        def do_POST(self) -> None:
            """Accept one export, draining the body so the client sees a clean response."""
            # Drained before replying: an exporter whose request body is never read gets a
            # broken pipe rather than the 200 this stub is trying to give it.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            with recording:
                seen.add(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            """Swallow the default request log, which would bury the case output."""
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


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

    # Every service is extracted, so common/base.yaml is the *only* source of the
    # shared restart/logging/networks fragment. The root file used to carry a second,
    # anchored copy for the services inlined in it; both are gone, and what is pinned
    # here is that end state. The whole key set is pinned rather than a deny-list of
    # `services` and the three anchor names it used to carry: a second copy of the
    # fragment re-added as `x-common` or `x-base` would pass a deny-list while
    # recreating exactly the duplicated-source problem, and any new top-level key here
    # is a claim this file makes about the stack that no per-module check can see.
    base_model = yaml.safe_load((REPO / "common" / "base.yaml").read_text(encoding="utf-8"))
    root_model = yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))
    shared = base_model.get("services", {}).get("defaults")
    expect("common/base.yaml declares a 'defaults' service", isinstance(shared, dict), f"got {shared!r}")
    root_keys = set(root_model)
    root_expected = {"name", "include", "volumes", "networks", "x-bundles", "x-app-variables"}
    expect(
        "compose.yaml declares name, the two registries, include, volumes and networks and nothing else",
        root_keys == root_expected,
        f"unexpected {sorted(root_keys - root_expected)}, "
        f"missing {sorted(root_expected - root_keys)} — a service or a "
        f"shared fragment back in the root file sits outside every per-module check and outside "
        f"the `include:` list that is supposed to be the record of what the stack runs",
    )
    expect(
        "the shared fragment declares only restart, logging and networks",
        isinstance(shared, dict) and set(shared) == {"restart", "logging", "networks"},
        f"common/base.yaml defaults declares {sorted(shared) if isinstance(shared, dict) else shared!r}",
    )

    # --- The Bundle registry (ADR 0014). ---
    # Names only, and the *only* place a Bundle name becomes legal. Asserted positively
    # here as well as negatively below, because every negative case plants a mutated root
    # file: if the shipped registry were itself absent or empty, every one of those cases
    # would still red for its own reason while the real file registered nothing.
    bundle_registry = root_model.get("x-bundles")
    expect(
        "compose.yaml declares a non-empty x-bundles registry",
        isinstance(bundle_registry, dict) and bool(bundle_registry),
        f"x-bundles is {bundle_registry!r} — read as 'no Bundles' the vocabulary would silently open",
    )
    registry_entries: dict[str, Any] = bundle_registry if isinstance(bundle_registry, dict) else {}
    for bundle_name, entry in sorted(registry_entries.items()):
        expect(
            f"the {bundle_name} Bundle entry declares exactly a description and a memory footprint",
            isinstance(entry, dict)
            and set(entry) == {"description", "memory"}
            and all(isinstance(entry[key], str) and entry[key].strip() for key in ("description", "memory")),
            f"x-bundles.{bundle_name} is {entry!r} — every Bundle states what it is for and roughly "
            f"what it costs (NFR-7), and a members list here would be a second, hand-maintained "
            f"answer to what starts (AD-7)",
        )
    # …and the registry is exactly the non-Module vocabulary, computed independently from
    # the module files rather than read back out of the resolver. A Bundle registered but
    # joined by nothing, or a profile joined but registered nowhere, fails here as well as
    # in lint-config — this is the reading that says *which* name is on the wrong side.
    declared_profile_names: set[str] = set()
    module_dir_names: set[str] = set()
    for module_path in sorted((REPO / "services").glob("*/compose.yaml")):
        module_dir_names.add(module_path.parent.name)
        module_body = yaml.safe_load(module_path.read_text(encoding="utf-8")) or {}
        for service_body in (module_body.get("services") or {}).values():
            if isinstance(service_body, dict) and isinstance(service_body.get("profiles"), list):
                declared_profile_names.update(str(name) for name in service_body["profiles"])
    non_module_profiles = declared_profile_names - module_dir_names
    expect(
        "the module files declare Bundle profiles at all",
        bool(non_module_profiles),
        "every declared profile is a Module name, so the registry reconciliation below checks nothing",
    )
    expect(
        "the x-bundles registry names exactly the profiles that are not Module names",
        set(registry_entries) == non_module_profiles,
        f"registered but joined by no Module: {sorted(set(registry_entries) - non_module_profiles)}; "
        f"joined but registered nowhere: {sorted(non_module_profiles - set(registry_entries))}",
    )
    # The footprint lives in the registry so CI can require it (NFR-7); README.md,
    # .env.example and CHANGELOG.md restate it, because those are where a developer
    # deciding what to start — or deciding whether to upgrade — actually looks. Four
    # statements of one number is drift waiting to happen, so all four are pinned to agree.
    #
    # Each footprint is bound to *its own* Bundle, by parsing each table into a
    # name -> footprint mapping and comparing mappings. Two substring searches over the
    # whole file would not do it: swap admin's and observability's figures and both strings
    # still occur somewhere, so the check passes over prose that now says the wrong thing
    # about both. An unanchored search also cannot tell a table row from a figure restated
    # in a nearby sentence, which is why the prose around these tables names no figures of
    # its own and points at the table instead.
    registry_footprints = {
        name: str(entry["memory"])
        for name, entry in registry_entries.items()
        if isinstance(entry, dict) and isinstance(entry.get("memory"), str)
    }

    # The Bundle table only: other tables in the same documents have the same row shape,
    # so the parse starts at this table's header and stops at the first line that is not a
    # row. Without that anchor `| \`pixi run init\` | ... |` joins the mapping. The rows are
    # indented inside a list item in CHANGELOG.md and flush left in README.md, so both the
    # header and the row test are taken on the stripped line.
    def markdown_footprints(document: str) -> dict[str, str]:
        found: dict[str, str] = {}
        lines = document.splitlines()
        for index, line in enumerate(lines):
            if not line.strip().startswith("| Bundle | Memory |"):
                continue
            for row in lines[index + 1 :]:
                if not row.strip().startswith("|"):
                    break
                cells = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", row.strip())
                if cells:
                    found[cells.group(1)] = cells.group(2)
            break
        return found

    readme_text = (REPO / "README.md").read_text(encoding="utf-8")
    changelog_text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    readme_footprints = markdown_footprints(readme_text)
    changelog_footprints = markdown_footprints(changelog_text)
    # `#   <name>  <footprint>  <description>` — the same table as a dotenv comment.
    env_footprints = dict(
        re.findall(
            r"^#\s{2,}([a-z][a-z-]*)\s{2,}(~[\d.]+\s*[KMG]B)\s{2,}\S",
            (REPO / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    for where, found in (
        ("README.md", readme_footprints),
        (".env.example", env_footprints),
        ("CHANGELOG.md", changelog_footprints),
    ):
        # A parse that matched nothing would make the equality below fail loudly rather
        # than pass, but it would blame the *content* for what is really a broken parse,
        # so the two are separated: this says the table is still where the check looks.
        expect(
            f"{where} still carries a Bundle table this check can read",
            bool(found),
            f"parsed no Bundle rows out of {where} — the table moved or changed shape, so the "
            f"footprint pin below would be reporting on nothing",
        )
        expect(
            f"{where}'s Bundle table states the footprint the registry declares, Bundle by Bundle",
            found == registry_footprints,
            f"{where} says {found}, the x-bundles registry says {registry_footprints} — "
            f"differing for {sorted(k for k in set(found) | set(registry_footprints) if found.get(k) != registry_footprints.get(k))}. "
            f"The registry and the prose are statements of one number and must agree",
        )
    # …and the line a legacy checkout is told to add is the one .env.example actually
    # ships. resolve_selection.py holds it as a literal on purpose — it is the advice
    # given precisely when the environment cannot be trusted — so the agreement is pinned
    # here rather than by having the refusal read another file to compose its own message.
    shipped_selection = ""
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("COMPOSE_PROFILES="):
            shipped_selection = line.split("=", 1)[1].strip()
    advised = re.search(
        r'^LEGACY_UPGRADE_SELECTION\s*=\s*"([^"]+)"',
        (REPO / "scripts" / "resolve_selection.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    expect(
        "the resolver's upgrade advice is the Selection .env.example ships",
        advised is not None and advised.group(1) == shipped_selection and bool(shipped_selection),
        f"resolver advises {advised.group(1) if advised else None!r}, .env.example ships "
        f"{shipped_selection!r} — a refusal that names a line the template does not carry is "
        f"advice that stops being true the moment the default moves",
    )
    # …and so do the two documents a migrating developer actually reads. The refusal is
    # only one of the four places this line is printed: README.md's upgrade callout and
    # CHANGELOG.md's breaking-change section each restate it verbatim, and those are the
    # copies someone pastes into a .env. The same argument that pins the refusal pins them.
    upgrade_advice = f"COMPOSE_PROFILES={shipped_selection}"
    for where, document in (("README.md", readme_text), ("CHANGELOG.md", changelog_text)):
        expect(
            f"{where} tells a legacy checkout to add the Selection .env.example ships",
            bool(shipped_selection) and upgrade_advice in document,
            f"{where} never says {upgrade_advice!r} — the migration advice a developer "
            f"pastes has drifted from the default the template carries",
        )

    # `include:` is the registry, and nothing else reconciles it against the module
    # directories. A module missing from the list is linted by every check, renders
    # nothing into the model, and validates green — the failure is a service that is
    # simply absent from the running stack. Three directions, all cheap: every module
    # file appears in the list, every entry resolves to a real file, and every entry
    # names a module — an entry pointing anywhere else would put service definitions
    # back where assert_config.py's module_composes(), assert_pins.py and every other
    # per-module check cannot see them, which is the inlining this story removed in a
    # new shape. Compose accepts both a bare path and the long `- path: <file>` form,
    # so both are read and anything else is named rather than skipped.
    include_entries = root_model.get("include") or []
    expect(
        "compose.yaml's include: is a non-empty list",
        isinstance(include_entries, list) and bool(include_entries),
        f"include: is {include_entries!r}, so every registry assertion below would pass vacuously",
    )
    included: list[str] = []
    for entry in include_entries if isinstance(include_entries, list) else []:
        if isinstance(entry, str):
            included.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            included.append(str(entry["path"]))
        else:
            expect(
                f"compose.yaml's include: entry {entry!r} is a path this check can read",
                False,
                "neither a bare path nor the long `- path: <file>` form, so it is unverifiable here",
            )
    registered = {(REPO / entry).resolve() for entry in included}
    module_files = sorted((REPO / "services").glob("*/compose.yaml"))
    expect("there are module files to reconcile", bool(module_files), "found no services/*/compose.yaml")
    for module in module_files:
        expect(
            f"compose.yaml's include: registers {module.relative_to(REPO).as_posix()}",
            module.resolve() in registered,
            "the module is linted by every check and contributes nothing to the model",
        )
    for entry in included:
        expect(
            f"compose.yaml's include: entry {entry} resolves to a file",
            (REPO / entry).is_file(),
            "the entry names a file that does not exist",
        )
        # The leading `./` is optional to Compose and optional here: the direction above
        # compares resolved paths, so `services/x/compose.yaml` already satisfies it, and
        # a rule that rejected that spelling would hand the author a diagnostic claiming
        # their module is invisible to module_composes() — which globs services/*/compose.yaml
        # from disk and would have found it. What must hold is the location, since a module
        # anywhere else is what those globs really would miss.
        expect(
            f"compose.yaml's include: entry {entry} is a module file",
            re.fullmatch(r"(?:\./)?services/[^/]+/compose\.yaml", entry) is not None,
            "not services/<name>/compose.yaml, so the services it declares are invisible to "
            "assert_config.py's module_composes() and to every other per-module check",
        )

    # minio-init is a one-shot helper, and `extends` made its `restart` override
    # load-bearing: while it aliased x-logging it only restated Docker's own default, but
    # common/base.yaml declares `restart: unless-stopped`, so an extending helper without
    # the override is restarted the instant it exits 0 and loops forever. Nothing in the
    # static gate reads `restart` — assert_config.py deliberately does not, precisely
    # because this override is sanctioned — so deleting the line leaves `pixi run ci`
    # green and only the running stack shows it. Both halves are asserted: the extends
    # block, because without it the override is redundant again, and the override itself.
    minio_model = yaml.safe_load((REPO / "services" / "minio" / "compose.yaml").read_text(encoding="utf-8"))
    minio_services = minio_model.get("services")
    helper = minio_services.get("minio-init") if isinstance(minio_services, dict) else None
    expect("services/minio/compose.yaml declares the minio-init helper", isinstance(helper, dict), f"got {helper!r}")
    helper = helper if isinstance(helper, dict) else {}
    extends_block = helper.get("extends")
    helper_extends = extends_block if isinstance(extends_block, dict) else {}
    expect(
        "minio-init inherits the shared fragment through extends",
        str(helper_extends.get("file", "")).endswith("common/base.yaml")
        and helper_extends.get("service") == "defaults",
        f"extends is {extends_block!r}, which is not common/base.yaml's `defaults`",
    )
    expect(
        "minio-init overrides the inherited restart policy with 'no'",
        helper.get("restart") == "no",
        f"restart is {helper.get('restart')!r}, so the one-shot helper restart-loops forever",
    )

    # Mailpit's durability is two keys in one module file and nothing else. Drop either
    # and the model still renders, `config -q` still passes, assert_config.py still passes
    # (it reads image, logging and ports, by design), and the smoke test still passes —
    # it sends a message and reads the count back inside one run, which succeeds against
    # an in-memory store. The volume is mounted and never written, and every captured
    # email is lost on the next restart. Same class as the minio-init override above: a
    # value that only the running stack, days later, would show missing.
    mailpit_model = yaml.safe_load((REPO / "services" / "mailpit" / "compose.yaml").read_text(encoding="utf-8"))
    mailpit_services = mailpit_model.get("services")
    mailpit = mailpit_services.get("mailpit") if isinstance(mailpit_services, dict) else None
    expect("services/mailpit/compose.yaml declares the mailpit service", isinstance(mailpit, dict), f"got {mailpit!r}")
    mailpit = mailpit if isinstance(mailpit, dict) else {}
    mailpit_environment = mailpit.get("environment")
    mailpit_env = mailpit_environment if isinstance(mailpit_environment, dict) else {}
    expect(
        "mailpit stores its database on the mounted volume",
        str(mailpit_env.get("MP_DATABASE", "")).startswith("/data/"),
        f"MP_DATABASE is {mailpit_env.get('MP_DATABASE')!r}, so Mailpit keeps messages in memory and loses them on restart",
    )
    expect(
        "mailpit mounts mailpit-data at the path MP_DATABASE writes to",
        "mailpit-data:/data" in [str(m) for m in mailpit.get("volumes", [])],
        f"volumes are {mailpit.get('volumes')!r}, so the database path is not on the named volume",
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
        # Below a module root, not beside the module file: `services/**/*.y*ml` has to
        # reach a module's own config, not just its compose.yaml, and a fixture at
        # services/loki/ would pass even if the `**` collapsed to a single level.
        ("lint-yaml", REPO / "services" / "loki" / "conf" / "zz_selftest_defect.yaml", "root:\n  a: 1\n      b: 2\n"),
        # The same depth argument for JSON: `services/**/*.json` must reach a module's
        # conf/ directory, which is where the pgAdmin server registration now lives.
        ("lint-json", REPO / "services" / "pgadmin" / "conf" / "zz_selftest_defect.json", '{\n  "a": 1,\n}\n'),
        # …and the same defect beside a module file rather than below it, so a `**` that
        # ever stopped matching the module root itself would be caught too. It sits
        # beside services/keycloak/compose.yaml, not in seed/: that directory is
        # bind-mounted wholesale into Keycloak's --import-realm path, so a fixture
        # surviving a killed run would break the next realm import.
        (
            "lint-json",
            REPO / "services" / "keycloak" / "zz_selftest_defect.json",
            '{\n  "a": 1,\n}\n',
        ),
        # The YAML half of that same pair, which the conf/ fixture above cannot stand in
        # for. Narrowing `services/**/*.y*ml` to `services/**/conf/*.y*ml` leaves that one
        # matched and empties no `empties` entry, so every case here would pass while none
        # of the thirteen module compose.yaml files — the stack itself — was yamllinted.
        # Placed in redisinsight/, the one module with neither a conf/ nor a seed/
        # directory bind-mounted into it, so a fixture surviving a killed run reaches no
        # container.
        (
            "lint-yaml",
            REPO / "services" / "redisinsight" / "zz_selftest_defect.yaml",
            "root:\n  a: 1\n      b: 2\n",
        ),
        # The shell half of that same argument, and the reason `services/**/*.sh` is a
        # term in its own right rather than a leftover from the Postgres seed scripts:
        # since story 2-4 every Module owns a smoke.sh, so that pattern now reaches the
        # verification for the whole stack. Placed in redisinsight/ for the same reason as
        # the YAML fixture above — the one module with nothing bind-mounted into a
        # container, so a fixture surviving a killed run reaches no container.
        (
            "lint-shell",
            REPO / "services" / "redisinsight" / "zz_selftest_defect.sh",
            '#!/usr/bin/env bash\nv="$1"\necho $v\n',
        ),
        # One entry per term of the lint-python body, the way the sibling glob terms are
        # pinned one entry each. `lint-python` names two literal directories, and a single
        # fixture under either one keeps the task red while the *other* term is deleted —
        # so dropping `scripts` from the body would leave ruff and mypy seeing none of this
        # repository's own Python with the whole gate green.
        (
            "lint-python",
            REPO / "scripts" / "zz_selftest_defect.py",
            "import os\n\n\ndef undocumented(unannotated):\n    return os\n",
        ),
        # Below the example's own directory, not beside it: `lint-python` names the literal
        # directory `examples`, and a fixture at examples/ would pass even if the walk had
        # stopped descending. This is the term that stops `examples` being deleted
        # from the lint-python body — every other assertion about the example reads its
        # source directly and would agree with itself whether or not ruff and mypy ever saw
        # it. Format-clean on purpose, so `ruff format --check` passes it through to
        # `ruff check`, which is the term being pinned.
        (
            "lint-python",
            REPO / EXAMPLE_DIR / "worked-example" / "zz_selftest_defect.py",
            "import os\n\n\ndef undocumented(unannotated):\n    return os\n",
        ),
        (
            "lint-compose",
            REPO / "compose.override.yaml",
            "services:\n  zz-selftest-defect:\n    image: alpine\n    depends_on:\n      - zz-absent-service\n",
        ),
        # A Module directory carrying no `x-endpoints:` block at all. `lint-config` refuses
        # that too, but this is the generator's own refusal and it is the one that matters
        # here: a Module the walk reached and could document nothing about must be a named
        # failure, never a document that is quietly one section short. The fixture is a
        # directory of its own, which planted() creates and removes again.
        (
            "lint-endpoints",
            REPO / "services" / "zz-selftest-defect" / "compose.yaml",
            "services:\n  zz-selftest-defect:\n    image: alpine:3\n    profiles: [zz-selftest-defect]\n",
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
    #
    # `moved_aside()` renames tracked files in place and restores them in a `finally`.
    # A killed run — SIGKILL, a power cut — skips that restore, and the entries below
    # hide the CI workflow and every module compose file and config: the tree would be
    # left with no CI workflow and no stack at all, each file sitting beside where it
    # belongs under a `.selftest-moved` suffix. Same hazard the Keycloak JSON fixture is
    # placed to avoid, one size larger. `git status` names every one, and renaming the
    # `.selftest-moved` files back — or `git checkout --` — restores the tree.
    empties: list[tuple[str, list[Path]]] = [
        # The whole recursive `services/**/*.sh` term, not just the Postgres seed scripts
        # it was pinned on before story 2-4. Thirteen Module smoke.sh files now live under
        # that pattern, so hiding the seed scripts alone no longer empties it: the task
        # would pass, this case would fail, and the term could be deleted from pixi.toml
        # with every Module's own verification going unlinted. Same widening the lint-yaml
        # `services/` entry below already carries, one story later.
        ("lint-shell", sorted((REPO / "services").rglob("*.sh"))),
        # The three glob halves of the lint-yaml pattern, pinned one term per entry.
        # Hiding several at once would let any one of `common/**/*.y*ml`,
        # `services/**/*.y*ml` and `.github/workflows/*.y*ml` be deleted from pixi.toml
        # with every case here still passing, because another term would empty the file
        # set on its own. The `services/` pin is the whole recursive term, not just
        # `services/*/compose.yaml`: since the admin and observability modules landed,
        # most YAML that term reaches is module config below the module root, and a pin
        # on the compose files alone would no longer empty it.
        ("lint-yaml", sorted((REPO / "common").rglob("*.y*ml"))),
        ("lint-yaml", sorted((REPO / "services").rglob("*.yaml")) + sorted((REPO / "services").rglob("*.yml"))),
        (
            "lint-yaml",
            sorted((REPO / ".github" / "workflows").glob("*.yaml"))
            + sorted((REPO / ".github" / "workflows").glob("*.yml")),
        ),
        # The lint-json pattern's one glob term. The other term, the literal
        # `renovate.json`, is not pinned here and does not need to be: a literal path
        # cannot silently stop matching, which is the only failure this mechanism
        # detects, and assert_renovate.py parses that file on every run anyway.
        ("lint-json", sorted((REPO / "services").rglob("*.json"))),
        # The generator's own walk. With no module file there is no catalog to render from,
        # and a document rendered over an empty walk would document nothing and say so at
        # exit 0 — the pass-over-nothing every other entry here exists to prevent.
        ("lint-endpoints", sorted((REPO / "services").glob("*/compose.yaml"))),
    ]
    for task, targets in empties:
        expect(f"{task} has a glob target to empty", bool(targets), "found no files to hide")
        with moved_aside(targets):
            r = pixi(task)
            expect(f"{task} fails on an empty file set", r.returncode != 0, "an empty file set passed")

    # --- The gotcha register's shape, one case per row of the contract (ADR 0016). ---
    #
    # Staged with moved_aside + planted rather than by editing in place: the tracked
    # register is renamed, the defect is written at its path, and the original comes back
    # however the case ends. planted() refuses a path that already exists, so the pair
    # cannot silently overwrite a Module's real gotchas. redisinsight/ is the subject for
    # the same reason it carries the shell and YAML fixtures — it is the one Module with
    # nothing bind-mounted into a container, so a fixture surviving a killed run reaches no
    # container.
    register = REPO / "services" / "redisinsight" / "gotchas.md"
    clean_register = (
        "# redisinsight — gotchas\n"
        "\n"
        "### A planted entry\n"
        "\n"
        "- **Symptom:** Nothing observable; this file is a self-test fixture.\n"
        "- **Cause:** The self-test replaced the tracked register to prove the check bites.\n"
        "- **Fix:** The `finally` in moved_aside puts the tracked file back.\n"
        "- **Affected versions:** Not version-specific\n"
    )
    # The needles are what a reader has to be told to act: which file, which entry, and
    # what is wrong with it. A check that exits 1 saying only "invalid" is a check nobody
    # can use, so each row asserts the diagnostic and not just the status.
    register_cases: list[tuple[str, str, list[str]]] = [
        (
            "an entry with no Fix field",
            clean_register.replace("- **Fix:** The `finally` in moved_aside puts the tracked file back.\n", ""),
            ["gotchas.md", "A planted entry", "Fix"],
        ),
        (
            "a placeholder value",
            clean_register.replace("Not version-specific", "TBD"),
            ["gotchas.md", "A planted entry", "TBD"],
        ),
        (
            "an empty value",
            clean_register.replace("- **Fix:** The `finally` in moved_aside puts the tracked file back.", "- **Fix:**"),
            ["gotchas.md", "A planted entry", "Fix", "empty"],
        ),
        (
            "fields out of order",
            clean_register.replace(
                "- **Symptom:** Nothing observable; this file is a self-test fixture.\n"
                "- **Cause:** The self-test replaced the tracked register to prove the check bites.\n",
                "- **Cause:** The self-test replaced the tracked register to prove the check bites.\n"
                "- **Symptom:** Nothing observable; this file is a self-test fixture.\n",
            ),
            ["gotchas.md", "A planted entry", "Symptom, Cause, Fix, Affected versions"],
        ),
        (
            "a field bullet outside any entry",
            clean_register.replace("### A planted entry\n", "- **Note:** a stray bullet.\n\n### A planted entry\n"),
            ["gotchas.md", "line 3"],
        ),
        (
            "an H1 that does not name the Module",
            clean_register.replace("# redisinsight — gotchas", "# gotchas"),
            ["gotchas.md", "# redisinsight — gotchas"],
        ),
        (
            "a register with no entries",
            "# redisinsight — gotchas\n",
            ["gotchas.md"],
        ),
        # Not a placeholder — is_placeholder intercepts those one branch earlier — and not a
        # version either. Without this row the "names no version" arm could be deleted and
        # `Affected versions: all releases` would become acceptable with the suite green.
        (
            "an Affected versions: naming no version",
            clean_register.replace("Not version-specific", "all releases"),
            ["gotchas.md", "A planted entry", "names no version"],
        ),
        (
            "a Verified by: naming a path that does not exist",
            clean_register + "- **Verified by:** `services/gone/smoke.sh` — nothing lives there.\n",
            ["gotchas.md", "A planted entry", "services/gone/smoke.sh"],
        ),
        # A directory exists, and is not a check. Resolving the path with `.exists()` would
        # accept this and the dead-link detection the field exists for would be bypassed.
        (
            "a Verified by: naming a directory rather than a file",
            clean_register + "- **Verified by:** `services` — a directory, not a check.\n",
            ["gotchas.md", "A planted entry", "is not a file"],
        ),
        # The path has to be findable, which means backticked and first. A value that only
        # describes a check in prose leaves nothing to resolve, so nothing rots loudly.
        (
            "a Verified by: with no backticked path",
            clean_register + "- **Verified by:** the postgres smoke check asserts it.\n",
            ["gotchas.md", "A planted entry", "backticks"],
        ),
    ]
    for case, body_text, needles in register_cases:
        with moved_aside([register]), planted(register, body_text):
            r = pixi("lint-gotchas")
            expect(f"lint-gotchas rejects {case}", r.returncode != 0, "exited 0")
            unsaid = [needle for needle in needles if needle not in r.stderr]
            expect(
                f"lint-gotchas names the file and the defect for {case}",
                not unsaid,
                f"never said {unsaid}; stderr: {r.stderr!r}",
            )
            expect(
                f"lint-gotchas signs off on nothing for {case}",
                "redisinsight: OK" not in r.stdout,
                f"stdout: {r.stdout!r}",
            )

    # ...and the shape the contract sanctions stays sanctioned, optional fifth field
    # included. Without this the whole check could be inverted to reject everything and
    # every negative above would still be green.
    with moved_aside([register]), planted(register, clean_register):
        r = pixi("lint-gotchas")
        expect("lint-gotchas accepts an entry in the shape", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")
    with (
        moved_aside([register]),
        planted(
            register,
            clean_register + "- **Verified by:** `services/redisinsight/smoke.sh` — this Module's own checks.\n",
        ),
    ):
        r = pixi("lint-gotchas")
        expect(
            "lint-gotchas accepts the optional Verified by: field",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )

    # The empty-walk guard, which no defect case above can reach: a checker whose glob
    # stopped matching would report nothing, find nothing wrong and exit 0.
    tracked_registers = sorted((REPO / "services").glob("*/gotchas.md"))
    expect("there are gotcha registers to check", bool(tracked_registers), "found none")
    with moved_aside(tracked_registers):
        r = pixi("lint-gotchas")
        expect("lint-gotchas fails when the walk is empty", r.returncode != 0, "an empty file set passed")
        expect("lint-gotchas says the walk was empty", "walk was empty" in r.stderr, f"stderr: {r.stderr!r}")

    # The clean direction over the real tree, and the number it walked. "OK" on its own is
    # a sentence a check that walked one file writes just as happily as one that walked
    # thirteen, so the line count is asserted against the glob.
    r = pixi("lint-gotchas")
    reported = [line for line in r.stdout.splitlines() if ": OK " in line]
    expect("lint-gotchas passes over the tracked registers", r.returncode == 0, f"stderr: {r.stderr!r}")
    expect(
        "lint-gotchas reports every Module's register",
        len(reported) == len(tracked_registers)
        and all(f"{path.parent.name}: OK " in r.stdout for path in tracked_registers),
        f"{len(tracked_registers)} registers; stdout: {r.stdout!r}",
    )

    # --- A configuration fact two gotchas' Fix names, asserted rather than only described. ---
    #
    # `services/prometheus/gotchas.md` and `services/tempo/gotchas.md` both stand on this
    # flag and it is the `Verified by:` target of both: it is one feature split across two
    # Modules, and deleting it leaves a stack that is green everywhere while Grafana's
    # service map stays permanently empty. Mailpit's MP_DATABASE, the third entry naming
    # this file, is already pinned above rather than a second time here.
    prometheus_module = yaml.safe_load((REPO / "services" / "prometheus" / "compose.yaml").read_text(encoding="utf-8"))
    prometheus_command = prometheus_module["services"]["prometheus"].get("command") or []
    expect(
        "prometheus accepts Tempo's span-metric remote writes",
        "--web.enable-remote-write-receiver" in [str(item) for item in prometheus_command],
        f"command: {prometheus_command} — without the receiver those writes are refused and "
        f"Grafana's service map stays permanently empty",
    )

    # --- The README carries no copy of the register. ---
    # Eight entries used to live in both places, free to drift, and the copy in the README
    # is the one nobody editing services/<name>/ ever sees. The heading stays because it is a
    # stable reference — readers know it, and links from outside this repository resolve to
    # its anchor — so what is pinned is that the section holds no entries.
    readme_text = (REPO / "README.md").read_text(encoding="utf-8")
    heading = "## Gotchas worth knowing"
    expect(
        "README keeps the gotchas heading, a stable reference outside links resolve to",
        heading in readme_text,
        "heading is gone, so every link to its anchor now lands nowhere",
    )
    below_heading = readme_text.split(heading, 1)[1]
    gotchas_section = below_heading.split("\n## ", 1)[0]
    # Any bullet marker, at any indentation: a bolded entry that grew back nested under a
    # paragraph, or written with `*`, is the same duplicated gotcha as one at column zero.
    strays = [line for line in gotchas_section.splitlines() if re.match(r"^\s*[-*] \*\*", line)]
    expect(
        "the README gotchas section carries no entries of its own",
        not strays,
        f"{len(strays)} bolded bullet(s) back in the README: {strays[:3]} — they belong in the "
        f"affected Module's gotchas.md, where a second copy cannot drift from the first",
    )

    # --- The endpoint document cannot drift (ADR 0017). ---
    #
    # One case per row of the generator's contract. Every fixture is staged with
    # moved_aside + planted — the tracked file is renamed, the mutation is written at its
    # path, and the original comes back however the case ends — so nothing tracked is edited
    # in place and planted() refuses a path that is still occupied.
    app_root = REPO / "compose.yaml"
    app_root_text = app_root.read_text(encoding="utf-8")
    app_registry = root_model.get("x-app-variables")
    # Asserted positively here as well as negatively below, for the same reason the Bundle
    # registry is: every negative case plants a mutated root file, so if the shipped registry
    # were itself absent or empty every one of them would still red for its own reason while
    # the real file registered nothing.
    expect(
        "compose.yaml declares a non-empty x-app-variables registry",
        isinstance(app_registry, dict) and bool(app_registry),
        f"x-app-variables is {app_registry!r} — read as 'no application variables' the "
        f"application tier ADR 0003 defines would silently cease to exist",
    )
    shipped_variables: dict[str, Any] = app_registry if isinstance(app_registry, dict) else {}
    # Set equality, the same way MAKE_FORWARDS pins the Makefile's recipes. Every assertion
    # below iterates `shipped_variables`, so all of them are satisfied by a registry that has
    # quietly lost an entry — and the endpoint half cannot lose one (assert_config.py
    # reconciles x-endpoints: against the ports the Module publishes) while this half was
    # reconciled against nothing at all.
    expect(
        "the x-app-variables registry declares exactly the application variables it is meant to",
        set(shipped_variables) == APP_VARIABLES,
        f"registry declares {sorted(shipped_variables)}; APP_VARIABLES expects "
        f"{sorted(APP_VARIABLES)} — added {sorted(set(shipped_variables) - APP_VARIABLES)}, "
        f"lost {sorted(APP_VARIABLES - set(shipped_variables))}",
    )
    for variable_name, entry in sorted(shipped_variables.items()):
        expect(
            f"the {variable_name} registry entry names one Module, one endpoint, a value and a purpose",
            isinstance(entry, dict) and set(entry) == {"module", "endpoint", "value", "description"},
            f"x-app-variables.{variable_name} is {entry!r} — an entry names exactly one owning "
            f"Module and one of that Module's endpoint keys, which is what makes the name "
            f"unambiguous where names look alike (ADR 0003, ADR 0017)",
        )

    # The banner is the file's only defence against being hand-edited, and it is worth
    # nothing if it names a task that does not exist. Every task it names is checked against
    # the manifest rather than against a literal, so renaming the task reds this.
    endpoint_document = REPO / "docs" / "ENDPOINTS.md"
    expect(
        "the generated endpoint document is committed",
        endpoint_document.is_file(),
        f"{endpoint_document} is absent, so `pixi run lint-endpoints` has nothing to compare against",
    )
    endpoint_text = endpoint_document.read_text(encoding="utf-8") if endpoint_document.is_file() else ""
    banner_tasks = re.findall(r"`pixi run ([a-z-]+)`", endpoint_text.split("-->", 1)[0])
    expect(
        "the endpoint document's banner names tasks pixi.toml actually declares",
        bool(banner_tasks) and all(name in tasks for name in banner_tasks),
        f"banner names {banner_tasks}; pixi.toml declares {sorted(tasks)}",
    )

    # The writer itself, which nothing else here runs — and it is the task the do-not-edit
    # banner and the stale-document diagnostic both tell a contributor to reach for. A
    # generator that could no longer write would leave `pixi run ci` green while the one
    # instruction every reader is given was broken.
    # Staged with moved_aside like every other case here: asserted *after* the write, this
    # would otherwise leave the tracked document rewritten on the one run where the render
    # and the commit disagree — the failing run, when a clean tree matters most.
    with moved_aside([endpoint_document]):
        r = pixi("endpoints")
        regenerated = endpoint_document.read_text(encoding="utf-8") if endpoint_document.is_file() else ""
        endpoint_document.unlink(missing_ok=True)
    expect("endpoints regenerates the document", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")
    expect(
        "regenerating leaves the committed document byte-for-byte",
        regenerated == endpoint_text,
        "`pixi run endpoints` renders something other than what is committed, so what is "
        "committed is not what the module metadata renders",
    )

    # The two combinations `settle()` refuses, both of which pixi makes reachable by
    # accident: task arguments are appended to the task body, so a Selection typed after
    # either task name arrives as an option the body never asked for. Untested, the guards
    # can be dropped or inverted with the whole gate green — and the damage from the second
    # is a truncated tracked file that only the *next* lint-endpoints run reports.
    settled_refusals: list[tuple[str, tuple[str, ...], list[str]]] = [
        (
            "a Selection typed after `pixi run endpoints`",
            ("endpoints", "postgres"),
            ["docs/ENDPOINTS.md", "postgres"],
        ),
        (
            "a Selection typed after `pixi run lint-endpoints`",
            ("lint-endpoints", "postgres"),
            ["--check", "SELECTION"],
        ),
    ]
    for case, task_argv, needles in settled_refusals:
        r = pixi(*task_argv)
        expect(f"the generator refuses {case}", r.returncode != 0, f"exited 0; stdout: {r.stdout!r}")
        unsaid = [needle for needle in needles if needle not in r.stderr]
        expect(
            f"the generator says why it refuses {case}",
            not unsaid,
            f"never said {unsaid}; stderr: {r.stderr!r}",
        )
        expect(
            f"the committed document survives {case}",
            endpoint_document.read_text(encoding="utf-8") == endpoint_text,
            "the tracked document was rewritten by a call that should never have written",
        )

    # --- The `env` format, which is how the worked example learns an address (ADR 0019). ---
    #
    # Driven through the script rather than a task, because no task declares it: `pixi run
    # endpoints` fixes --format markdown --write, and appending `--format env` to that body
    # is one of the narrowing refusals asserted just above. scripts/example.sh calls the
    # generator directly, and this is that call.
    def generate(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return tool([sys.executable, str(REPO / "scripts" / "endpoints.py"), *argv], cwd=REPO, env=env)

    r = generate("--all", "--format", "env")
    env_lines = r.stdout.splitlines()
    expect("the generator renders --format env over the whole catalog", r.returncode == 0, f"stderr: {r.stderr!r}")
    malformed = [line for line in env_lines if not ENV_LINE.match(line)]
    expect(
        "--format env emits NAME=value lines and nothing else",
        bool(env_lines) and not malformed,
        f"{len(env_lines)} line(s), {malformed} of them not assignments — this format is read by "
        f"`export`, where a heading, a blank line or an indent becomes part of a name or a value",
    )
    expect(
        "--format env emits every application variable and no endpoint row",
        {line.split("=", 1)[0] for line in env_lines} == APP_VARIABLES,
        f"emitted {sorted({line.split('=', 1)[0] for line in env_lines})}; the registry declares "
        f"{sorted(APP_VARIABLES)}. A `*_PORT` here would be a Module-tier name in an application's "
        f"environment (ADR 0003)",
    )

    # …and it narrows with the Selection, which is the property that makes the example's own
    # refusal reachable: a Selection without object storage must publish no AWS_ENDPOINT_URL,
    # or the application would be handed an address for a Module that is not running.
    r = generate("postgres", "--format", "env")
    expect("--format env narrows to one Module's variables", r.returncode == 0, f"stderr: {r.stderr!r}")
    expect(
        "--format env for postgres alone publishes DATABASE_URL and nothing else",
        bool(r.stdout.splitlines()) and {line.split("=", 1)[0] for line in r.stdout.splitlines()} == {"DATABASE_URL"},
        f"stdout: {r.stdout!r}",
    )
    r = generate("postgres", "redis", "--format", "env")
    narrowed = {line.split("=", 1)[0] for line in r.stdout.splitlines()}
    expect(
        "--format env for a Selection without object storage publishes no AWS_ENDPOINT_URL",
        r.returncode == 0 and "AWS_ENDPOINT_URL" not in narrowed and "DATABASE_URL" in narrowed,
        f"exit {r.returncode}; emitted {sorted(narrowed)}",
    )

    # …and it narrows the same way with *no* Selection argument at all, which is the call
    # scripts/example.sh actually makes: the runner resolves the ambient Selection into
    # COMPOSE_PROFILES and then asks the generator for "whatever that is". Every case above
    # passes a Selection explicitly, so all of them stay green if `env` is dropped from the
    # fallback tuple in main() — and a narrowed run would then be handed every Module's
    # variables and connect to Modules it never started.
    ambient = dict(os.environ)
    ambient["COMPOSE_PROFILES"] = "postgres,redis"
    r = generate("--format", "env", env=ambient)
    ambient_names = {line.split("=", 1)[0] for line in r.stdout.splitlines()}
    expect(
        "--format env reads the ambient Selection when it is given none",
        r.returncode == 0
        and ambient_names == {"DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"},
        f"exit {r.returncode}; COMPOSE_PROFILES=postgres,redis emitted {sorted(ambient_names)} — "
        f"the whole registry here means the fallback stopped admitting this format, and the "
        f"runner would export addresses for Modules the Selection never started",
    )
    expect(
        "--format env with no Selection publishes nothing for a Module outside COMPOSE_PROFILES",
        "AWS_ENDPOINT_URL" not in ambient_names,
        f"emitted {sorted(ambient_names)}",
    )

    # The refusal pixi makes reachable by accident, for the new format too: task arguments
    # are appended to the task body, so `pixi run endpoints --format env` arrives after the
    # body's own `--format markdown --write docs/ENDPOINTS.md` and wins. Untested, the guard
    # can be dropped or inverted and the damage is a tracked document silently replaced by a
    # dotenv that only the *next* lint-endpoints run reports.
    r = pixi("endpoints", "--format", "env")
    expect("the generator refuses --format env against the committed document", r.returncode != 0, "exited 0")
    unsaid = [needle for needle in ("docs/ENDPOINTS.md", "env") if needle not in r.stderr]
    expect(
        "the generator names the format and the document when it refuses",
        not unsaid,
        f"never said {unsaid}; stderr: {r.stderr!r}",
    )
    expect(
        "the committed document survives --format env --write",
        endpoint_document.read_text(encoding="utf-8") == endpoint_text,
        "the tracked document was replaced by a dotenv render",
    )

    # --- The worked example reads the contract and nothing else (ADR 0019). ---
    #
    # Static, and that is the whole point: this is the half of the story that runs in the
    # `validate` job. A renamed or deleted `x-app-variables:` key fails here, naming the
    # example's read of it, with no container runtime anywhere in the run.
    example_app = REPO / EXAMPLE_APP
    expect("the worked example is committed", example_app.is_file(), f"{EXAMPLE_APP} is absent")
    example_python = sorted((REPO / EXAMPLE_DIR).rglob("*.py"))
    expect(f"there is Python under {EXAMPLE_DIR}/ to scan", bool(example_python), "found none")
    # Exactly one module, and it is the one the pin names. Without this the union below is a
    # guarantee about a directory, and a second module arriving with a REQUIRED table of its
    # own would either move the union — failing loudly, which is fine — or, far worse, read
    # nothing through a table at all and be covered only by the inline-read scan. Adding a
    # second example is a deliberate act that has to revisit this pin.
    expect(
        f"{EXAMPLE_DIR}/ holds exactly the one module the name pin names",
        [str(path.relative_to(REPO)) for path in example_python] == [EXAMPLE_APP],
        f"found {[str(path.relative_to(REPO)) for path in example_python]}; the pin covers "
        f"{EXAMPLE_APP} alone — a second module here needs its own entry in this pin",
    )
    # The union across every Python file under examples/, not the one module's table: what
    # the spec, ADR 0019 and the comment on EXAMPLE_READ all describe is a guarantee about
    # the directory, and reading a single file would be a guarantee about a file.
    example_reads: set[str] = set()
    for path in example_python:
        example_reads |= set(EXAMPLE_READ.findall(path.read_text(encoding="utf-8")))
    expect(
        "the worked example declares exactly the application variables it is meant to read",
        example_reads == EXAMPLE_VARIABLES,
        f"{EXAMPLE_DIR}/ reads {sorted(example_reads)}; EXAMPLE_VARIABLES expects "
        f"{sorted(EXAMPLE_VARIABLES)} — added {sorted(example_reads - EXAMPLE_VARIABLES)}, "
        f"lost {sorted(EXAMPLE_VARIABLES - example_reads)}",
    )
    expect(
        "every name the worked example reads is a key of the x-app-variables registry",
        bool(example_reads) and example_reads <= set(shipped_variables),
        f"reads {sorted(example_reads - set(shipped_variables))} which the registry does not "
        f"declare — the example consumes the contract, it does not extend it",
    )

    # Every tracked file under examples/, Python or not: a README or a shell helper added
    # later carrying a `postgresql://…@postgres:5432/…` is the same second copy of the
    # contract as a DSN in the application would be. Driven off what git tracks rather than
    # off the glob, so the transient __pycache__ a mypy or a test run leaves behind is not a
    # subject — it is ignored, untracked, and not something a reviewer can act on.
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(
            str(path.relative_to(REPO)) for path in sorted(REPO.glob(f"{EXAMPLE_DIR}/**/*")) if path.is_file()
        ),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    skipped = {line.strip() for line in ignored.stdout.splitlines() if line.strip()}
    example_files = [
        path
        for path in sorted(REPO.glob(f"{EXAMPLE_DIR}/**/*"))
        if path.is_file() and str(path.relative_to(REPO)) not in skipped
    ]
    expect(f"there are tracked files under {EXAMPLE_DIR}/ to scan", bool(example_files), "found none")
    for path in example_files:
        subject = path.relative_to(REPO)
        # Read inside a guard, the way scripts/endpoints.py guards its own reads: a binary
        # dropped in here would otherwise end the whole self-test in a UnicodeDecodeError
        # traceback, abandoning every case after it — the silent-shape failure a named
        # refusal exists to replace.
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            expect(f"{subject} can be read as text", False, f"unreadable: {exc}")
            continue
        # Every read goes through the declared table. An `os.environ["SOMETHING"]` written
        # inline would be a name outside it, and the set equality above would keep agreeing
        # with a list that was no longer the whole list.
        expect(
            f"{subject} reads no environment variable outside its declared table",
            not INLINE_ENV_READ.search(text),
            f"{subject} names a variable inline at os.environ or os.getenv; every read belongs in REQUIRED",
        )
        # The headline rule. A hand-written address here is the second copy ADR 0017 exists
        # to forbid, and it would keep working for exactly as long as nobody changed a port.
        literals = sorted({match.group(0) for match in CONNECTION_STRING.finditer(text)})
        expect(
            f"{subject} states no address literal of its own",
            not literals,
            f"{subject} writes {literals} — every address the example uses is exported by "
            f"scripts/example.sh from `endpoints.py --format env`, so a literal here is a copy "
            f"free to drift from the registry that publishes it",
        )
    # The pattern itself, pinned from both sides. A widened rule that stopped matching a DSN,
    # or a narrowed one that started matching this repository's own `file.py:120` citations,
    # would leave every case above green while the rule said something else entirely.
    caught = [
        "http://minio:9000",
        "postgresql://devinfra:devinfra@postgres:5432/devinfra",
        "redis://:devinfra@redis:6379/0",
        "localhost:5432",
        "127.0.0.1:9100",
        "[::1]:6379",
        "minio:9000",
        "@postgres:5432",
        '"minio:9000"',
        "`postgres:5432`",
    ]
    missed = [
        "scripts/endpoints.py:714",
        "compose.yaml:115-196",
        "https://keepachangelog.com/en/1.1.0/",
        "service.name",
        "x-app-variables:",
        '"timeout": 30',
        "Args:",
    ]
    expect(
        "the address-literal pattern catches every spelling of a host and port",
        all(CONNECTION_STRING.search(sample) for sample in caught),
        f"missed {[sample for sample in caught if not CONNECTION_STRING.search(sample)]}",
    )
    expect(
        "the address-literal pattern leaves this repository's own citations alone",
        not any(CONNECTION_STRING.search(sample) for sample in missed),
        f"flagged {[sample for sample in missed if CONNECTION_STRING.search(sample)]} — a check "
        f"that fails a file for citing a line number is a check that gets deleted",
    )

    # The example's own refusal, driven with the generator's real output for a Selection that
    # leaves object storage out. It must name the variable *and* the integration it serves,
    # and it must do so before anything is connected to: a run that opened five connections
    # and then discovered the sixth address was missing would spend its whole timeout budget
    # finding out what it already knew. No container runtime is involved — this is the
    # `validate` job's half of the story (ADR 0019).
    narrowed_render = generate("postgres", "redis", "--format", "env")
    expect(
        "the generator renders a Selection the example cannot satisfy",
        narrowed_render.returncode == 0 and bool(narrowed_render.stdout.strip()),
        f"exit {narrowed_render.returncode}: {narrowed_render.stderr!r}",
    )
    partial_env = {name: value for name, value in os.environ.items() if name not in APP_VARIABLES}
    for assignment in narrowed_render.stdout.splitlines():
        assigned_name, _, assigned_value = assignment.partition("=")
        partial_env[assigned_name] = assigned_value
    r = tool([sys.executable, str(example_app)], cwd=REPO, env=partial_env)
    expect("the worked example refuses a Selection that omits a Module", r.returncode != 0, "exited 0")
    unsaid = [needle for needle in ("AWS_ENDPOINT_URL", "object storage") if needle not in r.stderr]
    expect(
        "the refusal names the absent variable and the integration it serves",
        not unsaid,
        f"never said {unsaid}; stderr: {r.stderr!r}",
    )
    expect(
        "the worked example connects to nothing when it refuses",
        ": OK" not in r.stdout and ": FAIL" not in r.stdout,
        f"stdout: {r.stdout!r} — the refusal is stated before the first connection is opened",
    )

    # The second refusal, and the one a Selection cannot produce: every variable is present
    # and one of them says the collector speaks a protocol these exporters do not. The
    # message is carefully worded and, untested, could be inverted — `!=` to `==` — with the
    # whole gate green and every run posting protobuf at a port that answers gRPC.
    full_render = generate("--all", "--format", "env")
    expect(
        "the generator renders the whole registry for the protocol case",
        full_render.returncode == 0 and bool(full_render.stdout.strip()),
        f"exit {full_render.returncode}: {full_render.stderr!r}",
    )
    grpc_env = {name: value for name, value in os.environ.items() if name not in APP_VARIABLES}
    for assignment in full_render.stdout.splitlines():
        assigned_name, _, assigned_value = assignment.partition("=")
        grpc_env[assigned_name] = assigned_value
    grpc_env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    r = tool([sys.executable, str(example_app)], cwd=REPO, env=grpc_env)
    expect("the worked example refuses a protocol it does not speak", r.returncode != 0, "exited 0")
    unsaid = [needle for needle in ("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc", "http/protobuf") if needle not in r.stderr]
    expect(
        "the protocol refusal names the value it got and the one it speaks",
        not unsaid,
        f"never said {unsaid}; stderr: {r.stderr!r}",
    )
    expect(
        "the worked example connects to nothing when the protocol is wrong",
        ": OK" not in r.stdout and ": FAIL" not in r.stdout,
        f"stdout: {r.stdout!r} — a wrong protocol is settled before the first connection too",
    )

    # --- A service is down: the rest still run, and the telemetry still leaves (ADR 0019). ---
    #
    # The other half of the refusal above, and the harder half. A *missing* variable is a
    # refusal before anything opens; a variable that names an address nothing answers on is
    # a failure five integrations have to survive. The example exists to say which
    # connection detail is wrong, so abandoning the run at the first one — the shape a bare
    # `except` around the whole loop, or a `raise` inside it, would produce — would report
    # one defect per run over a stack with three.
    #
    # Every address is a closed loopback port except the collector's, which is a stub in
    # this process. That is what makes the last assertion possible: `telemetry: OK` is the
    # example's own claim that it flushed, and the set of paths the stub actually saw is the
    # independent answer. A flush that silently dropped logs would satisfy the first and
    # fail the second, and the arrival checks in scripts/example.sh would then be waiting
    # their whole budget on a signal that never left this process.
    with stub_collector() as (collector, exported_paths):
        down_env = {name: value for name, value in os.environ.items() if name not in APP_VARIABLES}
        down_env.update(
            {
                "OIDC_ISSUER": f"http://127.0.0.1:{CLOSED_PORTS['keycloak']}/realms/zz-selftest",
                "OIDC_DISCOVERY_URL": (
                    f"http://127.0.0.1:{CLOSED_PORTS['keycloak']}/realms/zz-selftest/.well-known/openid-configuration"
                ),
                "OIDC_CLIENT_ID": "zz-selftest",
                "OIDC_CLIENT_SECRET": "zz-selftest",
                "DATABASE_URL": f"postgresql://zz:zz@127.0.0.1:{CLOSED_PORTS['postgres']}/zz",
                "REDIS_URL": f"redis://:zz@127.0.0.1:{CLOSED_PORTS['redis']}/0",
                "AWS_ENDPOINT_URL": f"http://127.0.0.1:{CLOSED_PORTS['object-storage']}",
                "AWS_ACCESS_KEY_ID": "zz-selftest",
                "AWS_SECRET_ACCESS_KEY": "zz-selftest",
                "SMTP_HOST": "127.0.0.1",
                "SMTP_PORT": str(CLOSED_PORTS["mailpit"]),
                "OTEL_EXPORTER_OTLP_ENDPOINT": collector,
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                # A proxied machine would otherwise send urllib and botocore to the proxy
                # rather than to the closed port, and the case would hang or report the
                # proxy's refusal instead of the service's. Both spellings, because the
                # standard library reads the lowercase one and botocore the uppercase.
                "no_proxy": "*",
                "NO_PROXY": "*",
                # botocore's own retry budget, capped in the *child's* environment and
                # nowhere else. A refused connection is retryable, so the default five
                # attempts with exponential backoff would make this the slowest case in the
                # file for no extra signal — and capping it inside main.py would be the
                # example reading a name that is not in its declared table.
                "AWS_MAX_ATTEMPTS": "1",
                "AWS_RETRY_MODE": "standard",
            }
        )
        down = tool([sys.executable, str(example_app)], cwd=REPO, env=down_env)
        verdicts = {
            parsed.group("integration"): parsed
            for parsed in (VERDICT.match(line) for line in down.stdout.splitlines())
            if parsed is not None
        }
    unreachable = sorted(CLOSED_PORTS)
    expect(
        "the worked example reports every integration when one is down",
        set(verdicts) == set(unreachable) | {"telemetry"},
        f"reported {sorted(verdicts)}; expected {sorted(set(unreachable) | {'telemetry'})} — a run "
        f"that stopped at the first failure names one broken connection detail per run",
    )
    for integration in unreachable:
        verdict = verdicts.get(integration)
        expect(
            f"the {integration} integration reports FAIL against a closed port",
            verdict is not None and verdict.group("outcome") == "FAIL",
            f"reported {verdict.group(0)!r}" if verdict is not None else "reported no verdict at all",
        )
    # The clients' own words, not a uniform wrapper. Asserted by *distinctness* rather than
    # by a substring: five clients word a refused connection five different ways and spell
    # the errno differently per platform, so pinning any one phrase would pin the platform
    # instead. A `FAIL could not connect` written by the example itself would collapse these
    # five to one, which is the regression this catches.
    details = [
        found.group("detail") or "" for integration, found in sorted(verdicts.items()) if integration in CLOSED_PORTS
    ]
    expect(
        "each failing integration reports its own client's error",
        len(details) == len(unreachable) and len(set(details)) == len(unreachable) and all(details),
        f"reported {details} — five clients cannot word a refused connection identically unless "
        f"the example is wording it for them",
    )
    # …and each one is a named exception carrying a message, which is the shape a client's
    # own diagnostic has and a sentence the example wrote for it does not.
    #
    # The *address* is deliberately not asserted: three of the five clients name it and two
    # do not — urllib raises `URLError: <urlopen error [Errno 61] Connection refused>` and
    # smtplib re-raises the bare `ConnectionRefusedError` — and that is the price of
    # reporting what the client actually said. The integration name on the same line is what
    # maps the failure back to a variable in docs/ENDPOINTS.md, and it is pinned by the set
    # equality above.
    expect(
        "each failing integration names the exception its client raised",
        all(CLIENT_ERROR.match(detail) for detail in details),
        f"reported {details} — a FAIL line with no exception type is the example paraphrasing "
        f"its clients rather than quoting them",
    )
    telemetry_verdict = verdicts.get("telemetry")
    expect(
        "the telemetry still flushes after five integrations failed",
        telemetry_verdict is not None and telemetry_verdict.group("outcome") == "OK",
        f"telemetry reported {telemetry_verdict.group(0)!r}"
        if telemetry_verdict is not None
        else "telemetry reported no verdict at all",
    )
    expect(
        "all three signals really left the process before it exited",
        exported_paths == set(OTLP_PATHS),
        f"the collector saw {sorted(exported_paths)}, not {sorted(OTLP_PATHS)} — `telemetry: OK` is "
        f"the example's own claim, and this is the independent answer",
    )
    expect(
        "the worked example exits non-zero when an integration failed",
        down.returncode != 0,
        f"exited {down.returncode} with {len(unreachable)} integration(s) reporting FAIL",
    )
    expect(
        "the worked example names the failed integrations on stderr",
        all(integration in down.stderr for integration in unreachable),
        f"stderr: {down.stderr!r}",
    )

    # --- The runner's shape, with every seam stubbed (ADR 0019). ---
    #
    # The order is the contract, and it is not observable from a healthy run: the
    # application has to have finished and flushed before either arrival check runs, or both
    # are racing telemetry that is still in a batch processor. Driven through
    # DEVINFRA_PYTHON and DEVINFRA_CURL so the whole sequence is provable with no stack.
    example_stub = r"""#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >> "$STUB_RECORD"
case "${1:-}" in
  -c) printf '%s' "${3:-}"; exit 0 ;;
  scripts/resolve_selection.py) printf '%s' "${STUB_SELECTION:-}"; exit "${STUB_SELECT_EXIT:-0}" ;;
  scripts/endpoints.py) printf '%s\n' "${STUB_ENV_OUTPUT:-}"; exit "${STUB_ENDPOINTS_EXIT:-0}" ;;
  examples/worked-example/main.py)
    printf 'ENV:DATABASE_URL=%s\n' "${DATABASE_URL-<unset>}" >> "$STUB_RECORD"
    exit "${STUB_APP_EXIT:-0}" ;;
  scripts/check_dashboards.py) exit "${STUB_DASHBOARDS_EXIT:-0}" ;;
esac
exit 0
"""
    # Echoes its own arguments back by default, which is what makes the marker search
    # assertable: the URL the runner built carries the marker, so a stub that answers with
    # its argv answers the way a Mailpit holding the message would, without this case having
    # to know a marker the runner mints for itself.
    #
    # STUB_CURL_BODY overrides that with a fixed answer, which is the *other* Mailpit
    # failure — the API answered, and what came back holds no marker. Exiting non-zero and
    # answering with the wrong thing are two different branches of the runner, and a stub
    # that could only do the first would leave the second untested.
    example_curl = r"""#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >> "$STUB_RECORD"
if [ -n "${STUB_CURL_BODY:-}" ]; then
  printf '%s\n' "$STUB_CURL_BODY"
else
  printf '%s\n' "$@"
fi
exit "${STUB_CURL_EXIT:-0}"
"""
    with tempfile.TemporaryDirectory() as tmp:
        example_stubs = Path(tmp)
        python_stub = example_stubs / "zz-stub-python"
        python_stub.write_text(example_stub, encoding="utf-8", newline="\n")
        python_stub.chmod(0o755)
        curl_stub = example_stubs / "zz-stub-curl"
        curl_stub.write_text(example_curl, encoding="utf-8", newline="\n")
        curl_stub.chmod(0o755)

        def example_run(**overrides: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
            record = example_stubs / f"record-{len(list(example_stubs.glob('record-*')))}"
            env = dict(os.environ)
            env["DEVINFRA_PYTHON"] = str(python_stub)
            env["DEVINFRA_CURL"] = str(curl_stub)
            env["STUB_RECORD"] = str(record)
            env["COMPOSE_PROFILES"] = "core,observability"
            env["STUB_SELECTION"] = "keycloak,mailpit,minio,otel-collector,postgres,redis"
            env["STUB_ENV_OUTPUT"] = "DATABASE_URL=zz-selftest-dsn\nSMTP_HOST=zz-selftest-host"
            # Defined-but-empty rather than absent, so a value in this process's own
            # environment cannot change what a case is driving.
            for name in (
                "STUB_APP_EXIT",
                "STUB_CURL_EXIT",
                "STUB_CURL_BODY",
                "STUB_DASHBOARDS_EXIT",
                "STUB_ENDPOINTS_EXIT",
            ):
                env[name] = ""
            env.update(overrides)
            return run_script("example.sh", env=env), recorded(record)

        example_clean, example_trace = example_run()
        expect(
            "the example runner exits 0 when every step answers",
            example_clean.returncode == 0,
            f"exit {example_clean.returncode}: {(example_clean.stdout + example_clean.stderr)!r}",
        )

        # Ordering by first occurrence, not by presence: every one of these appears in a run
        # that called them in any order at all.
        def first_index(needle: str) -> int:
            return next((i for i, entry in enumerate(example_trace) if needle in entry), -1)

        example_order = {
            "the resolver": first_index("scripts/resolve_selection.py"),
            "the generator": first_index("scripts/endpoints.py"),
            "the application": first_index(EXAMPLE_APP),
            "the Mailpit search": first_index("/api/v1/search"),
            "the dashboard check": first_index("scripts/check_dashboards.py"),
        }
        expect(
            "the runner reaches the resolver, the generator, the application and both arrival checks",
            all(index >= 0 for index in example_order.values()),
            f"never reached {[name for name, index in example_order.items() if index < 0]}; recorded {example_trace}",
        )
        expect(
            "the runner runs the application, then Mailpit, then the dashboard check",
            list(example_order.values()) == sorted(example_order.values()),
            f"reached them in the order {sorted(example_order, key=lambda label: example_order[label])} — the arrival "
            f"checks race the application's own flush unless they follow it",
        )
        expect(
            "the runner exports the generator's own output into the application's environment",
            "ENV:DATABASE_URL=zz-selftest-dsn" in example_trace,
            f"the application saw {[entry for entry in example_trace if entry.startswith('ENV:')]} — the "
            f"values must come from `endpoints.py --format env` and from nowhere else (ADR 0017)",
        )
        expect(
            "the runner searches Mailpit for the marker it minted",
            any("/api/v1/search?query=devinfra-example-" in entry for entry in example_trace),
            f"recorded {example_trace}",
        )
        expect(
            "the runner asks the dashboard check about that same marker",
            "--service" in example_trace
            and example_trace[example_trace.index("--service") + 1 : example_trace.index("--service") + 2]
            == [next(entry for entry in example_trace if entry.startswith("devinfra-example-"))],
            f"recorded {example_trace}",
        )

        # Each arrival check failing on its own. Both must fail the run: an example whose
        # telemetry never arrived proved the client constructed, which is the thing this
        # story exists not to settle for.
        example_failed, example_trace = example_run(STUB_CURL_EXIT="1")
        expect("the runner fails when Mailpit does not answer", example_failed.returncode != 0, "exited 0")
        expect(
            "the runner names the marker it searched Mailpit for",
            "devinfra-example-" in example_failed.stderr,
            f"stderr: {example_failed.stderr!r}",
        )
        expect(
            "the runner does not check the dashboards after the mail check failed",
            first_index("scripts/check_dashboards.py") < 0,
            f"recorded {example_trace}",
        )

        # The other Mailpit branch, and the one the story's matrix actually names: the API
        # answered, and what came back holds no marker. This is what a message the SMTP
        # server accepted and then did not store looks like from outside — Mailpit is
        # healthy, `curl -sf` succeeds, and the search result is empty. A runner that only
        # checked the exit status would call that a pass and hand a green build to a
        # developer whose mail never arrived.
        example_failed, example_trace = example_run(
            STUB_CURL_BODY='{"messages_count":0,"messages":[],"total":0}',
        )
        expect(
            "the runner fails when Mailpit answers with no message carrying the marker",
            example_failed.returncode != 0,
            f"exited 0 over {example_failed.stdout!r} — a search that found nothing is not a pass",
        )
        expect(
            "the runner reports the marker it searched Mailpit for when nothing came back",
            "devinfra-example-" in example_failed.stderr,
            f"stderr: {example_failed.stderr!r} — the marker is the only thing a developer can "
            f"search the mailbox for themselves",
        )
        expect(
            "the runner checks no dashboard after Mailpit answered without the marker",
            first_index("scripts/check_dashboards.py") < 0,
            f"recorded {example_trace}",
        )

        example_failed, _ = example_run(STUB_DASHBOARDS_EXIT="1")
        expect("the runner fails when a signal never arrived", example_failed.returncode != 0, "exited 0")

        # …and the application's own failure ends the run before either arrival check, which
        # is what stops a broken integration being reported as a missing message.
        example_failed, example_trace = example_run(STUB_APP_EXIT="1")
        expect("the runner fails when an integration failed", example_failed.returncode != 0, "exited 0")
        expect(
            "the runner checks no arrival after the application failed",
            first_index("/api/v1/search") < 0 and first_index("scripts/check_dashboards.py") < 0,
            f"recorded {example_trace}",
        )

        # A Selection whose Modules own no application variable at all. The generator answers
        # with nothing and exits 0 — correct for the generator, and fatal here: an example
        # run with an empty environment would refuse one variable at a time rather than
        # saying the Selection is the problem.
        example_failed, example_trace = example_run(STUB_ENV_OUTPUT="")
        expect(
            "the runner refuses a Selection that publishes no application variable", example_failed.returncode != 0, "0"
        )
        expect(
            "the runner does not run the application over an empty environment",
            first_index(EXAMPLE_APP) < 0,
            f"recorded {example_trace}",
        )

    # The clean direction over the real tree, and the number it walked. "OK" on its own is a
    # sentence a check that walked one Module writes just as happily as one that walked
    # thirteen, so the line count is asserted against the catalog.
    r = pixi("lint-endpoints")
    endpoint_reported = [line for line in r.stdout.splitlines() if ": OK " in line]
    expect("lint-endpoints passes over the tracked tree", r.returncode == 0, f"output: {(r.stdout + r.stderr)!r}")
    expect(
        "lint-endpoints reports every Module and every application variable",
        bool(module_dir_names)
        and bool(shipped_variables)
        and all(f"{module_name}: OK " in r.stdout for module_name in module_dir_names)
        and all(f"{variable_name}: OK " in r.stdout for variable_name in shipped_variables)
        and len(endpoint_reported) >= len(module_dir_names) + len(shipped_variables),
        f"{len(endpoint_reported)} OK lines for {len(module_dir_names)} Modules and "
        f"{len(shipped_variables)} application variables; stdout: {r.stdout!r}",
    )

    # The mutations are built by round-tripping the real model through YAML rather than by
    # string surgery, so a case cannot accidentally take `include:` or `volumes:` with it and
    # red for a reason that has nothing to do with the registry.
    def root_with_variables(registry: Any) -> str:
        mutated = dict(root_model)
        if registry is None:
            mutated.pop("x-app-variables", None)
        else:
            mutated["x-app-variables"] = registry
        return str(yaml.safe_dump(mutated, sort_keys=False, default_flow_style=False))

    dsn_entry: dict[str, Any] = dict(shipped_variables.get("DATABASE_URL") or {})
    registry_defects: list[tuple[str, Any, list[str]]] = [
        (
            "a registry entry naming a Module that does not exist",
            {**shipped_variables, "DATABASE_URL": {**dsn_entry, "module": "zz-nope"}},
            ["DATABASE_URL", "zz-nope"],
        ),
        # The endpoint key is what ties the variable to one address rather than to a Module
        # in general. A key the Module does not publish is a pointer at nothing.
        (
            "a registry entry naming an endpoint its Module does not publish",
            {**shipped_variables, "DATABASE_URL": {**dsn_entry, "endpoint": "ZZ_NOPE_PORT"}},
            ["DATABASE_URL", "ZZ_NOPE_PORT", "POSTGRES_PORT"],
        ),
        # `<MODULE>_<CONCERN>` belongs to the Module tier and is owned by the Module it is
        # named for (ADR 0003). A name carrying one Module's prefix while another Module owns
        # the entry is exactly the collision the two-tier split exists to prevent.
        (
            "a registry name that belongs to another Module's tier",
            {**shipped_variables, "POSTGRES_URL": {**dsn_entry, "module": "redis", "endpoint": "REDIS_PORT"}},
            ["POSTGRES_URL", "postgres"],
        ),
        # Compose substitutes nothing for an undeclared reference with no default and merely
        # warns, which in a generated document is a connection string with a hole in it.
        (
            "a value referencing a variable nothing declares",
            {**shipped_variables, "DATABASE_URL": {**dsn_entry, "value": "postgresql://${ZZ_NOPE}@localhost:5432/x"}},
            ["ZZ_NOPE", "compose.yaml"],
        ),
        # The three unreadable shapes, each of which must read as "this registry is
        # unreadable" and never as "there are no application variables": read the second way
        # they would render a document with none and exit 0.
        ("a registry that is a list", ["DATABASE_URL"], ["x-app-variables"]),
        ("an empty registry", {}, ["x-app-variables"]),
        ("no registry at all", None, ["x-app-variables"]),
    ]
    for case, registry, needles in registry_defects:
        with moved_aside([app_root]), planted(app_root, root_with_variables(registry)):
            r = pixi("lint-endpoints")
            expect(f"lint-endpoints rejects {case}", r.returncode != 0, "exited 0")
            unsaid = [needle for needle in needles if needle not in r.stderr]
            expect(
                f"lint-endpoints names the defect for {case}",
                not unsaid,
                f"never said {unsaid}; stderr: {r.stderr!r}",
            )
            expect(
                f"lint-endpoints signs off on nothing for {case}",
                ": OK " not in r.stdout,
                f"stdout: {r.stdout!r}",
            )
    expect(
        "the root compose.yaml is restored byte-for-byte after the registry cases",
        app_root.read_text(encoding="utf-8") == app_root_text,
        "the real root file did not come back unchanged",
    )

    # A hand-edited document. The whole point of committing a generated file is that the
    # regeneration is diffed against it, so an edit that nothing regenerates must fail and
    # must say which command puts it back.
    stale_document = endpoint_text.replace("5432", "15432")
    expect(
        "the endpoint document carries a value the staleness case can change",
        stale_document != endpoint_text,
        "nothing to mutate, so the case below would be comparing a file with itself",
    )
    with moved_aside([endpoint_document]), planted(endpoint_document, stale_document):
        r = pixi("lint-endpoints")
        expect("lint-endpoints rejects a hand-edited document", r.returncode != 0, "exited 0")
        unsaid = [needle for needle in ("docs/ENDPOINTS.md", "pixi run endpoints", "15432") if needle not in r.stderr]
        expect(
            "lint-endpoints names the document, the difference and the regenerate command",
            not unsaid,
            f"never said {unsaid}; stderr: {r.stderr!r}",
        )

    # …and an absent document is a Refusal rather than a pass: there is nothing to compare a
    # regeneration against, which is not the same answer as "they agree".
    with moved_aside([endpoint_document]):
        r = pixi("lint-endpoints")
        expect("lint-endpoints fails when the document is absent", r.returncode != 0, "an absent document passed")
        unsaid = [needle for needle in ("docs/ENDPOINTS.md", "pixi run endpoints") if needle not in r.stderr]
        expect(
            "lint-endpoints says the document is not there to compare against",
            not unsaid,
            f"never said {unsaid}; stderr: {r.stderr!r}",
        )

    # The template-versus-default leg, which is required rather than extra. With the document
    # rendered from .env.example, changing only one of the two sources would leave the
    # document unchanged and the build green — the silent divergence this story closes.
    template_source = REPO / ".env.example"
    template_text = template_source.read_text(encoding="utf-8")
    disagreeing_template = template_text.replace("GRAFANA_PORT=3000", "GRAFANA_PORT=39999")
    expect(
        "the template declares the port the disagreement case changes",
        disagreeing_template != template_text,
        "GRAFANA_PORT=3000 is not in .env.example, so the case below would change nothing",
    )
    with moved_aside([template_source]), planted(template_source, disagreeing_template):
        r = pixi("lint-endpoints")
        expect("lint-endpoints rejects a template that disagrees with a compose default", r.returncode != 0, "exited 0")
        unsaid = [
            needle
            for needle in ("GRAFANA_PORT", ".env.example", "services/grafana/compose.yaml")
            if needle not in r.stderr
        ]
        expect(
            "lint-endpoints names the variable and both files it disagrees between",
            not unsaid,
            f"never said {unsaid}; stderr: {r.stderr!r}",
        )

    # …and the same pin outside the two registries. The port and the password are stated
    # again in a module's `environment:`, in scripts/lib/common.sh's four defaults and in
    # scripts/token.sh; pinning only the strings the document is rendered from would leave
    # every one of those free to drift from .env.example with the build green.
    restated_defaults: list[tuple[str, Path, str, list[str]]] = [
        (
            'a shell script whose `: "${VAR:=default}"` disagrees with the template',
            REPO / "scripts" / "token.sh",
            (REPO / "scripts" / "token.sh")
            .read_text(encoding="utf-8")
            .replace(': "${KEYCLOAK_PORT:=8080}"', ': "${KEYCLOAK_PORT:=9999}"'),
            ["KEYCLOAK_PORT", "scripts/token.sh", ".env.example"],
        ),
        (
            "a module `environment:` default that disagrees with the template",
            REPO / "services" / "postgres" / "compose.yaml",
            (REPO / "services" / "postgres" / "compose.yaml")
            .read_text(encoding="utf-8")
            .replace(
                "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-devinfra}", "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-zz}"
            ),
            ["POSTGRES_PASSWORD", "services/postgres/compose.yaml", ".env.example"],
        ),
    ]
    for case, restated_path, body_text, needles in restated_defaults:
        expect(
            f"the fixture for {case} actually differs from the tracked file",
            body_text != restated_path.read_text(encoding="utf-8"),
            "the mutation matched nothing, so this case would assert on the real file",
        )
        with moved_aside([restated_path]), planted(restated_path, body_text):
            r = pixi("lint-endpoints")
            expect(f"lint-endpoints rejects {case}", r.returncode != 0, "exited 0")
            unsaid = [needle for needle in needles if needle not in r.stderr]
            expect(
                f"lint-endpoints names the variable and the file for {case}",
                not unsaid,
                f"never said {unsaid}; stderr: {r.stderr!r}",
            )

    # The three per-entry shapes read_endpoints reports on. `lint-config` refuses a Module
    # for carrying no `x-endpoints:` block at all, and the case above covers that; a block
    # that is *present* but states an entry the generator cannot render is refused nowhere
    # else, and an entry silently skipped is an endpoint a developer is never told about.
    grafana_module = REPO / "services" / "grafana" / "compose.yaml"
    grafana_text = grafana_module.read_text(encoding="utf-8")
    endpoint_entry_defects: list[tuple[str, str, str, list[str]]] = [
        (
            "an x-endpoints entry that is not a mapping",
            "  GRAFANA_PORT:\n    url: http://localhost:${GRAFANA_PORT:-3000}\n"
            "    description: Grafana UI and HTTP API over Prometheus, Loki and Tempo.\n",
            "  GRAFANA_PORT: 3000\n",
            ["GRAFANA_PORT", "mapping"],
        ),
        (
            "an x-endpoints entry declaring an empty url",
            "    url: http://localhost:${GRAFANA_PORT:-3000}\n",
            '    url: ""\n',
            ["GRAFANA_PORT", "url"],
        ),
        (
            "an x-endpoints entry declaring an empty description",
            "    description: Grafana UI and HTTP API over Prometheus, Loki and Tempo.\n",
            '    description: ""\n',
            ["GRAFANA_PORT", "description"],
        ),
    ]
    for case, original_block, replacement, needles in endpoint_entry_defects:
        body_text = grafana_text.replace(original_block, replacement, 1)
        expect(
            f"the grafana fixture for {case} actually differs from the tracked file",
            body_text != grafana_text,
            "the mutation matched nothing, so this case would assert on the real Module file",
        )
        with moved_aside([grafana_module]), planted(grafana_module, body_text):
            r = pixi("lint-endpoints")
            expect(f"lint-endpoints rejects {case}", r.returncode != 0, "exited 0")
            unsaid = [needle for needle in ("services/grafana/compose.yaml", *needles) if needle not in r.stderr]
            expect(
                f"lint-endpoints names the Module and the entry for {case}",
                not unsaid,
                f"never said {unsaid}; stderr: {r.stderr!r}",
            )
    expect(
        "services/grafana/compose.yaml is restored byte-for-byte after the entry-shape cases",
        grafana_module.read_text(encoding="utf-8") == grafana_text,
        "the real Module file did not come back unchanged",
    )

    # The README legs. The Contents table keeps its Endpoint column, so what is pinned is
    # that every port literal in it is a port some Module actually publishes — and that the
    # rest of the file carries no connection string at all.
    readme_source = REPO / "README.md"
    readme_original = readme_source.read_text(encoding="utf-8")
    readme_defects: list[tuple[str, str, list[str]]] = [
        (
            "a Contents row stating a port no Module publishes",
            readme_original.replace("http://localhost:9090", "http://localhost:9999"),
            ["README.md", "9999"],
        ),
        # A parse that matched nothing must blame the parse, not the content: a reshaped
        # table would otherwise silently turn the port pin into a check over zero rows.
        (
            "a Contents table this check can no longer read",
            readme_original.replace("| Service | Version | Purpose | Endpoint |", "| Service | Version | Purpose |"),
            ["README.md", "Contents"],
        ),
        # The dotenv block this story removed, grown back. This is the AC in negative form.
        (
            "a connection string back in the README prose",
            readme_original.replace(
                "## Requirements",
                "DATABASE_URL=postgresql://devinfra:devinfra@localhost:5432/devinfra\n\n## Requirements",
                1,
            ),
            ["README.md", "connection string", "docs/ENDPOINTS.md"],
        ),
        # The same block under the other host spelling this repository uses for the very same
        # address — BIND_ADDRESS is 127.0.0.1, and the README says so — so a pin that knew
        # only `localhost` would let the removed block grow straight back.
        (
            "a connection string back in the README under the loopback spelling",
            readme_original.replace(
                "## Requirements",
                "AWS_ENDPOINT_URL=http://127.0.0.1:9100\n\n## Requirements",
                1,
            ),
            ["README.md", "connection string", "127.0.0.1"],
        ),
        # A port moved out of the row that publishes it and into one that does not. "Is this
        # some port the stack publishes" cannot see it — it is one — so what fails is that two
        # rows now claim Prometheus and none claims Grafana.
        (
            "a Contents row stating another Module's port",
            readme_original.replace("| http://localhost:3000 |", "| http://localhost:9090 |"),
            ["README.md", "prometheus"],
        ),
        # The reverse direction. "Every port stated is a port some Module publishes" is
        # satisfied by a table that states fewer and fewer of them, so a deleted row — or a
        # new Module nobody added one for — drops out of the at-a-glance index in silence.
        (
            "a Contents table naming no port for a Module the catalog publishes",
            readme_original.replace("| http://localhost:3000 |", "| n/a |"),
            ["README.md", "grafana"],
        ),
    ]
    for case, body_text, needles in readme_defects:
        expect(
            f"the README fixture for {case} actually differs from the tracked file",
            body_text != readme_original,
            "the mutation matched nothing, so this case would assert on the real README",
        )
        with moved_aside([readme_source]), planted(readme_source, body_text):
            r = pixi("lint-endpoints")
            expect(f"lint-endpoints rejects {case}", r.returncode != 0, "exited 0")
            unsaid = [needle for needle in needles if needle not in r.stderr]
            expect(
                f"lint-endpoints names the README defect for {case}",
                not unsaid,
                f"never said {unsaid}; stderr: {r.stderr!r}",
            )
    expect(
        "README.md is restored byte-for-byte after the endpoint cases",
        readme_source.read_text(encoding="utf-8") == readme_original,
        "the real README did not come back unchanged",
    )

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
            request: str | None = "postgres,redis",
            minio: str = "",
            fail_on: str = "",
        ) -> dict[str, str]:
            record.unlink(missing_ok=True)
            record.with_name(record.name + ".env").unlink(missing_ok=True)
            env = stub_env(compose_stub, record, stdout, services, exit_code, profiles, document, minio, fail_on)
            # Every script now resolves a Selection before it reaches the runtime, and an
            # empty one is refused rather than proceeded with (AD-18). So the ambient
            # request is stated here rather than inherited from whatever the developer
            # running the self-test happens to export — which also makes every recorded
            # COMPOSE_PROFILES value deterministic, because the resolver reads the real
            # services/*/compose.yaml and needs no stub of its own. `request=None` is for
            # the cases that must let a planted .env supply the value instead.
            env.pop("COMPOSE_PROFILES", None)
            if request is not None:
                env["COMPOSE_PROFILES"] = request
            return env

        core = "postgres\nredis\n"
        healthy = "postgres|devinfra-postgres|running|healthy|0\nredis|devinfra-redis|running|healthy|0\n"

        # The catalog, read straight off the filesystem — the independent expectation every
        # resolved-Selection assertion below is measured against. Not the resolver's own
        # answer: an expectation derived from the thing under test agrees with whatever it
        # happens to say, including a Selection list that quietly dropped a Module.
        every_module = sorted(path.parent.name for path in (REPO / "services").glob("*/compose.yaml"))
        expect("there are module directories to resolve", bool(every_module), "found no services/*/compose.yaml")
        all_modules = ",".join(every_module)
        # up-core requests the five core Modules by name; none of them depends on anything
        # outside the five, so the closure is the five.
        core_request = ["postgres", "redis", "keycloak", "minio", "mailpit"]
        core_modules = ",".join(sorted(core_request))

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
            "destroy.sh removes volumes with no --profile flag of its own",
            recorded(record) == ["down", "-v"],
            f"recorded {recorded(record)}",
        )
        # The flags left; the assertion surface moved to the environment. A destroy that
        # honoured a narrowed Selection would leave the volumes it was not asked about
        # behind while reporting that it removed every one.
        expect(
            "destroy.sh requests every Module",
            recorded_env(record) == [f"COMPOSE_PROFILES={all_modules}"],
            f"observed {recorded_env(record)}; expected every module: {every_module}",
        )

        env = fresh(healthy, core)
        env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "1", "0"
        r = run_script("keycloak-reimport.sh", env=env, stdin="reimport\n")
        args = recorded(record)
        expect("keycloak-reimport.sh accepts its exact word", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        # The whole point of story 3-2's correction. `--import-realm` ignoring an existing
        # realm is true; "so dropping the database is the only way" was not, and it is what
        # justified a DROP DATABASE in a task a developer runs to edit a JSON file. Asserted
        # over every recorded argument rather than over the psql call that used to be here,
        # so no future spelling of the same idea can slip back in.
        expect(
            "keycloak-reimport.sh drops no database",
            not any("DROP DATABASE" in argument.upper() for argument in args),
            f"recorded {args}",
        )
        expect(
            "keycloak-reimport.sh never invokes psql",
            "psql" not in args,
            f"recorded {args}",
        )
        expect(
            "keycloak-reimport.sh imports the seed realm through kc.sh",
            "/opt/keycloak/bin/kc.sh" in args
            and "import" in args
            and any(argument.endswith("-realm.json") for argument in args),
            f"recorded {args}",
        )
        # --override true is the difference between replacing the realm and doing nothing:
        # the value is asserted alongside the flag, because `--override false` is the
        # ignore-existing behaviour this script exists to escape.
        expect(
            "keycloak-reimport.sh imports with override on",
            "--override" in args and args[args.index("--override") + 1 :][:1] == ["true"],
            f"recorded {args}",
        )
        # Without a free management port the import commits the realm and *then* exits
        # non-zero on the collision with the running server's 9000, which under `set -e`
        # reports a failure for a write that landed.
        expect(
            "keycloak-reimport.sh gives the import its own management port",
            "--http-management-port" in args,
            f"recorded {args}",
        )
        # AD-12's mandatory half. The import is a separate JVM that never attaches to the
        # running server's cache, so without a restart afterwards the database and the
        # admin API disagree silently. Asserted as *after* the import, not merely present:
        # a restart before the write would leave exactly the stale cache it exists to clear.
        import_at = args.index("import") if "import" in args else -1
        restart_at = next(
            (i for i in range(len(args) - 1) if args[i] == "restart" and args[i + 1] == "keycloak"),
            -1,
        )
        expect(
            "keycloak-reimport.sh restarts keycloak after the import",
            import_at >= 0 and restart_at > import_at,
            f"import at {import_at}, restart at {restart_at}; recorded {args}",
        )

        # --- Backup and restore: an archive is a directory, and every failure is loud. ---
        # Story 3.4 replaced a single `pg_dumpall` stream with a per-Module capture, so the
        # argv these cases assert on is new; what they hold to account is unchanged in kind.

        def plant_backup(
            directory: Path,
            manifest_lines: list[str],
            databases: tuple[str, ...] = ("devinfra",),
            buckets: tuple[str, ...] = (),
            realm: str | None = None,
        ) -> Path:
            """Write a backup directory of the shape scripts/backup.sh produces."""
            directory.mkdir(parents=True)
            (directory / "manifest.txt").write_text(
                "\n".join(["# devinfra backup", *manifest_lines]) + "\n", encoding="utf-8"
            )
            if databases:
                (directory / "postgres").mkdir()
                for name in databases:
                    with gzip.open(directory / "postgres" / f"{name}.sql.gz", "wb") as dump:
                        dump.write(b"-- empty\n")
            for name in buckets:
                (directory / "minio" / name).mkdir(parents=True)
            if realm is not None:
                (directory / "keycloak").mkdir()
                (directory / "keycloak" / f"{realm}-realm.json").write_text("{}", encoding="utf-8")
            return directory

        def restore_env(request: str = "postgres,redis") -> dict[str, str]:
            """A stub environment whose trailing health wait terminates immediately."""
            environment = fresh(healthy, core, request=request)
            environment["WAIT_ATTEMPTS"], environment["WAIT_INTERVAL"] = "1", "0"
            return environment

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

        # A pre-3.4 cluster archive. `backups/` is untracked, so real ones sit in working
        # clones; a pg_dumpall stream opens with CREATE ROLE and cannot apply under
        # ON_ERROR_STOP=1, so the honest outcome is a refusal that says why — never a
        # silently weaker restore for that one path.
        legacy = stubs / "postgres-20260101-000000.sql.gz"
        with gzip.open(legacy, "wb") as archive:
            archive.write(b"-- empty\n")
        r = run_script("restore.sh", str(legacy), env=fresh())
        expect("restore.sh refuses a legacy pg_dumpall archive", r.returncode != 0, "exited 0")
        expect("restore.sh names the legacy archive", str(legacy) in r.stderr, f"stderr: {r.stderr!r}")
        expect(
            "restore.sh says why a legacy archive cannot be applied",
            "pg_dumpall" in r.stderr and "ON_ERROR_STOP" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect("restore.sh touched no runtime for a legacy archive", not recorded(record), f"{recorded(record)}")

        # A directory that is not a backup: no manifest, so there is no record of what it
        # holds and nothing may be inferred from a glob.
        not_a_backup = stubs / "not-a-backup"
        not_a_backup.mkdir()
        r = run_script("restore.sh", str(not_a_backup), env=fresh())
        expect("restore.sh refuses a directory with no manifest", r.returncode != 0, "exited 0")
        expect("restore.sh names the non-backup path", str(not_a_backup) in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore.sh touched no runtime for a non-backup path", not recorded(record), f"{recorded(record)}")

        # A component the Selection excludes is refused, not skipped: writing it would act
        # on a Module nobody asked for, and skipping it would report a restore that did not
        # happen. Refused before anything is written.
        excluded = plant_backup(
            stubs / "excluded",
            ["selection: keycloak,mailpit,minio,postgres,redis", "postgres: devinfra", "minio: uploads"],
            buckets=("uploads",),
        )
        r = run_script("restore.sh", str(excluded), env=restore_env("postgres,redis"))
        expect("restore.sh refuses a component outside the Selection", r.returncode != 0, "exited 0")
        expect(
            "restore.sh names the excluded Module and the Selection",
            "minio" in r.stderr and "postgres,redis" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect("restore.sh writes nothing when it refuses a component", not recorded(record), f"{recorded(record)}")

        # A manifest that names more than the archive holds. Without a name-by-name check
        # this restores one database of three, prints "Restored from" and exits 0 — the
        # partial success the whole story exists to remove.
        truncated = stubs / "truncated"
        plant_backup(
            truncated,
            ["selection: postgres,redis", "postgres: devinfra keycloak app_test"],
            databases=("devinfra",),
        )
        r = run_script("restore.sh", str(truncated), env=restore_env())
        expect("restore.sh refuses a manifest naming a dump that is not there", r.returncode != 0, "exited 0")
        expect(
            "restore.sh names the missing dump",
            "keycloak.sql.gz" in r.stderr or "app_test.sql.gz" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect("restore.sh writes nothing for a truncated archive", not recorded(record), f"{recorded(record)}")

        # …and the other direction. A component directory the manifest does not name never
        # reaches the Selection check, so its dumps would be applied to a stack that never
        # agreed to hold them.
        unnamed = stubs / "unnamed"
        plant_backup(unnamed, ["selection: postgres,redis", "postgres: devinfra"], realm="devinfra")
        r = run_script("restore.sh", str(unnamed), env=restore_env())
        expect("restore.sh refuses a component the manifest does not name", r.returncode != 0, "exited 0")
        expect("restore.sh names the unrecorded component", "keycloak" in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore.sh writes nothing for an unrecorded component", not recorded(record), f"{recorded(record)}")

        # restore: a relative path is read against the caller's directory, not the
        # repository root that common.sh cds to.
        elsewhere = stubs / "elsewhere"
        elsewhere.mkdir()
        plant_backup(elsewhere / "dump", ["selection: postgres,redis", "postgres: devinfra"])
        r = run_script("restore.sh", "./dump", env=restore_env(), cwd=elsewhere)
        expect(
            "restore.sh resolves a relative path against the caller's directory",
            "psql" in recorded(record),
            f"exit {r.returncode}, recorded {recorded(record)}, stderr {r.stderr!r}",
        )
        # ON_ERROR_STOP=1 on *every* psql invocation, asserted over the argv rather than
        # over the script text: without it psql exits 0 for a stream whose statements
        # errored, and a half-applied restore is counted as applied (NFR-5).
        args = recorded(record)
        psql_calls = [index for index, argument in enumerate(args) if argument == "psql"]
        expect("restore.sh invoked psql at all", bool(psql_calls), f"recorded {args}")
        expect(
            "every psql restore.sh runs carries -v ON_ERROR_STOP=1",
            all(args[index + 1 : index + 3] == ["-v", "ON_ERROR_STOP=1"] for index in psql_calls),
            f"recorded {args}",
        )

        # AD-12's ordering, asserted by argv index rather than by reading the script: the
        # dependents the resolver names are stopped before the first write and started after
        # the last, because `DROP DATABASE keycloak` fails while Keycloak holds a connection
        # to it. The Selection is `core`, whose only Postgres dependent is Keycloak.
        #
        # Two databases, not one: with a single dump the first and the last write are the
        # same index, and "started after the last write" could not tell a start that follows
        # the loop from one inside it.
        ordered = plant_backup(
            stubs / "ordered",
            ["selection: core", "postgres: app_test devinfra"],
            databases=("app_test", "devinfra"),
        )
        r = run_script("restore.sh", str(ordered), env=restore_env("core"))
        args = recorded(record)
        stop_at = next((i for i in range(len(args) - 1) if args[i] == "stop" and args[i + 1] == "keycloak"), -1)
        start_at = next((i for i in range(len(args) - 1) if args[i] == "start" and args[i + 1] == "keycloak"), -1)
        writes = [index for index, argument in enumerate(args) if argument == "psql"]
        first_write = writes[0] if writes else -1
        last_write = writes[-1] if writes else -1
        expect("restore.sh exits 0 over a stubbed runtime", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect("restore.sh applies one dump per database the manifest names", len(writes) == 2, f"recorded {args}")
        expect(
            "restore.sh stops the Postgres dependents before the first write",
            stop_at >= 0 and first_write > stop_at,
            f"stop at {stop_at}, first psql at {first_write}; recorded {args}",
        )
        expect(
            "restore.sh starts the Postgres dependents after the last write",
            start_at > last_write >= 0,
            f"start at {start_at}, last psql at {last_write}; recorded {args}",
        )
        # AC4's second half: health is awaited *after* the dependents are started, so the
        # command does not return while Keycloak is up but not yet answering. `ps` is what
        # scripts/wait-healthy.sh asks the runtime, so its index is where the wait began.
        wait_at = next((index for index in range(len(args)) if args[index] == "ps" and index > start_at), -1)
        expect(
            "restore.sh waits for health after starting what it stopped",
            start_at >= 0 and wait_at > start_at,
            f"start at {start_at}, ps at {wait_at}; recorded {args}",
        )
        # …and it hard-codes neither the Module nor the service: the list comes from
        # `select.sh --dependents postgres`, so a Selection with no dependent stops nothing.
        none_stopped = plant_backup(stubs / "nodeps", ["selection: postgres,redis", "postgres: devinfra"])
        r = run_script("restore.sh", str(none_stopped), env=restore_env("postgres,redis"))
        args = recorded(record)
        expect(
            "restore.sh stops nothing when the Selection holds no Postgres dependent",
            "stop" not in args,
            f"recorded {args}",
        )

        # A write that fails ends the restore *and* still starts the dependents again. Only
        # `psql` is made to fail, because a stub that failed everything would fail the
        # recovery too and the recording would prove nothing: the point is that a restore
        # which died mid-stream does not also leave Keycloak stopped.
        failing = plant_backup(stubs / "failing", ["selection: core", "postgres: devinfra"])
        r = run_script("restore.sh", str(failing), env=restore_env("core") | {"STUB_FAIL_ON": "psql"})
        args = recorded(record)
        stop_at = next((i for i in range(len(args) - 1) if args[i] == "stop" and args[i + 1] == "keycloak"), -1)
        start_at = next((i for i in range(len(args) - 1) if args[i] == "start" and args[i + 1] == "keycloak"), -1)
        expect("restore.sh exits non-zero when a write fails", r.returncode != 0, "exited 0 on a failed write")
        expect(
            "restore.sh starts the dependents again after a failed write",
            stop_at >= 0 and start_at > stop_at,
            f"stop at {stop_at}, start at {start_at}; recorded {args}",
        )

        # The object-storage half of a restore, which nothing above reaches: every archive
        # that restores to completion elsewhere in this suite holds `postgres/` alone, and
        # the two that carry a `minio/` are refusal cases that exit before the first write.
        # Without this, inverting the mirror — writing the live bucket *into* the archive
        # stage instead of the objects back — leaves the whole suite green.
        objects = plant_backup(
            stubs / "objects",
            [
                "selection: minio,postgres,redis",
                "postgres: devinfra",
                "minio: uploads artifacts",
                "skipped: keycloak (not in the Selection)",
            ],
            buckets=("uploads", "artifacts"),
        )
        r = run_script("restore.sh", str(objects), env=restore_env("core"))
        args = recorded(record)
        lines = [argument.strip() for argument in args]
        cp_at = next(
            (
                index
                for index in range(len(args) - 2)
                if args[index] == "cp"
                and args[index + 1].endswith("/minio")
                and args[index + 2].startswith("minio:/tmp/devinfra-restore-")
            ),
            -1,
        )
        expect(
            "restore.sh exits 0 over an archive holding objects",
            r.returncode == 0,
            f"exit {r.returncode}: {r.stderr!r}",
        )
        expect(
            "restore.sh copies the staged objects into the container",
            cp_at >= 0,
            f"recorded {args}",
        )
        # Cleared before the copy, because `cp` into a path that already exists nests one
        # level and a leftover from a recycled PID would make every mirror path below miss.
        stage = args[cp_at + 2].split(":", 1)[1] if cp_at >= 0 else ""
        clear_at = next(
            (
                index
                for index in range(len(args) - 2)
                if args[index] == "rm" and args[index + 1] == "-rf" and args[index + 2] == stage
            ),
            -1,
        )
        expect(
            "restore.sh clears the container-side stage before copying into it",
            0 <= clear_at < cp_at,
            f"clear at {clear_at}, cp at {cp_at}; recorded {args}",
        )
        # One mirror per bucket the manifest names, in its order: the trailing
        # `sh "$stage" "$bucket"` triple is what the container-side program is handed. Read
        # as that triple rather than as "whatever follows the stage path", because the stage
        # is also an argument of the two `rm -rf` calls that bracket the copy.
        mirrored = [
            args[index + 2]
            for index in range(len(args) - 2)
            if stage and args[index] == "sh" and args[index + 1] == stage
        ]
        expect(
            "restore.sh mirrors each bucket the manifest names",
            mirrored == ["uploads", "artifacts"],
            f"mirrored {mirrored}; recorded {args}",
        )
        # The direction and the flags, pinned as the recorded program text rather than
        # inferred: `--remove` is what makes the bucket end as captured rather than as a
        # union with whatever the re-provisioned stack seeded, and `$1/$2` -> `local/$2` is
        # the archive going back into the server, not the server into the archive.
        expect(
            "restore.sh mirrors the archive into the bucket, replacing what is there",
            'exec mc mirror --overwrite --remove "$1/$2" "local/$2"' in lines,
            f"recorded {args}",
        )
        expect(
            "restore.sh recreates a bucket that was deleted since the capture",
            'mc mb --ignore-existing "local/$2"' in lines,
            f"recorded {args}",
        )
        start_at = next((i for i in range(len(args) - 1) if args[i] == "start" and args[i + 1] == "keycloak"), -1)
        last_write = max((index for index, argument in enumerate(args) if argument == "psql"), default=-1)
        expect(
            "restore.sh mirrors the objects between the last write and the restart",
            last_write >= 0 and last_write < cp_at < start_at,
            f"last psql at {last_write}, cp at {cp_at}, start at {start_at}; recorded {args}",
        )
        # What the archive never held, said out loud: backup records the Modules the
        # Selection excluded, and a restore that reads them and says nothing leaves the
        # operator to assume a narrower archive covered what it did not.
        expect(
            "restore.sh reports what the archive never held",
            "Not in this archive: keycloak (not in the Selection)" in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # A server that really lists no bucket is recorded with an explicit marker, and
        # restore reads it as "nothing to mirror" rather than as a bucket named `(no`.
        # Neither end of that contract is observed by any other case.
        no_buckets = plant_backup(
            stubs / "no-buckets",
            ["selection: core", "postgres: devinfra", "minio: (no buckets)"],
        )
        (no_buckets / "minio").mkdir()
        r = run_script("restore.sh", str(no_buckets), env=restore_env("core"))
        args = recorded(record)
        expect("restore.sh exits 0 over a bucketless archive", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "restore.sh mirrors nothing for a bucketless archive",
            not any(argument.strip().startswith("exec mc mirror") for argument in args),
            f"recorded {args}",
        )

        # The by-name truncation refusals, for the two components that had none. A manifest
        # naming a bucket or a realm whose files are absent must refuse before the first
        # write, not fail mid-mirror with Postgres already rewritten.
        short_buckets = plant_backup(
            stubs / "short-buckets",
            ["selection: core", "postgres: devinfra", "minio: uploads artifacts"],
            buckets=("uploads",),
        )
        r = run_script("restore.sh", str(short_buckets), env=restore_env("core"))
        expect("restore.sh refuses a manifest naming a bucket that is not there", r.returncode != 0, "exited 0")
        expect("restore.sh names the missing bucket", "artifacts" in r.stderr, f"stderr: {r.stderr!r}")
        expect("restore.sh writes nothing for a missing bucket", not recorded(record), f"{recorded(record)}")

        short_realm = plant_backup(
            stubs / "short-realm",
            ["selection: core", "postgres: devinfra", "keycloak: devinfra"],
        )
        (short_realm / "keycloak").mkdir()
        r = run_script("restore.sh", str(short_realm), env=restore_env("core"))
        expect("restore.sh refuses a manifest naming a realm that is not there", r.returncode != 0, "exited 0")
        expect(
            "restore.sh names the missing realm export",
            "devinfra-realm.json" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect("restore.sh writes nothing for a missing realm export", not recorded(record), f"{recorded(record)}")

        # backup: the directory is named on success and never left behind on failure.
        backups = REPO / "backups"
        before = set(backups.glob("*")) if backups.exists() else set()

        def new_backups() -> list[Path]:
            return sorted(path for path in backups.glob("*") if path not in before)

        r = run_script("backup.sh", env=fresh(stdout="app_test\ndevinfra\n"))
        created = new_backups()
        args = recorded(record)
        try:
            expect("backup.sh exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect("backup.sh writes exactly one directory", len(created) == 1, f"created {created}")
            expect(
                "backup.sh dumps one database at a time, never the whole cluster",
                "pg_dump" in args and "pg_dumpall" not in args,
                f"recorded {args}",
            )
            # --create --clean --if-exists is what makes a dump self-contained: it emits
            # DROP DATABASE IF EXISTS and CREATE DATABASE, which is the only shape that
            # applies under ON_ERROR_STOP=1 into a cluster that already exists. Asserted as
            # a slice around the `pg_dump` token rather than as set membership over the flat
            # recording: the first `-U` in that recording belongs to the psql call that
            # listed the databases, so a `pg_dump` invoked as some other user, without -T,
            # or missing a flag would satisfy a membership check while this fails.
            dump_at = args.index("pg_dump") if "pg_dump" in args else -1
            expect(
                "backup.sh dumps each database self-contained, as POSTGRES_USER",
                dump_at >= 3
                and args[dump_at - 3 : dump_at + 8]
                == [
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "-U",
                    "devinfra",
                    "--create",
                    "--clean",
                    "--if-exists",
                    "-d",
                    "app_test",
                ],
                f"pg_dump at {dump_at}; recorded {args}",
            )
            if created:
                archive_dir = created[0]
                dumps = sorted(path.name for path in (archive_dir / "postgres").glob("*.sql.gz"))
                expect(
                    "backup.sh writes one dump per database the server named",
                    dumps == ["app_test.sql.gz", "devinfra.sql.gz"],
                    f"wrote {dumps}",
                )
                manifest_text = (archive_dir / "manifest.txt").read_text(encoding="utf-8")
                expect(
                    "backup.sh records the captured databases in the manifest",
                    "postgres: app_test devinfra" in manifest_text,
                    f"manifest: {manifest_text!r}",
                )
                # A Module outside the Selection is recorded as skipped, not failed: that is
                # the difference between "this backup does not cover object storage" and
                # "this backup is incomplete and nobody said so".
                expect(
                    "backup.sh records a Module outside the Selection as skipped",
                    "skipped: minio (not in the Selection)" in manifest_text
                    and "skipped: keycloak (not in the Selection)" in manifest_text,
                    f"manifest: {manifest_text!r}",
                )
                expect(
                    "backup.sh writes nothing for a Module outside the Selection",
                    not (archive_dir / "minio").exists() and not (archive_dir / "keycloak").exists(),
                    f"directory holds {sorted(path.name for path in archive_dir.iterdir())}",
                )
                expect(
                    "backup.sh names the archive it wrote",
                    archive_dir.name in r.stdout,
                    f"stdout: {r.stdout!r}",
                )
        finally:
            for path in new_backups():
                shutil.rmtree(path, ignore_errors=True)

        # The whole capture, over a Selection that holds all three stateful Modules. The
        # bucket listing comes from its own stub answer because a database list and an
        # `mc ls --json` listing are parsed differently and cannot be the same text —
        # which is also the shape of the real thing, two different servers answering two
        # different questions.
        listing = '{"status":"success","type":"folder","key":"uploads/"}\n{"key":"artifacts/"}'
        r = run_script("backup.sh", env=fresh(stdout="devinfra\n", request="core", minio=listing))
        created = new_backups()
        args = recorded(record)
        try:
            expect("backup.sh exits 0 over a full Selection", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect("backup.sh writes one directory for a full Selection", len(created) == 1, f"created {created}")
            if created:
                manifest_text = (created[0] / "manifest.txt").read_text(encoding="utf-8")
                expect(
                    "backup.sh captures all three stateful Modules when the Selection holds them",
                    "postgres: devinfra" in manifest_text
                    and "minio: uploads artifacts" in manifest_text
                    and "keycloak: devinfra" in manifest_text,
                    f"manifest: {manifest_text!r}",
                )
                expect(
                    "backup.sh records nothing as skipped when the Selection holds every stateful Module",
                    "skipped:" not in manifest_text,
                    f"manifest: {manifest_text!r}",
                )
            # The bucket names come from the server's listing, never from MINIO_BUCKETS: one
            # mirror per bucket the listing named, and the archive would otherwise stop at
            # whatever that variable was last edited to say.
            mirrored = [argument for argument in args if argument in ("uploads", "artifacts")]
            expect(
                "backup.sh mirrors each bucket the server listed",
                mirrored == ["uploads", "artifacts"],
                f"mirrored {mirrored}; recorded {args}",
            )
            # The realm export carries the free management port. Without it the export
            # writes the file and then exits 1 on the port the running server holds.
            expect(
                "backup.sh gives the realm export its own management port",
                "--http-management-port" in args and args[args.index("--http-management-port") + 1 :][:1] == ["9999"],
                f"recorded {args}",
            )
        finally:
            for path in new_backups():
                shutil.rmtree(path, ignore_errors=True)

        # A server that lists no bucket at all. The manifest says so with an explicit
        # marker rather than an empty list, because `minio: ` and "the bucket names went
        # missing" read the same on disk — and restore, which reads this line back, refuses
        # the empty form. The two ends of that contract are asserted here and above.
        r = run_script("backup.sh", env=fresh(stdout="devinfra\n", request="core", minio="\n"))
        created = new_backups()
        args = recorded(record)
        try:
            expect(
                "backup.sh exits 0 over a bucketless server",
                r.returncode == 0,
                f"exit {r.returncode}: {r.stderr!r}",
            )
            if created:
                manifest_text = (created[0] / "manifest.txt").read_text(encoding="utf-8")
                expect(
                    "backup.sh records a bucketless server with an explicit marker",
                    "minio: (no buckets)" in manifest_text,
                    f"manifest: {manifest_text!r}",
                )
            expect(
                "backup.sh mirrors nothing when the server lists no bucket",
                not any(argument.strip().startswith("exec mc mirror") for argument in args),
                f"recorded {args}",
            )
        finally:
            for path in new_backups():
                shutil.rmtree(path, ignore_errors=True)

        # A capture step that fails takes the whole backup with it, partial directory and
        # all: an archive missing a Module in the Selection is not a smaller backup.
        r = run_script("backup.sh", env=fresh(exit_code="1"))
        leftovers = new_backups()
        expect("backup.sh fails when a capture step fails", r.returncode != 0, "exited 0 on a failed capture")
        expect("backup.sh names the Module and the step that failed", "postgres" in r.stderr, f"{r.stderr!r}")
        expect("backup.sh leaves no partial directory behind", not leftovers, f"left {leftovers}")
        for path in leftovers:
            shutil.rmtree(path, ignore_errors=True)

        # A Selection holding nothing stateful is a refusal, not an empty archive: an empty
        # directory carrying a manifest would be a valid-looking backup of nothing.
        r = run_script("backup.sh", env=fresh(request="mailpit"))
        leftovers = new_backups()
        expect("backup.sh refuses a Selection with nothing stateful", r.returncode != 0, "exited 0")
        expect(
            "backup.sh says the Selection holds no stateful Module",
            "no stateful Module" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect("backup.sh writes nothing when it refuses", not leftovers, f"left {leftovers}")
        for path in leftovers:
            shutil.rmtree(path, ignore_errors=True)

        # --- The resolver's third request form, and the predicate two scripts share. ---
        # restore.sh must not hard-code "keycloak and pgadmin", and `compose stop` speaks
        # services rather than Modules, so the answer is service names scoped to the
        # Selection.
        r = run_script("select.sh", "--dependents", "postgres", env=fresh(request="core"))
        expect(
            "select.sh --dependents names the Postgres dependents in a core Selection",
            r.returncode == 0 and r.stdout.strip() == "keycloak",
            f"exit {r.returncode}, stdout {r.stdout!r}, stderr {r.stderr!r}",
        )
        r = run_script("select.sh", "--dependents", "postgres", env=fresh(request="core,admin"))
        expect(
            "select.sh --dependents widens with the Selection",
            r.returncode == 0 and r.stdout.strip() == "keycloak,pgadmin",
            f"exit {r.returncode}, stdout {r.stdout!r}, stderr {r.stderr!r}",
        )
        r = run_script("select.sh", "--dependents", "postgres", env=fresh(request="postgres"))
        expect(
            "select.sh --dependents prints an empty line for an empty set",
            r.returncode == 0 and r.stdout == "\n",
            f"exit {r.returncode}, stdout {r.stdout!r}",
        )
        # The pixi task takes one `names` argument, so this spelling arrives as a single
        # word-joined string. It is the form the flattening in select.sh exists for, and
        # nothing else here exercises it: a plain two-element check on argv would break
        # `pixi run select "--dependents postgres"` with the suite still green.
        r = pixi("select", "--dependents postgres", env=fresh(request="core"))
        expect(
            "the select task answers --dependents given as one argument",
            r.returncode == 0 and r.stdout.strip() == "keycloak",
            f"exit {r.returncode}, stdout {r.stdout!r}, stderr {r.stderr!r}",
        )
        r = run_script("select.sh", "--dependents", "zz-nope", env=fresh(request="core"))
        expect("select.sh --dependents refuses an unknown Module", r.returncode != 0, "exited 0")
        expect("select.sh --dependents prints nothing when it refuses", r.stdout == "", f"stdout: {r.stdout!r}")
        expect("select.sh --dependents names the unknown Module", "zz-nope" in r.stderr, f"stderr: {r.stderr!r}")

        # `selected` is the membership test backup.sh and restore.sh both ask. The comma
        # fences are the whole point: without them a Selection holding `redisinsight` would
        # answer yes for `redis`, and a backup would try to dump a Module that is not there.
        probe = subprocess.run(
            [
                "bash",
                "-c",
                "source scripts/lib/common.sh\n"
                'COMPOSE_PROFILES="postgres,redisinsight"\n'
                "selected postgres && echo yes-postgres\n"
                "selected redisinsight && echo yes-redisinsight\n"
                "selected redis || echo no-redis\n"
                "selected minio || echo no-minio\n",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
        )
        expect(
            "selected answers yes for a Module in the resolved Selection",
            probe.stdout.split() == ["yes-postgres", "yes-redisinsight", "no-redis", "no-minio"],
            f"stdout: {probe.stdout!r}, stderr: {probe.stderr!r}",
        )

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
        # The register's export-side entry, asserted rather than only described. Without a
        # free management port the export writes the realm file and *then* exits 1 on the
        # collision with the running server's 9000 — verified against the pinned 26.7.3 —
        # so `set -e` reported a failure for an export that had already landed.
        expect(
            "keycloak-export.sh gives the export its own management port",
            "--http-management-port" in args and args[args.index("--http-management-port") + 1 :][:1] == ["9999"],
            f"recorded {args}",
        )
        expect(
            "keycloak-export.sh copies container path to repository path, in that order",
            args[-3:]
            == [
                "cp",
                "keycloak:/tmp/kc-export/devinfra-realm.json",
                "services/keycloak/seed/devinfra-realm.json",
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

        # urls: complete on a fresh clone — no .env, no COMPOSE_PROFILES — at the defaults
        # compose.yaml interpolates. The expectation is derived from the module files, never
        # restated as a tuple: the hard-coded list of twelve display names and fourteen ports
        # this replaces *was* the drift it was supposed to catch, having silently lost
        # LOKI_PORT, TEMPO_PORT and KEYCLOAK_MGMT_PORT while its own header claimed that a
        # service missing from it is a service a developer cannot find.
        endpoint_defaults: dict[str, str] = {}
        declared_endpoint_keys: set[str] = set()
        for module_path in sorted((REPO / "services").glob("*/compose.yaml")):
            published = (yaml.safe_load(module_path.read_text(encoding="utf-8")) or {}).get("x-endpoints") or {}
            for key, entry in published.items():
                declared_endpoint_keys.add(str(key))
                url = str(entry.get("url", "")) if isinstance(entry, dict) else ""
                fallback = re.search(rf"\$\{{{re.escape(str(key))}:-([^}}]*)\}}", url)
                if fallback is not None:
                    endpoint_defaults[str(key)] = fallback.group(1)
        # The derivation, before anything is asserted with it: a walk that found no key, or
        # a key whose own url does not carry its default, would make every expectation below
        # pass over nothing.
        expect(
            "every x-endpoints: key names its own default in its url",
            bool(endpoint_defaults) and set(endpoint_defaults) == declared_endpoint_keys,
            f"derived defaults for {sorted(endpoint_defaults)} out of {sorted(declared_endpoint_keys)}",
        )
        r = pixi("urls", env=fresh(request=None))
        expect("urls exits 0 without .env", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        for key, fallback_value in sorted(endpoint_defaults.items()):
            expect(
                f"urls prints {key} at compose's {fallback_value} without .env",
                key in r.stdout and fallback_value in r.stdout,
                f"stdout: {r.stdout!r}",
            )

        # An explicit Selection argument — the other way all three entry points are invoked,
        # and the only thing here that proves an argument reaches the generator at all.
        r = pixi("urls", "postgres", env=fresh(request=None))
        expect("urls with an explicit Selection exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "urls with an explicit Selection prints only that Module's endpoints",
            "POSTGRES_PORT" in r.stdout and "GRAFANA_PORT" not in r.stdout,
            f"stdout: {r.stdout!r}",
        )
        # The Application variables section renders at all. Every other assertion here is
        # satisfied by the endpoint half alone — `zzuser` and every port reach stdout through
        # an endpoint URL — so that whole branch could be deleted with the suite still green.
        expect(
            "urls renders the application variables the Selection's Modules own",
            "DATABASE_URL=" in r.stdout,
            f"stdout: {r.stdout!r}",
        )
        # …and renders none for a Selection whose closure owns none. `grafana` pulls in
        # Prometheus, Loki and Tempo and nothing that the registry names.
        r = pixi("urls", "grafana", env=fresh(request=None))
        expect("urls exits 0 for a Selection that owns no application variable", r.returncode == 0, f"{r.stderr!r}")
        expect(
            "urls renders no application variable a Selection does not reach",
            "DATABASE_URL=" not in r.stdout and "Application variables" not in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # …and a name nothing answers to is the resolver's own refusal, with nothing at all on
        # stdout: a caller reading this listing must never receive a partial one (AD-18).
        r = pixi("urls", "zz-nope", env=fresh(request=None))
        expect("urls refuses an unknown Selection name", r.returncode != 0, "exited 0")
        expect("urls prints no endpoint when it refuses", "_PORT" not in r.stdout, f"stdout: {r.stdout!r}")
        expect("urls names the unknown Selection", "zz-nope" in r.stderr, f"stderr: {r.stderr!r}")

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
        spaced = plant_backup(spaced_dir / "20260101-000000", ["selection: postgres,redis", "postgres: devinfra"])
        r = pixi("restore", str(spaced), env=restore_env())
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
            r = pixi("psql", env=fresh(request=None))
            expect(
                "psql uses the user and database .env declares",
                recorded(record) == ["exec", "postgres", "psql", "-U", "zzuser", "-d", "zzdb"],
                f"recorded {recorded(record)}",
            )
            # The argv above is identical whether or not psql.sh resolved anything, so the
            # ambient resolution is asserted on the environment the stub actually saw. The
            # planted request is `admin,observability`, whose closure is ten Modules and so
            # differs from itself: deleting `select_ambient` from psql.sh reds this, where a
            # request whose closure were its own name would not.
            expect(
                "psql resolves the ambient Selection before reaching the runtime",
                recorded_env(record)
                == [
                    "COMPOSE_PROFILES=flower,grafana,loki,otel-collector,pgadmin,"
                    "postgres,prometheus,redis,redisinsight,tempo"
                ],
                f"observed {recorded_env(record)}",
            )
            r = pixi("redis-cli", env=fresh(request=None))
            expect(
                "redis-cli uses the password .env declares",
                recorded(record) == ["exec", "redis", "redis-cli", "-a", "zzpass", "--no-auth-warning"],
                f"recorded {recorded(record)}",
            )
            # The listing is generated and Selection-scoped now, so this case proves both
            # halves at once. The planted .env asks for `admin,observability`, whose closure
            # holds Grafana and Postgres but not Keycloak: the Grafana port and the Postgres
            # user .env declares must reach stdout, and Keycloak's realm and port must not.
            # A listing that ignored .env would print none of the four; one that ignored the
            # Selection would print all four.
            r = pixi("urls", env=fresh(request=None))
            expect("urls exits 0 with a .env present", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect("urls prints the port .env declares", "31337" in r.stdout, f"stdout: {r.stdout!r}")
            expect("urls prints the user .env declares", "zzuser" in r.stdout, f"stdout: {r.stdout!r}")
            expect(
                "urls leaves out a Module the ambient Selection excludes",
                "KEYCLOAK_PORT" not in r.stdout and "zzrealm" not in r.stdout and "8080" not in r.stdout,
                f"Keycloak is outside the admin,observability closure; stdout: {r.stdout!r}",
            )
            # …and `--all`, which the wrapper's header and CHANGELOG.md both document, escapes
            # that Selection: the Module the case above proved absent must be back.
            r = pixi("urls", "--all", env=fresh(request=None))
            expect("urls --all exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect(
                "urls --all prints a Module the ambient Selection excludes",
                "KEYCLOAK_PORT" in r.stdout and "zzrealm" in r.stdout,
                f"stdout: {r.stdout!r}",
            )

            # The stub is a child process, so what it observes is what .env
            # actually exported. Without `set -a` around the source these values
            # would stay shell-local and every child would see nothing — and with a
            # profile on every service, the Selection would then be empty and the
            # script would refuse rather than reach the runtime at all. What the stub
            # sees is .env's request `admin` *resolved*: the three admin services plus
            # the Postgres and Redis they talk to, which is the whole point of the
            # resolver and is pinned literally here rather than recomputed: `admin` and
            # `observability` between them pull in Postgres and Redis, and nothing else.
            r = pixi("ps", env=fresh(request=None))
            expect(
                "values from .env are exported to child processes, resolved",
                recorded_env(record)
                == [
                    "COMPOSE_PROFILES=flower,grafana,loki,otel-collector,pgadmin,"
                    "postgres,prometheus,redis,redisinsight,tempo"
                ],
                f"observed {recorded_env(record)}",
            )

            # An environment may carry a name that is not a valid shell identifier — a
            # pixi task with an `env` table leaves one called `?` behind — and the
            # export/replay above is where that bites: `declare -x ?="0"` makes `declare`
            # fail, and under `set -e` every script that sources common.sh dies before it
            # reaches the runtime. The five lifecycle tasks with an `env` table hit this
            # for real; this states it directly so the filter cannot be deleted.
            env = fresh(request=None)
            env["?"] = "0"
            r = pixi("ps", env=env)
            expect(
                "a script survives an environment carrying an invalid identifier",
                r.returncode == 0 and "not a valid identifier" not in r.stderr,
                f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
            )
            expect(
                "the .env value still wins after the replay skips the invalid name",
                recorded_env(record)
                == [
                    "COMPOSE_PROFILES=flower,grafana,loki,otel-collector,pgadmin,"
                    "postgres,prometheus,redis,redisinsight,tempo"
                ],
                f"observed {recorded_env(record)}",
            )

            # up-core requests the five core Modules by name and then execs the health
            # wait, which re-reads .env. If .env won, the wait would cover the very
            # containers up-core excluded. Clearing COMPOSE_PROFILES used to do this job
            # and no longer can: with a profile on every service an empty value selects
            # nothing, which is the breaking change ADR 0013 records.
            env = fresh(healthy, core, request=None)
            env["WAIT_ATTEMPTS"], env["WAIT_INTERVAL"] = "1", "0"
            r = run_script("up-core.sh", env=env)
            profiles = recorded_env(record)
            expect("up-core.sh exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
            expect("up-core.sh starts the stack", recorded(record)[:2] == ["up", "-d"], f"recorded {recorded(record)}")
            expect("up-core.sh made more than one runtime call", len(profiles) > 1, f"observed {profiles}")
            expect(
                "every up-core.sh runtime call sees exactly the five core Modules",
                set(profiles) == {f"COMPOSE_PROFILES={core_modules}"},
                f"observed {profiles}; expected {core_modules}",
            )
            expect(
                "up-core.sh does not drag in the admin services .env asks for",
                all("pgadmin" not in line for line in profiles),
                f"observed {profiles}",
            )

        # --- An empty Selection is refused, and the runtime is never reached. ---
        # One of the two mitigations ADR 0013 leans on: a .env predating Selection, or one
        # that lost the line, fails loudly instead of starting nothing and reporting
        # success. Asserted on behaviour rather than on the source text, because the way
        # this regresses is invisible to a grep — `select_profiles` written as
        # `local x="$(...)"` takes `local`'s exit status, swallows the refusal and runs on
        # with an empty COMPOSE_PROFILES. Every other case here supplies a non-empty
        # request, so all of them stay green through exactly that mutation.
        r = pixi("ps", env=fresh(request=None))
        expect("a script refuses an empty ambient Selection", r.returncode != 0, "exited 0 with nothing selected")
        expect("the refusal names the variable to fix", "COMPOSE_PROFILES" in r.stderr, f"stderr: {r.stderr!r}")
        expect("a refused Selection never reaches the runtime", not recorded(record), f"recorded {recorded(record)}")

        # …and the case that actually happens to people: a real `.env` that predates
        # Selection. The case above has no .env at all, which is a fresh clone; this one
        # is a file full of working values that simply never had the line, and a second
        # with the line present but empty. Both are driven through the task surface —
        # `pixi run start`, what a developer types — rather than through the script
        # directly, because `start` depends on `init` and a refusal that happened after
        # init had already run would be a refusal that still touched the checkout.
        #
        # The advice is asserted, not just the variable name. "COMPOSE_PROFILES is unset"
        # tells a reader which line is missing and nothing about what to write in it, and
        # the value that answers that question is a Bundle list that did not exist before
        # this story — which is why the release note and the registry ship together.
        upgrade_line = "COMPOSE_PROFILES=core,admin,observability"
        legacy_env = (
            "# A .env written before Selection existed.\n"
            "POSTGRES_USER=zzuser\n"
            "POSTGRES_DB=zzdb\n"
            "REDIS_PASSWORD=zzpass\n"
        )
        for case, body in (
            ("a legacy .env with no COMPOSE_PROFILES line", legacy_env),
            ("a .env whose COMPOSE_PROFILES is empty", legacy_env + "COMPOSE_PROFILES=\n"),
        ):
            with planted(REPO / ".env", body):
                r = pixi("start", env=fresh(request=None))
                expect(f"the task surface refuses {case}", r.returncode != 0, "exited 0 with nothing selected")
                expect(
                    f"the refusal for {case} names the variable",
                    "COMPOSE_PROFILES" in r.stderr,
                    f"stderr: {r.stderr!r}",
                )
                expect(
                    f"the refusal for {case} names the exact line to add",
                    upgrade_line in r.stderr,
                    f"never said {upgrade_line!r}; stderr: {r.stderr!r}",
                )
                expect(
                    f"nothing is started for {case}",
                    not recorded(record),
                    f"the container runtime was invoked: {recorded(record)}",
                )

        # --- The resolver's interpreter is a seam, and a missing PyYAML is a diagnostic. ---
        # Every lifecycle script runs Python now, where none did before, so the two ways
        # that can fail outside pixi are pinned: the DEVINFRA_PYTHON escape hatch the
        # refusal itself tells the reader to reach for, and the named refusal that
        # replaced a raw ModuleNotFoundError traceback.
        python_stub = stubs / "python-stub"
        python_stub.write_text(
            '#!/usr/bin/env bash\necho "zz-stub-interpreter $1"\n',
            encoding="utf-8",
            newline="\n",
        )
        python_stub.chmod(0o755)
        env = fresh()
        env["DEVINFRA_PYTHON"] = str(python_stub)
        r = run_script("select.sh", "postgres", env=env)
        expect(
            "select.sh runs the interpreter DEVINFRA_PYTHON names",
            r.stdout.strip() == "zz-stub-interpreter scripts/resolve_selection.py",
            f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}",
        )

        # urls.sh reaches the same seam, and it is now the script whose failure mode outside
        # pixi is exactly the missing-PyYAML refusal above: it used to be pure bash.
        env = fresh()
        env["DEVINFRA_PYTHON"] = str(python_stub)
        r = run_script("urls.sh", "postgres", env=env)
        expect(
            "urls.sh runs the interpreter DEVINFRA_PYTHON names",
            r.stdout.strip() == "zz-stub-interpreter scripts/endpoints.py",
            f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}",
        )

        # …and the same entry point producing an actual listing. README.md promises
        # `./scripts/urls.sh` runs standalone, and every other assertion about it goes
        # through `pixi run urls` or through the stub above — both of which would stay green
        # if the wrapper stopped printing anything at all. The interpreter is named rather
        # than inherited from PATH so the case says nothing about the developer's python3.
        env = fresh(request=None)
        env["DEVINFRA_PYTHON"] = sys.executable
        r = run_script("urls.sh", "postgres", env=env)
        expect("urls.sh standalone exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr!r}")
        expect(
            "urls.sh standalone prints the Selection's endpoints",
            "POSTGRES_PORT" in r.stdout and "GRAFANA_PORT" not in r.stdout,
            f"stdout: {r.stdout!r}",
        )

        # An interpreter that cannot import PyYAML, expressed as a shim ahead of it on
        # PYTHONPATH rather than by building an environment without the package.
        shim = stubs / "noyaml"
        shim.mkdir(exist_ok=True)
        (shim / "yaml.py").write_text(
            'raise ModuleNotFoundError("No module named yaml")\n', encoding="utf-8", newline="\n"
        )
        env = fresh()
        env["PYTHONPATH"] = str(shim)
        r = run_script("select.sh", "postgres", env=env)
        expect("select.sh refuses an interpreter without PyYAML", r.returncode != 0, "exited 0")
        expect("the refusal names PyYAML", "PyYAML" in r.stderr, f"stderr: {r.stderr!r}")
        expect("the refusal names the interpreter seam", "DEVINFRA_PYTHON" in r.stderr, f"stderr: {r.stderr!r}")
        expect("the refusal is a diagnostic, not a traceback", "Traceback" not in r.stderr, f"stderr: {r.stderr!r}")
        expect("a refused resolver prints nothing on stdout", not r.stdout, f"stdout: {r.stdout!r}")

        # --- Every lifecycle task reaches the runtime through the DEVINFRA_COMPOSE seam. ---
        # Five task bodies used to name `docker compose` directly. pixi.toml has no
        # shell expansion, so those five ignored DEVINFRA_COMPOSE entirely and a
        # contributor who set it started the stack under Docker regardless — the
        # setting appeared to work and did not. These cases run the tasks themselves,
        # so a body repointed back at a runtime fails here rather than at review.
        #
        # No task body names a profile either, for the same reason. Five of them used to
        # carry `--profile admin --profile observability`, which is a second, drifting
        # statement of what the stack contains and one a thirteenth Module would have
        # escaped. The flags are gone; the assertion surface is the Selection each task
        # exports, which is what Compose actually acts on.
        seam_tasks = {
            "start": (["up", "-d"], "postgres,redis"),
            "down": (["down"], all_modules),
            "stop": (["stop"], all_modules),
            "pull": (["pull"], all_modules),
            "config": (["config"], all_modules),
            "dump-logs": (
                [
                    "logs",
                    # Uncoloured because the destination is a CI log, and bounded because
                    # an unbounded dump of fourteen services buries the failure it exists
                    # to explain. `logs.sh` cannot be reused: it hard-codes -f and hangs.
                    "--no-color",
                    "--tail=200",
                ],
                all_modules,
            ),
        }
        # `start` depends on init, which would otherwise create a .env and leave it.
        with planted(REPO / ".env", (REPO / ".env.example").read_text(encoding="utf-8")):
            for task_name, (expected_argv, expected_selection) in seam_tasks.items():
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
                expect(
                    f"{task_name} acts on the {'all-Modules' if expected_selection == all_modules else 'ambient'} "
                    f"Selection",
                    recorded_env(record) == [f"COMPOSE_PROFILES={expected_selection}"],
                    f"observed {recorded_env(record)}; expected {expected_selection}",
                )

        # The `select` task, at the surface a developer reaches. Its argument list is one
        # `names` value, so several names travel comma-joined — the form the README
        # documents — and with no argument at all the empty value falls through to
        # COMPOSE_PROFILES, which is what makes a bare `pixi run select` print what the
        # current .env asks for. Nothing else executes that fallback.
        r = pixi("select", "keycloak", env=fresh())
        expect("the select task exits 0 for a Module name", r.returncode == 0, f"stderr: {r.stderr!r}")
        expect(
            "the select task prints the closure, not the request",
            "keycloak,mailpit,postgres" in r.stdout,
            f"stdout: {r.stdout!r}",
        )
        r = pixi("select", "postgres,redis", env=fresh())
        expect(
            "the select task takes several names comma-joined, as the README documents",
            r.returncode == 0 and "postgres,redis" in r.stdout,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )
        with planted(REPO / ".env", dotenv):
            r = pixi("select", env=fresh(request=None))
            expect(
                "the select task with no argument reads the Selection from .env",
                r.returncode == 0
                and "flower,grafana,loki,otel-collector,pgadmin,postgres,prometheus,redis,redisinsight,tempo"
                in r.stdout,
                f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
            )
        # …and with neither an argument nor a .env there is no Selection to print, which is
        # the refusal rather than an empty line at exit 0.
        r = pixi("select", env=fresh(request=None))
        expect("the select task refuses an empty Selection", r.returncode != 0, "exited 0")
        expect(
            "the select task names COMPOSE_PROFILES when it refuses",
            "COMPOSE_PROFILES" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # The seam's default, against the real runtime: only it can say what an
        # unset DEVINFRA_COMPOSE actually reaches. An exported-but-empty value must
        # read as unset too, exactly as ${DEVINFRA_COMPOSE:-docker compose} does —
        # taken as a value it splits to an empty argv and the first real argument
        # is executed as the command. Docker stays the default; the Podman job
        # changes where the API lives, not what this falls back to.
        for value in (None, ""):
            env = dict(os.environ)
            # compose.sh resolves a Selection before it reaches the runtime, and an empty
            # one is refused, so the request is stated rather than inherited.
            env["COMPOSE_PROFILES"] = "postgres"
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

        # --- lint-compose: one `config -q` per Selection this repository can name. ---
        # Every Module's own closure, every Bundle's closure, and every Module at once
        # (ADR 0013). The power set that used to stand here is gone: with a profile on
        # every service it is 2^17 renders, which would never finish.
        #
        # The four Bundles are written out rather than read from the registry: an
        # expectation taken from the file under test agrees with whatever that file
        # happens to say, including a Bundle quietly dropped from lint-compose's
        # enumeration. `lint-compose.sh` itself is unchanged by ADR 0014 — it enumerates
        # from `select.sh --selections`, so it picked the two new Bundles up by
        # construction, and this is what proves it did.
        #
        # The resolver needs no stub of its own — it parses the real
        # services/*/compose.yaml — so the Selections and the value each one exports are
        # deterministic and can be pinned exactly.
        two = "admin\ncore\nminimal\nobservability\n"
        groups = ["admin", "core", "minimal", "observability"]
        expected_requests = [*every_module, *groups, "--all"]
        # One `config --profiles` to read the declared groups, then one `config -q` per
        # Selection. No --profile flag anywhere: the Selection travels in the environment.
        every_selection = ["config", "--profiles"] + ["config", "-q"] * len(expected_requests)
        r = pixi("lint-compose", env=fresh(profiles=two))
        expect(
            "lint-compose exits 0 when every Selection validates",
            r.returncode == 0,
            f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
        )
        expect(
            "lint-compose validates one Selection per Module, per Bundle and one for every Module",
            recorded(record) == every_selection,
            f"recorded {recorded(record)}; expected {len(expected_requests)} Selections",
        )
        # The enumeration itself, proved load-bearing: a Selection list that dropped a
        # Module would fail here naming the Module, because the expectation is built from
        # the services/ directory listing rather than from the resolver's own answer.
        printed = {
            line.split("lint-compose: Selection ", 1)[1].split(" -> ", 1)[0]
            for line in r.stdout.splitlines()
            if line.startswith("lint-compose: Selection ")
        }
        expect(
            "lint-compose names every Module, every Bundle and the all-Modules request",
            printed == set(expected_requests),
            f"missing {sorted(set(expected_requests) - printed)}, unexpected {sorted(printed - set(expected_requests))}",
        )
        # …and the value each one exports is the resolved closure, never the raw request.
        # `COMPOSE_PROFILES=admin` reaching Compose would select the three admin services
        # without the Postgres and Redis they talk to, which is the failure AD-16 exists
        # to prevent.
        exported = recorded_env(record)
        expect(
            "lint-compose exports a resolved Selection for every render",
            exported[0] == "COMPOSE_PROFILES=" and len(exported) == len(expected_requests) + 1,
            f"observed {exported}",
        )
        expect(
            "lint-compose expands the admin Bundle to its closure, not to 'admin'",
            "COMPOSE_PROFILES=flower,pgadmin,postgres,redis,redisinsight" in exported,
            f"observed {exported}",
        )
        # …and the two Bundles ADR 0014 registers reach it the same way, through the
        # resolver's own enumeration. lint-compose.sh names no Bundle of its own, so a
        # registry entry no Module joins would surface here as a Selection that resolves
        # to nothing rather than as a name this file forgot to list.
        expect(
            "lint-compose validates the core Bundle's closure",
            "COMPOSE_PROFILES=keycloak,mailpit,minio,postgres,redis" in exported,
            f"observed {exported}",
        )
        expect(
            "lint-compose validates the minimal Bundle's closure",
            "COMPOSE_PROFILES=postgres,redis" in exported,
            f"observed {exported}",
        )
        expect(
            "lint-compose validates every Module at once as its last Selection",
            exported[-1] == f"COMPOSE_PROFILES={all_modules}",
            f"observed {exported[-1]!r}; expected every module: {all_modules}",
        )

        # A profile list that was read successfully and is empty is a failure in its own
        # right: with a Module profile on every service the model declares at least one
        # profile per Module, so an empty answer means the enumeration found nothing.
        # Reported as a pass over one combination is what this used to do (DW-32).
        r = pixi("lint-compose", env=fresh(profiles=""))
        expect("lint-compose fails when the model declares no profile at all", r.returncode != 0, "exited 0")
        expect(
            "lint-compose says the empty profile list cannot be right",
            "declares no profiles at all" in r.stderr,
            f"stderr: {r.stderr!r}",
        )
        expect(
            "lint-compose validated no Selection it could not trust the enumeration for",
            recorded(record) == ["config", "--profiles"],
            f"recorded {recorded(record)}",
        )

        # A failure in one Selection must not hide the others.
        r = pixi("lint-compose", env=fresh(profiles=two, exit_code="1"))
        listed = {line.strip() for line in r.stderr.splitlines() if line.startswith("  ")}
        expect("lint-compose fails when a Selection fails", r.returncode != 0, "exited 0")
        expect(
            "lint-compose names every failing Selection",
            listed == set(expected_requests),
            f"listed {listed}; stderr: {r.stderr!r}",
        )

        # An enumeration that could not be read must never be treated as "no
        # profiles": that would skip the group Selections and report success.
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
            # Every real service renders with the shared logging options, which it reads
            # from common/base.yaml through `extends` — the `x-logging` anchor that was the
            # other source is gone with the last inlined service. The stub document carries
            # them too; a service written here without them is stating that defect
            # deliberately.
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
        # Since story 2-4 a module directory is a Module, and lint-config refuses one that
        # does not carry the contract. This fixture exists on disk while lint-config runs
        # in a dozen cases below, one of which expects exit 0, so it is made
        # contract-complete rather than exempted by a `zz-*` special case in production
        # code — an exemption there would be a hole every real Module could fall through.
        # The bodies planted below all carry the healthcheck and the x-endpoints block the
        # contract's other two legs need.
        contract_siblings = {
            "smoke.sh": (
                "# Self-test fixture. Sourced by scripts/smoke-test.sh, never executed.\n"
                "#\n"
                "# shellcheck shell=bash\n"
                "\n"
                "true\n"
            ),
            "gotchas.md": "# zz-selftest-defect — gotchas\n\nA self-test fixture. It runs nothing and bites nobody.\n",
            "seed.none": (
                "# Why this Module ships no seed/ directory.\n\nA self-test fixture loads nothing at first boot.\n"
            ),
        }
        for sibling, sibling_body in contract_siblings.items():
            (defect_module / sibling).write_text(sibling_body, encoding="utf-8", newline="\n")
        contract_prefix = (
            "x-endpoints:\n"
            "  ZZ_SELFTEST_PORT:\n"
            "    url: http://localhost:1\n"
            "    description: a self-test fixture endpoint\n"
            "services:\n"
            "  zz-selftest-defect:\n"
            "    image: alpine:3.22\n"
            # Its own Module name, the leg ADR 0013 adds — these cases are about the
            # volume and network stanzas, and a fixture failing the profile leg instead
            # would say nothing about either.
            "    profiles: [zz-selftest-defect]\n"
            '    healthcheck:\n      test: ["CMD", "true"]\n'
            '    ports:\n      - "127.0.0.1:${ZZ_SELFTEST_PORT:-19990}:1"\n'
        )
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
                with planted(fixture, contract_prefix + body):
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

            # The Compose-v2 import rule, which is not AD-5's rule and fails on the
            # identifier-only form the cases above sanction: across `include`, a module may
            # name a top-level resource only when the root's declaration of it is bare. The
            # root declares `networks: devinfra:` with `name:` and `driver:`, so Compose
            # v2.29.7 refuses the whole model — `networks.devinfra conflicts with imported
            # resource`, exit 15 — while Compose v5.3.0 resolves it without complaint. That
            # split is why the check is static: `lint-compose` renders with whatever runtime
            # the developer has, so this defect passed every local gate and failed all three
            # CI jobs. See the 2026-09-07 amendment in docs/adr/0004.
            fixture = defect_module / "compose.yaml"
            with planted(fixture, contract_prefix + "networks:\n  devinfra:\n"):
                r = pixi("lint-config", env=fresh(document=clean_doc))
                expect(
                    "lint-config rejects a module redeclaring a network the root declares with keys",
                    r.returncode != 0,
                    "exited 0",
                )
                expect(
                    "lint-config names the conflicting module, stanza and root keys",
                    "services/zz-selftest-defect/compose.yaml" in r.stderr
                    and "networks.devinfra" in r.stderr
                    and "conflicts with imported resource" in r.stderr,
                    f"stderr: {r.stderr!r}",
                )
                # The advice must be "delete it", not "strip it to an identifier": the entry
                # above *is* an identifier and Compose still refuses it. A diagnostic that
                # reached for the AD-5 wording would send the next author round the loop.
                expect(
                    "lint-config does not misreport the conflict as an AD-5 extra-key defect",
                    "names the identifier and" not in r.stderr,
                    f"stderr: {r.stderr!r}",
                )

            # The other side of the same rule, and the reason it is stated against the
            # *root's* body rather than the module's: `volumes: postgres-data:` is bare in
            # the root, so a module may name it, and Compose v2 accepts that. A check that
            # banned every redeclaration would reject the tracked postgres module.
            with planted(fixture, contract_prefix + "volumes:\n  postgres-data:\n"):
                r = pixi("lint-config", env=fresh(document=clean_doc))
                expect(
                    "lint-config accepts a module redeclaring a volume the root declares bare",
                    r.returncode == 0,
                    f"output: {(r.stdout + r.stderr)!r}",
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

        # --- The Module contract (ADR 0012): one negative per leg, plus the ownership
        # rule and the three x-requires rules. A leg that is never removed is a leg the
        # check could quietly stop asserting — the tracked Modules all satisfy it, so the
        # clean run says nothing about whether the check is still there. Every case
        # asserts three things: a non-zero exit, a diagnostic naming the module *and* the
        # missing thing, and no `OK` on stdout, because stdout and stderr interleave by
        # buffering and a run that ended on a wall of OK lines would read as a pass.
        contract_module = REPO / "services" / "zz-selftest-contract"
        complete_siblings = {
            "smoke.sh": (
                "# Self-test fixture. Sourced by scripts/smoke-test.sh, never executed.\n"
                "#\n"
                "# shellcheck shell=bash\n"
                "\n"
                "true\n"
            ),
            "gotchas.md": "# zz-selftest-contract — gotchas\n\nA self-test fixture. It runs nothing.\n",
            "seed.none": (
                "# Why this Module ships no seed/ directory.\n\nA self-test fixture loads nothing at first boot.\n"
            ),
        }
        endpoints_block = (
            "x-endpoints:\n  ZZ_SELFTEST_PORT:\n    url: http://localhost:1\n    description: a fixture endpoint\n"
        )
        # Every service a Module owns declares its own Module name in `profiles:` — the
        # leg ADR 0013 adds — so the fixture bodies carry it too. Without it every case
        # below would fail for that reason as well, and the four sanctioned shapes would
        # stop being sanctioned.
        own_profile = "    profiles: [zz-selftest-contract]\n"
        primary_block = (
            "services:\n"
            "  zz-selftest-contract:\n"
            "    image: alpine:3.22\n" + own_profile + '    healthcheck:\n      test: ["CMD", "true"]\n'
            '    ports:\n      - "127.0.0.1:${ZZ_SELFTEST_PORT:-19991}:1"\n'
        )
        # The same service with the probe declared only to be cancelled, and with no probe
        # at all: `disable: true` and `test: NONE` are Compose's own off switches, and both
        # are truthy mappings a presence check would accept.
        no_probe_block = (
            "services:\n"
            "  zz-selftest-contract:\n"
            "    image: alpine:3.22\n" + own_profile + '    ports:\n      - "127.0.0.1:${ZZ_SELFTEST_PORT:-19991}:1"\n'
        )
        disabled_probe_block = no_probe_block + "    healthcheck:\n      disable: true\n"
        cancelled_probe_block = no_probe_block + '    healthcheck:\n      test: ["NONE"]\n'
        # A probe, but nothing published: the endpoint above then describes a port that no
        # longer exists, which is the same drift one file later.
        probe_no_ports_block = (
            "services:\n"
            "  zz-selftest-contract:\n"
            "    image: alpine:3.22\n" + own_profile + '    healthcheck:\n      test: ["CMD", "true"]\n'
        )
        complete_body = endpoints_block + primary_block

        @contextlib.contextmanager
        def contract_fixture(body: str, siblings: dict[str, str]) -> Iterator[None]:
            # Reused and torn down whole, exactly as defect_module is, so a killed run
            # neither aborts the next one nor needs a hand cleanup.
            contract_module.mkdir(exist_ok=True)
            try:
                for name, text in siblings.items():
                    (contract_module / name).write_text(text, encoding="utf-8", newline="\n")
                (contract_module / "compose.yaml").write_text(body, encoding="utf-8", newline="\n")
                yield
            finally:
                shutil.rmtree(contract_module, ignore_errors=True)

        def without(*names: str) -> dict[str, str]:
            return {name: text for name, text in complete_siblings.items() if name not in names}

        # A comment-only marker is the silent skip in file form: it satisfies a
        # presence check while stating nothing. Both marker legs are pinned against it.
        empty_marker = "# a heading and nothing else\n"

        contract_cases: list[tuple[str, str, dict[str, str], list[str]]] = [
            ("a missing smoke.sh", complete_body, without("smoke.sh"), ["zz-selftest-contract", "smoke.sh"]),
            ("a missing gotchas.md", complete_body, without("gotchas.md"), ["zz-selftest-contract", "gotchas.md"]),
            (
                "neither seed/ nor seed.none",
                complete_body,
                without("seed.none"),
                ["zz-selftest-contract", "seed/", "seed.none"],
            ),
            (
                "a seed.none with no justification",
                complete_body,
                {**complete_siblings, "seed.none": empty_marker},
                ["zz-selftest-contract", "seed.none"],
            ),
            (
                "no healthcheck and no healthcheck.none",
                endpoints_block + no_probe_block,
                complete_siblings,
                ["zz-selftest-contract", "healthcheck"],
            ),
            (
                "a healthcheck.none with no justification",
                endpoints_block + no_probe_block,
                {**complete_siblings, "healthcheck.none": empty_marker},
                ["zz-selftest-contract", "healthcheck.none"],
            ),
            (
                "no x-endpoints block",
                primary_block,
                complete_siblings,
                ["zz-selftest-contract", "x-endpoints"],
            ),
            (
                "an empty x-endpoints block",
                "x-endpoints: {}\n" + primary_block,
                complete_siblings,
                ["zz-selftest-contract", "x-endpoints"],
            ),
            # The drift urls.sh had accumulated before ADR 0017 generated it, stated where
            # it can be checked: a port the module publishes but no endpoint names.
            (
                "a published port no x-endpoints entry names",
                endpoints_block + primary_block + '      - "127.0.0.1:${ZZ_OTHER_PORT:-19992}:2"\n',
                complete_siblings,
                ["zz-selftest-contract", "ZZ_OTHER_PORT"],
            ),
            # Compose's own off switch for a probe. `healthcheck: {disable: true}` is a
            # truthy mapping, so a leg that only asked whether the key was present would
            # accept a Module shipping no probe and no marker either.
            (
                "a healthcheck declared only to be disabled",
                endpoints_block + disabled_probe_block,
                complete_siblings,
                ["zz-selftest-contract", "healthcheck"],
            ),
            # The other off switch, and the one the `disable: true` case never reaches:
            # healthcheck_declared() returns at `disable` before it ever reads `test:`, so
            # deleting both NONE branches leaves every case above green while a Module
            # shipping Compose's documented probe-cancellation passes the leg.
            (
                "a healthcheck whose test cancels the probe",
                endpoints_block + cancelled_probe_block,
                complete_siblings,
                ["zz-selftest-contract", "healthcheck"],
            ),
            # The reverse direction of the endpoint rule. Without it, deleting a ports:
            # line leaves the endpoint declared forever and the block starts lying in
            # exactly the way scripts/urls.sh did before ADR 0017 generated it.
            (
                "an x-endpoints entry naming a port the Module does not publish",
                endpoints_block + probe_no_ports_block,
                complete_siblings,
                ["zz-selftest-contract", "ZZ_SELFTEST_PORT"],
            ),
            # A literal host port names no variable, so no x-endpoints entry can ever
            # reconcile against it — it would otherwise slip the whole rule by contributing
            # nothing to either side of the comparison.
            (
                "a published port that interpolates no variable",
                endpoints_block + primary_block + '      - "127.0.0.1:15432:5432"\n',
                complete_siblings,
                ["zz-selftest-contract", "127.0.0.1:15432:5432"],
            ),
            # The reverse direction. The root compose.yaml declares no services: key, so
            # a module file is the only place a service can come from — which makes this
            # the rule that stops a Compose service no Module directory owns existing.
            (
                "a service the module does not own",
                complete_body + "  grafana:\n    image: alpine:3.22\n",
                complete_siblings,
                ["zz-selftest-contract", "grafana"],
            ),
            (
                "a module file with no primary service",
                endpoints_block + "services:\n  zz-selftest-contract-worker:\n    image: alpine:3.22\n",
                complete_siblings,
                ["zz-selftest-contract", "primary"],
            ),
            (
                "an x-requires naming a module that does not exist",
                complete_body + "x-requires:\n  zz-nosuch:\n    - ZZ_SELFTEST_PORT\n",
                complete_siblings,
                ["zz-selftest-contract", "zz-nosuch"],
            ),
            (
                "an x-requires naming an endpoint the provider does not publish",
                complete_body.replace(
                    '      test: ["CMD", "true"]\n',
                    '      test: ["CMD", "true"]\n    depends_on:\n      - postgres\n',
                )
                + "x-requires:\n  postgres:\n    - ZZ_NOSUCH_PORT\n",
                complete_siblings,
                ["zz-selftest-contract", "postgres", "ZZ_NOSUCH_PORT"],
            ),
            # ADR 0002: a dependency not expressed as depends_on does not exist, so a
            # requirement without the matching edge is a declaration free to drift.
            (
                "an x-requires with no matching depends_on",
                complete_body + "x-requires:\n  postgres:\n    - POSTGRES_PORT\n",
                complete_siblings,
                ["zz-selftest-contract", "postgres", "depends_on"],
            ),
            # ADR 0013's leg. A service with no `profiles:` key joins *every* Selection:
            # it starts whatever was asked for and no Selection can leave it out, while
            # `config -q` passes and every other check here stays green. This is the
            # state the whole stack was in before Selection existed.
            (
                "a service with no profiles: key of its own",
                complete_body.replace(own_profile, ""),
                complete_siblings,
                ["zz-selftest-contract", "profiles"],
            ),
            # …and one carrying a profile that is not its Module's. `select.sh <module>`
            # would then resolve to a Module whose service the request does not select.
            (
                "a service whose profiles: omits its own Module name",
                complete_body.replace(own_profile, "    profiles: [admin]\n"),
                complete_siblings,
                ["zz-selftest-contract", "admin"],
            ),
            # The reverse direction, and the one that breaks the headline property while
            # every gate stays green. A service carrying *another* Module's name joins that
            # Module's Selection: `select.sh postgres` would then emit two Modules, so
            # "two names, two containers" stops holding and one Module has reached into
            # another Module's Selection (AD-15).
            (
                "a service carrying another Module's name in profiles:",
                complete_body.replace(own_profile, "    profiles: [zz-selftest-contract, postgres]\n"),
                complete_siblings,
                ["zz-selftest-contract", "postgres"],
            ),
            # ADR 0014's leg, and the gap story 2-5 left open. A profile that is neither
            # this Module's own name nor a name the root registry registers passed every
            # check before the registry existed: `select.sh` accepts it, because its
            # vocabulary *is* this profile index, so a typo silently became a request name
            # resolving to whatever happened to carry it, with nothing describing it and no
            # footprint anywhere. Note it is *not* another Module's name, so the AD-15 leg
            # above cannot catch it — this needs the registry to be checkable at all.
            (
                "a profile no x-bundles entry registers",
                complete_body.replace(own_profile, "    profiles: [zz-selftest-contract, zz-nosuch]\n"),
                complete_siblings,
                ["zz-selftest-contract", "zz-nosuch"],
            ),
            # …and the closure half of the same decision. A registered Bundle is
            # dependency-closed *by declaration*: the Modules that join it are closed under
            # depends_on, so handing the raw name to Compose already selects everything it
            # needs. This fixture joins `minimal` and depends on a Module that has not, which
            # is exactly the shape `admin` was in before this story — working only because
            # `select.sh` expanded it at runtime, and broken the moment anything did not.
            (
                "a Module that breaks a Bundle open by depending outside it",
                complete_body.replace(own_profile, "    profiles: [zz-selftest-contract, minimal]\n").replace(
                    '      test: ["CMD", "true"]\n',
                    '      test: ["CMD", "true"]\n    depends_on:\n      - mailpit\n',
                ),
                complete_siblings,
                ["minimal", "zz-selftest-contract", "mailpit"],
            ),
            # The helper half. `minio-init` takes `[minio]`, identical to its primary:
            # a helper selected by a different set either starts without the service it
            # exists to serve, or is left behind when that service is selected. The
            # helper here carries its Module name, so only the equality leg can catch it.
            (
                "a helper whose profile set differs from its primary's",
                complete_body
                + "  zz-selftest-contract-init:\n    image: alpine:3.22\n"
                + "    profiles: [zz-selftest-contract, admin]\n",
                complete_siblings,
                ["zz-selftest-contract", "zz-selftest-contract-init"],
            ),
        ]
        for case, body, siblings, needles in contract_cases:
            with contract_fixture(body, siblings):
                r = pixi("lint-config", env=fresh(document=clean_doc))
                expect(f"lint-config rejects {case}", r.returncode != 0, "exited 0")
                unsaid = [needle for needle in needles if needle not in r.stderr]
                expect(
                    f"lint-config names the module and the defect for {case}",
                    not unsaid,
                    f"never said {unsaid}; stderr: {r.stderr!r}",
                )
                expect(
                    f"lint-config signs off on nothing for {case}",
                    "OK" not in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

        # …and the shapes the contract sanctions stay sanctioned. A one-shot helper named
        # `<dir>-<role>` is legitimate — minio-init is the tracked one — and the
        # healthcheck leg is asserted on the primary only, because a helper that exits 0
        # has nothing to keep healthy. A rule that rejected either would reject the stack.
        with contract_fixture(
            complete_body
            + "  zz-selftest-contract-init:\n    image: alpine:3.22\n"
            + own_profile
            + '    restart: "no"\n',
            complete_siblings,
        ):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect(
                "lint-config accepts a helper service named <module>-<role> with no healthcheck",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # …and joining a Bundle the registry *does* register is accepted, which is the
        # branch every negative above shares and none of them proves. A vocabulary check
        # inverted to reject every non-Module profile would red every case above for the
        # right-looking reason while making the four shipped Bundles undeclarable.
        with contract_fixture(
            complete_body.replace(own_profile, "    profiles: [zz-selftest-contract, observability]\n"),
            complete_siblings,
        ):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect(
                "lint-config accepts a Module joining a registered Bundle",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # --- The Bundle registry itself, mutated in the root compose.yaml (ADR 0014). ---
        # Staged with moved_aside + planted rather than by editing in place: the real file
        # is renamed, the mutation is written at its path, and the original comes back
        # however the case ends. planted() refuses a path that already exists, so the pair
        # cannot silently overwrite tracked content if the rename ever failed.
        #
        # The mutations are built by round-tripping the real model through YAML rather than
        # by string surgery, so a case cannot accidentally take `volumes:` or `include:`
        # with it and red for a reason that has nothing to do with the registry.
        root_source = REPO / "compose.yaml"
        root_text = root_source.read_text(encoding="utf-8")
        root_parsed = yaml.safe_load(root_text)

        def root_with(registry: Any) -> str:
            mutated = dict(root_parsed)
            if registry is None:
                mutated.pop("x-bundles", None)
            else:
                mutated["x-bundles"] = registry
            return str(yaml.safe_dump(mutated, sort_keys=False, default_flow_style=False))

        # `.get`, not indexing: an absent or malformed registry is already a named failure
        # from the positive assertions near the top of this run, and a KeyError here would
        # abort the whole self-test before it reported that failure — losing the diagnostic
        # behind a traceback about the fixture that was trying to describe it.
        shipped = dict(root_parsed.get("x-bundles") or {})
        minimal_entry = shipped.get("minimal")
        shipped_minimal: dict[str, Any] = minimal_entry if isinstance(minimal_entry, dict) else {}
        unjoined = {**shipped, "zz-unjoined": {"description": "nothing joins this", "memory": "~1 MB"}}
        no_memory = {**shipped, "minimal": {"description": shipped_minimal.get("description", "the data layer")}}
        extra_key = {
            **shipped,
            "minimal": {**shipped_minimal, "modules": ["postgres", "redis"]},
        }

        registry_cases: list[tuple[str, Any, list[str]]] = [
            # A name a developer can ask for that resolves to nothing. This is the
            # enumeration proved load-bearing: a membership check that named no Bundle
            # would pass over an empty set and say so in an OK line.
            ("a registered Bundle no Module joins", unjoined, ["zz-unjoined"]),
            # The footprint is NFR-7's requirement, and it lives here rather than only in
            # the README precisely so CI can insist on it.
            ("a registry entry with no memory footprint", no_memory, ["minimal", "memory"]),
            # A members list is the drift AD-7 exists to prevent: a second, hand-maintained
            # answer to what starts, free to disagree with the profiles: that decide it.
            ("a registry entry carrying a members list", extra_key, ["minimal", "modules"]),
            # …and the two malformed shapes. Both must be read as "this registry is
            # unreadable", never as "there are no Bundles": root_declarations() coerces a
            # non-mapping to {} and would do exactly that, which is why the registry has a
            # reader of its own.
            ("a registry that is a list", ["minimal", "core"], ["x-bundles"]),
            # …and the third unreadable shape, which is the one that looks most like an
            # answer: a well-formed mapping holding nothing. Read as "there are no
            # Bundles" it would pass the membership check over an empty set and sign off.
            ("an empty registry", {}, ["x-bundles"]),
            ("no registry at all", None, ["x-bundles"]),
        ]
        for case, registry, needles in registry_cases:
            with moved_aside([root_source]):
                with planted(root_source, root_with(registry)):
                    r = pixi("lint-config", env=fresh(document=clean_doc))
                    expect(f"lint-config rejects {case}", r.returncode != 0, "exited 0")
                    unsaid = [needle for needle in needles if needle not in r.stderr]
                    expect(
                        f"lint-config names the registry defect for {case}",
                        not unsaid,
                        f"never said {unsaid}; stderr: {r.stderr!r}",
                    )
                    expect(
                        f"lint-config signs off on nothing for {case}",
                        "OK" not in r.stdout,
                        f"stdout: {r.stdout!r}",
                    )
        expect(
            "the root compose.yaml is restored byte-for-byte after the registry cases",
            root_source.read_text(encoding="utf-8") == root_text,
            "the real root file did not come back unchanged",
        )

        # …and the brace-less interpolation Compose accepts just as readily. A pattern that
        # only matched `${NAME}` would let `$NAME` publish a port that no endpoint had to
        # name, which is the reconciliation slipped entirely rather than failed.
        with contract_fixture(
            endpoints_block + primary_block.replace("${ZZ_SELFTEST_PORT:-19991}", "$ZZ_SELFTEST_PORT"),
            complete_siblings,
        ):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect(
                "lint-config reconciles a port written $NAME as well as ${NAME}",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # The depends_on edge may be held by any service the Module owns, and may name the
        # provider's own helper: minio-init is the tracked shape of both. Demanding the edge
        # on the primary, naming the provider's primary, would reject a genuine dependency.
        with contract_fixture(
            endpoints_block
            + primary_block
            + "  zz-selftest-contract-init:\n    image: alpine:3.22\n"
            + own_profile
            + "    depends_on:\n      - minio-init\n"
            + "x-requires:\n  minio:\n    - MINIO_API_PORT\n",
            complete_siblings,
        ):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect(
                "lint-config accepts an x-requires edge held by a helper and naming a helper",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # …and the marker legs, satisfied by a justified marker rather than by the thing
        # itself. Without these the two `justified()` branches could both be inverted and
        # only the negatives above would notice.
        with contract_fixture(
            endpoints_block + no_probe_block,
            {**complete_siblings, "healthcheck.none": "# why\n\nthe image is distroless, so there is nothing to run\n"},
        ):
            r = pixi("lint-config", env=fresh(document=clean_doc))
            expect(
                "lint-config accepts a justified healthcheck.none in place of a healthcheck",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )

        # The contract line has to name the number it walked, for the same reason the
        # identifier-only line does: "OK 0 module file(s) carry the Module contract" is a
        # sentence a check that walked nothing can also write.
        tracked_modules = sorted((REPO / "services").glob("*/compose.yaml"))
        r = pixi("lint-config", env=fresh(document=clean_doc))
        expect(
            "lint-config reports the Module contract over every tracked module file",
            f"OK {len(tracked_modules)} module file(s) carry the Module contract" in r.stdout,
            f"{len(tracked_modules)} module files; stdout: {r.stdout!r}",
        )
        # The registry line names its number for the same reason. "OK 0 registered
        # Bundle(s)" is a sentence a check that iterated nothing writes just as happily,
        # and it is the sentence a malformed registry read as `{}` would have produced.
        expect(
            "lint-config reports every registered Bundle by name and footprint",
            f"OK {len(registry_entries)} registered Bundle(s)" in r.stdout
            # Read from registry_footprints, which already skipped any entry carrying no
            # string `memory:` — that entry is a named failure of its own further up, and
            # indexing it here would abort the run rather than let that failure be reported.
            and all(f"{name} ({footprint})" in r.stdout for name, footprint in registry_footprints.items()),
            f"{len(registry_entries)} Bundles registered; stdout: {r.stdout!r}",
        )

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

        # Bind sources, the property with no other net. A module writes them against its
        # own directory, and a mistyped one never fails: Docker creates the missing path
        # as an empty *directory* and starts the container, so pgAdmin comes up with no
        # server registration while `/misc/ping` still answers 200 and the smoke suite
        # passes. Three cases, because existence alone is not the rule — the empty
        # directory Docker leaves behind would satisfy an `exists()` check forever after
        # the first `up`, which is exactly when the defect is already live.
        def bound(source: Path, target: str = "/pgadmin4/servers.json") -> str:
            return rendered(
                {
                    "pgadmin": {
                        "image": "dpage/pgadmin4:9.8",
                        "ports": [],
                        "volumes": [{"type": "bind", "source": str(source), "target": target, "read_only": True}],
                    }
                }
            )

        r = pixi("lint-config", env=fresh(document=bound(REPO / "services" / "pgadmin" / "conf" / "servers.json")))
        expect(
            "lint-config accepts a bind source that is a real file",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )

        no_source = REPO / "services" / "pgadmin" / "conf" / "zz_selftest_absent.json"
        r = pixi("lint-config", env=fresh(document=bound(no_source)))
        expect("lint-config rejects a bind source that does not exist", r.returncode != 0, "exited 0")
        expect(
            "lint-config names the service, the missing source and what Docker would do",
            "pgadmin" in r.stderr and no_source.name in r.stderr and "empty directory" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

        # The state Docker leaves after starting once with the mistyped source above.
        # Planted rather than described, so the check is proved against the real
        # filesystem it reads. Reused and removed whole, the way defect_module is,
        # so a killed run neither aborts the next one nor needs a hand cleanup.
        materialised = REPO / "services" / "pgadmin" / "conf" / "zz_selftest_materialised"
        materialised.mkdir(exist_ok=True)
        try:
            r = pixi("lint-config", env=fresh(document=bound(materialised)))
            expect(
                "lint-config rejects a bind source Docker already materialised as an empty directory",
                r.returncode != 0,
                "exited 0 — an existence-only check passes here, and this is the state after the first `up`",
            )
            expect(
                "lint-config names the empty directory and the .gitkeep way out",
                "pgadmin" in r.stderr and materialised.name in r.stderr and ".gitkeep" in r.stderr,
                f"stderr: {r.stderr!r}",
            )
        finally:
            shutil.rmtree(materialised, ignore_errors=True)

        # ...and the directory sources that are legitimate stay legitimate. A directory
        # holding nothing but .gitkeep is the case the rule above has to not break, and it
        # is the way out the diagnostic itself recommends. Planted rather than pointed at a
        # tracked directory: services/grafana/dashboards/ used to be the example and now
        # ships a provisioned dashboard, so a case named for .gitkeep would have quietly
        # stopped testing the thing it says. Removed whole in a finally, as `materialised` is.
        gitkeep_only = REPO / "services" / "pgadmin" / "conf" / "zz_selftest_gitkeep_only"
        gitkeep_only.mkdir(exist_ok=True)
        try:
            (gitkeep_only / ".gitkeep").write_text("", encoding="utf-8", newline="\n")
            r = pixi("lint-config", env=fresh(document=bound(gitkeep_only, "/dashboards")))
            expect(
                "lint-config accepts a directory source that carries only .gitkeep",
                r.returncode == 0,
                f"output: {(r.stdout + r.stderr)!r}",
            )
        finally:
            shutil.rmtree(gitkeep_only, ignore_errors=True)

        # The shipped drop-zone is no longer near-empty, and that is the point of the
        # story: it carries the provisioned dashboard. Asserted separately from the rule
        # above so the two cannot be confused for one another again.
        r = pixi("lint-config", env=fresh(document=bound(REPO / "services" / "grafana" / "dashboards", "/dashboards")))
        expect(
            "lint-config accepts the populated dashboards drop-zone",
            r.returncode == 0,
            f"output: {(r.stdout + r.stderr)!r}",
        )

        env = fresh(profiles=two, document=clean_doc)
        env["COMPOSE_PROFILES"] = "admin,observability"
        r = pixi("lint-config", env=env)
        expect(
            "lint-config renders one document per Selection, not per profile subset",
            recorded(record).count("--format") == len(every_module) + len(groups) + 1,
            f"recorded {recorded(record).count('--format')} renders; expected "
            f"{len(every_module)} Modules + {len(groups)} groups + the all-Modules Selection",
        )
        expect(
            "lint-config clears COMPOSE_PROFILES so each Selection is exactly its flags",
            set(recorded_env(record)) == {"COMPOSE_PROFILES="},
            f"observed {recorded_env(record)}",
        )
        # The enumeration proved load-bearing, the same way lint-compose's is: the
        # expectation is built from the services/ directory listing, so a Selection list
        # that dropped a Module fails here naming the Module rather than passing quietly
        # over a shorter list. Every Module must appear as its own --profile flag at
        # least once, because every Module is its own Selection.
        flagged = {
            argument
            for previous, argument in zip(recorded(record), recorded(record)[1:], strict=False)
            if previous == "--profile"
        }
        expect(
            "lint-config renders a Selection for every Module in the catalog",
            set(every_module) <= flagged,
            f"never rendered {sorted(set(every_module) - flagged)}",
        )

        # An enumeration that could not be read must never be treated as "no
        # profiles": that would leave the model's own profile list unreconciled against
        # the resolver's, and report success.
        env = fresh(profiles=two, document=clean_doc)
        env["STUB_PROFILES_EXIT"] = "1"
        r = pixi("lint-config", env=env)
        expect("lint-config fails when the profiles cannot be enumerated", r.returncode != 0, "exited 0")

        # …and a profile the model declares that the resolver cannot name is a Selection
        # nothing would ever validate. Without this the reconciliation could be deleted
        # and every case here would still pass.
        env = fresh(profiles=two + "zz-unresolvable-profile\n", document=clean_doc)
        r = pixi("lint-config", env=env)
        expect("lint-config rejects a declared profile the resolver cannot name", r.returncode != 0, "exited 0")
        expect(
            "lint-config names the profile no Selection covers",
            "zz-unresolvable-profile" in r.stderr,
            f"stderr: {r.stderr!r}",
        )

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
        # root and the root's own pins missing from the module, and go red on a correct
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

        # --- The carve: the driver enumerates, the Modules own the checks. ---
        # `lint-config` already refuses a Module with no smoke.sh, but only over the
        # module files it finds; this pins the other direction, that the set of scripts
        # the driver's glob will walk is exactly the set of Modules. A smoke.sh under a
        # directory with no compose.yaml would be a check nothing owns.
        smoke_owners = sorted(path.parent.name for path in (REPO / "services").glob("*/smoke.sh"))
        module_owners = sorted(path.parent.name for path in (REPO / "services").glob("*/compose.yaml"))
        expect("there are Modules to enumerate", bool(module_owners), "found no services/*/compose.yaml")
        expect(
            "the driver's glob reaches exactly one smoke.sh per Module",
            smoke_owners == module_owners,
            f"smoke.sh under {smoke_owners}; modules {module_owners}",
        )

        # The contract leg behind smoke.sh is presence-only — `MODULE_FILES` asks `is_file()`
        # and nothing more — so a Module's verification is deletable from inside that Module
        # with every gate still green: lint-config sees the file, lint-shell parses it, and
        # the driver sources a body of comments that contributes no pass, no fail and no skip.
        # That is the silent skip in file form, which is exactly what seed.none and
        # healthcheck.none are refused for. Asserted over every Module, not only the
        # healthcheck-exempt ones: an exempt Module has more riding on its script (the marker
        # moved its readiness gate there) but a Module with a probe still owns the only
        # verification of what it actually does. Stated generically over the counting helpers
        # rather than as `assert_ready`, because the collector's compensation is an
        # `assert_contains` round-trip, not a readiness poll. No non-empty guard on the
        # exempt set: zero healthcheck.none markers is a good outcome, not a regression.
        counted = re.compile(r"^\s*(assert_contains|assert_ready|check_http|pass|fail)\b", re.MULTILINE)
        silent = [
            name
            for name in module_owners
            if not counted.search((REPO / "services" / name / "smoke.sh").read_text(encoding="utf-8"))
        ]
        expect(
            "every Module asserts something in its own smoke.sh",
            not silent,
            f"{silent} carry a smoke.sh with no counted assertion, so nothing they do is verified",
        )

        # Core must gain no list of Modules. The whole point of the carve is that a new
        # Module enters the suite by existing, not by being named here — a hard-coded name
        # would put the catalog back in Core one entry at a time, and the first one to
        # arrive would look harmless. Case-insensitive, because prose in a comment is
        # exactly how such a name gets in: mentioning one in the driver's own header is
        # the same coupling as branching on it.
        driver_text = (REPO / "scripts" / "smoke-test.sh").read_text(encoding="utf-8")
        named_modules = [name for name in module_owners if re.search(rf"(?i)\b{re.escape(name)}\b", driver_text)]
        expect(
            "the smoke driver names no Module",
            not named_modules,
            f"scripts/smoke-test.sh names {named_modules}",
        )

        # --- smoke-test: FR-5 by default, FR-16 under SMOKE_STRICT. ---
        # The stub answers `ps` with nothing, so no service is running. .env is
        # planted because the suite interpolates ports at shell level.
        with planted(REPO / ".env", (REPO / ".env.example").read_text(encoding="utf-8")):
            r = run_script("smoke-test.sh", env=fresh())
            expect("smoke-test exits 0 when a service is absent", r.returncode == 0, f"exit {r.returncode}")
            expect("smoke-test reports SKIP by default", "SKIP" in r.stdout, f"stdout: {r.stdout!r}")
            expect("smoke-test skips the absent keycloak", "keycloak not running" in r.stdout, f"stdout: {r.stdout!r}")
            # One skip per Module and no more: a carve that dropped a Module's script, or
            # a driver that stopped enumerating, would still satisfy the two assertions
            # above while reporting a suite that checked less than it says.
            # Counted per Module rather than as a global SKIP total: a Module's own script
            # can emit skips of its own — grafana's and the collector's both do — so the
            # total stops being one-per-Module the moment any Module is selected.
            per_module = {name: r.stdout.count(f"{name} not running") for name in module_owners}
            expect(
                "smoke-test skips every Module by name when nothing is running",
                all(count == 1 for count in per_module.values()),
                f"per-Module skip lines {per_module}; stdout: {r.stdout!r}",
            )

            # A partial Selection, which is the case the whole skip/pass split exists for
            # and the one neither the all-absent nor the full-stack run reaches. The stub
            # answers `ps` with two names, so exactly two Modules are running. What is
            # asserted is the split: the selected Modules are attempted and the rest are
            # skipped by name. Whether their checks then pass is a property of a real
            # runtime — against a stub every one of them fails, which is itself the proof
            # that the driver sourced the two scripts instead of skipping them.
            running_modules = ["postgres", "redis"]
            unselected = [name for name in module_owners if name not in running_modules]
            r = run_script("smoke-test.sh", env=fresh(stdout="\n".join(running_modules) + "\n"))
            expect(
                "a partial Selection skips exactly the Modules that are not running",
                all(r.stdout.count(f"{name} not running") == 1 for name in unselected),
                f"per-Module skip lines "
                f"{ {name: r.stdout.count(f'{name} not running') for name in unselected} }; "
                f"stdout: {r.stdout!r}",
            )
            expect(
                "a partial Selection skips neither of the Modules that are running",
                not any(f"{name} not running" in r.stdout for name in running_modules),
                f"stdout: {r.stdout!r}",
            )
            expect(
                "a partial Selection runs the selected Modules' own checks",
                (r.stdout.count("PASS") + r.stdout.count("FAIL")) > 0,
                f"the two sourced scripts reported no check at all; stdout: {r.stdout!r}",
            )

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

            # The driver's own empty-set guard. Without a case, deleting it leaves
            # `pixi run ci` green while `pixi run smoke` reports success having walked no
            # Modules at all — the silent skip the guard exists to be.
            smoke_scripts = sorted((REPO / "services").glob("*/smoke.sh"))
            expect("there are module smoke scripts to hide", bool(smoke_scripts), "found no services/*/smoke.sh")
            with moved_aside(smoke_scripts):
                r = pixi("smoke", env=fresh())
                expect("smoke-test refuses an empty Module set", r.returncode != 0, "a suite over zero Modules")
                expect(
                    "smoke-test names the empty Module set",
                    "services/*/smoke.sh" in r.stderr,
                    f"stderr: {r.stderr!r}",
                )

            # A module script that does not parse must be a failure naming the Module, not a
            # Module that quietly contributes nothing. `source` returns non-zero and the
            # loop carries on, so without the guard the suite exits 0 having skipped a
            # Module's checks without once saying so. Planted beside a real Module's script
            # rather than as a new directory, because a new directory would fail
            # lint-config's contract check for unrelated reasons — and the stub reports
            # every Module as running, so this one is reached.
            # Moved aside and re-planted rather than overwritten in place: the good body then
            # survives on disk under the .moved name, where an in-place write holds it only in
            # this process's memory and a killed run leaves the tracked file corrupted. It is
            # also what every neighbouring fixture does, and `planted` refuses to overwrite.
            broken = REPO / "services" / "redisinsight" / "smoke.sh"
            good_body = broken.read_text(encoding="utf-8")
            with moved_aside([broken]), planted(broken, good_body + "\nif then fi\n"):
                env = fresh(stdout="redisinsight\n")
                r = run_script("smoke-test.sh", env=env)
                expect("smoke-test fails on a module script that cannot be sourced", r.returncode != 0, "exited 0")
                expect(
                    "smoke-test names the Module whose checks did not run",
                    "redisinsight smoke checks could not be sourced" in r.stdout,
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
            # `python3` is the DEVINFRA_PYTHON default, and it is in the list for the same
            # reason the other three are: a deferred check written in Python fails a dozen
            # assertions for one reason nothing reports when the interpreter is absent.
            # Named here so dropping it from REQUIRED_TOOLS cannot pass unnoticed.
            for needed in ("curl", "openssl", "base64", "python3"):
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

            # …and a runtime that answers `version` but cannot load the project. `ps`
            # ignores active profiles, but it still has to resolve the model, so an
            # unresolved Selection — COMPOSE_PROFILES naming a Module without its
            # dependencies — fails there and nowhere else. Discarding that status read as
            # "nothing is running": every Module skipped, and the default suite exited 0
            # having verified nothing. The three preflights above cannot see it, because
            # each of them passes.
            env = fresh()
            env["STUB_PS_EXIT"] = "1"
            r = run_script("smoke-test.sh", env=env)
            expect(
                "smoke-test fails when the compose project does not load",
                r.returncode != 0,
                "exited 0 having skipped every Module — the oracle read a broken project as an empty stack",
            )
            expect(
                "smoke-test says the project did not load and names the variable",
                "did not load" in r.stderr and "COMPOSE_PROFILES" in r.stderr,
                f"stderr: {r.stderr!r}",
            )
            expect(
                "smoke-test reports no clean pass when the project does not load",
                "PASS" not in r.stdout and "0 failed" not in r.stdout,
                f"stdout: {r.stdout!r}",
            )
            # The diagnostic is the whole value of the branch, so it is read rather than
            # merely searched for a substring: `${V:+'$V'}${V:-unset}` fires both arms for
            # a set value and printed it twice, with literal backslashes, while a
            # `"COMPOSE_PROFILES" in stderr` assertion stayed green.
            shown = [line for line in r.stderr.splitlines() if line.strip().startswith("COMPOSE_PROFILES is ")]
            expect(
                "smoke-test prints one COMPOSE_PROFILES line when the project does not load",
                len(shown) == 1,
                f"stderr lines: {shown!r}",
            )
            expect(
                "smoke-test shows the Selection once, unmangled",
                bool(shown) and shown[0].strip() == "COMPOSE_PROFILES is 'postgres,redis'.",
                f"line: {shown[0]!r}" if shown else "no line",
            )

            # The strict TASK, not just the script with the variable injected by
            # hand: deleting `env = { SMOKE_STRICT = "1" }` from pixi.toml would
            # otherwise leave CI silently running the skip-tolerant suite.
            r = pixi("smoke-strict", env=fresh())
            expect("the smoke-strict task fails on an absent service", r.returncode != 0, "exited 0")
            expect("the smoke-strict task prints no SKIP line", "SKIP" not in r.stdout, f"stdout: {r.stdout!r}")

            # --- The `defer` seam: a check one Module registers, run after all of them. ---
            # A check whose subject is a side effect another Module's checks produce cannot
            # run where it is written, because the Modules are enumerated in glob order and
            # nothing may reorder them (ADR 0015). Planted over a real Module's script the
            # way the unparseable-body case is, so no new directory appears for lint-config
            # to reject for unrelated reasons.
            donor = REPO / "services" / "postgres" / "smoke.sh"
            donor_body = donor.read_text(encoding="utf-8")
            expect("there is a Module script to plant over", bool(donor_body), f"{donor} is empty")
            deferring_a_pass = (
                "# shellcheck shell=bash\n"
                "zz_selftest_deferred() { pass 'zz-deferred-ran'; }\n"
                "defer zz_selftest_deferred\n"
                "pass 'zz-inline-ran'\n"
            )
            with moved_aside([donor]), planted(donor, deferring_a_pass):
                r = run_script("smoke-test.sh", env=fresh(stdout="postgres\nredis\n"))
                expect(
                    "the driver runs a function a Module deferred",
                    "zz-deferred-ran" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )
                # The ordering is the whole reason the seam exists, so it is asserted rather
                # than assumed: the deferred line must come after the last Module's section,
                # not merely somewhere in the output.
                last_module = module_owners[-1]
                expect(
                    "a deferred function runs after every Module's script",
                    "zz-deferred-ran" in r.stdout
                    and "zz-inline-ran" in r.stdout
                    and r.stdout.index("zz-inline-ran")
                    < r.stdout.rindex(last_module)
                    < r.stdout.index("zz-deferred-ran"),
                    f"last Module {last_module!r}; stdout: {r.stdout!r}",
                )

            # …and its verdict is counted, not merely printed. A driver that registered the
            # function and never invoked it would exit 0 here having reported nothing about
            # a check the Module declared — the silent skip in its newest form. Only
            # postgres is running, so the deferred failure is the only one there can be.
            deferring_a_failure = (
                "# shellcheck shell=bash\n"
                "zz_selftest_deferred_fail() { fail 'zz-deferred-failed' 'by design'; }\n"
                "defer zz_selftest_deferred_fail\n"
                "pass 'zz-inline-ran'\n"
            )
            with moved_aside([donor]), planted(donor, deferring_a_failure):
                r = run_script("smoke-test.sh", env=fresh(stdout="postgres\n"))
                expect(
                    "a deferred failure fails the suite",
                    r.returncode != 0,
                    "exited 0 — a registered function that never ran would look exactly like this",
                )
                expect(
                    "a deferred failure is reported by name",
                    "zz-deferred-failed" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            # A name no function answers to. A typo, or a module script that stopped
            # part-way through and never reached the definition, would otherwise print
            # `command not found` on stderr, count nothing, and leave the suite exiting 0
            # with the registered check reporting neither pass, fail nor skip.
            deferring_a_typo = "# shellcheck shell=bash\ndefer zz_selftest_no_such_function\npass 'zz-inline-ran'\n"
            with moved_aside([donor]), planted(donor, deferring_a_typo):
                r = run_script("smoke-test.sh", env=fresh(stdout="postgres\n"))
                expect(
                    "a deferred name that is not a function fails the suite",
                    r.returncode != 0,
                    "exited 0 — the registered check reported nothing at all",
                )
                expect(
                    "the driver names the deferred function it could not call",
                    "zz_selftest_no_such_function" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            # The FR-5 arm of the deferred dashboard check, which no other case reaches:
            # CI starts every Module, so only a partial Selection gets here. Grafana alone
            # is running, so the injecting Modules are absent and the check must skip
            # naming them rather than fail on backends nobody started. The exit status is
            # deliberately not asserted — with only grafana "running", the datasource
            # curls in its own smoke.sh reach a real host port and are not hermetic; the
            # skip line is the part that is.
            r = run_script("smoke-test.sh", env=fresh(stdout="grafana\n"))
            expect(
                "the deferred dashboard check skips when the injecting Modules are absent",
                "dashboard panels return data —" in r.stdout and "otel-collector" in r.stdout,
                f"stdout: {r.stdout!r}",
            )
            expect(
                "the deferred dashboard check asserts nothing when it skips",
                "dashboard panel for" not in r.stdout,
                f"stdout: {r.stdout!r}",
            )

            # --- The panel-render check itself, against a stub Grafana. ---
            # A provisioned dashboard that loads is not a dashboard that works: every panel
            # can resolve to empty while Grafana renders tidy "No data" boxes and every
            # provisioning assertion still passes. These cases pin the four verdicts and the
            # two refusals, with no container runtime anywhere: the checker reaches the
            # backends through Grafana's datasource proxy, which speaks each backend's
            # native API and can therefore be stubbed with three fixed JSON shapes.
            checker = str(REPO / "scripts" / "check_dashboards.py")
            shipped_dir = REPO / "services" / "grafana" / "dashboards"
            marker = "zz-selftest-marker"

            def check_dashboards(*args: str) -> subprocess.CompletedProcess[str]:
                return tool([sys.executable, checker, "--service", marker, *args], cwd=REPO)

            # The shipped dashboard, read directly rather than through the checker: an
            # expectation derived from the thing under test agrees with whatever it says,
            # including a dashboard that quietly lost a signal.
            #
            # Panel *targets* only, mirroring what the checker resolves. A whole-document
            # walk also collects the `service` template variable's own `uid: prometheus`,
            # which means "the shipped dashboards cover all three signals" would still
            # pass with the metrics panel deleted — the exact regression the case exists
            # to catch.
            def target_uid(source: object) -> str | None:
                if isinstance(source, dict) and isinstance(source.get("uid"), str):
                    return str(source["uid"])
                return source if isinstance(source, str) else None

            def panels_of(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
                for panel in node.get("panels") or []:
                    if isinstance(panel, dict):
                        yield panel
                        yield from panels_of(panel)

            def datasource_uids(document: dict[str, Any]) -> set[str]:
                found: set[str] = set()
                for panel in panels_of(document):
                    panel_uid = target_uid(panel.get("datasource"))
                    for target in panel.get("targets") or []:
                        if not isinstance(target, dict):
                            continue
                        uid = target_uid(target.get("datasource")) or panel_uid
                        if uid is not None:
                            found.add(uid)
                return found

            shipped_dashboards = sorted(shipped_dir.glob("*.json"))
            expect("the Grafana Module ships a dashboard", bool(shipped_dashboards), f"{shipped_dir} holds no *.json")
            shipped_uids: set[str] = set()
            shipped_titles: list[str] = []
            for path in shipped_dashboards:
                document = json.loads(path.read_text(encoding="utf-8"))
                shipped_uids |= datasource_uids(document)
                shipped_titles += [
                    panel["title"]
                    for panel in document.get("panels", [])
                    if isinstance(panel, dict) and isinstance(panel.get("title"), str)
                ]
            pinned = {"prometheus", "loki", "tempo"}
            expect(
                "the shipped dashboards name only the pinned datasource UIDs",
                shipped_uids <= pinned,
                f"{sorted(shipped_uids - pinned)} is not provisioned in datasources.yaml",
            )
            expect(
                "the shipped dashboards cover all three signals",
                pinned <= shipped_uids,
                f"no panel targets {sorted(pinned - shipped_uids)}",
            )

            # `allowUiUpdates` is not cosmetic and its default is the wrong one here.
            # With UI updates allowed, "Save dashboard" in the browser writes a second
            # copy into the grafana-data volume that the tracked file no longer describes
            # — and Grafana reports `meta.provisioned: false` even for a dashboard the
            # file provider loaded, so the smoke suite's "provisioned from the bind mount"
            # assertion has nothing observable to stand on. Verified against
            # grafana/grafana:13.2.1, not assumed.
            provider = yaml.safe_load(
                (REPO / "services" / "grafana" / "conf" / "provisioning" / "dashboards" / "dashboards.yaml").read_text(
                    encoding="utf-8"
                )
            )
            providers = provider.get("providers") or []
            expect("the Grafana Module provisions dashboards from a file provider", bool(providers), f"{provider!r}")
            expect(
                "the dashboard provider keeps the tracked file the only source of truth",
                all(entry.get("allowUiUpdates") is False for entry in providers),
                f"{[entry.get('allowUiUpdates') for entry in providers]} — a UI save would fork into grafana-data "
                f"and meta.provisioned would report false for a file-provisioned dashboard",
            )

            # The credential form `services/grafana/smoke.sh` actually passes. urllib does
            # not act on `user:pass@host`, so the checker has to split the userinfo out and
            # send an Authorization header itself; without a case, a Grafana that required
            # a login would answer 401 for every panel and nothing here would have said so.
            with stub_grafana("results") as (url, authorizations):
                credentialed = url.replace("http://", "http://zzuser:zz%3Apass@")
                r = check_dashboards("--dashboards-dir", str(shipped_dir), "--grafana-url", credentialed)
                expect(
                    "check-dashboards accepts a userinfo URL",
                    r.returncode == 0,
                    f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
                )
                sent = sorted(set(authorizations))
                expect(
                    "check-dashboards turns the URL's userinfo into an Authorization header",
                    bool(sent)
                    and all(value.startswith("Basic ") for value in sent)
                    and all(
                        base64.b64decode(value.removeprefix("Basic ")).decode("utf-8") == "zzuser:zz:pass"
                        for value in sent
                    ),
                    f"headers seen: {sent!r}",
                )

            with stub_grafana("results") as (url, _):
                r = check_dashboards("--dashboards-dir", str(shipped_dir), "--grafana-url", url)
                expect(
                    "check-dashboards exits 0 when every signal answers with data",
                    r.returncode == 0,
                    f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
                )
                for signal in ("traces", "logs", "metrics"):
                    expect(
                        f"check-dashboards reports {signal} OK",
                        f"{signal}: OK" in r.stdout,
                        f"stdout: {r.stdout!r}",
                    )
                # The variable substitution is what makes the check about *this run's*
                # telemetry rather than about whatever is lying in the backends. A query
                # that kept its variable is matched by the label-value spelling
                # `$service"`, which the panel titles — reported verbatim, as the reader
                # will search for them in the JSON — cannot produce.
                expect(
                    "check-dashboards substitutes the dashboard's $service variable",
                    marker in r.stdout and '$service"' not in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            with stub_grafana("empty") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir", str(shipped_dir), "--grafana-url", url, "--budget-seconds", "0"
                )
                expect("check-dashboards fails when a panel returns nothing", r.returncode != 0, "exited 0")
                expect(
                    "check-dashboards names the dashboard, the panel and the query it ran",
                    all(f"{signal}: EMPTY" in r.stdout for signal in ("traces", "logs", "metrics"))
                    and all(path.name in r.stdout for path in shipped_dashboards)
                    and all(title in r.stdout for title in shipped_titles)
                    and marker in r.stdout,
                    f"panels {shipped_titles!r}; stdout: {r.stdout!r}",
                )

            # A first empty answer is not yet evidence of a broken panel: Tempo's search
            # API can lag a by-ID lookup by a block flush and Prometheus needs at least one
            # scrape interval, so the checker retries an empty signal on a bounded budget.
            # The stub answers the first call for each datasource with nothing and every
            # call after it with a row, so the pair below separates the two things a single
            # case would confuse — that the retry happens at all, and that the first answer
            # really was empty rather than the stub always saying yes.
            with stub_grafana("empty-once") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir",
                    str(shipped_dir),
                    "--grafana-url",
                    url,
                    "--budget-seconds",
                    "30",
                    "--interval-seconds",
                    "0",
                )
                expect(
                    "check-dashboards retries a signal that was empty on the first attempt",
                    r.returncode == 0 and all(f"{signal}: OK" in r.stdout for signal in ("traces", "logs", "metrics")),
                    f"exit {r.returncode}: {(r.stdout + r.stderr)!r}",
                )

            with stub_grafana("empty-once") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir", str(shipped_dir), "--grafana-url", url, "--budget-seconds", "0"
                )
                expect(
                    "check-dashboards reports EMPTY when the budget allows no retry",
                    r.returncode != 0 and "EMPTY" in r.stdout,
                    f"exit {r.returncode}: {r.stdout!r} — the retry case above would then prove nothing",
                )

            # ...and the other half of that bargain: a malformed query stays malformed, so
            # an ERROR is terminal. The same stub, refusing only the first call per
            # datasource: a checker that retried an ERROR the way it retries an EMPTY would
            # get a row on the second attempt and report three passing panels.
            with stub_grafana("error-once") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir",
                    str(shipped_dir),
                    "--grafana-url",
                    url,
                    "--budget-seconds",
                    "30",
                    "--interval-seconds",
                    "0",
                )
                expect(
                    "check-dashboards never retries a query the proxy refused",
                    r.returncode != 0 and "ERROR" in r.stdout,
                    f"exit {r.returncode}: {r.stdout!r}",
                )

            # A signal with two panels, the second of which is broken. Every panel for a
            # signal has to be run, not just enough of them to find data: stopping at the
            # first that returned rows leaves the broken one unqueried, and "a panel edited
            # into a broken query fails this check" is what the CHANGELOG, the gotchas and
            # ADR 0015 all promise. `error-later` answers the first call per datasource
            # with a row and refuses every one after it, which is exactly that shape.
            two_panel_dir = stubs / "dashboards-two-panels"
            two_panel_dir.mkdir(exist_ok=True)
            (two_panel_dir / "two-metrics.json").write_text(
                json.dumps(
                    {
                        "uid": "zz-selftest-two",
                        "title": "two metrics panels",
                        "panels": [
                            {
                                "id": 1,
                                "title": "the working panel",
                                "datasource": {"type": "prometheus", "uid": "prometheus"},
                                "targets": [{"refId": "A", "expr": '{service_name="$service"}'}],
                            },
                            {
                                "id": 2,
                                "title": "the broken panel",
                                "datasource": {"type": "prometheus", "uid": "prometheus"},
                                "targets": [{"refId": "A", "expr": '{{{service_name="$service"}'}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            with stub_grafana("error-later") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir", str(two_panel_dir), "--grafana-url", url, "--budget-seconds", "0"
                )
                expect(
                    "check-dashboards runs every panel for a signal, not just until one returns data",
                    r.returncode != 0 and "metrics: ERROR" in r.stdout and "the broken panel" in r.stdout,
                    f"exit {r.returncode}: {r.stdout!r} — a first-hit OK hides the second panel entirely",
                )

            # ...and the same shape with the second panel merely *empty* rather than
            # refused. A signal whose first panel has data and whose second renders a "No
            # data" box is a broken dashboard: reducing to the first panel that found rows
            # would report OK and leave the reader staring at the empty box this story
            # exists to remove. The same stub, inverted.
            with stub_grafana("empty-later") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir", str(two_panel_dir), "--grafana-url", url, "--budget-seconds", "0"
                )
                expect(
                    "check-dashboards reports a signal EMPTY when any of its panels returned nothing",
                    r.returncode != 0 and "metrics: EMPTY" in r.stdout and "the broken panel" in r.stdout,
                    f"exit {r.returncode}: {r.stdout!r} — a sibling panel with data must not mask an empty one",
                )

            # Three panel-discovery rules that the shipped dashboard, being one flat list
            # of visible targets pinned by `datasource` objects, exercises not at all:
            # descending into a collapsed row, skipping a target the panel author hid, and
            # reading the bare-string `datasource` a Grafana 8 export writes. Posed in one
            # fixture against `error-later`, which answers the first call per datasource
            # with a row and refuses every one after: `logs: OK` therefore holds only if
            # the folded child was found (else MISSING) *and* the hidden target was not run
            # (else the second call refuses and the signal is ERROR).
            row_dir = stubs / "dashboards-row"
            row_dir.mkdir(exist_ok=True)
            (row_dir / "folded.json").write_text(
                json.dumps(
                    {
                        "uid": "zz-selftest-row",
                        "title": "a collapsed row",
                        "panels": [
                            {
                                "id": 1,
                                "type": "row",
                                "title": "folded away",
                                "collapsed": True,
                                "panels": [
                                    {
                                        "id": 2,
                                        "title": "the folded panel",
                                        "datasource": "loki",
                                        "targets": [
                                            {"refId": "A", "expr": '{service_name="$service"}'},
                                            {"refId": "B", "expr": "{nonsense=", "hide": True},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            with stub_grafana("error-later") as (url, _):
                r = check_dashboards("--dashboards-dir", str(row_dir), "--grafana-url", url, "--budget-seconds", "0")
                expect(
                    "check-dashboards runs a panel folded inside a collapsed row, and skips a hidden target",
                    "logs: OK" in r.stdout and "the folded panel" in r.stdout,
                    f"stdout: {r.stdout!r} — MISSING means the row was never descended, "
                    f"ERROR means the hidden target was run",
                )

            # Only `$service` is substituted, and only when that is the whole variable
            # name. `service_name` is the label every shipped panel filters on, so
            # `$service_name` is a name someone will write: a plain string replace turns it
            # into `<marker>_name` and the backend answers a valid query about a label
            # value nobody emits — an EMPTY whose reason has nothing to do with the panel.
            # The reported query is the substituted one, so the raw name surviving in the
            # output is the assertion.
            prefix_dir = stubs / "dashboards-prefix-variable"
            prefix_dir.mkdir(exist_ok=True)
            (prefix_dir / "other-variable.json").write_text(
                json.dumps(
                    {
                        "uid": "zz-selftest-prefix",
                        "title": "a variable whose name starts with service",
                        "panels": [
                            {
                                "id": 1,
                                "title": "two variables",
                                "datasource": {"type": "prometheus", "uid": "prometheus"},
                                "targets": [{"refId": "A", "expr": '{service_name="$service",tier="$service_tier"}'}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            with stub_grafana("results") as (url, _):
                r = check_dashboards("--dashboards-dir", str(prefix_dir), "--grafana-url", url)
                expect(
                    "check-dashboards substitutes $service without eating a longer variable name",
                    f'service_name="{marker}"' in r.stdout and "$service_tier" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            with stub_grafana("error") as (url, _):
                r = check_dashboards(
                    "--dashboards-dir", str(shipped_dir), "--grafana-url", url, "--budget-seconds", "0"
                )
                expect("check-dashboards fails when the proxy refuses a query", r.returncode != 0, "exited 0")
                expect(
                    "check-dashboards names the status and the reason",
                    "ERROR" in r.stdout and "HTTP 400" in r.stdout and "syntax error" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            # A dashboards directory tracked only by its .gitkeep is the state this story
            # found the repository in, and it must never read as three passing panels.
            empty_dir = stubs / "dashboards-none"
            empty_dir.mkdir(exist_ok=True)
            (empty_dir / ".gitkeep").write_text("", encoding="utf-8", newline="\n")
            r = check_dashboards("--dashboards-dir", str(empty_dir), "--grafana-url", "http://127.0.0.1:1")
            expect("check-dashboards refuses a directory with no dashboards", r.returncode != 0, "exited 0")
            expect(
                "check-dashboards names the empty directory",
                empty_dir.name in r.stderr,
                f"stderr: {r.stderr!r}",
            )

            broken_dir = stubs / "dashboards-broken"
            broken_dir.mkdir(exist_ok=True)
            (broken_dir / "broken.json").write_text('{\n  "panels": [\n', encoding="utf-8", newline="\n")
            r = check_dashboards("--dashboards-dir", str(broken_dir), "--grafana-url", "http://127.0.0.1:1")
            expect("check-dashboards refuses a dashboard that does not parse", r.returncode != 0, "exited 0")
            expect(
                "check-dashboards names the unparseable file and the parse error",
                "broken.json" in r.stderr and "invalid JSON" in r.stderr,
                f"stderr: {r.stderr!r}",
            )

            # A signal no panel targets is a hole in the UI, not a signal that passed by
            # having nothing to ask. Reported without a request being made at all.
            partial_dir = stubs / "dashboards-partial"
            partial_dir.mkdir(exist_ok=True)
            (partial_dir / "metrics-only.json").write_text(
                json.dumps(
                    {
                        "uid": "zz-selftest-partial",
                        "title": "metrics only",
                        "panels": [
                            {
                                "id": 1,
                                "type": "timeseries",
                                "title": "only metrics",
                                "datasource": {"type": "prometheus", "uid": "prometheus"},
                                "targets": [{"refId": "A", "expr": '{service_name="$service"}'}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            with stub_grafana("results") as (url, _):
                r = check_dashboards("--dashboards-dir", str(partial_dir), "--grafana-url", url)
                expect("check-dashboards fails when a signal has no panel", r.returncode != 0, "exited 0")
                expect(
                    "check-dashboards names each signal no panel covers",
                    "traces: MISSING" in r.stdout and "logs: MISSING" in r.stdout and "metrics: OK" in r.stdout,
                    f"stdout: {r.stdout!r}",
                )

            # --- Selection, against the real runtime. ---
            # The stub has no profile semantics at all, so only Compose itself can say
            # what a Selection actually renders. Three things are asserted here and
            # nowhere else: that the empty Selection now renders *nothing* — the breaking
            # change ADR 0013 records, and the reason the resolver refuses it — that each
            # Module's resolved closure renders exactly the services that Module and its
            # dependencies own, and that each group's closure does the same.
            real = dict(os.environ)
            real["COMPOSE_PROFILES"] = ""
            empty_selection = tool(["docker", "compose", "config", "--services"], cwd=REPO, env=real)
            empty_services = {line.strip() for line in empty_selection.stdout.splitlines() if line.strip()}
            expect(
                "the empty Selection renders no service at all",
                empty_selection.returncode == 0 and not empty_services,
                f"exit {empty_selection.returncode}, rendered {sorted(empty_services)} — with a Module profile on "
                f"every service an unset COMPOSE_PROFILES must select nothing, which is exactly why "
                f"scripts/select.sh refuses it (AD-18)",
            )

            def resolve_names(*names: str) -> str:
                answer = run_script("select.sh", *names, env=real)
                expect(
                    f"select.sh resolves {' '.join(names)}",
                    answer.returncode == 0,
                    f"exit {answer.returncode}: {answer.stderr!r}",
                )
                return answer.stdout.strip()

            def selects(selection: str) -> set[str]:
                one = dict(real)
                one["COMPOSE_PROFILES"] = selection
                rendered_services = tool(["docker", "compose", "config", "--services"], cwd=REPO, env=one)
                return {line.strip() for line in rendered_services.stdout.splitlines() if line.strip()}

            # Membership pinned literally, service by service, against the real runtime —
            # an independent list, not a reading of any file the check also reads. A
            # service that lost its own `profiles:` key would join every Selection at
            # once, and a Module that gained a service nobody expected would show up here.
            expected_members = {
                "postgres": {"postgres"},
                "redis": {"redis"},
                "keycloak": {"keycloak", "postgres", "mailpit"},
                "minio": {"minio", "minio-init"},
                "mailpit": {"mailpit"},
                "pgadmin": {"pgadmin", "postgres"},
                "redisinsight": {"redisinsight", "redis"},
                "flower": {"flower", "redis"},
                "prometheus": {"prometheus"},
                "loki": {"loki"},
                "tempo": {"tempo"},
                "otel-collector": {"otel-collector", "loki", "tempo"},
                "grafana": {"grafana", "prometheus", "loki", "tempo"},
                "admin": {"pgadmin", "redisinsight", "flower", "postgres", "redis"},
                "observability": {"otel-collector", "prometheus", "loki", "tempo", "grafana"},
                # The two Bundles ADR 0014 registers. `core` is the five services
                # up-core.sh used to name one by one — plus minio-init, which takes its
                # primary's profile set exactly — and `minimal` is the data layer alone.
                "core": {"postgres", "redis", "keycloak", "minio", "minio-init", "mailpit"},
                "minimal": {"postgres", "redis"},
            }
            expect(
                "every Module has a membership expectation of its own",
                set(every_module) <= set(expected_members),
                f"unlisted: {sorted(set(every_module) - set(expected_members))}",
            )
            for request, members in expected_members.items():
                selected = selects(resolve_names(request))
                expect(
                    f"the {request} Selection renders exactly {sorted(members)}",
                    selected == members,
                    f"unexpected {sorted(selected - members)}, missing {sorted(members - selected)}",
                )

            # …and Bundles compose. A multi-Bundle request is the shape a developer
            # actually types once more than one Bundle exists, and it is its own case:
            # every membership above is a single name, so a resolver that dropped all but
            # the first request, or that failed to union two closures, would pass every
            # one of them. `core,observability` is the two disjoint Bundles, so its answer
            # is exactly the union of theirs and nothing has to be restated here.
            composed = expected_members["core"] | expected_members["observability"]
            expect(
                "the core,observability Selection renders both Bundles and nothing else",
                selects(resolve_names("core,observability")) == composed,
                f"expected {sorted(composed)}, got {sorted(selects(resolve_names('core,observability')))}",
            )

            # Adding Bundle names to a service's `profiles:` must change what *no*
            # existing Selection resolves to — profiles are added to, never replaced. The
            # three that could have moved are pinned as literal strings rather than
            # recomputed: `admin` gained Postgres and Redis as members, which is what makes
            # it dependency-closed by declaration, and the whole argument that the change
            # is inert is that its closure is the same five it always was. Postgres gained
            # three Bundle names and still answers only to `postgres`; Keycloak gained
            # `core` and still pulls in exactly Mailpit and Postgres.
            for request, unchanged in (
                ("admin", "flower,pgadmin,postgres,redis,redisinsight"),
                ("observability", "grafana,loki,otel-collector,prometheus,tempo"),
                ("keycloak", "keycloak,mailpit,postgres"),
                ("postgres", "postgres"),
                ("redis", "redis"),
            ):
                expect(
                    f"the {request} Selection resolves exactly as it did before Bundles existed",
                    resolve_names(request) == unchanged,
                    f"{request} now resolves to {resolve_names(request)!r}, not {unchanged!r} — "
                    f"a Bundle name added to a profiles: list changed what an existing "
                    f"Selection starts, which ADR 0014 forbids",
                )
            # …and `core` resolves to exactly the five Modules up-core.sh used to name one
            # by one, which is what lets that script name a Bundle instead of a list.
            expect(
                "the core Bundle resolves to the five Modules up-core started by name",
                resolve_names("core") == core_modules,
                f"core resolves to {resolve_names('core')!r}, not {core_modules!r}",
            )

            # The headline case, end to end: two Modules, two containers, and none of the
            # eleven services a bare `up` used to start alongside them.
            two_modules = selects(resolve_names("postgres", "redis"))
            expect(
                "a Selection of postgres and redis renders exactly those two services",
                two_modules == {"postgres", "redis"},
                f"rendered {sorted(two_modules)}",
            )

            # …and the all-Modules request renders every service the stack has, which is
            # what `down`, `stop`, `pull`, `dump-logs`, `config` and `destroy` act on.
            everything = selects(resolve_names("--all"))
            expect(
                "the all-Modules Selection renders every service",
                everything == set().union(*expected_members.values()),
                f"missing {sorted(set().union(*expected_members.values()) - everything)}",
            )

            # An unresolved Selection reaching Compose fails, and that is correct
            # behaviour rather than a defect (AD-16): Postgres cannot carry the `keycloak`
            # profile without Keycloak editing Postgres's file, which AD-15 forbids.
            raw = dict(real)
            raw["COMPOSE_PROFILES"] = "keycloak"
            bypassed = tool(["docker", "compose", "config", "-q"], cwd=REPO, env=raw)
            expect(
                "an unresolved Selection is refused by Compose itself",
                bypassed.returncode != 0 and "undefined service" in bypassed.stderr,
                f"exit {bypassed.returncode}: {bypassed.stderr!r}",
            )
            # …and the resolved one is not.
            keycloak_ok = dict(real)
            keycloak_ok["COMPOSE_PROFILES"] = resolve_names("keycloak")
            validated = tool(["docker", "compose", "config", "-q"], cwd=REPO, env=keycloak_ok)
            expect(
                "the resolved Selection validates",
                validated.returncode == 0,
                f"exit {validated.returncode}: {validated.stderr!r}",
            )

            # --- The shipped defaults must resolve to every Module. ---
            # A fresh checkout, and both CI stack jobs, start exactly what the stack
            # started before Selection existed (AD-18). Asserted, not stated.
            dotenv_default = ""
            for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
                if line.startswith("COMPOSE_PROFILES="):
                    dotenv_default = line.split("=", 1)[1].strip()
            expect(".env.example ships a COMPOSE_PROFILES line", bool(dotenv_default), "no COMPOSE_PROFILES= found")
            expect(
                ".env.example's Selection resolves to every Module",
                resolve_names(dotenv_default) == all_modules,
                f"{dotenv_default!r} resolves to {resolve_names(dotenv_default)!r}, not {all_modules!r}",
            )

            # The resolver's own refusals, at the seam a contributor reaches (AD-21).
            # Each must exit non-zero, explain itself on stderr, and print nothing at all
            # on stdout — a caller substituting this command must never get a partial
            # Selection.
            for names, needle, case in (
                ([""], "COMPOSE_PROFILES", "an empty request"),
                (["zz-nosuch-module"], "zz-nosuch-module", "an unknown name"),
            ):
                refused = run_script("select.sh", *names, env=real)
                expect(f"select.sh refuses {case}", refused.returncode != 0, "exited 0")
                expect(f"select.sh explains {case}", needle in refused.stderr, f"stderr: {refused.stderr!r}")
                expect(f"select.sh prints nothing on stdout for {case}", not refused.stdout, f"{refused.stdout!r}")
            unknown = run_script("select.sh", "zz-nosuch-module", env=real)
            expect(
                "select.sh lists the valid names when it refuses one",
                all(name in unknown.stderr for name in every_module),
                f"stderr: {unknown.stderr!r}",
            )

            # Resolving an already-resolved Selection returns the same set, which is what
            # lets a resolved value survive a script that re-sources .env.
            once = resolve_names("keycloak")
            expect(
                "select.sh is idempotent",
                resolve_names(once) == once,
                f"{once!r} resolved again to {resolve_names(once)!r}",
            )

            # And a request taken from the environment is the same as one given as
            # arguments — the path every script that resolves the ambient Selection uses.
            ambient = dict(real)
            ambient["COMPOSE_PROFILES"] = "keycloak"
            from_env = run_script("select.sh", env=ambient)
            expect(
                "select.sh reads the request from COMPOSE_PROFILES when given no arguments",
                from_env.returncode == 0 and from_env.stdout.strip() == once,
                f"exit {from_env.returncode}: {from_env.stdout!r} vs {once!r}",
            )

            # A depends_on edge no Module owns is refused rather than silently dropped
            # from the closure: a resolver that dropped it would start a stack missing
            # the service the edge exists for, and nothing else would notice.
            orphan_module = REPO / "services" / "zz-selftest-orphan"
            orphan_module.mkdir(exist_ok=True)
            try:
                with planted(
                    orphan_module / "compose.yaml",
                    "services:\n  zz-selftest-orphan:\n    image: alpine:3.22\n"
                    "    profiles: [zz-selftest-orphan]\n"
                    "    depends_on:\n      - zz-absent-service\n",
                ):
                    orphan_result = run_script("select.sh", "postgres", env=real)
                    expect("select.sh refuses a depends_on no Module owns", orphan_result.returncode != 0, "exited 0")
                    expect(
                        "select.sh names the edge it cannot resolve",
                        "zz-absent-service" in orphan_result.stderr,
                        f"stderr: {orphan_result.stderr!r}",
                    )
                    expect(
                        "select.sh prints nothing on stdout for an unownable edge",
                        not orphan_result.stdout,
                        f"stdout: {orphan_result.stdout!r}",
                    )
            finally:
                shutil.rmtree(orphan_module, ignore_errors=True)

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
    # examples/ joins the walk for the same reason .githooks/ did: it is checked-in Python
    # that `pixi run lint-python` and `pixi run example` both reach, so a clone whose
    # examples/ arrived ignored has an example that lints locally and is not there at all.
    sources = (
        sorted((REPO / "scripts").rglob("*.sh"))
        + sorted((REPO / "scripts").rglob("*.py"))
        + sorted((REPO / EXAMPLE_DIR).rglob("*.py"))
        + hook_sources
    )
    expect("scripts/ has files to check for tracking", bool(sources), "no scripts found")
    expect(
        f"{EXAMPLE_DIR}/ has Python to check for tracking",
        bool(sorted((REPO / EXAMPLE_DIR).rglob("*.py"))),
        f"nothing under {EXAMPLE_DIR}/ — the coverage widening above would check nothing",
    )
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

    # --- Every script that reaches Compose resolves its Selection first. ---
    # AD-16 is a rule about every path to the runtime, not about the six that happened
    # to be rewritten. A script added later that calls `compose` without resolving would
    # act on whatever COMPOSE_PROFILES literally says — `admin` alone, selecting the
    # three admin services without the Postgres and Redis they talk to — and every other
    # check here would pass. The two exceptions are named, so removing a resolver call
    # from a third script fails this rather than joining a category.
    #
    #   lint-compose.sh drives the Selection under test itself, one per iteration.
    #   smoke-test.sh's oracle is observational by contract: `ps`, `exec` and `version`
    #   ignore active profiles entirely, so it reads what is up rather than what was asked
    #   for, and making it consult the resolver would break that.
    resolver_exempt = {"lint-compose.sh", "smoke-test.sh"}
    calls_compose = re.compile(r"(?<![\w./-])compose\s")
    unresolved: list[str] = []
    reaches_compose: list[str] = []
    for script in sorted((REPO / "scripts").glob("*.sh")):
        script_text = script.read_text(encoding="utf-8")
        script_code = "\n".join(line for line in script_text.splitlines() if not line.lstrip().startswith("#"))
        if not calls_compose.search(script_code):
            continue
        reaches_compose.append(script.name)
        if script.name in resolver_exempt:
            continue
        if "select_profiles" not in script_code and "select_ambient" not in script_code:
            unresolved.append(script.name)
    expect("there are scripts that reach Compose", bool(reaches_compose), "found none — this rule checked nothing")
    expect(
        "every script that calls compose resolves its Selection first",
        not unresolved,
        f"{unresolved} call compose without select_profiles/select_ambient",
    )
    # …and both exemptions are real files that really do call compose. An exemption for a
    # script that no longer exists, or no longer reaches the runtime, is a hole waiting
    # for the next script of that name.
    expect(
        "both resolver exemptions are scripts that actually reach Compose",
        resolver_exempt <= set(reaches_compose),
        f"exempt but not reaching Compose: {sorted(resolver_exempt - set(reaches_compose))}",
    )
    # The example's runner reaches no container runtime at all — it talks to the published
    # host ports the way an application does — so the rule above never sees it. It still has
    # to resolve first, and for a sharper reason: the Selection decides which application
    # variables exist, so asking the generator before resolving would export a set that does
    # not describe the stack that is running (ADR 0019).
    runner_code = "\n".join(
        line
        for line in (REPO / "scripts" / "example.sh").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    expect(
        "the example runner resolves its Selection before asking the generator anything",
        "select_ambient" in runner_code and runner_code.index("select_ambient") < runner_code.index("endpoints.py"),
        "scripts/example.sh reaches scripts/endpoints.py without calling select_ambient first",
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
    # The stack job runs the suite twice, and the order is the contract: `ci-stack-cycle`
    # takes the stack down and brings it back, so it proves nothing at all unless the run
    # that started it came first. `example` sits between the two for the same kind of
    # reason — it needs the stack the first step left running, and running it before the
    # cycle and the restore spends the least of the job's 15-minute budget on a contract
    # that no longer connects (ADR 0019).
    expected_work = {
        "validate": ["pixi run ci"],
        "stack": [
            "pixi run ci-stack",
            "pixi run example",
            "pixi run ci-stack-cycle",
            "pixi run ci-stack-restore",
        ],
        "stack-podman": ["pixi run ci-stack-podman"],
    }
    expect("ci.yml declares exactly the three CI jobs", set(ci_jobs) == set(expected_work), f"{set(ci_jobs)}")
    for job_name, expected_commands in expected_work.items():
        job = ci_jobs.get(job_name, {})
        work = [
            str(step.get("run")).strip()
            for step in job.get("steps") or []
            if step.get("run") is not None and str(step.get("if", "")).strip() != "failure()"
        ]
        expect(f"CI job {job_name} runs exactly {expected_commands}", work == expected_commands, f"runs {work}")

    # Both stack jobs must start every Module. Equality against `config --profiles` is
    # what this used to say, and it cannot survive Selection: there are seventeen declared
    # profiles now and a job must not name every Module by hand. What matters is not the
    # spelling but what it resolves to, so that is what is asserted — on the Podman job
    # too, so "no service is silently excluded under Podman" is a gate rather than a
    # sentence in the README.
    catalog = sorted(path.parent.name for path in (REPO / "services").glob("*/compose.yaml"))
    expect("there are Modules for the CI jobs to start", bool(catalog), "found no services/*/compose.yaml")
    for job_name in ("stack", "stack-podman"):
        job_profiles = str(ci_jobs.get(job_name, {}).get("env", {}).get("COMPOSE_PROFILES", ""))
        expect(f"the {job_name} job states a Selection", bool(job_profiles), "no COMPOSE_PROFILES in the job env")
        job_selection = run_script("select.sh", job_profiles)
        expect(
            f"the {job_name} job's Selection resolves",
            job_selection.returncode == 0,
            f"{job_profiles!r}: exit {job_selection.returncode}: {job_selection.stderr!r}",
        )
        expect(
            f"the {job_name} job's Selection resolves to every Module",
            job_selection.stdout.strip() == ",".join(catalog),
            f"workflow: {job_profiles!r} resolves to {job_selection.stdout.strip()!r}; "
            f"missing {sorted(set(catalog) - set(job_selection.stdout.strip().split(',')))}",
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
    for task_name in ("ci", "ci-stack", "ci-stack-cycle", "ci-stack-podman", "example", "ps", "dump-logs"):
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
    # The cycle is `down` and then the same tasks again, in that order. Written as an
    # equality because the value is entirely in the sequence: a chain that lost `down`
    # would still pass a subset check while proving nothing about volume state, and one
    # that ran `down` last would leave CI's diagnostics with nothing to inspect.
    expect(
        "`pixi run ci-stack-cycle` takes the stack down, brings it back and re-runs the strict suite",
        chain("ci-stack-cycle") == ["down", "start", "wait", "smoke-strict"],
        f"ci-stack-cycle depends on {chain('ci-stack-cycle')}",
    )
    # The third stack task, and the only one that says anything about the backup. It is a
    # command rather than a chain — the round trip has to interleave planting, destroying
    # and restoring — so what is pinned is that CI's step reaches the script that does it.
    restore_task = tasks.get("ci-stack-restore")
    restore_cmd = str(restore_task.get("cmd", "")) if isinstance(restore_task, dict) else str(restore_task)
    expect(
        "`pixi run ci-stack-restore` runs the backup round trip",
        restore_cmd.strip() == "./scripts/verify-restore.sh",
        f"ci-stack-restore runs {restore_cmd!r}",
    )
    # The same pin for the worked example, and for the same reason. The workflow step is
    # asserted to be `pixi run example` a few lines above, which says nothing at all about
    # what that task does: rewiring the body to `echo ok` would keep the step green, keep
    # the chain assertions green, and stop the example ever running in CI.
    example_task = tasks.get("example")
    example_cmd = str(example_task.get("cmd", "")) if isinstance(example_task, dict) else str(example_task)
    expect(
        "`pixi run example` runs the worked example's runner",
        example_cmd.strip() == "./scripts/example.sh",
        f"example runs {example_cmd!r}",
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
        "the pre-commit hook runs the endpoint drift check",
        "lint-endpoints" in precommit_members,
        f"precommit runs {sorted(precommit_members)} — ADR 0017 and the README both say the "
        f"hook is where a drifted docs/ENDPOINTS.md is caught, and precommit <= lint alone "
        f"would let it be dropped from the hook with every other assertion green",
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

        yaml_defect = REPO / "services" / "loki" / "conf" / "zz_selftest_hook_defect.yaml"
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

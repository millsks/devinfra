#!/usr/bin/env python3
"""Run the shipped Grafana dashboards' own panel queries and prove they return data.

A provisioned dashboard that loads is not a dashboard that works: every panel on it can
resolve to an empty result and Grafana will still render three tidy "No data" boxes. This
runs each panel's query and fails when it comes back empty, which is the only difference a
developer opening Grafana actually cares about.

The queries are *read out of the dashboard JSON*, never restated here. That is the whole
point: editing a panel into a broken query has to fail this check, and it cannot if the
checker carries its own copy of what the panel is supposed to ask.

The requests go through Grafana rather than straight to the backends —
`/api/datasources/proxy/uid/<uid>/…` — so what is proved is that *Grafana* can reach the
datasource with the panel's own expression, which is what a browser does. The proxy speaks
each backend's native API, so no per-datasource query-model translation is needed and the
whole thing stays testable against a stub HTTP server.

    python scripts/check_dashboards.py --grafana-url http://admin:admin@127.0.0.1:3000 --service my-service

One `<signal>: OK|EMPTY|ERROR|MISSING <detail>` line per signal on stdout, and exit 0 only
when all three are OK. A directory with no dashboards and a dashboard that does not parse
are refusals on stderr, never a quiet pass — a check that walked nothing has verified
nothing.

Stdlib only, and no third-party HTTP client, because `scripts/smoke-test.sh` may run in a
bare checkout through the DEVINFRA_PYTHON seam.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Datasource UID -> the signal it carries, in the order the report prints them.
#: The UIDs are pinned in services/grafana/conf/provisioning/datasources/datasources.yaml
#: and a dashboard must reference them literally, which is what makes this mapping legal.
SIGNALS: dict[str, str] = {"tempo": "traces", "loki": "logs", "prometheus": "metrics"}

#: The key each datasource's panel target carries its query under. Tempo's editor writes
#: `query`; the Prometheus and Loki editors write `expr`.
QUERY_KEYS: dict[str, str] = {"tempo": "query", "loki": "expr", "prometheus": "expr"}


@dataclass(frozen=True)
class Panel:
    """One panel target that names one of the pinned datasource UIDs.

    Attributes:
        dashboard: Path of the dashboard the panel came from, relative to the
            dashboards directory. Relative rather than just the file name because the
            provider sets `foldersFromFilesStructure: true`, so subdirectories are an
            expected layout and two `overview.json` files in different folders must not
            produce the same evidence line.
        title: The panel's own title, as a reader sees it in Grafana.
        uid: Datasource UID the target is pinned to.
        query: The target's query, with the dashboard variables already substituted.
    """

    dashboard: str
    title: str
    uid: str
    query: str


@dataclass(frozen=True)
class Verdict:
    """What one signal's panels reported.

    Attributes:
        status: One of OK, EMPTY, ERROR, MISSING.
        detail: Human-readable evidence, naming the dashboard, panel and query.
    """

    status: str
    detail: str


class Refusal(Exception):
    """Raised when the check cannot be run at all, rather than run and pass."""


def datasource_uid(value: object) -> str | None:
    """Read a datasource UID out of a panel's or target's `datasource` field.

    Args:
        value: The raw field, which is a mapping in every current schema version and a
            bare datasource *name* in dashboards exported from Grafana 8 and earlier.

    Returns:
        The UID, or None when the field names no UID this checker recognises.
    """
    if isinstance(value, dict):
        uid = value.get("uid")
        return uid if isinstance(uid, str) else None
    if isinstance(value, str):
        return value
    return None


def iter_panel_dicts(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Walk a dashboard's panels, descending into collapsed rows.

    A collapsed row is itself a panel and carries its children under its own `panels`
    key, so a flat read of the top-level list silently misses every panel a reader
    happened to fold away before saving.

    Args:
        node: A dashboard document, or a row panel.

    Yields:
        Each panel mapping found, rows included.
    """
    for panel in node.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        yield panel
        yield from iter_panel_dicts(panel)


#: `$service` / `${service}`, and nothing that merely starts with those letters. Grafana
#: expands the longest variable name that matches, so a plain string replace would rewrite
#: `$service_name` — a name this repository invites, since `service_name` is the label every
#: panel filters on — into `<marker>_name`. That is worse than not substituting it: the
#: backend answers a syntactically valid query about a label value nobody emits, and the
#: check reports EMPTY for a reason that has nothing to do with the panel.
SERVICE_VARIABLE = re.compile(r"\$(?:\{service\}|service(?![A-Za-z0-9_]))")


def substitute(query: str, service: str) -> str:
    """Replace the dashboard's `service` variable with a concrete value.

    Both spellings Grafana accepts are handled. Any other variable is left exactly as
    written, so it reaches the backend raw and comes back as a loud 400 — which is what
    README.md and services/grafana/gotchas.md tell whoever drops a dashboard in here.

    Args:
        query: The panel's query, as written in the dashboard.
        service: Value to substitute.

    Returns:
        The query with no `service` variable left in it.
    """
    return SERVICE_VARIABLE.sub(lambda _: service, query)


def read_panels(directory: Path, service: str) -> list[Panel]:
    """Collect every panel target pinned to one of the three signal datasources.

    Args:
        directory: Directory holding the provisioned dashboard JSON.
        service: Value substituted for the dashboards' `service` variable.

    Returns:
        One entry per query, in file then panel order.

    Raises:
        Refusal: If the directory holds no dashboards, or one of them does not parse.
    """
    if not directory.is_dir():
        raise Refusal(f"no such dashboards directory: {directory}")
    files = sorted(path for path in directory.rglob("*.json") if path.is_file())
    if not files:
        raise Refusal(
            f"{directory} holds no *.json dashboards — the provider would load nothing "
            f"and Grafana would open on an empty folder"
        )
    panels: list[Panel] = []
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Refusal(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise Refusal(f"{path}: unreadable: {exc}") from exc
        if not isinstance(document, dict):
            raise Refusal(f"{path}: not a dashboard — the top level is {type(document).__name__}, not an object")
        for panel in iter_panel_dicts(document):
            panel_uid = datasource_uid(panel.get("datasource"))
            title = panel.get("title")
            for target in panel.get("targets") or []:
                if not isinstance(target, dict) or target.get("hide") is True:
                    continue
                uid = datasource_uid(target.get("datasource")) or panel_uid
                if uid not in SIGNALS:
                    continue
                query = target.get(QUERY_KEYS[uid])
                if not isinstance(query, str) or not query.strip():
                    continue
                panels.append(
                    Panel(
                        dashboard=str(path.relative_to(directory)),
                        title=title if isinstance(title, str) and title else "(untitled panel)",
                        uid=uid,
                        query=substitute(query, service),
                    )
                )
    return panels


def proxy_request(base: str, panel: Panel, now: float, lookback: float) -> str:
    """Build the Grafana datasource-proxy URL that runs one panel's query.

    Args:
        base: Grafana's base URL, with any userinfo already stripped.
        panel: The panel whose query is to be run.
        now: Current wall-clock time, in seconds since the epoch.
        lookback: How far back the Loki and Tempo query windows reach, in seconds. The
            Prometheus branch sends an instant query, which takes a single `time` and no
            window at all, so this does not reach it.

    Returns:
        The fully-formed URL, native API path and query string included.
    """
    start = now - lookback
    if panel.uid == "prometheus":
        path = "api/v1/query"
        params = {"query": panel.query, "time": f"{now:.3f}"}
    elif panel.uid == "loki":
        path = "loki/api/v1/query_range"
        params = {
            "query": panel.query,
            "start": f"{int(start * 1_000_000_000)}",
            "end": f"{int(now * 1_000_000_000)}",
            "limit": "5",
            "direction": "backward",
        }
    else:
        path = "api/search"
        params = {"q": panel.query, "start": f"{int(start)}", "end": f"{int(now)}", "limit": "5"}
    return f"{base}/api/datasources/proxy/uid/{panel.uid}/{path}?{urllib.parse.urlencode(params)}"


def fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    """Perform one GET, returning the status and body rather than raising on either.

    Args:
        url: Absolute URL to request.
        headers: Request headers, carrying the Authorization value when one is needed.
        timeout: Per-request timeout in seconds.

    Returns:
        The HTTP status and the response body. A transport failure — connection refused,
        DNS, timeout, a response truncated mid-body — is reported as status 0 with the
        reason as the body, so the caller reports it as an ERROR naming the panel rather
        than dying on a traceback. `http.client`'s own exceptions are in that set because
        urllib wraps only the ones raised while *sending*: a `BadStatusLine` or an
        `IncompleteRead` from reading the answer reaches this handler unwrapped.
    """
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
        return status, body
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def count_results(uid: str, body: str) -> int | None:
    """Count the rows one backend's native answer carries.

    Args:
        uid: Datasource UID the answer came from.
        body: The response body.

    Returns:
        The number of series, streams or traces, or None when the body is not the JSON
        shape that datasource is documented to return — which is a defect to report, not
        an emptiness to retry.

        Tempo is the one place an absent key counts as zero rather than as a defect:
        `traces` is a repeated protobuf field, so a build that renders an empty search
        result as `{}` is answering correctly, and reading that as ERROR would skip the
        retry budget the lagging search index needs. Prometheus's and Loki's
        `data.result` stays strict — both always emit it, so an absent one is a shape
        this checker does not understand and should say so.
    """
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    if uid == "tempo":
        traces = document.get("traces", [])
        return len(traces) if isinstance(traces, list) else None
    data = document.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    return len(result) if isinstance(result, list) else None


def first_line(text: str) -> str:
    """Return a body's first non-blank line, truncated for a one-line report.

    Args:
        text: Any response body.

    Returns:
        The first non-blank line, or a placeholder when the body is blank.
    """
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return "(empty body)"


def probe(panels: list[Panel], base: str, headers: dict[str, str], timeout: float, lookback: float) -> Verdict:
    """Run every one of a signal's panels once and reduce the answers to one verdict.

    Every panel is run, not just enough of them to find data, and the worst answer wins.
    Stopping at the first panel that returned rows would leave a second, broken panel for
    the same signal unqueried, and "a panel that returns nothing is a named failure" is the
    property the whole thing exists for — a signal with one working panel and one rendering
    "No data" is a broken dashboard exactly as much as one with a 400 is.

    Args:
        panels: The panels for this signal; never empty.
        base: Grafana's base URL.
        headers: Request headers.
        timeout: Per-request timeout in seconds.
        lookback: Query window, in seconds back from now.

    Returns:
        ERROR when any panel answered with a non-200 or an unreadable body, otherwise
        EMPTY when any panel returned no rows, otherwise OK.
    """
    now = time.time()
    error: Verdict | None = None
    found: Verdict | None = None
    empty: Verdict | None = None
    for panel in panels:
        where = f"'{panel.title}' in {panel.dashboard}"
        status, body = fetch(proxy_request(base, panel, now, lookback), headers, timeout)
        if status != 200:
            shown = f"HTTP {status}" if status else "no response"
            if error is None:
                error = Verdict("ERROR", f"{where} query {panel.query!r} failed: {shown}: {first_line(body)}")
            continue
        rows = count_results(panel.uid, body)
        if rows is None:
            if error is None:
                error = Verdict(
                    "ERROR",
                    f"{where} query {panel.query!r} answered 200 with a body this datasource "
                    f"never returns: {first_line(body)}",
                )
        elif rows > 0:
            if found is None:
                found = Verdict("OK", f"{where} returned {rows} result(s) for {panel.query!r}")
        elif empty is None:
            empty = Verdict("EMPTY", f"{where} returned nothing for {panel.query!r}")
    # A signal with no panel is MISSING and never reaches here, so one of the three is set.
    # The last arm is unreachable rather than a fallback, and says so rather than raising.
    return error or empty or found or Verdict("EMPTY", "this signal has no panel to run")


def report(
    panels: list[Panel],
    base: str,
    headers: dict[str, str],
    timeout: float,
    lookback: float,
    budget: float,
    interval: float,
) -> dict[str, Verdict]:
    """Resolve every signal to a verdict, retrying the empty ones on a bounded budget.

    Tempo's search API can lag a by-ID lookup by a block flush, and Prometheus needs at
    least one scrape interval, so a first empty answer is not yet evidence of a broken
    panel. An ERROR is not retried: a malformed query stays malformed.

    Args:
        panels: Every panel found, across all signals.
        base: Grafana's base URL.
        headers: Request headers.
        timeout: Per-request timeout in seconds.
        lookback: Query window, in seconds back from now.
        budget: How long to keep retrying the empty signals, in seconds.
        interval: Delay between attempts, in seconds.

    Returns:
        One verdict per signal, keyed by signal name.
    """
    uid_of = {name: uid for uid, name in SIGNALS.items()}
    by_signal = {signal: [panel for panel in panels if SIGNALS[panel.uid] == signal] for signal in SIGNALS.values()}
    verdicts: dict[str, Verdict] = {}
    pending: list[str] = []
    for signal, found in by_signal.items():
        if found:
            pending.append(signal)
        else:
            verdicts[signal] = Verdict(
                "MISSING",
                f"no panel targets the '{uid_of[signal]}' datasource, so nothing in Grafana shows this signal",
            )
    deadline = time.monotonic() + budget
    while pending:
        still_empty: list[str] = []
        for signal in pending:
            verdict = probe(by_signal[signal], base, headers, timeout, lookback)
            verdicts[signal] = verdict
            if verdict.status == "EMPTY":
                still_empty.append(signal)
        pending = still_empty
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return verdicts


def split_credentials(url: str) -> tuple[str, dict[str, str]]:
    """Separate a Grafana URL's userinfo from the URL itself.

    `urllib` does not act on `user:pass@host`, so the credentials are turned into an
    explicit Authorization header. Accepting them in the URL is what lets the caller pass
    the same `http://user:pass@host:port` string the smoke suite already builds.

    Args:
        url: Grafana's base URL, with or without userinfo.

    Returns:
        The URL with userinfo removed and no trailing slash, and the headers to send.

    Raises:
        Refusal: If the URL is not http or https, or names no host.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refusal(f"--grafana-url must be an http(s) URL naming a host, got {url!r}")
    netloc = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
    headers = {"Accept": "application/json"}
    if parts.username is not None:
        raw = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
        headers["Authorization"] = "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", "")), headers


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Returns:
        The parser, with every default stated so the smoke suite passes only what varies.
    """
    parser = argparse.ArgumentParser(
        prog="check_dashboards.py",
        description="Run the provisioned dashboards' own panel queries through Grafana and fail on an empty panel.",
    )
    parser.add_argument("--dashboards-dir", default="services/grafana/dashboards", help="directory of dashboard JSON")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000", help="Grafana base URL, userinfo allowed")
    parser.add_argument("--service", required=True, help="value substituted for the dashboards' $service variable")
    parser.add_argument("--budget-seconds", type=float, default=60.0, help="how long to retry an empty signal")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="delay between retries")
    parser.add_argument(
        "--lookback-seconds",
        type=float,
        default=3600.0,
        help="how far back the Loki and Tempo query windows reach; the Prometheus panel query is an instant query",
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="per-request timeout")
    return parser


def main(argv: list[str]) -> int:
    """Check every signal and report one line each.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        Process exit status: 0 only when all three signals are OK.
    """
    args = build_parser().parse_args(argv)
    try:
        base, headers = split_credentials(args.grafana_url)
        panels = read_panels(Path(args.dashboards_dir), args.service)
    except Refusal as exc:
        sys.stderr.write(f"check-dashboards: {exc}\n")
        return 1
    verdicts = report(
        panels,
        base,
        headers,
        args.timeout_seconds,
        args.lookback_seconds,
        args.budget_seconds,
        args.interval_seconds,
    )
    status = 0
    for signal in SIGNALS.values():
        verdict = verdicts[signal]
        sys.stdout.write(f"{signal}: {verdict.status} {verdict.detail}\n")
        if verdict.status != "OK":
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

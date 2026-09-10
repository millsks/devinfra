#!/usr/bin/env python3
"""Connect to every Module this stack publishes a contract for, using only that contract.

`docs/ENDPOINTS.md` lists fifteen application variables and, before this file existed,
nothing ever connected with them: the smoke suite speaks each service's own CLI from inside
its container, and the observability checks post hand-built OTLP JSON with `curl`. A renamed
port, a rotated credential or a wrong DSN spelling in `x-app-variables:` survived the whole
gate and was found by the first developer who pasted the published value into an
application. This is that developer, run on every CI build.

Everything it needs comes from the environment, and every name it reads is a key of the root
`compose.yaml`'s `x-app-variables:` registry (ADR 0003's application tier, ADR 0017) —
`REQUIRED` below is that list, and `scripts/lint_selftest.py` pins it to the registry by set
equality, so renaming a registry key fails `pixi run test` with no container in sight. There
is no fallback, no default and no hand-written address anywhere in this file: the values are
exported by `scripts/example.sh` from `scripts/endpoints.py --format env`, which is the only
thing in this repository that states a connection string (ADR 0019).

Six integrations, each a round trip the service itself has to answer rather than a client
that merely constructed cleanly, and each inside its own span, carrying its own log record
and incrementing its own counter under one `service.name` — the run's marker, which is what
`scripts/example.sh` then searches Mailpit and Grafana for.

    python examples/worked-example/main.py
    python examples/worked-example/main.py --service-name devinfra-example-0123abcd

This is a **worked example, not a production application template**: it opens a connection
per integration and throws it away, keeps no pool, retries nothing and holds no secret
management. What it demonstrates is the contract, not the architecture.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import boto3
import psycopg
import redis
from opentelemetry import trace
from opentelemetry._logs import Logger, SeverityNumber, set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

#: Every environment variable this application reads, and the integration each one serves.
#:
#: Every name here is a key of the root `compose.yaml`'s `x-app-variables:` registry, and
#: nothing else in this file reads the environment — `scripts/lint_selftest.py` asserts both
#: halves, so a name that is not in the registry, or a read that goes around this table,
#: fails `pixi run test`. The integration each serves is carried alongside the name because
#: a refusal that says only "AWS_ENDPOINT_URL is unset" tells a developer running a narrowed
#: Selection nothing about which Module they left out.
#:
#: `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are the two registry names deliberately
#: absent: this example ships no Celery worker, and reading a variable it does not use would
#: make the refusal below fire for a Module it does not need.
REQUIRED: dict[str, str] = {
    "DATABASE_URL": "Postgres",
    "REDIS_URL": "Redis",
    "OIDC_ISSUER": "Keycloak",
    "OIDC_DISCOVERY_URL": "Keycloak",
    "OIDC_CLIENT_ID": "Keycloak",
    "OIDC_CLIENT_SECRET": "Keycloak",
    "AWS_ENDPOINT_URL": "object storage",
    "AWS_ACCESS_KEY_ID": "object storage",
    "AWS_SECRET_ACCESS_KEY": "object storage",
    "SMTP_HOST": "Mailpit",
    "SMTP_PORT": "Mailpit",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "telemetry",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "telemetry",
}

#: The one wire protocol this application speaks, and the value the registry publishes for
#: `OTEL_EXPORTER_OTLP_PROTOCOL`. A different value is refused rather than ignored: the
#: exporters below are the HTTP ones, so a `grpc` here would mean the collector's gRPC port
#: was published and this run would post protobuf at an endpoint that does not speak it.
PROTOCOL = "http/protobuf"

#: How long any one blocking network call this file makes may take, in seconds — the two
#: Keycloak requests and the SMTP conversation. The five clients that bring their own
#: timeouts keep them; this is for the two contracts the standard library speaks, where
#: the default is no timeout at all and a service that accepts a connection and never
#: answers would hang the run rather than fail it (NFR-5).
TIMEOUT = 15.0

#: The counter every integration increments, one point per integration per run.
COUNTER_NAME = "devinfra_example_integration"

#: The table the Postgres round trip inserts into. Created if it is not there and left in
#: place afterwards, holding no rows: dropping it would race a second run, and an empty
#: table is not state a later run can read as its own (the re-run criterion is about rows,
#: keys and objects, and this deletes all three).
TABLE = "devinfra_example"


class Refusal(Exception):
    """A stated reason to stop, in the shape every script in this repository refuses with."""


@dataclass(frozen=True)
class Telemetry:
    """The three signal paths, and the providers that have to be flushed before exit.

    Attributes:
        tracer: Tracer each integration opens its span on.
        logger: Logger each integration emits its record through.
        counter: Counter each integration increments.
        tracer_provider: Trace provider, flushed and shut down at the end of the run.
        logger_provider: Log provider, flushed and shut down at the end of the run.
        meter_provider: Metric provider, flushed and shut down at the end of the run.
    """

    tracer: trace.Tracer
    logger: Logger
    counter: Counter
    tracer_provider: TracerProvider
    logger_provider: LoggerProvider
    meter_provider: MeterProvider


def settings() -> dict[str, str]:
    """Read every contract variable out of the environment, refusing before anything opens.

    Returns:
        Variable name to value, for every name in `REQUIRED`.

    Raises:
        Refusal: If any name is unset or empty, naming each one and the integration it
            serves — a narrowed Selection is the usual cause, and the Module is what the
            developer has to act on.
    """
    found: dict[str, str] = {}
    absent: list[str] = []
    for name, integration in REQUIRED.items():
        value = os.environ.get(name, "")
        if not value:
            absent.append(f"{name} ({integration})")
            continue
        found[name] = value
    if absent:
        raise Refusal(
            "the environment states no "
            + ", ".join(absent)
            + " — every value this example reads is published by `python scripts/endpoints.py --format env` "
            "for the Modules in the resolved Selection, so a missing one means that Module is "
            "outside COMPOSE_PROFILES. Nothing was connected to"
        )
    if found["OTEL_EXPORTER_OTLP_PROTOCOL"] != PROTOCOL:
        raise Refusal(
            f"OTEL_EXPORTER_OTLP_PROTOCOL is {found['OTEL_EXPORTER_OTLP_PROTOCOL']!r}, and this "
            f"example speaks {PROTOCOL!r} only — the exporters it configures are the HTTP ones, "
            f"so any other value would post protobuf at an endpoint that does not accept it"
        )
    return found


def telemetry_for(endpoint: str, marker: str) -> Telemetry:
    """Configure the three OpenTelemetry providers against the collector, under one marker.

    The marker is set as the `service.name` **resource** attribute rather than as a span or
    log attribute, because that is what the shipped dashboard's three panels select on:
    `{resource.service.name="$service"}` for Tempo and `{service_name="$service"}` for Loki
    and Prometheus. An attribute anywhere else would arrive and match nothing.

    Args:
        endpoint: The OTLP/HTTP base the registry publishes, without a signal path.
        marker: The run's `service.name`.

    Returns:
        The tracer, logger and counter, with the providers that own them.
    """
    resource = Resource.create({"service.name": marker})
    base = endpoint.rstrip("/")

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tracer_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs")))
    set_logger_provider(logger_provider)

    # A short export interval, not the 60s default: the whole run is over in seconds, and a
    # metric still sitting in the reader when the process exits is a metric the arrival check
    # waits its whole budget for. `force_flush` at the end covers the rest.
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
        export_interval_millis=5000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])

    meter = meter_provider.get_meter("devinfra.example")
    return Telemetry(
        tracer=tracer_provider.get_tracer("devinfra.example"),
        logger=logger_provider.get_logger("devinfra.example"),
        counter=meter.create_counter(COUNTER_NAME, unit="1", description="One point per integration per run."),
        tracer_provider=tracer_provider,
        logger_provider=logger_provider,
        meter_provider=meter_provider,
    )


def post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST a form-encoded body and read the JSON answer back.

    Args:
        url: The endpoint to post to.
        fields: Form fields, sent as `application/x-www-form-urlencoded`.

    Returns:
        The decoded JSON object.

    Raises:
        Refusal: If the answer is not a JSON object.
    """
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    # The URL is the registry's own `token_endpoint`/`introspection_endpoint`, read out of the
    # discovery document this run just fetched — never a string this file composed.
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise Refusal(f"{url} answered with {type(body).__name__}, not a JSON object")
    return body


def check_keycloak(config: dict[str, str], marker: str) -> None:
    """Fetch discovery, take a client-credentials token and have the realm introspect it.

    Introspection is the round trip: a token this process merely received is a string, where
    one the realm answers `active: true` for is a token the realm itself minted, signed and
    still recognises. Both endpoints come out of the discovery document rather than being
    built from `OIDC_ISSUER`, so no URL path is written down here either.

    Args:
        config: The contract variables.
        marker: The run's marker, reported on the success line; Keycloak is never sent it.

    Raises:
        Refusal: If discovery names a different issuer, if either endpoint is absent from
            it, or if the realm reports the token inactive.
    """
    with urllib.request.urlopen(config["OIDC_DISCOVERY_URL"], timeout=TIMEOUT) as response:
        discovery = json.loads(response.read().decode("utf-8"))
    if not isinstance(discovery, dict):
        raise Refusal("the discovery document is not a JSON object")
    if discovery.get("issuer") != config["OIDC_ISSUER"]:
        raise Refusal(
            f"the discovery document names issuer {discovery.get('issuer')!r}, and OIDC_ISSUER is "
            f"{config['OIDC_ISSUER']!r} — a client configured from one and validating against the "
            f"other rejects every token the realm mints"
        )
    for key in ("token_endpoint", "introspection_endpoint"):
        if not discovery.get(key):
            raise Refusal(f"the discovery document states no {key}")

    granted = post_form(
        str(discovery["token_endpoint"]),
        {
            "grant_type": "client_credentials",
            "client_id": config["OIDC_CLIENT_ID"],
            "client_secret": config["OIDC_CLIENT_SECRET"],
        },
    )
    token = str(granted.get("access_token", ""))
    if not token:
        raise Refusal(f"the token endpoint returned no access_token: {sorted(granted)}")

    introspected = post_form(
        str(discovery["introspection_endpoint"]),
        {
            "token": token,
            "client_id": config["OIDC_CLIENT_ID"],
            "client_secret": config["OIDC_CLIENT_SECRET"],
        },
    )
    if introspected.get("active") is not True:
        raise Refusal(f"the realm introspected the token as inactive: {introspected!r}")
    sys.stdout.write(f"    token for {introspected.get('client_id', config['OIDC_CLIENT_ID'])}, marker {marker}\n")


def check_postgres(config: dict[str, str], marker: str) -> None:
    """Insert a marker row, read it back through the server, and delete it.

    Args:
        config: The contract variables.
        marker: The run's marker, used as the row's primary key.

    Raises:
        Refusal: If the row does not come back, or comes back as something else.
    """
    with psycopg.connect(config["DATABASE_URL"]) as connection, connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (marker text PRIMARY KEY, seen_at timestamptz NOT NULL)")
        cursor.execute(f"INSERT INTO {TABLE} (marker, seen_at) VALUES (%s, now())", (marker,))
        cursor.execute(f"SELECT marker FROM {TABLE} WHERE marker = %s", (marker,))
        row = cursor.fetchone()
        if row is None or row[0] != marker:
            raise Refusal(f"the row inserted as {marker!r} read back as {row!r}")
        cursor.execute(f"DELETE FROM {TABLE} WHERE marker = %s", (marker,))
        cursor.execute(f"SELECT count(*) FROM {TABLE} WHERE marker = %s", (marker,))
        remaining = cursor.fetchone()
        if remaining is None or remaining[0] != 0:
            raise Refusal(f"the row survived its own delete: {remaining!r}")
    sys.stdout.write(f"    inserted, read back and deleted {marker} in {TABLE}\n")


def check_redis(config: dict[str, str], marker: str) -> None:
    """Set a marker key, read it back and delete it.

    Args:
        config: The contract variables.
        marker: The run's marker, used as the key and as its value.

    Raises:
        Refusal: If the key does not come back, or survives its delete.
    """
    client = redis.from_url(config["REDIS_URL"])
    try:
        key = f"{TABLE}:{marker}"
        # A TTL as well as an explicit delete: a run killed between the set and the delete
        # leaves nothing behind either, which is what makes the re-run criterion hold for a
        # failed run and not only for a clean one.
        client.set(key, marker, ex=300)
        # `get` is declared as returning `bytes | str` because the client can be built with
        # `decode_responses=True`; this one is not, so it answers bytes. Decoding whichever
        # arrives keeps the comparison honest under both spellings rather than asserting one.
        stored = client.get(key)
        readback = stored.decode("utf-8") if isinstance(stored, bytes) else stored
        if readback != marker:
            raise Refusal(f"the key set to {marker!r} read back as {stored!r}")
        client.delete(key)
        if client.exists(key):
            raise Refusal(f"the key {key!r} survived its own delete")
        sys.stdout.write(f"    set, read back and deleted {key}\n")
    finally:
        client.close()


def check_object_storage(config: dict[str, str], marker: str) -> None:
    """List the buckets, then put, get and delete a marker object in the first one.

    The bucket comes from the listing rather than from `MINIO_BUCKETS`: that is a Module-tier
    tunable, not a contract variable, and reading it here would make this file depend on
    something `docs/ENDPOINTS.md` never published.

    Args:
        config: The contract variables.
        marker: The run's marker, used as the object key and as its body.

    Raises:
        Refusal: If the server offers no bucket, or the object does not come back intact.
    """
    client = boto3.client(
        "s3",
        endpoint_url=config["AWS_ENDPOINT_URL"],
        aws_access_key_id=config["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=config["AWS_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
    )
    buckets = [str(entry["Name"]) for entry in client.list_buckets().get("Buckets", [])]
    if not buckets:
        raise Refusal("the S3 endpoint answered with no bucket at all, so there is nowhere to round-trip an object")
    bucket, key = buckets[0], f"{TABLE}/{marker}.txt"
    client.put_object(Bucket=bucket, Key=key, Body=marker.encode("utf-8"))
    try:
        stored = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        if bytes(stored) != marker.encode("utf-8"):
            raise Refusal(f"the object put as {marker!r} read back as {stored!r}")
    finally:
        client.delete_object(Bucket=bucket, Key=key)
    # The delete proved, the way the row count and the key existence prove the other two.
    # `delete_object` answers 204 for a key that was never there as readily as for one it
    # removed, so calling it is not evidence: the listing is. A prefix listing rather than a
    # `head_object`, because S3 answers a deleted key with the same 404 as an endpoint that
    # never held it, and the prefix says which of the two this is.
    remaining = [str(entry["Key"]) for entry in client.list_objects_v2(Bucket=bucket, Prefix=key).get("Contents", [])]
    if remaining:
        raise Refusal(f"the object survived its own delete: {bucket} still lists {remaining}")
    sys.stdout.write(f"    put, read back and deleted {key} in bucket {bucket} of {len(buckets)}\n")


def check_mail(config: dict[str, str], marker: str) -> None:
    """Hand one message carrying the marker to the SMTP server.

    The arrival half is not here: reading it back needs Mailpit's HTTP API port, which is a
    Module-tier name and not a contract variable, so `scripts/example.sh` proves the arrival
    and this proves the handover (ADR 0015's split, one story later).

    Args:
        config: The contract variables.
        marker: The run's marker, carried in the subject.

    Raises:
        Refusal: If `SMTP_PORT` is not a number.
    """
    try:
        port = int(config["SMTP_PORT"])
    except ValueError as exc:
        raise Refusal(f"SMTP_PORT is {config['SMTP_PORT']!r}, which is not a port number: {exc}") from exc
    message = EmailMessage()
    message["Subject"] = f"devinfra worked example {marker}"
    message["From"] = "example@devinfra.local"
    message["To"] = "dev@devinfra.local"
    message.set_content(f"This message carries the marker {marker} so the runner can find it.")
    with smtplib.SMTP(config["SMTP_HOST"], port, timeout=TIMEOUT) as server:
        server.send_message(message)
    sys.stdout.write(f"    handed one message carrying {marker} to the SMTP server\n")


def check_telemetry(telemetry: Telemetry, marker: str) -> None:
    """Flush all three signal providers, so nothing this run emitted is still buffered.

    This is the sixth integration and it is deliberately last: every span, log record and
    counter point the five above produced is still in a batch processor until this returns,
    and the arrival check `scripts/example.sh` runs next would otherwise be racing them.

    Args:
        telemetry: The providers to flush.
        marker: The run's marker, for the diagnostic.

    Raises:
        Refusal: If any provider reports that it could not flush inside its budget.
    """
    unflushed = [
        name
        for name, flushed in (
            ("traces", telemetry.tracer_provider.force_flush()),
            ("logs", telemetry.logger_provider.force_flush()),
            ("metrics", telemetry.meter_provider.force_flush()),
        )
        if not flushed
    ]
    if unflushed:
        raise Refusal(
            f"the {', '.join(unflushed)} provider(s) did not flush, so telemetry for {marker} never "
            f"left this process and no amount of waiting on Grafana would find it"
        )
    sys.stdout.write(f"    flushed traces, logs and metrics for service.name={marker}\n")


def run(config: dict[str, str], telemetry: Telemetry, marker: str) -> int:
    """Run every integration, each in its own span, and report one line each.

    Every integration runs even when an earlier one failed: a developer whose Redis
    container died wants to know that the other five still work, not just the first failure.

    Args:
        config: The contract variables.
        telemetry: The tracer, logger and counter.
        marker: The run's marker.

    Returns:
        Process exit status: 0 only when every integration passed.
    """
    integrations: list[tuple[str, Callable[[], None]]] = [
        ("keycloak", lambda: check_keycloak(config, marker)),
        ("postgres", lambda: check_postgres(config, marker)),
        ("redis", lambda: check_redis(config, marker)),
        ("object-storage", lambda: check_object_storage(config, marker)),
        ("mailpit", lambda: check_mail(config, marker)),
        ("telemetry", lambda: check_telemetry(telemetry, marker)),
    ]
    failed: list[str] = []
    for name, check in integrations:
        with telemetry.tracer.start_as_current_span(f"example.{name}") as span:
            span.set_attribute("devinfra.integration", name)
            try:
                check()
            # Broad on purpose, and never swallowed: every client here raises its own
            # hierarchy — psycopg.Error, redis.RedisError, botocore.exceptions.ClientError,
            # smtplib.SMTPException, urllib.error.URLError — and naming all five would still
            # let a sixth kind of failure escape as a traceback that abandons the remaining
            # integrations. The error is reported with the client's own words and the run
            # exits non-zero (NFR-5).
            except Exception as exc:
                failed.append(name)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                telemetry.logger.emit(
                    body=f"{name}: FAIL {exc}",
                    severity_number=SeverityNumber.ERROR,
                    severity_text="ERROR",
                    attributes={"devinfra.integration": name, "devinfra.marker": marker},
                )
                telemetry.counter.add(1, {"integration": name, "outcome": "fail"})
                sys.stdout.write(f"{name}: FAIL {type(exc).__name__}: {exc}\n")
                continue
            telemetry.logger.emit(
                body=f"{name}: OK",
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                attributes={"devinfra.integration": name, "devinfra.marker": marker},
            )
            telemetry.counter.add(1, {"integration": name, "outcome": "ok"})
            sys.stdout.write(f"{name}: OK\n")
    if failed:
        sys.stderr.write(f"example: {len(failed)} integration(s) failed: {', '.join(failed)}\n")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Describe the command line.

    Returns:
        The parser, with its one default stated.
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Exercise every integration the x-app-variables: registry publishes a contract for.",
    )
    parser.add_argument(
        "--service-name",
        default=None,
        help=(
            "the run's service.name and marker, which the runner then searches Mailpit and "
            "Grafana for. Defaults to a generated devinfra-example-<8 hex>"
        ),
    )
    return parser


def main(argv: list[str]) -> int:
    """Read the contract, emit under one marker and round-trip every integration.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        Process exit status: 0 only when every integration passed.
    """
    args = build_parser().parse_args(argv)
    marker = args.service_name or f"devinfra-example-{secrets.token_hex(4)}"
    try:
        config = settings()
    except Refusal as exc:
        sys.stderr.write(f"example: {exc}\n")
        return 1
    # Printed before anything is attempted, so a run that dies half way still tells whoever
    # is reading Grafana which service.name to look under.
    sys.stdout.write(f"service.name={marker}\n")
    telemetry = telemetry_for(config["OTEL_EXPORTER_OTLP_ENDPOINT"], marker)
    try:
        return run(config, telemetry, marker)
    finally:
        # Shutdown after the verdict, never instead of it: `check_telemetry` already flushed,
        # and this is what closes the exporters' sessions so the process can exit promptly.
        telemetry.tracer_provider.shutdown()
        telemetry.logger_provider.shutdown()
        telemetry.meter_provider.shutdown()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

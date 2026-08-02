# Copyright (c) 2026 John Carter. All rights reserved.
"""
Shared integration test fixtures.

Integration tests run against DynamoDB Local (docker). Start it before
running — ``uv run inv dynamo-start`` is the supported route, since it
honours ``CHANNEL_DYNAMO_PORT`` and names the container accordingly.
The equivalent by hand::

    docker run -p 8000:8000 amazon/dynamodb-local:latest

Note the container **always** listens on 8000 internally, so only the
host side of that mapping varies: a private instance on port 8123 is
``-p 8123:8000``, never ``-p 8123:8123`` (which would bind nothing).
Point the suite at it with ``CHANNEL_DYNAMO_PORT=8123``.

Every pytest session provisions its **own** table and drops it at
session end — see the per-run isolation block below (#466).
"""

from __future__ import annotations

import contextlib
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import pytest

from channel._table_schema import provision
from tests.integration._helpers import (
    RUN_TABLE_PREFIX,
    FakeS3,
    is_abandoned_run_table,
    is_run_table_name,
)

# Hosts that are safe targets for the destructive drop+recreate in
# starter_table. Anything else (including hostnames that merely contain
# "localhost" as a substring, e.g. ``localhost.evil.com``) must be
# rejected — the substring form is a footgun, urlparse().hostname is
# the only correct check.
_SAFE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "dynamodb-local"}

# ── Per-run table isolation (#466) ────────────────────────────────────────────
#
# Parallel issue-workers share one DynamoDB Local container, and every run
# used to provision the *same* table name. ``starter_table`` drops and
# recreates that table at session start, so a second run beginning midway
# through a first one deleted the table out from under it — surfacing as
# ``ResourceNotFoundException`` / "Cannot do operations on a non-existent
# table" in tests with no relationship to the diff under test. Isolating
# per run beats serialising behind a lock: parallel workers keep running
# in parallel.
#
# Two consequences worth stating explicitly:
#   * The name is **forced**, not ``setdefault``-ed. An inherited
#     ``CHANNEL_TABLE_NAME`` (CI's ``channel-integration``, and the same
#     value from ``inv test-combined-coverage`` before #466) is precisely
#     the shared state being removed, so honouring it would reinstate the
#     bug. ``CHANNEL_INTEGRATION_TABLE_NAME`` pins a fixed name for
#     debugging a single run — never set it when runs may overlap.
#   * The table is always freshly provisioned from
#     ``channel._table_schema``, so a long-lived container can no longer
#     serve a table whose schema predates a GSI addition (one was found
#     13 days old, missing ``RefreshByUserIndex`` from #290).
#
# The naming constants and the sweep predicate live in ``_helpers`` so
# they can be unit-tested — see ``tests/unit/test_integration_isolation.py``.
RUN_TABLE_NAME = os.environ.get("CHANNEL_INTEGRATION_TABLE_NAME") or (
    f"{RUN_TABLE_PREFIX}-{uuid4().hex[:8]}"
)


def _default_endpoint() -> str:
    """Default ``DYNAMODB_ENDPOINT``, honouring ``CHANNEL_DYNAMO_PORT``.

    Only consulted under a bare ``pytest`` invocation — every ``inv``
    entry point passes ``DYNAMODB_ENDPOINT`` explicitly, having already
    validated the port in ``tasks._resolve_dynamo_port``. This mirrors
    that validation (rather than falling back to 8000 on a typo) for the
    same reason it exists there: a silent fallback would point the run
    at the *shared* container while the caller believes they are on a
    private one.
    """
    port = os.environ.get("CHANNEL_DYNAMO_PORT", "").strip()
    if not port:
        return "http://localhost:8000"
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f"CHANNEL_DYNAMO_PORT must be a TCP port number, got {port!r}")
    return f"http://localhost:{port}"


# Override AWS creds for DynamoDB Local
os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
# NOT ``setdefault(..., _default_endpoint())`` — Python evaluates call
# arguments eagerly, so that form runs the port validation even when
# DYNAMODB_ENDPOINT is already set, letting a malformed
# CHANNEL_DYNAMO_PORT fail an ``inv`` run that never consults the port
# at all. Guard the call instead, so the docstring's "only consulted
# under a bare pytest invocation" is literally true.
if "DYNAMODB_ENDPOINT" not in os.environ:
    os.environ["DYNAMODB_ENDPOINT"] = _default_endpoint()
os.environ.setdefault("CHANNEL_JWT_SECRET", "integration-test-secret")
os.environ["CHANNEL_TABLE_NAME"] = RUN_TABLE_NAME


@pytest.fixture(scope="session")
def table_name() -> str:
    return os.environ["CHANNEL_TABLE_NAME"]


@pytest.fixture(scope="session")
def dynamodb_resource() -> Any:
    """Session-scoped DynamoDB resource pointed at DynamoDB Local."""
    return boto3.resource(
        "dynamodb",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=os.environ["DYNAMODB_ENDPOINT"],
    )


def _drop_table(dynamodb_resource: Any, name: str) -> None:
    """Delete ``name`` and wait for it to go away; no-op if absent."""
    table = dynamodb_resource.Table(name)
    try:
        table.delete()
    except dynamodb_resource.meta.client.exceptions.ResourceNotFoundException:
        return
    table.wait_until_not_exists()


def _sweep_abandoned_run_tables(dynamodb_resource: Any, current_table: str) -> None:
    """Drop per-run tables left behind by sessions that never unwound.

    Eligibility is decided by :func:`is_abandoned_run_table` (name shape
    + age), which is unit-tested against the real names this container
    holds. ``current_table`` is skipped unconditionally: with
    ``CHANNEL_INTEGRATION_TABLE_NAME`` pinned to a hex-shaped name whose
    table is already stale, sweeping it here would race the
    ``_drop_table`` call that immediately follows — that one tolerates a
    missing table, but not a ``ResourceInUseException`` from a table
    already in ``DELETING``.

    Entirely best-effort otherwise: a peer sweeping the same table
    concurrently must not fail anyone's suite.
    """
    now = datetime.now(timezone.utc)
    for table in dynamodb_resource.tables.all():
        # Gate on the name first. ``ListTables`` already supplied it,
        # whereas ``creation_date_time`` lazily costs a ``DescribeTable``
        # — and Python evaluates call arguments eagerly, so folding this
        # into the full predicate would describe every table in a shared
        # container instead of only the candidates.
        if table.name == current_table or not is_run_table_name(table.name):
            continue
        with contextlib.suppress(Exception):
            if is_abandoned_run_table(table.name, table.creation_date_time, now):
                table.delete()


@pytest.fixture(scope="session")
def starter_table(dynamodb_resource: Any, table_name: str) -> Any:
    """Provision the StarterTable in DynamoDB Local for the test session.

    Mirrors the schema declared in :mod:`infra.stacks.channel_stack` —
    ``PK`` / ``SK`` partition + sort, six GSIs (the ``GSI{1..5}PK`` /
    ``GSI{1..3,5}SK`` slots plus the ``owner_pk`` / ``owner_sk``
    AssetOwnerIndex from #324), and the ``ttl`` attribute used for TTL
    sweeps. CDK is not invoked because DynamoDB Local doesn't run
    CloudFormation; this fixture is the test-environment analogue.

    The table name is unique per session (#466), so this run owns it
    outright: it is created here and dropped at session end, and no
    concurrent run can touch it. The pre-create drop still runs because
    ``CHANNEL_INTEGRATION_TABLE_NAME`` can pin a fixed name. No
    table-row cleanup between individual tests — tests use unique state
    strings to avoid cross-pollution (same pattern as the e2e suite).

    **Safety guard**: ``DYNAMODB_ENDPOINT`` at the top of this file is
    set via ``setdefault()``, which means a developer or CI environment
    that already has it set could point this fixture at a real DynamoDB
    endpoint, where the destructive drop steps below would delete an
    externally-managed table. We refuse to proceed unless the parsed
    ``DYNAMODB_ENDPOINT`` hostname is in :data:`_SAFE_LOCAL_HOSTS` —
    the suite is DynamoDB-Local-only by design. Parsing with
    ``urlparse`` (rather than substring matching) blocks bypasses like
    ``http://localhost.evil.com``.
    """
    endpoint = os.environ.get("DYNAMODB_ENDPOINT", "")
    hostname = urlparse(endpoint).hostname
    if hostname not in _SAFE_LOCAL_HOSTS:
        raise RuntimeError(
            f"Refusing to provision integration test table against non-local DynamoDB "
            f"endpoint {endpoint!r} (parsed hostname: {hostname!r}). This fixture "
            f"performs a destructive drop+recreate; set DYNAMODB_ENDPOINT to "
            f"http://localhost:8000 (or a DynamoDB Local URL whose hostname is one of "
            f"{sorted(_SAFE_LOCAL_HOSTS)}) before running the integration suite."
        )

    _sweep_abandoned_run_tables(dynamodb_resource, table_name)
    _drop_table(dynamodb_resource, table_name)

    provision(dynamodb_resource.meta.client, table_name)
    table = dynamodb_resource.Table(table_name)

    try:
        yield table
    finally:
        # Best-effort: a failed teardown leaks one table into the
        # container, which the next session's sweep collects. It must
        # never turn a green suite red.
        with contextlib.suppress(Exception):
            _drop_table(dynamodb_resource, table_name)


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    """S3 stub patched over ``storage._get_s3_client`` — one canonical
    fixture for the attachment-cascade, asset-storage, and asset-API
    suites (previously three identical per-file copies)."""
    fake = FakeS3()
    monkeypatch.setattr("channel.storage._get_s3_client", lambda: fake)
    return fake

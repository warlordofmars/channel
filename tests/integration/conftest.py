# Copyright (c) 2026 John Carter. All rights reserved.
"""
Shared integration test fixtures.

Integration tests run against DynamoDB Local (docker).
Start it before running: docker run -p 8000:8000 amazon/dynamodb-local:latest
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import boto3
import pytest

from channel._table_schema import provision
from tests.integration._helpers import FakeS3

# Hosts that are safe targets for the destructive drop+recreate in
# starter_table. Anything else (including hostnames that merely contain
# "localhost" as a substring, e.g. ``localhost.evil.com``) must be
# rejected — the substring form is a footgun, urlparse().hostname is
# the only correct check.
_SAFE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "dynamodb-local"}

# Override AWS creds for DynamoDB Local
os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("CHANNEL_JWT_SECRET", "integration-test-secret")
os.environ.setdefault("CHANNEL_TABLE_NAME", "channel-test")


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


@pytest.fixture(scope="session")
def starter_table(dynamodb_resource: Any, table_name: str) -> Any:
    """Provision the StarterTable in DynamoDB Local for the test session.

    Mirrors the schema declared in :mod:`infra.stacks.channel_stack` —
    ``PK`` / ``SK`` partition + sort, five GSIs (the ``GSI{1..4}PK`` /
    ``GSI{1..2}SK`` slots plus the ``owner_pk`` / ``owner_sk``
    AssetOwnerIndex from #324), and the ``ttl`` attribute used for TTL
    sweeps. CDK is not invoked because DynamoDB Local doesn't run
    CloudFormation; this fixture is the test-environment analogue.

    Drops and recreates the table if it already exists from a previous
    run so the suite starts clean each session. No table-row cleanup
    between individual tests — tests use unique state strings to avoid
    cross-pollution (same pattern as the e2e suite).

    **Safety guard**: the integration env vars at the top of this file
    are set via ``setdefault()``, which means a developer or CI
    environment that already has ``CHANNEL_TABLE_NAME`` /
    ``DYNAMODB_ENDPOINT`` set could point this fixture at a real
    DynamoDB endpoint and the destructive drop step below would delete
    an externally-managed table. We refuse to proceed unless the
    parsed ``DYNAMODB_ENDPOINT`` hostname is in
    :data:`_SAFE_LOCAL_HOSTS` — the suite is DynamoDB-Local-only by
    design. Parsing with ``urlparse`` (rather than substring matching)
    blocks bypasses like ``http://localhost.evil.com``.
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

    existing = {t.name for t in dynamodb_resource.tables.all()}
    if table_name in existing:
        dynamodb_resource.Table(table_name).delete()
        dynamodb_resource.Table(table_name).wait_until_not_exists()

    provision(dynamodb_resource.meta.client, table_name)
    table = dynamodb_resource.Table(table_name)

    yield table


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    """S3 stub patched over ``storage._get_s3_client`` — one canonical
    fixture for the attachment-cascade, asset-storage, and asset-API
    suites (previously three identical per-file copies)."""
    fake = FakeS3()
    monkeypatch.setattr("channel.storage._get_s3_client", lambda: fake)
    return fake

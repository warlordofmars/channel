# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for :mod:`starter.auth.state_store`.

These tests cover the four required cases from issue #23:

- happy path (put + consume returns the payload)
- missing key (consume returns None on ConditionalCheckFailed)
- expired item (consume returns None on app-level expiry check)
- atomic-consume race (one consumer wins, the other gets None)

DynamoDB calls are mocked at the ``_get_table()`` helper boundary so
these tests run without any external service. Integration tests in
``tests/integration/test_auth_state_store.py`` cover the same surface
against DynamoDB Local.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from channel.auth import state_store


@pytest.fixture
def fake_table():
    """Patch ``state_store._get_table`` to return a MagicMock surface.

    The yielded mock is the Table object — call ``.put_item.assert_*`` /
    ``.delete_item.return_value = ...`` on it from the test body.
    """
    table = MagicMock(name="StarterTable")
    with patch.object(state_store, "_get_table", return_value=table):
        yield table


class _LogCapture:
    """Context manager that captures records from the state_store logger.

    The project's structured logger sets ``propagate=False`` on the
    ``starter`` tree so :data:`pytest`'s ``caplog`` fixture cannot see
    the records via the root logger — mirror the pattern from
    ``tests/integration/test_startup.py``.

    On enter: pins the logger level to DEBUG (so INFO records always
    emit regardless of test order — without that pin, an earlier test
    that triggered ``configure_logging`` could leave the level at INFO
    or WARNING and silently drop the records this test relies on) and
    attaches a list-backed handler.

    On exit: removes the handler **and** restores the logger's prior
    level so this fixture never leaks global logger state into
    unrelated tests.

    Usage::

        with _LogCapture() as cap:
            do_something_that_logs()
        assert any("not present" in r.getMessage() for r in cap.records)
    """

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self.logger = logging.getLogger("channel.auth.state_store")
        self._handler: logging.Handler | None = None
        self._previous_level: int | None = None

    def __enter__(self) -> _LogCapture:
        captured = self.records

        class _ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        self._previous_level = self.logger.level
        self._handler = _ListHandler(level=logging.DEBUG)
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self._handler)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._handler is not None:
            self.logger.removeHandler(self._handler)
            self._handler = None
        if self._previous_level is not None:
            self.logger.setLevel(self._previous_level)
            self._previous_level = None


# ── put_state ────────────────────────────────────────────────────────────────


def test_put_state_writes_expected_pk_sk_and_ttl(fake_table):
    """Item must use the MGMT_STATE#{state} / META key shape with TTL."""
    state_store.put_state("abc123", ttl_seconds=600)

    fake_table.put_item.assert_called_once()
    item = fake_table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "MGMT_STATE#abc123"
    assert item["SK"] == "META"
    # ttl is an absolute Unix timestamp — must be an int (DynamoDB's TTL
    # service silently ignores non-integer values).
    assert isinstance(item["ttl"], int)
    # ttl_seconds is the operator-supplied window; ttl is created_at + ttl_seconds.
    assert item["ttl"] == item["created_at"] + 600
    assert item["ttl_seconds"] == 600


def test_put_state_default_ttl_is_600_seconds(fake_table):
    state_store.put_state("xyz")
    item = fake_table.put_item.call_args.kwargs["Item"]
    assert item["ttl_seconds"] == 600


def test_put_state_includes_caller_payload(fake_table):
    state_store.put_state("xyz", payload={"nonce": "n1", "redirect_uri": "/cb"})
    item = fake_table.put_item.call_args.kwargs["Item"]
    assert item["nonce"] == "n1"
    assert item["redirect_uri"] == "/cb"


def test_put_state_payload_cannot_overwrite_reserved_keys(fake_table):
    """Reserved keys (PK / SK / created_at / ttl / ttl_seconds) must win."""
    state_store.put_state(
        "abc",
        payload={
            "PK": "EVIL#hijack",
            "SK": "EVIL",
            "ttl": 1,
            "ttl_seconds": 9999,
            "created_at": 0,
            "nonce": "n1",
        },
        ttl_seconds=300,
    )
    item = fake_table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "MGMT_STATE#abc"
    assert item["SK"] == "META"
    assert item["ttl_seconds"] == 300
    assert item["nonce"] == "n1"  # non-reserved attributes still come through


# ── consume_state — happy path ───────────────────────────────────────────────


def test_consume_state_returns_payload_when_present_and_unexpired(fake_table):
    now = int(time.time())
    fake_table.delete_item.return_value = {
        "Attributes": {
            "PK": "MGMT_STATE#abc",
            "SK": "META",
            "created_at": now,
            "ttl_seconds": 600,
            "ttl": now + 600,
            "nonce": "n1",
        }
    }

    result = state_store.consume_state("abc")

    assert result is not None
    assert result["nonce"] == "n1"
    # Verify the delete was conditional on presence + asked for the old image.
    call = fake_table.delete_item.call_args.kwargs
    assert call["Key"] == {"PK": "MGMT_STATE#abc", "SK": "META"}
    assert call["ConditionExpression"] == "attribute_exists(PK)"
    assert call["ReturnValues"] == "ALL_OLD"


# ── consume_state — missing key ──────────────────────────────────────────────


def test_consume_state_returns_none_on_conditional_check_failed(fake_table):
    """Replay or already-consumed → ConditionalCheckFailedException → None."""
    fake_table.delete_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "The conditional request failed",
            }
        },
        operation_name="DeleteItem",
    )

    with _LogCapture() as cap:
        result = state_store.consume_state("never-existed")

    assert result is None
    info_lines = [r for r in cap.records if r.levelno == logging.INFO]
    assert any("not present" in r.getMessage() for r in info_lines), (
        f"expected an INFO log mentioning 'not present', got: "
        f"{[r.getMessage() for r in info_lines]}"
    )


def test_consume_state_propagates_unexpected_client_error(fake_table):
    """Non-ConditionalCheckFailed errors must surface, not return None."""
    fake_table.delete_item.side_effect = ClientError(
        error_response={"Error": {"Code": "ProvisionedThroughputExceededException"}},
        operation_name="DeleteItem",
    )

    with pytest.raises(ClientError):
        state_store.consume_state("abc")


def test_consume_state_returns_none_on_validation_exception(fake_table):
    """Malformed user-supplied state → ValidationException → None.

    State is taken from a query parameter on ``/auth/callback`` and is
    therefore attacker-controllable. DynamoDB rejects malformed/oversized
    keys with ``ValidationException``; treating it as "not present" gives
    the user the same 400 they'd get for an unknown state (correct
    contract for a bad input), instead of bubbling a 500.

    The log line MUST include the error code (so operators can
    distinguish this from genuine missing-state in metrics) but MUST NOT
    include the raw state value (user-controlled, log-injection risk).
    """
    raw_state = "evil\nstate-with-injected-newline"
    fake_table.delete_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ValidationException",
                "Message": "The provided key element does not match the schema",
            }
        },
        operation_name="DeleteItem",
    )

    with _LogCapture() as cap:
        result = state_store.consume_state(raw_state)

    assert result is None

    info_lines = [r for r in cap.records if r.levelno == logging.INFO]
    assert any("rejected by DynamoDB validation" in r.getMessage() for r in info_lines), (
        f"expected an INFO log mentioning 'rejected by DynamoDB validation', "
        f"got: {[r.getMessage() for r in info_lines]}"
    )

    # Error code must surface in the record (via extra={...} on the
    # LogRecord) so operators can distinguish this case in log analysis.
    assert any(getattr(r, "error_code", None) == "ValidationException" for r in info_lines), (
        "expected the ValidationException error code on the LogRecord"
    )

    # Raw state value MUST NOT appear anywhere on the record — neither
    # in the formatted message nor as an extra attribute. State is
    # user-controlled and logging it risks log injection (the value
    # above includes a newline expressly to detect that mistake).
    for record in info_lines:
        formatted = record.getMessage()
        assert raw_state not in formatted, f"raw state value leaked into log message: {formatted!r}"
        for attr_value in vars(record).values():
            if isinstance(attr_value, str):
                assert raw_state not in attr_value, (
                    f"raw state value leaked into log record attribute: {attr_value!r}"
                )


def test_consume_state_returns_none_when_attributes_missing(fake_table):
    """Defensive: ALL_OLD with no Attributes key → treat as not present."""
    fake_table.delete_item.return_value = {}  # no Attributes key

    with _LogCapture() as cap:
        result = state_store.consume_state("abc")

    assert result is None
    info_lines = [r for r in cap.records if r.levelno == logging.INFO]
    assert any("no attributes" in r.getMessage() for r in info_lines)


# ── consume_state — expired item ─────────────────────────────────────────────


def test_consume_state_returns_none_when_expired(fake_table):
    """Delete succeeds but app-level expiry fails → None + 'expired' log."""
    long_ago = int(time.time()) - 3600  # one hour ago
    fake_table.delete_item.return_value = {
        "Attributes": {
            "PK": "MGMT_STATE#stale",
            "SK": "META",
            "created_at": long_ago,
            "ttl_seconds": 600,  # 10-minute window expired ~50 minutes ago
            "ttl": long_ago + 600,
        }
    }

    with _LogCapture() as cap:
        result = state_store.consume_state("stale")

    assert result is None
    info_lines = [r for r in cap.records if r.levelno == logging.INFO]
    assert any("expired" in r.getMessage() for r in info_lines), (
        f"expected an INFO log mentioning 'expired', got: {[r.getMessage() for r in info_lines]}"
    )


def test_consume_state_returns_none_when_ttl_seconds_missing_and_default_expires(fake_table):
    """If ttl_seconds is absent the default (600s) is applied — and an old
    created_at means the row is treated as expired."""
    long_ago = int(time.time()) - 3600
    fake_table.delete_item.return_value = {
        "Attributes": {
            "PK": "MGMT_STATE#x",
            "SK": "META",
            "created_at": long_ago,
            # no ttl_seconds attribute
        }
    }
    assert state_store.consume_state("x") is None


# ── atomic-consume race ──────────────────────────────────────────────────────


def test_consume_state_atomic_race_one_winner_one_loser(fake_table):
    """Two concurrent consumes: first wins (returns payload), second gets None.

    Modelled by configuring the mock to return the payload on the first
    call and raise ConditionalCheckFailedException on the second.
    """
    now = int(time.time())
    fake_table.delete_item.side_effect = [
        {
            "Attributes": {
                "PK": "MGMT_STATE#race",
                "SK": "META",
                "created_at": now,
                "ttl_seconds": 600,
                "ttl": now + 600,
                "nonce": "n1",
            }
        },
        ClientError(
            error_response={"Error": {"Code": "ConditionalCheckFailedException"}},
            operation_name="DeleteItem",
        ),
    ]

    first = state_store.consume_state("race")
    second = state_store.consume_state("race")

    assert first is not None
    assert first["nonce"] == "n1"
    assert second is None


# ── _get_table ───────────────────────────────────────────────────────────────


def test_get_table_uses_env_vars(monkeypatch):
    """CHANNEL_TABLE_NAME / DYNAMODB_ENDPOINT must be read at call time."""
    monkeypatch.setenv("CHANNEL_TABLE_NAME", "test-table-from-env")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "http://localhost:9999")
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    captured: dict[str, Any] = {}

    class _FakeResource:
        def Table(self, name: str) -> str:
            captured["table_name"] = name
            return f"table:{name}"

    def _fake_resource(service: str, **kwargs: Any) -> _FakeResource:
        captured["service"] = service
        captured["kwargs"] = kwargs
        return _FakeResource()

    with patch.object(state_store.boto3, "resource", side_effect=_fake_resource):
        result = state_store._get_table()

    assert result == "table:test-table-from-env"
    assert captured["service"] == "dynamodb"
    assert captured["kwargs"]["region_name"] == "us-west-2"
    assert captured["kwargs"]["endpoint_url"] == "http://localhost:9999"
    assert captured["table_name"] == "test-table-from-env"


def test_get_table_uses_defaults_when_env_unset(monkeypatch):
    """No region env var set → region_name not passed; boto3 uses ~/.aws/config."""
    monkeypatch.delenv("CHANNEL_TABLE_NAME", raising=False)
    monkeypatch.delenv("DYNAMODB_ENDPOINT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    captured: dict[str, Any] = {}

    class _FakeResource:
        def Table(self, name: str) -> str:
            captured["table_name"] = name
            return name

    def _fake_resource(service: str, **kwargs: Any) -> _FakeResource:
        captured["kwargs"] = kwargs
        return _FakeResource()

    with patch.object(state_store.boto3, "resource", side_effect=_fake_resource):
        state_store._get_table()

    assert captured["table_name"] == "channel-dev"
    assert "region_name" not in captured["kwargs"]
    assert captured["kwargs"]["endpoint_url"] is None


def test_get_table_falls_back_to_aws_default_region(monkeypatch):
    """``AWS_DEFAULT_REGION`` is honoured when ``AWS_REGION`` is unset."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("CHANNEL_TABLE_NAME", "tbl")
    monkeypatch.delenv("DYNAMODB_ENDPOINT", raising=False)

    captured: dict[str, Any] = {}

    class _FakeResource:
        def Table(self, name: str) -> str:
            return name

    def _fake_resource(service: str, **kwargs: Any) -> _FakeResource:
        captured["kwargs"] = kwargs
        return _FakeResource()

    with patch.object(state_store.boto3, "resource", side_effect=_fake_resource):
        state_store._get_table()

    assert captured["kwargs"]["region_name"] == "eu-west-1"

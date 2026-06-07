# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for CloudWatch EMF metrics helpers."""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from channel.metrics import NAMESPACE, emit_metric, record_memory_write_outcome


def test_namespace_is_correct():
    assert NAMESPACE == "Channel"


@pytest.mark.asyncio
async def test_emit_metric_does_not_raise():
    """emit_metric should not raise in a non-Lambda environment (writes to stdout)."""
    await emit_metric("TestMetric", value=1.0)


@pytest.mark.asyncio
async def test_emit_metric_with_dimensions():
    await emit_metric("TestMetric", operation="test", environment="unit")


@pytest.mark.asyncio
async def test_record_memory_write_outcome_success_emits_success_counter():
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_write_outcome(success=True)

    mock_emit.assert_awaited_once_with("MemoryWriteSuccesses")


@pytest.mark.asyncio
async def test_record_memory_write_outcome_failure_emits_failure_counter():
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_write_outcome(success=False)

    mock_emit.assert_awaited_once_with("MemoryWriteFailures")


def test_record_memory_write_outcome_signature_locks_out_dimensions():
    """Per spec Risk #3: cardinality blowup. Signature must accept only
    ``success`` — no kwargs path for a future caller to slip an
    ``actor_id`` dimension through."""
    sig = inspect.signature(record_memory_write_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    # ``from __future__ import annotations`` stringifies annotations,
    # so we compare to the string ``"bool"``.
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_recall_outcome_success_emits_success_counter():
    from channel.metrics import record_recall_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_recall_outcome(success=True)
    mock_emit.assert_awaited_once_with("RecallSuccesses")


@pytest.mark.asyncio
async def test_record_recall_outcome_failure_emits_failure_counter():
    from channel.metrics import record_recall_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_recall_outcome(success=False)
    mock_emit.assert_awaited_once_with("RecallFailures")


def test_record_recall_outcome_signature_locks_out_dimensions():
    """Same cardinality guard as record_memory_write_outcome."""
    from channel.metrics import record_recall_outcome

    sig = inspect.signature(record_recall_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation == "bool"


@pytest.mark.asyncio
async def test_record_auto_title_outcome_success_emits_success_counter():
    from channel.metrics import record_auto_title_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_auto_title_outcome(success=True)
    mock_emit.assert_awaited_once_with("AutoTitleSuccesses")


@pytest.mark.asyncio
async def test_record_auto_title_outcome_failure_emits_failure_counter():
    from channel.metrics import record_auto_title_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_auto_title_outcome(success=False)
    mock_emit.assert_awaited_once_with("AutoTitleFailures")


def test_record_auto_title_outcome_signature_locks_out_dimensions():
    """Same cardinality guard."""
    from channel.metrics import record_auto_title_outcome

    sig = inspect.signature(record_auto_title_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation == "bool"


@pytest.mark.asyncio
async def test_record_chat_delete_memory_wipe_outcome_success_emits_success_counter():
    from channel.metrics import record_chat_delete_memory_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_memory_wipe_outcome(success=True)
    mock_emit.assert_awaited_once_with("ChatDeleteMemoryWipeSuccesses")


@pytest.mark.asyncio
async def test_record_chat_delete_memory_wipe_outcome_failure_emits_failure_counter():
    from channel.metrics import record_chat_delete_memory_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_memory_wipe_outcome(success=False)
    mock_emit.assert_awaited_once_with("ChatDeleteMemoryWipeFailures")


def test_record_chat_delete_memory_wipe_outcome_signature_locks_out_dimensions():
    """The function MUST NOT accept actor_id / chat_id / user_id args.
    Per-actor/-chat dimensions blow up CloudWatch metric cardinality.
    """
    import inspect

    from channel.metrics import record_chat_delete_memory_wipe_outcome

    sig = inspect.signature(record_chat_delete_memory_wipe_outcome)
    param_names = set(sig.parameters)
    forbidden = {"actor_id", "chat_id", "user_id"}
    leaked = param_names & forbidden
    assert not leaked, (
        f"record_chat_delete_memory_wipe_outcome must NOT accept {leaked} "
        "— per-actor/chat dimensions cause CloudWatch cardinality blowup"
    )


@pytest.mark.asyncio
async def test_record_followup_outcome_success_emits_success_counter():
    from channel.metrics import record_followup_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_followup_outcome(success=True)
    mock_emit.assert_awaited_once_with("FollowupGenSuccesses")


@pytest.mark.asyncio
async def test_record_followup_outcome_failure_emits_failure_counter():
    from channel.metrics import record_followup_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_followup_outcome(success=False)
    mock_emit.assert_awaited_once_with("FollowupGenFailures")


def test_record_followup_outcome_signature_locks_out_dimensions():
    """Same cardinality guard as the other counter helpers."""
    import inspect

    from channel.metrics import record_followup_outcome

    sig = inspect.signature(record_followup_outcome)
    assert list(sig.parameters.keys()) == ["success"]


# ----------------------------------------------------------------
# ChatDeleteAttachmentWipe (#174) — file attachments + vision (#109)
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_chat_delete_attachment_wipe_outcome_success_emits_success_counter():
    from channel.metrics import record_chat_delete_attachment_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_attachment_wipe_outcome(success=True)
    mock_emit.assert_awaited_once_with("ChatDeleteAttachmentWipeSuccesses")


@pytest.mark.asyncio
async def test_record_chat_delete_attachment_wipe_outcome_failure_emits_failure_counter():
    from channel.metrics import record_chat_delete_attachment_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_attachment_wipe_outcome(success=False)
    mock_emit.assert_awaited_once_with("ChatDeleteAttachmentWipeFailures")


def test_record_chat_delete_attachment_wipe_outcome_signature_locks_out_dimensions():
    """Per-actor / per-chat / per-attachment-id dimensions blow up
    CloudWatch cardinality — codify the locked signature."""

    from channel.metrics import record_chat_delete_attachment_wipe_outcome

    sig = inspect.signature(record_chat_delete_attachment_wipe_outcome)
    param_names = set(sig.parameters)
    forbidden = {"actor_id", "chat_id", "user_id", "attachment_id"}
    leaked = param_names & forbidden
    assert not leaked, (
        f"record_chat_delete_attachment_wipe_outcome must NOT accept {leaked} "
        "— per-actor/chat dimensions cause CloudWatch cardinality blowup"
    )
    assert list(sig.parameters.keys()) == ["success"]


# ----------------------------------------------------------------
# ToolCall counters (#181 / epic #128) — tool-use chassis
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_tool_call_outcome_success_emits_success_counter():
    from channel.metrics import record_tool_call_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_tool_call_outcome(success=True)
    mock_emit.assert_awaited_once_with("ToolCallSuccesses")


@pytest.mark.asyncio
async def test_record_tool_call_outcome_failure_emits_failure_counter():
    from channel.metrics import record_tool_call_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_tool_call_outcome(success=False)
    mock_emit.assert_awaited_once_with("ToolCallFailures")


def test_record_tool_call_outcome_signature_locks_out_dimensions():
    """The signature must reject per-tool / per-actor dimensions to keep
    CloudWatch metric cardinality bounded."""
    from channel.metrics import record_tool_call_outcome

    sig = inspect.signature(record_tool_call_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation == "bool"

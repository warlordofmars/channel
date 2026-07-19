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


# ----------------------------------------------------------------
# Memory TOOL counters (#400) — agent-driven remember/recall, kept
# distinct from the hook counters above so hook health stays isolated.
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_memory_tool_write_outcome_success_emits_success_counter():
    from channel.metrics import record_memory_tool_write_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_tool_write_outcome(success=True)
    mock_emit.assert_awaited_once_with("MemoryToolWriteSuccesses")


@pytest.mark.asyncio
async def test_record_memory_tool_write_outcome_failure_emits_failure_counter():
    from channel.metrics import record_memory_tool_write_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_tool_write_outcome(success=False)
    mock_emit.assert_awaited_once_with("MemoryToolWriteFailures")


def test_record_memory_tool_write_outcome_is_distinct_from_hook_counter():
    """#400: the tool emitter must NOT reuse the hook metric names, or the
    dashboard would re-conflate hook and tool writes."""
    from channel.metrics import record_memory_tool_write_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        import asyncio

        asyncio.run(record_memory_tool_write_outcome(success=True))
        asyncio.run(record_memory_tool_write_outcome(success=False))
    emitted = {call.args[0] for call in mock_emit.await_args_list}
    assert emitted == {"MemoryToolWriteSuccesses", "MemoryToolWriteFailures"}
    assert "MemoryWriteSuccesses" not in emitted
    assert "MemoryWriteFailures" not in emitted


def test_record_memory_tool_write_outcome_signature_locks_out_dimensions():
    """Same cardinality guard as the hook memory-write counter — no kwargs
    path for a future caller to slip a per-actor / per-session dimension."""
    from channel.metrics import record_memory_tool_write_outcome

    sig = inspect.signature(record_memory_tool_write_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_memory_tool_recall_outcome_success_emits_success_counter():
    from channel.metrics import record_memory_tool_recall_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_tool_recall_outcome(success=True)
    mock_emit.assert_awaited_once_with("MemoryToolRecallSuccesses")


@pytest.mark.asyncio
async def test_record_memory_tool_recall_outcome_failure_emits_failure_counter():
    from channel.metrics import record_memory_tool_recall_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_memory_tool_recall_outcome(success=False)
    mock_emit.assert_awaited_once_with("MemoryToolRecallFailures")


def test_record_memory_tool_recall_outcome_is_distinct_from_hook_counter():
    """#400: tool recall must NOT reuse the hook recall metric names."""
    from channel.metrics import record_memory_tool_recall_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        import asyncio

        asyncio.run(record_memory_tool_recall_outcome(success=True))
        asyncio.run(record_memory_tool_recall_outcome(success=False))
    emitted = {call.args[0] for call in mock_emit.await_args_list}
    assert emitted == {"MemoryToolRecallSuccesses", "MemoryToolRecallFailures"}
    assert "RecallSuccesses" not in emitted
    assert "RecallFailures" not in emitted


def test_record_memory_tool_recall_outcome_signature_locks_out_dimensions():
    """Same cardinality guard as the hook recall counter."""
    from channel.metrics import record_memory_tool_recall_outcome

    sig = inspect.signature(record_memory_tool_recall_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


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


@pytest.mark.asyncio
async def test_record_image_gen_outcome_success_emits_only_invocations():
    """A successful generation counts one ``ImageGenInvocations`` and no
    failure counter (#279)."""
    from channel.metrics import record_image_gen_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_image_gen_outcome(success=True)

    mock_emit.assert_awaited_once_with("ImageGenInvocations")


@pytest.mark.asyncio
async def test_record_image_gen_outcome_failure_emits_both_counters():
    """A failed / content-filtered generation counts BOTH the invocation
    and a failure, so the failure rate is Failures / Invocations."""
    from channel.metrics import record_image_gen_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_image_gen_outcome(success=False)

    mock_emit.assert_any_await("ImageGenInvocations")
    mock_emit.assert_any_await("ImageGenFailures")
    assert mock_emit.await_count == 2


def test_record_image_gen_outcome_signature_locks_out_dimensions():
    """No per-actor / per-chat / per-model dimension can slip in — and,
    because billing is deferred, deliberately no cost/quota dimension."""
    from channel.metrics import record_image_gen_outcome

    sig = inspect.signature(record_image_gen_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_chat_delete_asset_wipe_outcome_success_emits_success_counter():
    from channel.metrics import record_chat_delete_asset_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_asset_wipe_outcome(success=True)

    mock_emit.assert_awaited_once_with("ChatDeleteAssetWipeSuccesses")


@pytest.mark.asyncio
async def test_record_chat_delete_asset_wipe_outcome_failure_emits_failure_counter():
    from channel.metrics import record_chat_delete_asset_wipe_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_chat_delete_asset_wipe_outcome(success=False)

    mock_emit.assert_awaited_once_with("ChatDeleteAssetWipeFailures")


def test_record_chat_delete_asset_wipe_outcome_signature_locks_out_dimensions():
    """Same cardinality rule as the memory-write counters — no kwargs
    path for per-actor / per-chat / per-asset dimensions."""
    from channel.metrics import record_chat_delete_asset_wipe_outcome

    sig = inspect.signature(record_chat_delete_asset_wipe_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_asset_lazy_expiry_reaps_emits_both_counters():
    from channel.metrics import record_asset_lazy_expiry_reaps

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_asset_lazy_expiry_reaps(reaped=3, failed=2)

    mock_emit.assert_any_await("AssetLazyExpiryReaps", value=3.0)
    mock_emit.assert_any_await("AssetLazyExpiryReapFailures", value=2.0)
    assert mock_emit.await_count == 2


@pytest.mark.asyncio
async def test_record_asset_lazy_expiry_reaps_skips_zero_counters():
    from channel.metrics import record_asset_lazy_expiry_reaps

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_asset_lazy_expiry_reaps(reaped=0, failed=0)

    mock_emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_asset_lazy_expiry_reaps_emits_only_nonzero_side():
    from channel.metrics import record_asset_lazy_expiry_reaps

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_asset_lazy_expiry_reaps(reaped=1, failed=0)

    mock_emit.assert_awaited_once_with("AssetLazyExpiryReaps", value=1.0)


def test_record_asset_lazy_expiry_reaps_signature_locks_out_dimensions():
    """Two ints only — no kwargs path for dimensions (cardinality rule)."""
    from channel.metrics import record_asset_lazy_expiry_reaps

    sig = inspect.signature(record_asset_lazy_expiry_reaps)
    assert list(sig.parameters.keys()) == ["reaped", "failed"]
    assert all(p.annotation == "int" for p in sig.parameters.values())


@pytest.mark.asyncio
async def test_record_asset_persist_outcome_success_emits_success_counter():
    from channel.metrics import record_asset_persist_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_asset_persist_outcome(success=True)

    mock_emit.assert_awaited_once_with("AssetPersistSuccesses")


@pytest.mark.asyncio
async def test_record_asset_persist_outcome_failure_emits_failure_counter():
    from channel.metrics import record_asset_persist_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_asset_persist_outcome(success=False)

    mock_emit.assert_awaited_once_with("AssetPersistFailures")


def test_record_asset_persist_outcome_signature_locks_out_dimensions():
    """Same cardinality rule as the memory-write counters — no kwargs
    path for per-actor / per-chat / per-producer dimensions (#326)."""
    from channel.metrics import record_asset_persist_outcome

    sig = inspect.signature(record_asset_persist_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_mcp_tools_capped_emits_counter():
    from channel.metrics import record_mcp_tools_capped

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_mcp_tools_capped()

    mock_emit.assert_awaited_once_with("MCPToolsCapped")


def test_record_mcp_tools_capped_signature_locks_out_dimensions():
    """Counter-only (#389): the signature accepts NO arguments so a
    future caller cannot slip a per-server dimension through — cardinality
    scales with the registered-server set. Which server was capped (and
    which tools were dropped) lives in the ``mcp.tools_capped`` log line."""
    from channel.metrics import record_mcp_tools_capped

    sig = inspect.signature(record_mcp_tools_capped)
    assert list(sig.parameters.keys()) == []


@pytest.mark.asyncio
async def test_record_mcp_tool_result_truncated_emits_counter():
    from channel.metrics import record_mcp_tool_result_truncated

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_mcp_tool_result_truncated()

    mock_emit.assert_awaited_once_with("MCPToolResultTruncated")


def test_record_mcp_tool_result_truncated_signature_locks_out_dimensions():
    """Counter-only (#390): the signature accepts NO arguments so a
    future caller cannot slip a per-tool dimension through — cardinality
    scales with the registered tool set. Which tool was truncated (and the
    exact byte counts) lives in the ``mcp.tool_result_truncated`` log
    line."""
    from channel.metrics import record_mcp_tool_result_truncated

    sig = inspect.signature(record_mcp_tool_result_truncated)
    assert list(sig.parameters.keys()) == []

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


# ----------------------------------------------------------------
# Request + Bedrock SLIs (#111)
# ----------------------------------------------------------------


def _batch_call(mock_batch):
    """(metrics, dimension_sets) from a single ``_emit_batch`` await."""
    mock_batch.assert_awaited_once()
    args, kwargs = mock_batch.await_args
    metrics = kwargs.get("metrics", args[0] if args else None)
    dimension_sets = kwargs.get("dimension_sets", args[1] if len(args) > 1 else None)
    return metrics, dimension_sets


@pytest.mark.asyncio
async def test_record_request_outcome_emits_count_and_latency():
    from channel.metrics import record_request_outcome

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/api/models", status_code=200, duration_ms=12.5)

    metrics, _ = _batch_call(mock_batch)
    assert metrics == [
        ("RequestCount", 1.0, "Count"),
        ("RequestLatencyMs", 12.5, "Milliseconds"),
    ]


@pytest.mark.asyncio
async def test_record_request_outcome_latency_unit_is_milliseconds():
    """The issue body parenthesised "Microseconds"; the ``Ms`` suffix, the
    ``duration_ms`` log field and the existing ``StorageLatencyMs``
    convention all say milliseconds. Pinned so the unit can't drift."""
    from channel.metrics import record_request_outcome

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/health", status_code=200, duration_ms=1.0)

    metrics, _ = _batch_call(mock_batch)
    assert dict((name, unit) for name, _v, unit in metrics)["RequestLatencyMs"] == "Milliseconds"


@pytest.mark.asyncio
async def test_record_request_outcome_counts_4xx_only_for_4xx():
    from channel.metrics import record_request_outcome

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/api/chats", status_code=404, duration_ms=3.0)

    metrics, _ = _batch_call(mock_batch)
    names = [name for name, _v, _u in metrics]
    assert "Request4xxCount" in names
    assert "Request5xxCount" not in names


@pytest.mark.asyncio
async def test_record_request_outcome_counts_5xx_only_for_5xx():
    from channel.metrics import record_request_outcome

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/api/chats", status_code=503, duration_ms=3.0)

    metrics, _ = _batch_call(mock_batch)
    names = [name for name, _v, _u in metrics]
    assert "Request5xxCount" in names
    assert "Request4xxCount" not in names


@pytest.mark.asyncio
async def test_record_request_outcome_emits_aggregate_and_route_dimension_sets():
    from channel.metrics import ENVIRONMENT, record_request_outcome

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/api/chats/{chat_id}", status_code=200, duration_ms=5.0)

    _metrics, dimension_sets = _batch_call(mock_batch)
    assert dimension_sets == [
        {"Environment": ENVIRONMENT},
        {"Environment": ENVIRONMENT, "Route": "/api/chats/{chat_id}"},
    ]


@pytest.mark.asyncio
async def test_record_request_outcome_route_dimension_kill_switch(monkeypatch):
    """``CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED=0`` drops the per-route
    breakdown, leaving the aggregate series alarms and the admin readback
    consume untouched."""
    from channel.metrics import ENVIRONMENT, record_request_outcome

    monkeypatch.setenv("CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED", "0")
    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_request_outcome(route="/api/models", status_code=200, duration_ms=5.0)

    _metrics, dimension_sets = _batch_call(mock_batch)
    assert dimension_sets == [{"Environment": ENVIRONMENT}]


def test_record_request_outcome_signature_locks_out_dimensions():
    """``Route`` is the ONLY dimension this module permits, and it is
    permitted solely because it is a build-time-bounded enum (the mounted
    route templates + one fallback). The signature must therefore expose no
    ``**dimensions`` escape hatch for a per-actor / per-chat value."""
    from channel.metrics import record_request_outcome

    sig = inspect.signature(record_request_outcome)
    assert list(sig.parameters.keys()) == ["route", "status_code", "duration_ms"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in sig.parameters.values())


@pytest.mark.asyncio
async def test_record_request_outcome_end_to_end_does_not_raise():
    """Exercises the real ``_emit_batch`` (stdout sink outside Lambda)."""
    from channel.metrics import record_request_outcome

    await record_request_outcome(route="/health", status_code=200, duration_ms=1.0)


@pytest.mark.asyncio
async def test_record_bedrock_turn_success_emits_latency_and_tokens():
    from channel.metrics import ENVIRONMENT, record_bedrock_turn

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_bedrock_turn(duration_ms=1500.0, input_tokens=100, output_tokens=42)

    metrics, dimension_sets = _batch_call(mock_batch)
    assert metrics == [
        ("BedrockLatencyMs", 1500.0, "Milliseconds"),
        ("BedrockTokensIn", 100.0, "Count"),
        ("BedrockTokensOut", 42.0, "Count"),
    ]
    assert dimension_sets == [{"Environment": ENVIRONMENT}]


@pytest.mark.asyncio
async def test_record_bedrock_turn_non_throttle_error_counts_errors_only():
    from channel.metrics import record_bedrock_turn

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_bedrock_turn(
            duration_ms=10.0, input_tokens=0, output_tokens=0, error_code="bedrock_timeout"
        )

    metrics, _ = _batch_call(mock_batch)
    names = [name for name, _v, _u in metrics]
    assert "BedrockErrors" in names
    assert "BedrockThrottles" not in names


@pytest.mark.asyncio
async def test_record_bedrock_turn_throttle_counts_both():
    """The #391 ``bedrock_throttled`` classification finally becomes a
    metric: a quota wall must be distinguishable from a bug."""
    from channel.metrics import record_bedrock_turn

    with patch("channel.metrics._emit_batch", new=AsyncMock()) as mock_batch:
        await record_bedrock_turn(
            duration_ms=10.0, input_tokens=0, output_tokens=0, error_code="bedrock_throttled"
        )

    metrics, _ = _batch_call(mock_batch)
    names = [name for name, _v, _u in metrics]
    assert names.count("BedrockErrors") == 1
    assert names.count("BedrockThrottles") == 1


def test_record_bedrock_turn_signature_locks_out_dimensions():
    """``error_code`` is a branch selector, never a dimension — so the
    ``_STREAM_ERROR_MAP`` code set can grow without multiplying metrics.
    No ``**dimensions`` escape hatch, no per-actor / per-chat / per-model
    parameter."""
    from channel.metrics import record_bedrock_turn

    sig = inspect.signature(record_bedrock_turn)
    assert list(sig.parameters.keys()) == [
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "error_code",
    ]
    assert sig.parameters["error_code"].default is None


@pytest.mark.asyncio
async def test_record_bedrock_turn_end_to_end_does_not_raise():
    from channel.metrics import record_bedrock_turn

    await record_bedrock_turn(
        duration_ms=1.0, input_tokens=1, output_tokens=1, error_code="bedrock_throttled"
    )


# ----------------------------------------------------------------
# #245 — rolling head summary + history-window truncation counters
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_head_summary_outcome_success_emits_success_counter():
    from channel.metrics import record_head_summary_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_head_summary_outcome(success=True)

    mock_emit.assert_awaited_once_with("HeadSummarySuccesses")


@pytest.mark.asyncio
async def test_record_head_summary_outcome_failure_emits_failure_counter():
    from channel.metrics import record_head_summary_outcome

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_head_summary_outcome(success=False)

    mock_emit.assert_awaited_once_with("HeadSummaryFailures")


def test_record_head_summary_outcome_signature_locks_out_dimensions():
    """#245 carries the same cardinality rule as ``MemoryWriteFailures``:
    the signature accepts only ``success``, so no future caller can slip
    a per-actor or per-chat dimension through. Chat identity lives in the
    ``head_summary_failed`` log line."""
    from channel.metrics import record_head_summary_outcome

    sig = inspect.signature(record_head_summary_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    param = sig.parameters["success"]
    assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert param.annotation == "bool"


@pytest.mark.asyncio
async def test_record_history_window_truncated_emits_counter():
    from channel.metrics import record_history_window_truncated

    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_history_window_truncated()

    mock_emit.assert_awaited_once_with("HistoryWindowTruncated")


def test_record_history_window_truncated_signature_locks_out_dimensions():
    """Counter-only (#245): NO arguments, so a per-chat dimension can't
    be added later. How deep the window was, and for which chat, live in
    the ``history_window_truncated`` log line."""
    from channel.metrics import record_history_window_truncated

    sig = inspect.signature(record_history_window_truncated)
    assert list(sig.parameters.keys()) == []

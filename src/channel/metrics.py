# Copyright (c) 2026 John Carter. All rights reserved.
"""
CloudWatch custom metrics via Embedded Metric Format (EMF).

Lambda ships stdout to CloudWatch Logs; EMF lines are automatically parsed
into custom metrics by the CloudWatch agent — no PutMetricData calls needed.

In local dev / unit tests the EMF library detects a non-Lambda environment
and writes metrics to stdout instead (no-op from a CloudWatch perspective).

Usage:
    from channel.metrics import emit_metric

    await emit_metric("ToolInvocations", operation="remember")
    await emit_metric("ToolErrors", operation="remember")
    await emit_metric("StorageLatencyMs", value=42.0, unit="Milliseconds", operation="remember")
"""

from __future__ import annotations

import os

from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger

NAMESPACE = "Channel"
ENVIRONMENT = os.environ.get("STARTER_ENV", os.environ.get("ENV", "local"))


async def emit_metric(
    name: str,
    value: float = 1.0,
    unit: str = "Count",
    **dimensions: str,
) -> None:
    """Emit a single CloudWatch metric via EMF.

    Args:
        name: Metric name (e.g. "ToolInvocations").
        value: Metric value (default 1.0).
        unit: CloudWatch unit string (default "Count").
        **dimensions: Arbitrary key=value dimension pairs added to the metric.
            "Environment" is always included automatically.
    """
    logger = create_metrics_logger()
    logger.set_namespace(NAMESPACE)
    dims = {"Environment": ENVIRONMENT, **dimensions}
    logger.set_dimensions(dims)  # type: ignore[arg-type]
    logger.put_metric(name, value, unit)
    await logger.flush()


async def record_memory_write_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one AgentCore Memory write attempt.

    Counter-only — the signature deliberately accepts no dimensions so a
    future caller cannot accidentally add per-actor or per-session
    dimensions. Per-actor context belongs in structured logs, not
    metric dimensions: cardinality scales with active users, which
    blows up CloudWatch metric volume. See Phase 7c spec Risk #3.
    """
    metric = "MemoryWriteSuccesses" if success else "MemoryWriteFailures"
    await emit_metric(metric)


async def record_recall_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one RetrieveMemoryRecords attempt.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. Per Phase 7d spec Risk #6.
    """
    metric = "RecallSuccesses" if success else "RecallFailures"
    await emit_metric(metric)


async def record_auto_title_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one auto-title attempt.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. Per Phase 7d spec Risk #6.
    """
    metric = "AutoTitleSuccesses" if success else "AutoTitleFailures"
    await emit_metric(metric)


async def record_chat_delete_memory_wipe_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one AgentCore Memory wipe attempt on chat delete.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. No per-actor or per-chat dimensions.
    """
    metric = "ChatDeleteMemoryWipeSuccesses" if success else "ChatDeleteMemoryWipeFailures"
    await emit_metric(metric)


async def record_followup_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one follow-up suggestion generation.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. No per-actor or per-chat dimensions.
    """
    metric = "FollowupGenSuccesses" if success else "FollowupGenFailures"
    await emit_metric(metric)


async def record_chat_delete_attachment_wipe_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one chat-delete attachment-wipe cascade.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. ``success=False`` means at
    least one S3 object or ATTACHMENT row deletion failed for the
    cascade; partial-success cascades count as failures so the metric
    can drive alarming. Per-actor / per-chat / per-attachment dimensions
    are deliberately not accepted.
    """
    metric = "ChatDeleteAttachmentWipeSuccesses" if success else "ChatDeleteAttachmentWipeFailures"
    await emit_metric(metric)


async def record_chat_delete_asset_wipe_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one chat-delete asset-wipe cascade (#324).

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. ``success=False`` means at
    least one S3 object or ASSET row deletion failed for the cascade;
    partial-success cascades count as failures so the metric can drive
    alarming — the exact ``ChatDeleteAttachmentWipeFailures`` pattern.
    Per-actor / per-chat / per-asset dimensions are deliberately not
    accepted.
    """
    metric = "ChatDeleteAssetWipeSuccesses" if success else "ChatDeleteAssetWipeFailures"
    await emit_metric(metric)


async def record_asset_lazy_expiry_reaps(reaped: int, failed: int) -> None:
    """Emit counters for one browse-time lazy-expiry reap pass (#324, Q5).

    ``AssetLazyExpiryReaps`` carries the number of orphaned assets
    actually reaped (rows + S3 objects); ``AssetLazyExpiryReapFailures``
    the number that failed. Zero-valued counters are skipped so quiet
    browse pages emit nothing. Signature accepts only the two ints —
    same no-dimensions cardinality rule as
    :func:`record_memory_write_outcome`.
    """
    if reaped:
        await emit_metric("AssetLazyExpiryReaps", value=float(reaped))
    if failed:
        await emit_metric("AssetLazyExpiryReapFailures", value=float(failed))


async def record_asset_persist_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one asset-persist attempt (#326).

    One count per asset a producer (upload projection, code-exec
    image, fenced-code extraction) tries to persist. Counter-only —
    same cardinality-risk rationale as
    :func:`record_memory_write_outcome`; per-producer breakdowns
    belong in the structured ``asset.persist_failed`` log lines, not
    metric dimensions.
    """
    metric = "AssetPersistSuccesses" if success else "AssetPersistFailures"
    await emit_metric(metric)


async def record_image_gen_outcome(success: bool) -> None:
    """Emit CloudWatch counters for one ``generate_image`` invocation (#279).

    ``ImageGenInvocations`` counts EVERY invocation (success, content
    filter rejection, or error) — the "how much is Channel generating
    images" signal the epic asks for. ``ImageGenFailures`` additionally
    counts the non-success invocations so the failure rate is
    ``ImageGenFailures / ImageGenInvocations``. Counter-only — same
    cardinality-risk rationale as :func:`record_memory_write_outcome`; no
    per-actor / per-chat / per-model dimensions (billing is deferred, so
    there is deliberately no cost or quota dimension either).
    """
    await emit_metric("ImageGenInvocations")
    if not success:
        await emit_metric("ImageGenFailures")


async def record_tool_call_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one tool-call attempt (epic #128).

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. No per-tool or per-actor
    dimensions. Per-tool failure breakdowns belong in structured logs,
    which CloudWatch Logs Insights can query without dimension blowup.
    """
    metric = "ToolCallSuccesses" if success else "ToolCallFailures"
    await emit_metric(metric)


async def record_mcp_tools_capped() -> None:
    """Emit a CloudWatch counter for one MCP server whose advertised tool
    set exceeded the per-server budget and was truncated (#389).

    Counter-only — deliberately accepts NO arguments, mirroring the
    signature-lock discipline of :func:`record_memory_write_outcome`. The
    alarming signal is *how often* a real server exceeds the budget (a
    heavy server ships ~15-20K tokens of tool-schema overhead per turn);
    *which* server it was, and *which* tools were dropped, live in the
    structured ``mcp.tools_capped`` log line, queryable via CloudWatch
    Logs Insights without a per-server metric dimension (cardinality
    scales with the registered-server set — the same blowup guard as
    every other counter in this module).
    """
    await emit_metric("MCPToolsCapped")

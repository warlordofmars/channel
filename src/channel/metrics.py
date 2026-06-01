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

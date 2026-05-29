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

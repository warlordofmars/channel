# Copyright (c) 2026 John Carter. All rights reserved.
"""
CloudWatch custom metrics via Embedded Metric Format (EMF).

Lambda ships stdout to CloudWatch Logs; EMF lines are automatically parsed
into custom metrics by the CloudWatch agent — no PutMetricData calls needed.

In local dev / unit tests the EMF library detects a non-Lambda environment
and writes metrics to stdout instead (no-op from a CloudWatch perspective).

Usage:
    from channel.metrics import emit_metric

    await emit_metric("MemoryWriteSuccesses")
    await emit_metric("RequestLatencyMs", value=42.0, unit="Milliseconds")

Prefer one of the named ``record_*`` helpers below over a raw
``emit_metric`` call: they are where the no-dimensions cardinality rule
is enforced (and pinned by ``_signature_locks_out_dimensions`` tests in
``tests/unit/test_metrics.py``).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger

NAMESPACE = "Channel"
ENVIRONMENT = os.environ.get("CHANNEL_ENV", os.environ.get("ENV", "local"))

# Dimension value used when a request never matched a mounted FastAPI
# route (404s, CORS preflights short-circuited by CORSMiddleware, probes
# for `/wp-login.php`). Collapsing every unmatched URL to one bucket is
# what makes the ``Route`` dimension provably bounded — see
# :func:`record_request_outcome`.
REQUEST_ROUTE_FALLBACK = "other"

# Kill switch for the per-route dimension set (#111). The aggregate
# ``{Environment}`` series — the one alarms and the admin readback consume
# — is always emitted; this flag only controls the extra
# ``{Environment, Route}`` breakdown, which multiplies the custom-metric
# count by the number of routes that actually receive traffic. Default-on
# per the issue; set to ``"0"`` if the CloudWatch custom-metric bill
# outweighs the drill-down (CloudWatch Logs Insights can answer the same
# question from the structured request log lines for free).
_ROUTE_DIMENSION_ENV = "CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED"


def _route_dimension_enabled() -> bool:
    return os.environ.get(_ROUTE_DIMENSION_ENV, "1") == "1"


async def _emit_batch(
    metrics: Sequence[tuple[str, float, str]],
    dimension_sets: Sequence[dict[str, str]],
) -> None:
    """Emit several metrics in ONE EMF flush under one or more dimension sets.

    ``emit_metric`` is the single-metric / single-dimension-set workhorse;
    this is its batching sibling, needed by the per-request and per-Bedrock-turn
    recorders which emit 3-5 related values that must share a timestamp.
    Multiple dimension sets let one value be published both as an aggregate
    (``{Environment}``) and as a bounded breakdown (``{Environment, Route}``)
    without a second flush.

    Deliberately private: callers go through the named ``record_*`` helpers so
    the dimension sets stay under this module's control (cardinality guard).
    """
    logger = create_metrics_logger()
    logger.set_namespace(NAMESPACE)
    logger.set_dimensions(*dimension_sets)  # type: ignore[arg-type]
    for name, value, unit in metrics:
        logger.put_metric(name, value, unit)
    await logger.flush()


async def emit_metric(
    name: str,
    value: float = 1.0,
    unit: str = "Count",
    **dimensions: str,
) -> None:
    """Emit a single CloudWatch metric via EMF.

    Args:
        name: Metric name (e.g. "MemoryWriteSuccesses").
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


async def record_memory_tool_write_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one ``remember`` tool write (#400).

    Deliberately SEPARATE from :func:`record_memory_write_outcome` (which
    tracks the always-on ``AgentCoreMemoryHook``). The admin dashboard reads
    ``MemoryWriteSuccesses`` / ``MemoryWriteFailures`` as a hook-health
    signal; folding agent-driven ``remember`` writes into that pair would
    conflate hook and tool activity and could mask a hook regression as tool
    usage grows. Splitting the counters — rather than adding a
    ``source=hook|tool`` dimension — keeps hook health isolated without
    breaking the low-cardinality signature (a ``source`` dimension is bounded
    at two values, but a separate counter is simpler and matches the
    established one-metric-per-outcome pattern in this module). Same
    cardinality-risk rationale as :func:`record_memory_write_outcome` — no
    per-actor / per-session dimensions.
    """
    metric = "MemoryToolWriteSuccesses" if success else "MemoryToolWriteFailures"
    await emit_metric(metric)


async def record_memory_tool_recall_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one ``recall`` tool search (#400).

    Deliberately SEPARATE from :func:`record_recall_outcome` (the always-on
    ``AgentCoreRecallHook``) for the same isolation reason as
    :func:`record_memory_tool_write_outcome`: the dashboard's recall-success
    rate is a hook-health signal, so agent-driven ``recall`` searches get
    their own counters. Same cardinality-risk rationale — no per-actor /
    per-session dimensions.
    """
    metric = "MemoryToolRecallSuccesses" if success else "MemoryToolRecallFailures"
    await emit_metric(metric)


async def record_auto_title_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one auto-title attempt.

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. Per Phase 7d spec Risk #6.
    """
    metric = "AutoTitleSuccesses" if success else "AutoTitleFailures"
    await emit_metric(metric)


async def record_head_summary_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one rolling head-summary pass (#245).

    Fires only on turns where the token-budgeted history window actually
    slid past unsummarised turns — in-budget chats emit nothing, so the
    counter doubles as a "how many chats are long enough to need this"
    signal. ``success=False`` covers both a summariser exception and an
    empty generation (the summary row is left untouched in both cases,
    so ``covers_through`` doesn't advance and the next turn retries).

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. No per-actor or per-chat
    dimensions; chat identity belongs in the structured
    ``head_summary_failed`` log line, not a metric dimension.
    """
    metric = "HeadSummarySuccesses" if success else "HeadSummaryFailures"
    await emit_metric(metric)


async def record_history_window_truncated() -> None:
    """Emit a CloudWatch counter for one turn whose history window was
    truncated by the token budget (#245, prompted by #227).

    Before #245 the truncation was completely silent: a 312-message chat
    fed 100 messages to the model with nothing recording the loss, and
    the resulting "forgets the thread mid-chat" symptom took weeks to
    diagnose. This counter makes the condition a one-glance dashboard
    signal.

    Counter-only — deliberately accepts NO arguments, mirroring the
    signature-lock discipline of :func:`record_memory_write_outcome`.
    *Which* chat truncated, and by how much, live in the structured
    ``history_window_truncated`` log line, queryable via CloudWatch Logs
    Insights without a per-chat metric dimension.
    """
    await emit_metric("HistoryWindowTruncated")


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


async def record_mcp_tool_result_truncated() -> None:
    """Emit a CloudWatch counter for one oversized tool_result content
    block truncated before it returned to Bedrock in-loop (#390).

    Counter-only — deliberately accepts NO arguments, mirroring the
    signature-lock discipline of :func:`record_memory_write_outcome`. The
    alarming signal is *how often* a tool result exceeds the in-turn byte
    budget (a fat MCP response — GitHub API JSON, search results, file
    contents — inflates the input-token count of every subsequent turn in
    the tool-calling chain); *which* tool it was, and the exact byte
    counts, live in the structured ``mcp.tool_result_truncated`` log line,
    queryable via CloudWatch Logs Insights without a per-tool metric
    dimension (cardinality scales with the registered tool set — the same
    blowup guard as every other counter in this module).

    Complements :func:`record_mcp_tools_capped`: that counter bounds
    *tool-schema* input; this one bounds *tool-result* input.
    """
    await emit_metric("MCPToolResultTruncated")


async def record_request_outcome(route: str, status_code: int, duration_ms: float) -> None:
    """Emit the per-request SLI counters for one handled HTTP request (#111).

    Four metrics, one EMF flush:

    * ``RequestCount`` — every request, the error-rate denominator.
    * ``RequestLatencyMs`` — ``Milliseconds``. The issue body parenthesised
      "Microseconds", which contradicts the ``Ms`` suffix, the ``duration_ms``
      structured-log field this mirrors, and the existing ``StorageLatencyMs``
      convention — milliseconds is the reading that makes all four agree.
      Note this measures **time to response start**, not time to last byte:
      Starlette's ``BaseHTTPMiddleware`` returns from ``call_next`` once
      ``http.response.start`` arrives, so an SSE turn contributes its
      time-to-first-byte, not its multi-minute stream duration. That is the
      right shape for a latency SLI and is what makes a p99 threshold
      meaningful on a service whose slowest route streams by design.
    * ``Request4xxCount`` / ``Request5xxCount`` — emitted only for the class
      that actually occurred, so a quiet service publishes no zero-valued
      error series.

    Cardinality
    -----------
    Two dimension sets: the aggregate ``{Environment}`` (what the CDK alarms
    and the ``/api/admin/metrics/*`` readback consume — that endpoint pins the
    dimension set exactly, so the aggregate is the one it can see), and the
    optional ``{Environment, Route}`` breakdown.

    ``route`` MUST be a **route template** (``/api/chats/{chat_id}``), never a
    concrete URL path — the caller resolves it from the matched FastAPI route
    and passes :data:`REQUEST_ROUTE_FALLBACK` when nothing matched, which
    bounds the dimension at ``len(app.routes) + 1``. That bound is the whole
    reason a dimension is permissible here at all: it is a fixed, build-time
    enum, unlike the per-actor / per-chat dimensions every other counter in
    this module refuses. The signature accepts no ``**dimensions`` so a future
    caller cannot slip ``user_id`` alongside it.
    """
    metrics: list[tuple[str, float, str]] = [
        ("RequestCount", 1.0, "Count"),
        ("RequestLatencyMs", float(duration_ms), "Milliseconds"),
    ]
    if 400 <= status_code < 500:
        metrics.append(("Request4xxCount", 1.0, "Count"))
    elif status_code >= 500:
        metrics.append(("Request5xxCount", 1.0, "Count"))

    base = {"Environment": ENVIRONMENT}
    dimension_sets = [base]
    if _route_dimension_enabled():
        dimension_sets.append({**base, "Route": route})
    await _emit_batch(metrics, dimension_sets)


async def record_bedrock_turn(
    duration_ms: float,
    input_tokens: int,
    output_tokens: int,
    error_code: str | None = None,
) -> None:
    """Emit the per-turn Bedrock SLIs for one streamed chat turn (#111).

    Emitted for EVERY turn — success or failure — so the counters share a
    denominator:

    * ``BedrockLatencyMs`` — wall time of the Strands stream loop. Exactly one
      datapoint per turn, which makes its ``SampleCount`` statistic the
      turn count; the CDK Bedrock-error-rate alarm uses it as the denominator
      rather than paying for a separate ``BedrockTurns`` counter.
    * ``BedrockTokensIn`` / ``BedrockTokensOut`` — Bedrock's reported usage.
      Zero on a failed turn, which is correct (nothing was billed to us for a
      turn that never produced usage metadata).
    * ``BedrockErrors`` — one per failed turn, whatever the classification.
    * ``BedrockThrottles`` — the throttle subset. Split out because a
      ``ThrottlingException`` storm is operationally distinct from a bug: it
      is a quota wall, and the response is to switch model or raise the quota,
      not to ship a fix. Before this counter existed the ``bedrock_throttled``
      path from #391 was visible only as a log line.

    Cardinality
    -----------
    One dimension set, ``{Environment}`` — no per-actor / per-chat / per-model
    dimensions. ``error_code`` is a **branch selector**, not a dimension: it
    picks which counter increments and never reaches CloudWatch, so the
    ``_STREAM_ERROR_MAP`` code set can grow without multiplying metrics. The
    per-code breakdown stays in the ``chat_stream_failed`` log line (which
    already carries ``code=``), queryable via Logs Insights. The signature
    accepts no ``**dimensions``.
    """
    metrics: list[tuple[str, float, str]] = [
        ("BedrockLatencyMs", float(duration_ms), "Milliseconds"),
        ("BedrockTokensIn", float(input_tokens), "Count"),
        ("BedrockTokensOut", float(output_tokens), "Count"),
    ]
    if error_code is not None:
        metrics.append(("BedrockErrors", 1.0, "Count"))
        if error_code == "bedrock_throttled":
            metrics.append(("BedrockThrottles", 1.0, "Count"))
    await _emit_batch(metrics, [{"Environment": ENVIRONMENT}])

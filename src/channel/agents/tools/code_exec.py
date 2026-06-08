# Copyright (c) 2026 John Carter. All rights reserved.
"""``code_exec`` — second concrete chassis tool (#128 / #183).

Synchronously invokes the ``CodeExecLambda`` sandbox via boto3 and
returns the sandbox's response dict (``{stdout, stderr, exit_code,
duration_ms, truncated, timed_out, images}``).

The sandbox Lambda's ARN is resolved from ``STARTER_CODE_EXEC_LAMBDA_ARN``
on each call (cheap env lookup; no caching needed). The boto3 Lambda
client is lazy-loaded via ``@functools.lru_cache(maxsize=1)`` — mirrors
``web_search``'s ``_get_exa_search`` pattern so a cold start without
code-exec usage doesn't pay the boto3 client construction cost.

Errors from the boto3 invoke (throttling / network / sandbox init crash)
become Strands-``ToolResult``-shaped dicts so ``translate_event`` extracts
the reason string as the stable SSE ``error_type`` token. This mirrors
PR #225's error-shape contract for ``web_search`` and is critical: a
plain ``{"status": "error", "error_type": "..."}`` dict gets re-wrapped
by Strands' ``@tool`` decorator as a single JSON-stringified text block,
defeating reason extraction.

Non-zero subprocess exit codes are NOT errors at the chassis layer —
the sandbox returns them as part of a successful response and the
model decides what to do."""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any

import botocore.exceptions
from strands import tool

logger = logging.getLogger(__name__)


def _error_result(error_type: str) -> dict[str, Any]:
    """Build a Strands-``ToolResult``-shaped error dict.

    See ``src/channel/agents/tools/web_search.py:_error_result`` for the
    full rationale on why this shape (not ``{"error_type": "..."}``) is
    load-bearing for the SSE error contract."""
    return {"status": "error", "content": [{"text": error_type}]}


@functools.lru_cache(maxsize=1)
def _get_lambda_client():  # type: ignore[no-untyped-def]
    """Lazy-load the boto3 Lambda client.

    Mirrors the ``web_search._get_exa_search`` shape — deferring the
    boto3 import until the model actually calls ``code_exec`` keeps the
    Lambda client construction off the cold-start path for turns that
    don't trigger code execution."""
    import boto3  # noqa: PLC0415  # pragma: no cover

    return boto3.client("lambda")  # pragma: no cover


@tool
def code_exec(code: str) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess. Returns stdout, stderr, exit_code.

    Use when you need to compute, transform data, analyze a CSV, plot
    something, or run a quick simulation. The environment has numpy,
    pandas, matplotlib, requests, httpx, python-dateutil pre-installed.
    (scipy was excluded from v1 — the full sci-stack exceeded Lambda's
    250 MB unzipped limit; can move to a Lambda layer if needed.)

    To show a plot or other image to the user, save it to
    ``/tmp/<name>.png`` (or .jpg). The Channel UI automatically
    detects files saved there and renders them inline below your
    reply. DO NOT embed the file path as a markdown image
    (``![alt](/tmp/plot.png)``) in your reply text — that path
    doesn't resolve in the browser and renders as a broken link.
    Just save the file and describe what it shows. Up to 3 images
    per call, 1 MB each. PNG and JPG only.

    Network: outbound internet IS reachable from this sandbox (the
    Lambda runs outside any VPC, so AWS's managed runtime grants
    egress). What's NOT reachable is anything in Channel's IAM scope
    — DynamoDB, S3, Bedrock, Secrets, SSM — all blocked at the role
    level. Prefer to NOT call external APIs unless the user asked
    you to; treat outbound network as an explicit user-consented
    capability. Hard isolation behind a no-egress VPC is tracked as
    a follow-up.
    Filesystem: writable ``/tmp`` only; wiped between calls.
    Timeout: 270 seconds.
    stdout cap: 20 KB; stderr cap: 5 KB.

    Args:
        code: The Python source to execute.
    """
    arn = os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN")
    if not arn:
        return _error_result("not_configured")
    client = _get_lambda_client()
    try:
        resp = client.invoke(
            FunctionName=arn,
            InvocationType="RequestResponse",
            Payload=json.dumps({"code": code}).encode(),
        )
    except client.exceptions.TooManyRequestsException:
        logger.warning("code_exec.rate_limit code_len=%d", len(code))
        return _error_result("rate_limit")
    except botocore.exceptions.ClientError as exc:
        logger.warning("code_exec.client_error %r code_len=%d", exc, len(code))
        return _error_result("invoke_failed")
    if "FunctionError" in resp:
        logger.warning("code_exec.sandbox_init_error code_len=%d", len(code))
        return _error_result("sandbox_init_error")
    payload = json.loads(resp["Payload"].read())
    return payload  # type: ignore[no-any-return]

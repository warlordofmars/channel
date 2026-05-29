# Copyright (c) 2026 John Carter. All rights reserved.
"""
CSP violation reporting endpoint.

Browsers POST CSP violation reports here when a resource is blocked (or would
be blocked under Report-Only mode).  Each report is logged as structured JSON
and emits a ``CSPViolations`` EMF metric.

Unauthenticated on purpose — browsers don't send credentials with CSP POSTs.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Response, status

from channel.logging_config import get_logger
from channel.metrics import emit_metric

router = APIRouter(tags=["csp"])
logger = get_logger(__name__)

_FIELD_MAX_LEN = 2048


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _FIELD_MAX_LEN:
        return value[:_FIELD_MAX_LEN] + "…"
    return value


def _extract_legacy(body: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a legacy ``application/csp-report`` body."""
    report = body.get("csp-report")
    if not isinstance(report, dict):
        return None
    return {
        "violated_directive": _truncate(report.get("violated-directive", "")),
        "effective_directive": _truncate(
            report.get("effective-directive", report.get("violated-directive", ""))
        ),
        "blocked_uri": _truncate(report.get("blocked-uri", "")),
        "document_uri": _truncate(report.get("document-uri", "")),
        "source_file": _truncate(report.get("source-file", "")),
        "line_number": report.get("line-number"),
        "column_number": report.get("column-number"),
        "disposition": report.get("disposition", "report"),
    }


def _extract_modern(report: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a modern ``application/reports+json`` entry."""
    if report.get("type") != "csp-violation":
        return None
    body = report.get("body") or {}
    return {
        "violated_directive": _truncate(body.get("effectiveDirective", "")),
        "effective_directive": _truncate(body.get("effectiveDirective", "")),
        "blocked_uri": _truncate(body.get("blockedURL", "")),
        "document_uri": _truncate(body.get("documentURL", report.get("url", ""))),
        "source_file": _truncate(body.get("sourceFile", "")),
        "line_number": body.get("lineNumber"),
        "column_number": body.get("columnNumber"),
        "disposition": body.get("disposition", "report"),
    }


def _blocked_domain(blocked_uri: str) -> str:
    if not blocked_uri:
        return "none"
    if blocked_uri in {"inline", "eval", "self", "data"}:
        return blocked_uri
    parsed = urlparse(blocked_uri)
    return parsed.hostname or blocked_uri[:_FIELD_MAX_LEN]


async def _record_violation(violation: dict[str, Any]) -> None:
    logger.warning(
        "CSP violation: %s blocked %s",
        violation["violated_directive"] or "unknown",
        violation["blocked_uri"] or "unknown",
        extra={"csp": violation},
    )
    await emit_metric("CSPViolations")
    await emit_metric(
        "CSPViolations",
        directive=violation["violated_directive"] or "unknown",
        blocked_domain=_blocked_domain(violation["blocked_uri"]),
    )


@router.post(
    "/csp-report",
    summary="Receive a browser CSP violation report",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def receive_csp_report(request: Request) -> Response:
    raw = await request.body()
    if not raw:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    violations: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        legacy = _extract_legacy(payload)
        if legacy:
            violations.append(legacy)
    elif isinstance(payload, list):
        for report in payload:
            if isinstance(report, dict):
                modern = _extract_modern(report)
                if modern:
                    violations.append(modern)

    for v in violations:
        await _record_violation(v)

    return Response(status_code=status.HTTP_204_NO_CONTENT)

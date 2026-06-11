# Copyright (c) 2026 John Carter. All rights reserved.
"""
Structured JSON logging for Channel.

Call configure_logging(service) once at Lambda cold start (or local server
startup).  All subsequent calls to get_logger() return a logger whose output
is newline-delimited JSON compatible with CloudWatch Logs Insights.

Context variables (request_id, client_id) are injected automatically into
every log line within a request without explicit passing.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

# Per-request context — set at request boundary, read by the formatter.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_client_id_var: ContextVar[str] = ContextVar("client_id", default="")

# Module-level service metadata populated by configure_logging().
_SERVICE: str = ""
_VERSION: str = ""
_ENV: str = ""


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    _EXTRA_FIELDS = (
        "tool",
        "duration_ms",
        "status",
        "method",
        "path",
        "status_code",
        "error_type",
        "error_message",
    )

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": _SERVICE,
            "version": _VERSION,
            "env": _ENV,
            "message": record.getMessage(),
        }

        # Inject per-request context when available.
        if rid := _request_id_var.get():
            entry["request_id"] = rid
        if cid := _client_id_var.get():
            entry["client_id"] = cid

        # Structured fields forwarded via extra={} on the log call.
        for key in self._EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        if record.exc_info:
            entry["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Unknown"
            entry["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def configure_logging(service: str) -> None:
    """Configure the 'channel' logger with JSON output.

    Safe to call multiple times — only installs the handler once.
    """
    global _SERVICE, _VERSION, _ENV

    _SERVICE = service
    _ENV = os.environ.get("STARTER_ENV", os.environ.get("APP_ENV", "dev"))
    try:
        _VERSION = importlib.metadata.version("channel")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        _VERSION = os.environ.get("APP_VERSION", "dev")  # pragma: no cover

    logger = logging.getLogger("channel")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Silence noisy access logs from uvicorn — we log at middleware level.
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str = "channel") -> logging.Logger:
    """Return the named logger (default: root channel logger)."""
    return logging.getLogger(name)


def new_request_id() -> str:
    """Generate a short request correlation ID."""
    return uuid.uuid4().hex[:12]


def set_request_context(request_id: str, client_id: str = "") -> None:
    """Set per-request context variables (call at request boundary)."""
    _request_id_var.set(request_id)
    _client_id_var.set(client_id)


def fingerprint_id(value: str) -> str:
    """Deterministic non-reversible 16-char hex fingerprint for an
    identifier (user_id, server_id, etc.).

    Used by log calls that want to correlate events across requests
    or processes without writing raw identifiers into the log sink
    (Sonar pythonsecurity:S5145 + general PII discipline).

    SHA-256 truncated to 16 hex chars matches the existing pattern in
    :func:`channel.auth.logout._token_fingerprint`. Python's built-in
    ``hash()`` is salted per process, so the same input produces
    different output across cold starts — useless for correlation —
    which is why this helper exists.
    """
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

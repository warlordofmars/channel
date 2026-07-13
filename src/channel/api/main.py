# Copyright (c) 2026 John Carter. All rights reserved.
"""
Channel management FastAPI application.

Runs on port 8001 in development.
Add your API routes here — see api/_auth.py for auth dependencies.
"""

from __future__ import annotations

import importlib.metadata
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from channel.api._auth import require_admin  # noqa: F401 — re-exported for route use
from channel.api.admin import router as admin_router
from channel.api.attachments import router as attachments_router
from channel.api.chats import router as chats_router
from channel.api.csp import router as csp_router
from channel.api.models import router as models_router
from channel.api.prefs import router as prefs_router
from channel.auth.logout import router as logout_router
from channel.auth.mgmt_auth import router as mgmt_auth_router
from channel.logging_config import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_context,
)
from channel.startup import (
    validate_secrets_or_die,
    warn_unrotated_observability_params,
)

configure_logging("channel")
logger = get_logger(__name__)

# Fail-closed startup validation. Wired here at module import (not via
# @app.on_event("startup")) so any unrotated security-critical SSM
# parameter raises during AWS Lambda INIT and CloudFormation reports the
# deploy as failed. Both hooks no-op outside Lambda — see
# channel.startup for the placeholder check details.
validate_secrets_or_die()
warn_unrotated_observability_params()


def _app_version() -> str:
    if v := os.environ.get("APP_VERSION"):
        return v
    try:
        return importlib.metadata.version("channel")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "dev"  # pragma: no cover


APP_VERSION = _app_version()

app = FastAPI(
    title="Channel API",
    version=APP_VERSION,
    description="Starter management API — replace with your application routes.",
    docs_url=None,
    redoc_url=None,
)

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """Log every request with method, path, status code, and duration."""
    request_id = (
        request.headers.get("x-amzn-requestid")
        or request.headers.get("x-request-id")
        or new_request_id()
    )
    set_request_context(request_id)

    t0 = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - t0) * 1000)

    level = "warning" if response.status_code >= 400 else "info"
    getattr(logger, level)(
        "%s %s %d",
        request.method,
        request.url.path,
        response.status_code,
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


@app.middleware("http")
async def _verify_origin_secret(request: Request, call_next):
    """Reject requests missing the CloudFront X-Origin-Verify secret.

    Disabled when neither ``STARTER_ORIGIN_VERIFY_PARAM`` nor
    ``STARTER_ORIGIN_VERIFY_SECRET`` provides a secret (local dev /
    non-prod). The placeholder-value short-circuit was removed for the
    SSM-backed ``STARTER_ORIGIN_VERIFY_PARAM`` path — the fail-closed
    startup check (:mod:`channel.startup`) guarantees that secret is
    rotated before the Lambda will start. The direct env-var path
    (``STARTER_ORIGIN_VERIFY_SECRET``) is for local dev and bypasses
    startup validation.
    """
    from channel.auth.tokens import _origin_verify_secret

    expected = _origin_verify_secret()
    if expected and request.headers.get("x-origin-verify") != expected:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


# Management UI auth endpoints (unauthenticated — issues mgmt JWTs)
app.include_router(mgmt_auth_router)

# Management UI logout endpoint — requires a valid mgmt JWT to validate
# the caller before writing the audit entry. No /api prefix: the logout
# verb sits alongside /auth/login + /auth/callback in the auth namespace
# so the SPA can hit /auth/logout symmetrically.
app.include_router(logout_router)

# CSP report receiver — unauthenticated by design
app.include_router(csp_router, prefix="/api")

# Chat REST + SSE API — all endpoints require a valid mgmt JWT
app.include_router(chats_router, prefix="/api")

# Models allowlist — served at /api/models (separate router so the
# /models path isn't nested under chats_router's "/chats" prefix)
app.include_router(models_router, prefix="/api")

# Attachments presign + finalize — served at /api/attachments/* (#175)
app.include_router(attachments_router, prefix="/api")

# Admin user list + detail — requires mgmt JWT with role=admin (#235)
app.include_router(admin_router, prefix="/api")

# User preferences — GET/PUT /api/me/prefs (full paths declared on the
# router so no prefix needed here)
app.include_router(prefs_router)

# MCP server registry — /api/mcp/* (auth-gated) + /auth/mcp/callback
# (browser redirect target, unauthenticated, state-store guarded).
#
# Kill switch: ``STARTER_MCP_REGISTRY_ENABLED != "1"`` skips both
# router mounts so there's no /api/mcp/* surface at all. Defaults to
# enabled when unset so tests / local dev that don't provision the
# env var still work; CDK + ``inv dev`` both wire ``"1"`` explicitly,
# and ``_build_mcp_clients_for_chat`` short-circuits on the same flag
# so DDB reads are also skipped when disabled.
if os.environ.get("STARTER_MCP_REGISTRY_ENABLED", "1") == "1":
    from channel.api.mcp import callback_router as mcp_callback_router  # noqa: E402
    from channel.api.mcp import router as mcp_router  # noqa: E402

    app.include_router(mcp_router)
    app.include_router(mcp_callback_router)

# Dev-only debug router (Phase 7c). Mounted ONLY when the env flag is
# explicitly set; prod stacks must not set it. See channel_stack.py +
# tests/unit/test_channel_stack.py for the deploy-time guard.
if os.environ.get("STARTER_ENABLE_DEBUG_ENDPOINTS") == "1":
    from channel.api._debug import router as debug_router

    app.include_router(debug_router)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}

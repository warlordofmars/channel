# Copyright (c) 2026 John Carter. All rights reserved.
"""
Channel management FastAPI application.

Runs on port 8001 in development.
App construction, middleware, and ``/health`` only — domain routes live
in dedicated ``api/<area>.py`` router modules and are wired in here via
``app.include_router``. See ``api/_auth.py`` for the auth dependencies.
"""

from __future__ import annotations

import importlib.metadata
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from channel.api._auth import require_admin  # noqa: F401 — re-exported for route use
from channel.api.admin import router as admin_router
from channel.api.assets import router as assets_router
from channel.api.attachments import router as attachments_router
from channel.api.audit import router as audit_router
from channel.api.chats import router as chats_router
from channel.api.csp import router as csp_router
from channel.api.memory import router as memory_router
from channel.api.models import router as models_router
from channel.api.prefs import router as prefs_router
from channel.api.sessions import router as sessions_router
from channel.auth.logout import router as logout_router
from channel.auth.mgmt_auth import router as mgmt_auth_router
from channel.auth.refresh import router as refresh_router
from channel.logging_config import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_context,
)
from channel.metrics import REQUEST_ROUTE_FALLBACK, record_request_outcome
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
    description="Channel management API — chats, auth, MCP registry, assets, and admin.",
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


def _route_template(request: Request) -> str:
    """The matched route's **template** (``/api/chats/{chat_id}``), or
    :data:`REQUEST_ROUTE_FALLBACK` when nothing matched.

    ``FastAPI.APIRoute.matches`` stashes the matched route on the ASGI
    scope, and ``BaseHTTPMiddleware`` shares that same scope dict with the
    downstream app — so the key is populated by the time ``call_next``
    returns. Reading the template (rather than ``request.url.path``) is
    what keeps the ``Route`` metric dimension bounded by the mounted route
    set: a concrete path would mint one dimension value per chat id.
    Unmatched requests (404s, CORS preflights the CORSMiddleware
    short-circuits below this layer, vulnerability scans) collapse into
    the single fallback bucket.
    """
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else REQUEST_ROUTE_FALLBACK


async def _finish_request(
    request: Request,
    request_id: str,
    status_code: int,
    t0: float,
    *,
    unhandled: bool = False,
) -> None:
    """Emit the completion log line + the EMF request SLIs for one request."""
    duration_ms = int((time.monotonic() - t0) * 1000)

    # ``require_mgmt_user`` stashes the caller's fingerprint on request
    # state; re-seed the context here because BaseHTTPMiddleware runs the
    # downstream app in its own task, so a ContextVar set inside the
    # request does NOT propagate back out to this frame.
    set_request_context(request_id, getattr(request.state, "client_id", ""))

    level = "error" if unhandled else "warning" if status_code >= 400 else "info"
    getattr(logger, level)(
        "%s %s %d",
        request.method,
        request.url.path,
        status_code,
        # Only the unhandled path has a live exception to attach; on every
        # other path ``exc_info=True`` would be a no-op at best and would
        # splice an unrelated in-flight traceback at worst.
        exc_info=unhandled,
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        },
    )

    # Metering must never break a request: a CloudWatch/EMF hiccup degrades
    # observability, it does not fail the call. Same fail-soft posture as
    # every other metric emission in the codebase.
    try:
        await record_request_outcome(
            route=_route_template(request),
            status_code=status_code,
            duration_ms=duration_ms,
        )
    except Exception:
        logger.warning("request metric emission failed", exc_info=True)


@app.middleware("http")
async def _verify_origin_secret(request: Request, call_next):
    """Reject requests missing the CloudFront X-Origin-Verify secret.

    Disabled when neither ``CHANNEL_ORIGIN_VERIFY_PARAM`` nor
    ``CHANNEL_ORIGIN_VERIFY_SECRET`` provides a secret (local dev /
    non-prod). The placeholder-value short-circuit was removed for the
    SSM-backed ``CHANNEL_ORIGIN_VERIFY_PARAM`` path — the fail-closed
    startup check (:mod:`channel.startup`) guarantees that secret is
    rotated before the Lambda will start. The direct env-var path
    (``CHANNEL_ORIGIN_VERIFY_SECRET``) is for local dev and bypasses
    startup validation.
    """
    from channel.auth.tokens import _origin_verify_secret

    expected = _origin_verify_secret()
    if expected and request.headers.get("x-origin-verify") != expected:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


# Registered LAST, therefore the OUTERMOST user middleware: Starlette
# builds the stack so the most recently added wrapper runs first. That
# ordering is deliberate (#111) — from out here the request SLIs observe
# every response the app produces, including the origin-verify 403 above,
# which an inner layer would never see. It also means the request_id
# context is established before any other middleware runs.
@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """Log AND meter every request: method, path, status code, duration.

    One middleware rather than two (#111 lists the structured-log and EMF
    emissions as separate sub-tasks) because both need the same clock and
    the same post-``call_next`` state — a second middleware would time the
    request twice and add an ASGI layer per request for no new signal.

    ``duration_ms`` is time to **response start**, not to last byte: an SSE
    turn's ``call_next`` returns as soon as the headers are ready, so a
    multi-minute stream logs its time-to-first-byte. See
    ``record_request_outcome`` for why that is the right latency SLI here.

    Unhandled exceptions are metered as a synthetic 500 and re-raised.
    ``ServerErrorMiddleware`` — which turns an escaped exception into the
    500 the client actually receives — sits OUTSIDE the user middleware
    stack entirely, so without this the single most alarm-worthy class of
    failure (a route raising, e.g. the #291 denylist-read failure) would
    produce neither a log line nor a ``Request5xxCount`` datapoint, and
    ``ApiRequestErrorRate`` would stay flat through a real outage.
    ``except Exception`` deliberately excludes ``BaseException``, so a
    client disconnect (``CancelledError``) is not miscounted as a 500.
    """
    request_id = (
        request.headers.get("x-amzn-requestid")
        or request.headers.get("x-request-id")
        or new_request_id()
    )
    set_request_context(request_id)

    t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        await _finish_request(request, request_id, 500, t0, unhandled=True)
        raise

    await _finish_request(request, request_id, response.status_code, t0)
    return response


# Management UI auth endpoints (unauthenticated — issues mgmt JWTs)
app.include_router(mgmt_auth_router)

# Management UI logout endpoint — requires a valid mgmt JWT to validate
# the caller before writing the audit entry. No /api prefix: the logout
# verb sits alongside /auth/login + /auth/callback in the auth namespace
# so the SPA can hit /auth/logout symmetrically.
app.include_router(logout_router)

# Refresh-token exchange — POST /auth/refresh (#291). Deliberately
# UNauthenticated: the refresh token is itself the credential, and the
# access JWT it renews has usually already expired by the time a client
# calls this. CSRF is covered by the required X-Channel-Refresh header
# plus the SameSite=Strict/HttpOnly cookie the web transport uses. No
# /api prefix — it belongs in the /auth namespace beside login/logout.
app.include_router(refresh_router)

# CSP report receiver — unauthenticated by design
app.include_router(csp_router, prefix="/api")

# Chat REST + SSE API — all endpoints require a valid mgmt JWT
app.include_router(chats_router, prefix="/api")

# Models allowlist — served at /api/models (separate router so the
# /models path isn't nested under chats_router's "/chats" prefix)
app.include_router(models_router, prefix="/api")

# Attachments presign + finalize — served at /api/attachments/* (#175)
app.include_router(attachments_router, prefix="/api")

# Asset REST surface — per-chat list/get/content/delete + cross-chat
# browse; every route requires a valid mgmt JWT (#325)
app.include_router(assets_router, prefix="/api")

# Memory read surface — GET /api/memory/records (#475). Requires a valid
# mgmt JWT; every route is scoped to the caller's own AgentCore actor AND
# per-session ownership-verified against the raw JWT sub (see the module
# docstring's §Scoping — the actor partition alone is not the boundary; a
# read gate must not depend on a derivation's properties, #474/#485).
app.include_router(memory_router, prefix="/api")

# Admin user list + detail — requires mgmt JWT with role=admin (#235)
app.include_router(admin_router, prefix="/api")

# Audit-log query — GET /api/audit/events; requires mgmt JWT with
# role=admin, and records an ``audit.read`` event for every served
# request (#601)
app.include_router(audit_router, prefix="/api")

# User preferences — GET/PUT /api/me/prefs (full paths declared on the
# router so no prefix needed here)
app.include_router(prefs_router)

# Active sessions — GET/DELETE /api/me/sessions[/{device_id}]; every
# route requires a valid mgmt JWT and is scoped to the caller's own
# refresh-token rows (#293)
app.include_router(sessions_router, prefix="/api")

# MCP server registry — /api/mcp/* (auth-gated) + /auth/mcp/callback
# (browser redirect target, unauthenticated, state-store guarded).
#
# Kill switch: ``CHANNEL_MCP_REGISTRY_ENABLED != "1"`` skips both
# router mounts so there's no /api/mcp/* surface at all. Defaults to
# enabled when unset so tests / local dev that don't provision the
# env var still work; CDK + ``inv dev`` both wire ``"1"`` explicitly,
# and ``_build_mcp_clients_for_chat`` short-circuits on the same flag
# so DDB reads are also skipped when disabled.
if os.environ.get("CHANNEL_MCP_REGISTRY_ENABLED", "1") == "1":
    from channel.api.mcp import callback_router as mcp_callback_router  # noqa: E402
    from channel.api.mcp import router as mcp_router  # noqa: E402

    app.include_router(mcp_router)
    app.include_router(mcp_callback_router)

# Dev-only debug router (Phase 7c). Mounted ONLY when the env flag is
# explicitly set; prod stacks must not set it. See channel_stack.py +
# tests/unit/test_channel_stack.py for the deploy-time guard.
if os.environ.get("CHANNEL_ENABLE_DEBUG_ENDPOINTS") == "1":
    from channel.api._debug import router as debug_router

    app.include_router(debug_router)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}

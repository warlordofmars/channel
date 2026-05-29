# Copyright (c) 2026 John Carter. All rights reserved.
"""
AgentCore Starter management FastAPI application.

Runs on port 8001 in development.
Add your API routes here — see api/_auth.py for auth dependencies.
"""

from __future__ import annotations

import importlib.metadata
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from starter.api._auth import require_admin  # noqa: F401 — re-exported for route use
from starter.api.csp import router as csp_router
from starter.auth.mgmt_auth import router as mgmt_auth_router
from starter.logging_config import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_context,
)
from starter.startup import (
    validate_secrets_or_die,
    warn_unrotated_observability_params,
)

configure_logging("agentcore-starter")
logger = get_logger(__name__)

# Fail-closed startup validation. Wired here at module import (not via
# @app.on_event("startup")) so any unrotated security-critical SSM
# parameter raises during AWS Lambda INIT and CloudFormation reports the
# deploy as failed. Both hooks no-op outside Lambda — see
# starter.startup for the placeholder check details.
validate_secrets_or_die()
warn_unrotated_observability_params()


def _app_version() -> str:
    if v := os.environ.get("APP_VERSION"):
        return v
    try:
        return importlib.metadata.version("agentcore-starter")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "dev"  # pragma: no cover


APP_VERSION = _app_version()

app = FastAPI(
    title="AgentCore Starter API",
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
    startup check (:mod:`starter.startup`) guarantees that secret is
    rotated before the Lambda will start. The direct env-var path
    (``STARTER_ORIGIN_VERIFY_SECRET``) is for local dev and bypasses
    startup validation.
    """
    from starter.auth.tokens import _origin_verify_secret

    expected = _origin_verify_secret()
    if expected and request.headers.get("x-origin-verify") != expected:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


# Management UI auth endpoints (unauthenticated — issues mgmt JWTs)
app.include_router(mgmt_auth_router)

# CSP report receiver — unauthenticated by design
app.include_router(csp_router, prefix="/api")


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}

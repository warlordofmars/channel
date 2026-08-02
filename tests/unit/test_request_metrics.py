# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the per-request log + EMF middleware (#111).

The cardinality guard lives here rather than in ``test_metrics.py``: the
``Route`` dimension is only bounded if the *middleware* resolves the matched
route's template instead of the concrete URL path, so that is what these
tests pin.

Module resolution note: every fixture below re-imports ``channel.api.main``
instead of binding it at module import. ``test_mcp_api.py`` exercises the MCP
mount kill-switch by ``sys.modules.pop``-ing that module and importing it
fresh, which yields a genuinely NEW module object. A module-scope
``from channel.api.main import app`` would then hold an app whose middleware
closure reads a different globals dict than ``patch("channel.api.main....")``
writes to, and every patch here would silently no-op — order-dependently,
since the suite randomises test order.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api._auth import require_mgmt_user  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.logging_config import fingerprint_id  # noqa: E402
from channel.metrics import REQUEST_ROUTE_FALLBACK  # noqa: E402


@pytest.fixture
def main_mod() -> ModuleType:
    """The live ``channel.api.main`` module — see the module docstring."""
    return importlib.import_module("channel.api.main")


@pytest.fixture
def client(main_mod: ModuleType) -> TestClient:
    return TestClient(main_mod.app)


@pytest.fixture
def record_stub(main_mod: ModuleType):
    """Patch ``record_request_outcome`` at the middleware's own boundary."""
    with patch.object(main_mod, "record_request_outcome", new=AsyncMock()) as stub:
        yield stub


def _recorded(stub) -> dict:
    """The kwargs of the single ``record_request_outcome`` await."""
    stub.assert_awaited_once()
    return stub.await_args.kwargs


def test_records_route_status_and_duration_for_a_matched_route(
    client: TestClient, record_stub
) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    call = _recorded(record_stub)
    assert call["route"] == "/health"
    assert call["status_code"] == 200
    assert call["duration_ms"] >= 0


def test_parameterized_route_records_the_template_not_the_concrete_path(
    client: TestClient, record_stub
) -> None:
    """The cardinality guard. ``/api/chats/{chat_id}`` must NOT become one
    dimension value per chat id — that is exactly the blowup every counter
    in ``channel.metrics`` is written to avoid."""
    # No Authorization header: the route still matches (so the scope carries
    # it), then the bearer dependency rejects the request.
    client.get("/api/chats/11111111-2222-3333-4444-555555555555")

    call = _recorded(record_stub)
    assert call["route"] == "/api/chats/{chat_id}"
    assert "11111111" not in call["route"]


def test_unmatched_path_collapses_into_the_fallback_bucket(client: TestClient, record_stub) -> None:
    """404s (including vulnerability scans hammering random URLs) share one
    dimension value, so an attacker cannot inflate the metric bill."""
    response = client.get("/wp-login.php")

    assert response.status_code == 404
    assert _recorded(record_stub)["route"] == REQUEST_ROUTE_FALLBACK


def test_status_code_is_forwarded_for_client_errors(client: TestClient, record_stub) -> None:
    response = client.get("/api/models")

    assert response.status_code in (401, 403)
    assert _recorded(record_stub)["status_code"] == response.status_code


def test_unhandled_exception_is_metered_as_a_synthetic_500(
    client: TestClient, main_mod: ModuleType, record_stub
) -> None:
    """The alarm-worthiest case, and the one the middleware stack hides.

    ``ServerErrorMiddleware`` — which converts an escaped exception into
    the 500 the client receives — sits OUTSIDE the user middleware stack,
    so a route that raises would otherwise produce neither a log line nor
    a ``Request5xxCount`` datapoint, leaving ``ApiRequestErrorRate`` flat
    through a real outage. The exception must still propagate untouched.
    """

    def _boom() -> dict:
        raise RuntimeError("route exploded")

    main_mod.app.dependency_overrides[require_mgmt_user] = _boom
    try:
        with pytest.raises(RuntimeError, match="route exploded"):
            client.get("/api/models")
    finally:
        main_mod.app.dependency_overrides.clear()

    call = _recorded(record_stub)
    assert call["status_code"] == 500
    assert call["route"] == "/api/models"


def test_origin_verify_rejection_is_metered(
    client: TestClient, record_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_log_requests`` is registered last and is therefore the outermost
    user middleware, so it observes the origin-verify 403 that an inner
    layer would never see."""
    from channel.auth import tokens

    # The resolver is lru_cache'd; clear on both sides so neither a prior
    # test's value leaks in nor this one's leaks out.
    tokens._origin_verify_secret.cache_clear()
    monkeypatch.setenv("CHANNEL_ORIGIN_VERIFY_SECRET", "expected-secret")
    try:
        response = client.get("/health")
    finally:
        tokens._origin_verify_secret.cache_clear()

    assert response.status_code == 403
    call = _recorded(record_stub)
    assert call["status_code"] == 403
    # Rejected before routing, so no route matched.
    assert call["route"] == REQUEST_ROUTE_FALLBACK


def test_metric_emission_failure_never_breaks_the_request(
    client: TestClient, main_mod: ModuleType
) -> None:
    """Fail-soft: a CloudWatch hiccup degrades observability, not the API."""
    boom = AsyncMock(side_effect=RuntimeError("EMF sink unavailable"))
    with (
        patch.object(main_mod, "record_request_outcome", new=boom),
        patch.object(main_mod, "logger", wraps=main_mod.logger) as spy_logger,
    ):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    warned = [c for c in spy_logger.warning.call_args_list if "metric emission" in c.args[0]]
    assert warned, "the swallowed metric failure must still be logged"


def test_authenticated_request_seeds_client_id_from_the_jwt(
    client: TestClient, main_mod: ModuleType, record_stub
) -> None:
    """``client_id`` reaches the completion log line even though the auth
    dependency runs in a different task than the middleware — via
    ``request.state``, not the ContextVar the dependency also sets."""
    token = issue_mgmt_jwt(
        {
            "user_id": "user-observability-1",
            "email": "obs@test.com",
            "display_name": "Obs",
            "role": "user",
        }
    )
    with patch.object(main_mod, "set_request_context") as mock_ctx:
        response = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    # Last call is the post-``call_next`` re-seed; it carries the digest.
    request_id, client_id = mock_ctx.call_args.args
    assert request_id
    assert client_id == fingerprint_id("user-observability-1")
    # Never the raw JWT sub — in production that value is the user's email.
    assert "user-observability-1" not in client_id


def test_unauthenticated_request_leaves_client_id_empty(
    client: TestClient, main_mod: ModuleType, record_stub
) -> None:
    with patch.object(main_mod, "set_request_context") as mock_ctx:
        client.get("/health")

    assert mock_ctx.call_args.args[1] == ""

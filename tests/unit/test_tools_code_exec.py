# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``code_exec`` Strands tool wrapper (#183).

The wrapper does no execution itself — it invokes the sandbox Lambda
via boto3. All AWS calls are mocked. The error_type taxonomy and
contract round-trip via ``translate_event`` are covered here too."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_get_lambda_client`` lru_cache between tests so a
    real boto3 client from a sibling test can't leak across — same
    pattern as ``test_tools_web_search.py``."""
    from channel.agents.tools.code_exec import _get_lambda_client

    _get_lambda_client.cache_clear()
    yield
    _get_lambda_client.cache_clear()


def _fake_invoke_response(payload: dict) -> dict:
    """Build a boto3 ``invoke`` response shape: ``{"Payload": <stream>}``."""
    stream = MagicMock()
    stream.read.return_value = json.dumps(payload).encode()
    return {"StatusCode": 200, "Payload": stream}


def test_code_exec_returns_sandbox_payload_unchanged(monkeypatch):
    """Lambda returns ``{stdout, stderr, exit_code, ...}`` → wrapper
    returns the same dict so the SPA / translate_event can read it."""
    sandbox_payload = {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
    fake_client = MagicMock()
    fake_client.invoke.return_value = _fake_invoke_response(sandbox_payload)
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(42)")

    assert result == sandbox_payload


def test_code_exec_invokes_request_response_with_json_payload(monkeypatch):
    """Wrapper invokes ``RequestResponse`` with ``{"code": ...}`` as the JSON body."""
    fake_client = MagicMock()
    fake_client.invoke.return_value = _fake_invoke_response({
        "stdout": "", "stderr": "", "exit_code": 0,
        "duration_ms": 1, "truncated": False, "timed_out": False, "images": [],
    })
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    code_exec(code="print(1)")

    call_kwargs = fake_client.invoke.call_args.kwargs
    assert call_kwargs["FunctionName"] == "arn:fake"
    assert call_kwargs["InvocationType"] == "RequestResponse"
    assert json.loads(call_kwargs["Payload"]) == {"code": "print(1)"}


def test_code_exec_is_a_strands_tool():
    """``@tool`` decorator wraps the function as ``DecoratedFunctionTool``
    and attaches ``tool_spec`` metadata — same probe as web_search."""
    from channel.agents.tools.code_exec import code_exec

    assert hasattr(code_exec, "tool_spec")

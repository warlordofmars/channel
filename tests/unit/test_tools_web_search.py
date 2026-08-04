# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_search Exa wrapper (#182, #269).

**Transport-level by construction (#269).** Every error-path test drives
a real ``httpx.AsyncClient`` whose transport is an
``httpx.MockTransport``, so the status code the tool maps is one httpx
genuinely parsed off a response and raised through
``raise_for_status()`` — not a canned exception object handed to the
wrapper. That distinction is the whole point of the issue: the stable
vocabulary is *derived from the HTTP status*, the previous SDK-based
implementation discarded it, and a test that mocked the SDK's return
value therefore proved nothing about the derivation.
"""

from __future__ import annotations

import contextlib
import json
import logging

import httpx
import pytest


@contextlib.contextmanager
def _captured_logs(logger_name):
    """Capture one logger's output independently of propagation.

    ``caplog`` attaches to the ROOT logger, and
    ``logging_config.setup_logging()`` sets ``propagate = False`` on the
    ``channel`` logger — so once any sibling test in the session has
    initialised logging, ``caplog.text`` is empty here. That silently
    turns an assert-absence test (does the API key leak?) into a
    vacuous pass, which is worse than a failure. Attaching directly to
    the module's own logger is propagation-independent and behaves the
    same in an isolated run and in the full suite.

    Callers pass the shared ``channel.agents.tools`` parent rather
    than a module logger: ``_exa_post`` lives in ``web_search``, so
    a ``web_fetch`` upstream failure is logged by the *web_search*
    module logger under a ``web_fetch.`` prefix. Child-to-parent
    propagation is untouched by the ``channel`` logger's
    ``propagate = False`` (that only severs ``channel`` -> root)."""
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect()
    log = logging.getLogger(logger_name)
    previous_level = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_resolve_exa_api_key`` lru_cache between tests so a
    key resolved by a sibling test can't leak across — monkeypatching
    the env var away isn't enough; the cached value itself must go."""
    from channel.agents.tools.web_search import _resolve_exa_api_key

    _resolve_exa_api_key.cache_clear()
    yield
    _resolve_exa_api_key.cache_clear()


def _mock_exa(monkeypatch, *, json_body=None, status_code=200, raises=None, text_body=None):
    """Route the tool's ``httpx.AsyncClient`` through a ``MockTransport``.

    The tool builds its own client per call (by design), so the seam is
    the ``AsyncClient`` constructor rather than an injected instance.
    The REAL class is still used — only ``transport=`` is added — so
    ``client.post`` → ``raise_for_status`` → ``response.json`` all run
    httpx's genuine code path against the response below.

    Returns a dict capturing the constructor kwargs (for the timeout
    assertions) and every ``httpx.Request`` the tool actually sent (for
    URL / header / payload assertions)."""
    captured: dict = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["requests"].append(request)
        if raises is not None:
            raise raises
        if text_body is not None:
            return httpx.Response(status_code, text=text_body)
        return httpx.Response(status_code, json=json_body)

    real_client = httpx.AsyncClient

    def factory(**kwargs):
        captured["kwargs"] = kwargs
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return captured


def _sent_payload(captured):
    return json.loads(captured["requests"][0].content)


def _as_sse(tool_result):
    """Wrap a tool return value as the ``ToolResultEvent`` Strands emits
    for it (``toolUseId`` is required or ``translate_event`` skips)."""
    return {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-abc",
            "status": tool_result["status"],
            "content": tool_result["content"],
        },
    }


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_web_search_returns_tool_result_carrying_the_json_body(monkeypatch):
    """A 200 becomes the Strands ``ToolResult`` success envelope whose
    text block is the response body as JSON.

    #269 changed the serialization from the SDK's ``str(data)`` (a
    Python repr — single quotes, ``True``, ``None``) to ``json.dumps``.
    The envelope shape is unchanged, so the chassis and the SPA see
    exactly what they saw before; only the model's parse gets easier."""
    body = {
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "text": "Full page text here.",
                "favicon": None,
            }
        ]
    }
    _mock_exa(monkeypatch, json_body=body)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="latest RAG paper")

    assert result["status"] == "success"
    assert json.loads(result["content"][0]["text"]) == body
    # Not a Python repr — the SDK-era shape this replaces.
    assert "'" not in result["content"][0]["text"]


async def test_web_search_empty_results_is_success_not_error(monkeypatch):
    """Exa returns 0 hits → that's a successful search, not an error.
    The model decides whether 'no sources' should change its reply."""
    _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="something obscure")

    assert result["status"] == "success"
    assert json.loads(result["content"][0]["text"]) == {"results": []}


# ── Request shape ─────────────────────────────────────────────────────────────


async def test_web_search_sends_text_true_and_livecrawl_fallback_server_side(monkeypatch):
    """``text=True`` + ``livecrawl="fallback"`` are ALWAYS set by the
    wrapper regardless of caller args — the caller can't override them.

    The ``livecrawlTimeout`` pin is load-bearing: Exa's value is in
    MILLISECONDS, so a future "harmonization" to ``30`` (a 30 ms crawl
    budget — the #268 defect) must fail here, mirroring web_fetch's
    suite. ``/search`` nests these under ``contents``; ``/contents``
    takes them flat."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="anything")

    contents = _sent_payload(captured)["contents"]
    assert contents["text"] is True
    assert contents["livecrawl"] == "fallback"
    assert contents["livecrawlTimeout"] == 30_000


async def test_web_search_posts_to_exa_search_endpoint_with_key_header(monkeypatch):
    """The key travels as the ``x-api-key`` header. It is NOT written to
    ``os.environ`` any more — the SDK required that process-global
    mutation on every call, which a warm Lambda shares across concurrent
    invocations (#269)."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-secret")

    from channel.agents.tools.web_search import web_search

    await web_search(query="anything")

    request = captured["requests"][0]
    assert str(request.url) == "https://api.exa.ai/search"
    assert request.method == "POST"
    assert request.headers["x-api-key"] == "ek-secret"
    assert request.headers["x-exa-integration"] == "channel"


async def test_web_search_sets_bounded_timeouts(monkeypatch):
    """A direct HTTP client with no timeout would be a regression: the
    SDK inherited aiohttp's 5-minute default, far outside the chassis's
    120s wall-clock budget. The read budget must also sit ABOVE the 30s
    ``livecrawlTimeout`` we send, so Exa's crawl deadline is what
    expires first on a slow page."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="anything")

    timeout = captured["kwargs"]["timeout"]
    assert timeout.read == 45.0
    assert timeout.connect == 5.0
    assert timeout.read > 30.0


async def test_web_search_passes_query_and_optional_filters_through(monkeypatch):
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(
        query="latest RAG paper",
        num_results=3,
        category="research paper",
        include_domains=["arxiv.org"],
        exclude_domains=["spam.example"],
    )

    sent = _sent_payload(captured)
    assert sent["query"] == "latest RAG paper"
    assert sent["numResults"] == 3
    assert sent["category"] == "research paper"
    assert sent["includeDomains"] == ["arxiv.org"]
    assert sent["excludeDomains"] == ["spam.example"]
    assert sent["type"] == "auto"


async def test_web_search_omits_unset_optional_filters(monkeypatch):
    """``None``-valued filters are dropped rather than sent as null —
    matching the SDK's final compaction step, so Exa receives the same
    request it did before #269."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="anything")

    sent = _sent_payload(captured)
    assert "category" not in sent
    assert "includeDomains" not in sent
    assert "excludeDomains" not in sent


async def test_web_search_clamps_num_results_high(monkeypatch):
    """Model could ask for 100 results; wrapper clamps to 10."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="x", num_results=100)

    assert _sent_payload(captured)["numResults"] == 10


async def test_web_search_clamps_num_results_low(monkeypatch):
    """Model could ask for 0 results; wrapper clamps to 1."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="x", num_results=0)

    assert _sent_payload(captured)["numResults"] == 1


# ── Status-derived error vocabulary (#269) ────────────────────────────────────
#
# The tests #269 exists for. Each drives a real httpx response with a
# real status line through the real client, so the token can only come
# from the status code. Under the SDK every one of these arrived as
# ``{"status": "success", "content": [{"text": str(body)}]}`` and the
# SPA saw no ``tool_error`` frame at all.


@pytest.mark.parametrize(
    ("status_code", "expected_token"),
    [
        (429, "rate_limit"),
        (500, "upstream_5xx"),
        (502, "upstream_5xx"),
        (503, "upstream_5xx"),
        (599, "upstream_5xx"),
        (400, "bad_request"),
        (401, "bad_request"),
        (403, "bad_request"),
        (404, "bad_request"),
        (422, "bad_request"),
    ],
)
async def test_web_search_maps_http_status_to_stable_token(
    monkeypatch, status_code, expected_token
):
    # The body deliberately does NOT restate the status — the token must
    # be derived from the HTTP status line, which is precisely what the
    # SDK threw away.
    _mock_exa(monkeypatch, status_code=status_code, json_body={"error": "something went wrong"})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": expected_token}]}


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("handshake stalled"),
        httpx.PoolTimeout("no free connection"),
    ],
)
async def test_web_search_timeout_returns_timeout_token(monkeypatch, exc):
    """Every ``TimeoutException`` subclass maps to ``timeout`` — and the
    handler ORDER is load-bearing: ``TimeoutException`` subclasses
    ``TransportError``, so a wider catch placed first would silently
    swallow all three into ``connection_error``."""
    _mock_exa(monkeypatch, raises=exc)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "timeout"}]}


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("dns failure"),
        httpx.ReadError("connection reset"),
        httpx.ProtocolError("malformed frame"),
    ],
)
async def test_web_search_transport_failure_returns_connection_error(monkeypatch, exc):
    """We never reached Exa. Deliberately NOT folded into
    ``upstream_5xx`` (Exa reached, Exa broke): the operator response
    differs, and folding would make the log line say something untrue."""
    _mock_exa(monkeypatch, raises=exc)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "connection_error"}]}


async def test_web_search_non_json_200_body_returns_bad_response(monkeypatch):
    """Exa answered 200 with something we can't hand the model. Without
    a handler the ``json.JSONDecodeError`` escapes as a raw exception
    through the chassis — the unstable-prose failure mode #269 closes."""
    _mock_exa(monkeypatch, text_body="<html>gateway splash page</html>")
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "bad_response"}]}


async def test_web_search_returns_missing_key_when_resolve_fails(monkeypatch):
    """When ``_resolve_exa_api_key()`` raises (no env var + no SSM
    access), the wrapper returns the structured error rather than
    letting the exception bubble up through the chassis — and never
    issues a request."""
    captured = _mock_exa(monkeypatch, json_body={"results": []})
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("CHANNEL_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "missing_key"}]}
    assert captured["requests"] == []


async def test_api_key_never_appears_in_logs_or_returned_payload(monkeypatch):
    """The key is a credential: it may ride the request header and
    nothing else. Exercised on the failure path, where a careless
    implementation would log the request or echo it into the error."""
    captured = _mock_exa(monkeypatch, status_code=503, json_body={"error": "upstream is unwell"})
    monkeypatch.setenv("EXA_API_KEY", "ek-super-secret-value")

    from channel.agents.tools.web_search import web_search

    with _captured_logs("channel.agents.tools") as records:
        result = await web_search(query="anything")

    # The failure path must have logged *something*, or the leak
    # assertion below would hold trivially.
    assert records
    assert "ek-super-secret-value" not in "\n".join(records)
    assert "ek-super-secret-value" not in json.dumps(result)
    # …but it did reach Exa.
    assert captured["requests"][0].headers["x-api-key"] == "ek-super-secret-value"


# ── Helper-level unit coverage ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [(429, "rate_limit"), (500, "upstream_5xx"), (503, "upstream_5xx"), (418, "bad_request")],
)
def test_classify_status(code, expected):
    from channel.agents.tools.web_search import _classify_status

    assert _classify_status(code) == expected


def test_resolve_exa_api_key_prefers_env_var(monkeypatch):
    """Local dev path: EXA_API_KEY env var short-circuits the SSM call."""
    monkeypatch.setenv("EXA_API_KEY", "ek-from-env")

    from channel.agents.tools.web_search import _resolve_exa_api_key

    _resolve_exa_api_key.cache_clear()

    assert _resolve_exa_api_key() == "ek-from-env"


def test_web_search_is_a_strands_tool():
    """Strands' @tool decorator wraps the function as DecoratedFunctionTool
    and attaches tool_spec metadata. Same probe shape as current_time
    in PR-1 Task 4."""
    from channel.agents.tools.web_search import web_search

    assert hasattr(web_search, "tool_spec")


def test_web_search_underlying_is_async_so_strands_awaits_it():
    """REGRESSION GUARD for the 2026-06-07 coroutine-leak bug.

    ``web_search`` must be ``async def``: Strands'
    ``DecoratedFunctionTool.stream`` ``await``s ``iscoroutinefunction``
    tools and routes everything else through ``asyncio.to_thread``. If a
    future refactor makes ``web_search`` sync, the async HTTP call
    inside it would be shipped to the model as ``<coroutine object ...>``
    text. The bug surfaced only on the live dev environment (unit tests
    with sync mocks had passed). This guard catches the regression at
    the type-introspection layer."""
    import inspect

    from channel.agents.tools.web_search import web_search

    underlying = web_search._tool_func
    assert inspect.iscoroutinefunction(underlying), (
        "web_search must remain async — it awaits an httpx request, and a "
        "sync wrapper ships a coroutine object to the model instead of the "
        "resolved search results."
    )


# ── SSE contract round-trips ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status_code", "expected_token"),
    [(429, "rate_limit"), (503, "upstream_5xx"), (400, "bad_request")],
)
async def test_status_errors_round_trip_through_translate_event(
    monkeypatch, status_code, expected_token
):
    """The half of the contract #232's review found broken.

    An upstream HTTP failure must reach the SPA as a ``tool_error``
    frame whose ``error_type`` is the exact stable token. Before #269
    the SDK turned each of these into a *successful* tool result
    carrying Exa's error JSON, so ``translate_event`` produced no
    ``tool_error`` frame at all."""
    _mock_exa(monkeypatch, status_code=status_code, json_body={"error": "nope"})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_search import web_search

    kind, payload = translate_event(_as_sse(await web_search(query="anything")))

    assert kind == "tool_error"
    assert payload["error_type"] == expected_token


async def test_timeout_round_trips_through_translate_event(monkeypatch):
    """End-to-end contract test for the SSE error-type chain.

    The wrapper returns a ``ToolResult``-shaped dict; Strands' ``@tool``
    decorator preserves it (vs re-wrapping a plain ``error_type`` dict
    as a JSON-stringified text block); ``translate_event`` extracts the
    reason string from ``content[].text`` blocks. This test fails if any
    link in that chain breaks — a defensive guard against re-introducing
    the original bug where the wrapper returned ``{"status": "error",
    "error_type": "timeout"}`` and the SPA saw ``error_type="tool_failed"``."""
    _mock_exa(monkeypatch, raises=httpx.ReadTimeout("slow"))
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_search import web_search

    kind, payload = translate_event(_as_sse(await web_search(query="anything")))

    assert kind == "tool_error"
    assert payload["error_type"] == "timeout"


async def test_connection_error_round_trips_through_translate_event(monkeypatch):
    """``connection_error`` is new in #269 — pin its SSE round-trip too,
    since a token the SPA never receives is a token that doesn't exist."""
    _mock_exa(monkeypatch, raises=httpx.ConnectError("dns"))
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_search import web_search

    kind, payload = translate_event(_as_sse(await web_search(query="anything")))

    assert kind == "tool_error"
    assert payload["error_type"] == "connection_error"


async def test_success_round_trips_as_something_other_than_tool_error(monkeypatch):
    """The mirror assertion: a 200 must NOT produce a ``tool_error``
    frame. Guards against an over-eager error path reclassifying
    successes."""
    _mock_exa(monkeypatch, json_body={"results": [{"url": "https://example.com"}]})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_search import web_search

    kind, _payload = translate_event(_as_sse(await web_search(query="anything")))

    assert kind != "tool_error"

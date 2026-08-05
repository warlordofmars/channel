# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_fetch Exa wrapper (#232, #269).

Mirrors ``test_tools_web_search.py`` — same lazy-cache fixture, same
``httpx.MockTransport`` seam, same status→token assertions — plus the
two tokens only this tool can reach: ``invalid_url`` (client-side URL
validation) and ``fetch_failed`` (Exa answered 200 but the crawl of the
requested URL failed).

Both tools share ``_exa_post``, so the status-mapping tests here are
deliberately not deduplicated against ``web_search``'s: the point of
#232's parity requirement is that *each tool* is pinned to the
vocabulary, so a future divergence in either wrapper is caught by that
wrapper's own suite.
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
    ``propagate = False`` (that only severs ``channel`` -> root). Twin of the helper
    in ``test_tools_web_search.py``."""
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
    key resolved by a sibling test can't leak across. It lives in
    ``web_search`` — ``web_fetch`` imports it so both tools share one
    warm-pool SSM resolution."""
    from channel.agents.tools.web_search import _resolve_exa_api_key

    _resolve_exa_api_key.cache_clear()
    yield
    _resolve_exa_api_key.cache_clear()


def _mock_exa(monkeypatch, *, json_body=None, status_code=200, raises=None, text_body=None):
    """Route the tool's ``httpx.AsyncClient`` through a ``MockTransport``.

    See the twin helper in ``test_tools_web_search.py`` — the REAL
    ``AsyncClient`` is used with only a ``transport=`` added, so
    ``raise_for_status`` derives the token from a genuine status line
    rather than from a canned error object."""
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


_OK_BODY = {
    "results": [
        {"id": "https://example.com/page", "url": "https://example.com/page", "text": "Page."}
    ],
    "statuses": [{"id": "https://example.com/page", "status": "success"}],
}


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_web_fetch_returns_tool_result_carrying_the_json_body(monkeypatch):
    """A 200 with content becomes the Strands ``ToolResult`` success
    envelope whose text block is the response body as JSON (#269 —
    previously a Python repr produced by the SDK's ``str(data)``)."""
    _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result["status"] == "success"
    assert json.loads(result["content"][0]["text"]) == _OK_BODY


# ── Request shape ─────────────────────────────────────────────────────────────


async def test_web_fetch_posts_to_contents_endpoint_wrapping_url_in_a_list(monkeypatch):
    """Exa's contents endpoint takes ``urls: list`` — the wrapper exposes
    a single ``url`` arg (multi-URL fetch is out of scope per #232) and
    wraps it."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-secret")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page")

    request = captured["requests"][0]
    assert str(request.url) == "https://api.exa.ai/contents"
    assert request.headers["x-api-key"] == "ek-secret"
    assert request.headers["x-exa-integration"] == "channel"
    assert _sent_payload(captured)["urls"] == ["https://example.com/page"]


async def test_web_fetch_forces_text_true_and_livecrawl_fallback(monkeypatch):
    """``text=True`` + ``livecrawl="fallback"`` + a 30_000 ms crawl budget
    are ALWAYS set when no ``max_chars`` cap is given — matching
    ``web_search``'s extraction quality. The timeout assertion is
    load-bearing: Exa's ``livecrawlTimeout`` is in MILLISECONDS, and
    "harmonizing" it down to ``30`` would be a 30 ms crawl budget that
    fails every uncached fetch in production. Note ``/contents`` takes
    these FLAT, unlike ``/search``'s nested ``contents`` block."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page")

    sent = _sent_payload(captured)
    assert sent["text"] is True
    assert sent["livecrawl"] == "fallback"
    assert sent["livecrawlTimeout"] == 30_000


async def test_web_fetch_sets_bounded_timeouts(monkeypatch):
    """Parity with ``web_search``: a direct HTTP client with no timeout
    would be a regression against the chassis's 120s budget."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page")

    timeout = captured["kwargs"]["timeout"]
    assert timeout.read == 45.0
    assert timeout.connect == 5.0


async def test_web_fetch_max_chars_maps_to_exa_max_characters(monkeypatch):
    """``max_chars`` becomes Exa's server-side ``text.maxCharacters`` cap —
    no client-side slicing."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page", max_chars=5000)

    assert _sent_payload(captured)["text"] == {"maxCharacters": 5000}


async def test_web_fetch_clamps_max_chars_low(monkeypatch):
    """Model could ask for 0 (or negative) chars; Exa rejects non-positive
    ``maxCharacters``, so the wrapper clamps to 1."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page", max_chars=0)

    assert _sent_payload(captured)["text"] == {"maxCharacters": 1}


async def test_web_fetch_strips_surrounding_whitespace_before_fetching(monkeypatch):
    """User-pasted URLs commonly carry stray whitespace. Python 3.12's
    ``urlparse`` strips it before parsing (so validation already passes),
    but the string handed to Exa must be the CLEANED one — un-stripped,
    ``"https://example.com "`` even keeps the trailing space inside
    ``netloc``. Normalize once at entry (Copilot review, PR #255)."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="  https://example.com/page  ")

    assert _sent_payload(captured)["urls"] == ["https://example.com/page"]


async def test_web_fetch_accepts_uppercase_scheme(monkeypatch):
    """urlparse lowercases the scheme — ``HTTPS://`` is a valid fetch, not
    an ``invalid_url``."""
    _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="HTTPS://EXAMPLE.COM/page")

    assert result["status"] == "success"


# ── Client-side validation ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_url",
    [
        "not a url",
        "ftp://example.com/file.txt",
        "javascript:alert(1)",
        "http://",
        "//example.com/page",
        "http://[::1",  # unclosed IPv6 bracket — urlparse raises ValueError
        # Userinfo URLs: credentials must never transit to Exa —
        # authenticated fetching is out of scope (#232); mirrors
        # mcp.url_guard.validate_mcp_server_url's rule.
        "https://user:pass@example.com/page",
        "https://token@example.com/page",
        # Internal whitespace / control chars: urlparse strips many of
        # them PRE-parse, so without an explicit reject the validator
        # would judge a cleaned string while Exa receives the raw one.
        "https://exam ple.com/page",
        "https://example.com/pa\nth",
        "https://:8080/page",  # port-only authority — netloc truthy, hostname empty
    ],
)
async def test_web_fetch_rejects_invalid_urls(monkeypatch, bad_url):
    """Malformed / non-http(s) / credential-bearing URLs short-circuit to
    ``invalid_url`` BEFORE key resolution or any HTTP call — both keys
    are unset here, so a ``missing_key`` result would mean the
    validation order regressed."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("CHANNEL_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url=bad_url)

    assert result == {"status": "error", "content": [{"text": "invalid_url"}]}
    assert captured["requests"] == []


async def test_web_fetch_returns_missing_key_when_resolve_fails(monkeypatch):
    """When ``_resolve_exa_api_key()`` raises (no env var + no SSM access),
    the wrapper returns the structured error rather than letting the
    exception bubble up through the chassis."""
    captured = _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("CHANNEL_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "missing_key"}]}
    assert captured["requests"] == []


# ── Status-derived error vocabulary (#269) ────────────────────────────────────
#
# Until #269 these four tokens were UNREACHABLE from web_fetch: the
# installed ``strands_tools.exa`` caught its own network failures
# internally, never read ``response.status``, and wrapped 429/5xx error
# bodies as nominal successes. The handlers below are now the primary
# error path, exercised through a real httpx status line.


@pytest.mark.parametrize(
    ("status_code", "expected_token"),
    [
        (429, "rate_limit"),
        (500, "upstream_5xx"),
        (503, "upstream_5xx"),
        (599, "upstream_5xx"),
        (400, "bad_request"),
        (401, "bad_request"),
        (404, "bad_request"),
    ],
)
async def test_web_fetch_maps_http_status_to_stable_token(monkeypatch, status_code, expected_token):
    # The body deliberately does not restate the status — the token must
    # be derived from the HTTP status line.
    _mock_exa(monkeypatch, status_code=status_code, json_body={"error": "something went wrong"})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": expected_token}]}


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("stalled"), httpx.PoolTimeout("no conn")],
)
async def test_web_fetch_timeout_returns_timeout_token(monkeypatch, exc):
    _mock_exa(monkeypatch, raises=exc)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "timeout"}]}


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("dns failure"), httpx.ReadError("connection reset")]
)
async def test_web_fetch_transport_failure_returns_connection_error(monkeypatch, exc):
    _mock_exa(monkeypatch, raises=exc)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "connection_error"}]}


async def test_web_fetch_non_json_200_body_returns_bad_response(monkeypatch):
    _mock_exa(monkeypatch, text_body="<html>splash</html>")
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "bad_response"}]}


async def test_api_key_never_appears_in_logs_or_returned_payload(monkeypatch):
    """Parity with ``web_search``: the key rides the header and nothing
    else, including on the failure path."""
    captured = _mock_exa(monkeypatch, status_code=503, json_body={"error": "unwell"})
    monkeypatch.setenv("EXA_API_KEY", "ek-super-secret-value")

    from channel.agents.tools.web_fetch import web_fetch

    with _captured_logs("channel.agents.tools") as records:
        result = await web_fetch(url="https://example.com/page")

    # The failure path must have logged *something*, or the leak
    # assertion below would hold trivially.
    assert records
    assert "ek-super-secret-value" not in "\n".join(records)
    assert "ek-super-secret-value" not in json.dumps(result)
    assert captured["requests"][0].headers["x-api-key"] == "ek-super-secret-value"


# ── 200-with-crawl-failure → fetch_failed (#269) ──────────────────────────────
#
# The "failures masquerade as success" limb of #232's finding, and the
# one case no status code can express: /contents answers 200 while the
# per-URL verdict lives in ``statuses[]``.


@pytest.mark.parametrize(
    "tag",
    ["CRAWL_NOT_FOUND", "CRAWL_TIMEOUT", "CRAWL_LIVECRAWL_ERROR", "SOME_FUTURE_TAG"],
)
async def test_web_fetch_200_with_crawl_error_returns_fetch_failed(monkeypatch, tag):
    """ANY tag maps to the one token. Exa's tag vocabulary is
    undocumented, so enumerating it would recreate exactly the brittle
    upstream-string coupling #269 removes."""
    _mock_exa(
        monkeypatch,
        json_body={
            "results": [],
            "statuses": [
                {
                    "id": "https://example.com/page",
                    "status": "error",
                    "error": {"tag": tag, "httpStatusCode": 404},
                }
            ],
        },
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "fetch_failed"}]}


async def test_web_fetch_absent_results_key_with_error_status_still_fails(monkeypatch):
    """The mirror of the unfamiliar-shape rule above: `results` ABSENT
    (not merely empty) alongside an error status is still a genuine
    crawl failure.

    Tightening the guard to require the key to be present would let Exa
    reintroduce #269's exact bug — a failure reaching the model as a
    nominal success — just by omitting `results` on the failure path."""
    _mock_exa(
        monkeypatch,
        json_body={
            "statuses": [
                {"id": "https://example.com/page", "status": "error", "error": {"tag": "CRAWL_404"}}
            ]
        },
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "fetch_failed"}]}


async def test_web_fetch_crawl_error_tag_is_logged_not_enumerated(monkeypatch):
    """The tag carries the diagnosis, so it belongs in the log line even
    though it never branches the token."""
    _mock_exa(
        monkeypatch,
        json_body={
            "results": [],
            "statuses": [
                {"id": "https://example.com/page", "status": "error", "error": {"tag": "CRAWL_403"}}
            ],
        },
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    with _captured_logs("channel.agents.tools") as records:
        await web_fetch(url="https://example.com/page")

    assert "CRAWL_403" in "\n".join(records)


async def test_web_fetch_partial_success_still_returns_content(monkeypatch):
    """A 200 that DID return content stays on the success path even when
    a status entry reports an error — we requested exactly one URL, and
    content present means the fetch worked."""
    body = {
        "results": [{"id": "https://example.com/page", "text": "Page."}],
        "statuses": [
            {"id": "https://example.com/page", "status": "error", "error": {"tag": "CRAWL_WARN"}}
        ],
    }
    _mock_exa(monkeypatch, json_body=body)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result["status"] == "success"
    assert json.loads(result["content"][0]["text"]) == body


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"results": [], "statuses": []}, id="empty-statuses"),
        pytest.param({"results": []}, id="no-statuses-key"),
        pytest.param({"results": [], "statuses": "unexpected"}, id="statuses-not-a-list"),
        pytest.param(
            {"results": [], "statuses": [{"id": "u", "status": "success"}]}, id="status-not-error"
        ),
        pytest.param({"results": [], "statuses": ["unexpected"]}, id="entry-not-a-dict"),
        pytest.param([], id="body-not-a-dict"),
        # `results` present but not a list: an unfamiliar envelope, not
        # a crawl failure. Treating an unknown shape as "no content"
        # would turn a valid-but-unexpected 2xx into a phantom
        # fetch_failed (Copilot review, PR #540). Each of these carries
        # an error status, so only the results-shape check keeps them
        # on the success path.
        pytest.param(
            {"results": "", "statuses": [{"id": "u", "status": "error", "error": {"tag": "T"}}]},
            id="results-empty-string",
        ),
        pytest.param(
            {"results": {}, "statuses": [{"id": "u", "status": "error", "error": {"tag": "T"}}]},
            id="results-empty-dict",
        ),
        pytest.param(
            {"results": 0, "statuses": [{"id": "u", "status": "error", "error": {"tag": "T"}}]},
            id="results-zero",
        ),
        pytest.param(
            {
                "results": {"a": 1},
                "statuses": [{"id": "u", "status": "error", "error": {"tag": "T"}}],
            },
            id="results-non-empty-dict",
        ),
    ],
)
async def test_web_fetch_non_error_status_shapes_stay_on_success_path(monkeypatch, body):
    """``_crawl_failed_tag`` must only fire on the specific
    error-with-no-results shape. Anything it doesn't recognise passes
    through to the model — a heuristic that guessed here would turn
    unfamiliar-but-fine responses into phantom failures."""
    _mock_exa(monkeypatch, json_body=body)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result["status"] == "success"


@pytest.mark.parametrize(
    ("error_value", "expected_tag"),
    [
        pytest.param({"tag": "CRAWL_404"}, "CRAWL_404", id="tag-present"),
        pytest.param({"tag": ""}, "unknown", id="tag-empty"),
        pytest.param({}, "unknown", id="tag-missing"),
        pytest.param("a string", "unknown", id="error-not-a-dict"),
        pytest.param(None, "unknown", id="error-absent"),
    ],
)
def test_crawl_failed_tag_degrades_to_unknown(error_value, expected_tag):
    from channel.agents.tools.web_fetch import _crawl_failed_tag

    entry: dict = {"id": "https://example.com/page", "status": "error"}
    if error_value is not None:
        entry["error"] = error_value

    assert _crawl_failed_tag({"results": [], "statuses": [entry]}) == expected_tag


# ── Tool-shape guards ─────────────────────────────────────────────────────────


def test_web_fetch_is_a_strands_tool():
    """Strands' @tool decorator wraps the function as DecoratedFunctionTool
    and attaches tool_spec metadata. Same probe shape as ``web_search``."""
    from channel.agents.tools.web_fetch import web_fetch

    assert hasattr(web_fetch, "tool_spec")


def test_web_fetch_underlying_is_async_so_strands_awaits_it():
    """Parity guard with ``web_search``'s 2026-06-07 coroutine-leak fix.

    ``web_fetch`` must be ``async def`` because it awaits an httpx
    request. A sync wrapper would route through Strands'
    ``asyncio.to_thread`` branch and ship a coroutine repr to the model
    instead of the fetched page. See
    ``test_web_search_underlying_is_async_so_strands_awaits_it``."""
    import inspect

    from channel.agents.tools.web_fetch import web_fetch

    assert inspect.iscoroutinefunction(web_fetch._tool_func)


def test_web_fetch_shares_web_searchs_error_helpers():
    """The two tools must not grow a second error vocabulary — #232's
    parity requirement, and the reason ``_error_result`` /
    ``_success_result`` / ``_exa_post`` live in one module."""
    from channel.agents.tools import web_fetch as fetch_mod
    from channel.agents.tools import web_search as search_mod

    assert fetch_mod._error_result is search_mod._error_result
    assert fetch_mod._success_result is search_mod._success_result
    assert fetch_mod._exa_post is search_mod._exa_post
    assert fetch_mod._resolve_exa_api_key is search_mod._resolve_exa_api_key


# ── SSE contract round-trips ──────────────────────────────────────────────────


async def test_invalid_url_error_round_trips_through_translate_event(monkeypatch):
    """End-to-end contract test for the SSE error-type chain, exercising
    the ``invalid_url`` token that is unique to web_fetch: the wrapper
    returns a ``ToolResult``-shaped dict, Strands' ``@tool`` decorator
    preserves it, and ``translate_event`` extracts the reason string from
    ``content[].text`` — so the SPA renders ``invalid_url`` rather than a
    generic ``tool_failed``."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("CHANNEL_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    kind, payload = translate_event(_as_sse(await web_fetch(url="not a url")))

    assert kind == "tool_error"
    assert payload["error_type"] == "invalid_url"


@pytest.mark.parametrize(
    ("status_code", "expected_token"),
    [(429, "rate_limit"), (503, "upstream_5xx"), (404, "bad_request")],
)
async def test_status_errors_round_trip_through_translate_event(
    monkeypatch, status_code, expected_token
):
    """The half of the contract #232's review found broken, for
    web_fetch: an upstream HTTP failure must reach the SPA as a
    ``tool_error`` frame carrying the exact stable token."""
    _mock_exa(monkeypatch, status_code=status_code, json_body={"error": "nope"})
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    kind, payload = translate_event(_as_sse(await web_fetch(url="https://example.com/page")))

    assert kind == "tool_error"
    assert payload["error_type"] == expected_token


async def test_fetch_failed_round_trips_through_translate_event(monkeypatch):
    """``fetch_failed`` is new in #269 and is the token for the case that
    previously reached the model as a *successful* tool result carrying
    an error payload — so its SSE round-trip is the assertion that the
    masquerade is over."""
    _mock_exa(
        monkeypatch,
        json_body={
            "results": [],
            "statuses": [
                {"id": "https://example.com/page", "status": "error", "error": {"tag": "CRAWL_404"}}
            ],
        },
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    kind, payload = translate_event(_as_sse(await web_fetch(url="https://example.com/page")))

    assert kind == "tool_error"
    assert payload["error_type"] == "fetch_failed"


async def test_timeout_round_trips_through_translate_event(monkeypatch):
    _mock_exa(monkeypatch, raises=httpx.ReadTimeout("slow"))
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    kind, payload = translate_event(_as_sse(await web_fetch(url="https://example.com/page")))

    assert kind == "tool_error"
    assert payload["error_type"] == "timeout"


async def test_success_round_trips_as_something_other_than_tool_error(monkeypatch):
    """The mirror assertion: a 200 with content must NOT produce a
    ``tool_error`` frame."""
    _mock_exa(monkeypatch, json_body=_OK_BODY)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    kind, _payload = translate_event(_as_sse(await web_fetch(url="https://example.com/page")))

    assert kind != "tool_error"

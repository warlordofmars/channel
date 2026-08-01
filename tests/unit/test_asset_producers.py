# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the asset producers (#326, epic #321).

Covers the fenced-code extraction rules (line threshold, mermaid
exclusion, fence_index ordinals, title resolution), the three persist
producers (upload projection, code-exec images, fence extraction) with
their fail-soft per-asset isolation, the kill-switch, and the card
descriptor shape the SSE frames carry.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest

from channel.agents import asset_producers as ap
from channel.models import ASSET_INLINE_CONTENT_MAX_BYTES, Asset, Attachment

# ----------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------


def _fence(lines: int, lang: str = "python", info_extra: str = "") -> str:
    info = f"{lang} {info_extra}".strip()
    body = "\n".join(f"line_{i}" for i in range(lines))
    return f"```{info}\n{body}\n```"


def _attachment(att_id: str = "att-1", mime: str = "image/png") -> Attachment:
    return Attachment(
        id=att_id,
        user_id="u-1",
        name=f"{att_id}.bin",
        mime=mime,
        size_bytes=42,
        s3_key=f"attachments/user/u-1/{att_id}",
        s3_bucket="channel-attachments-test",
        checksum_sha256="0" * 64,
        created_at="2026-07-13T00:00:00.000000+00:00",
    )


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("channel.agents.asset_producers.record_asset_persist_outcome", mock)
    return mock


@pytest.fixture
def put_asset_capture(monkeypatch: pytest.MonkeyPatch) -> list[Asset]:
    captured: list[Asset] = []
    monkeypatch.setattr(
        "channel.agents.asset_producers.storage.put_asset",
        lambda asset: captured.append(asset),
    )
    return captured


@pytest.fixture
def put_bytes_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_put_bytes(*, chat_id: str, asset_id: str, data: bytes, mime: str) -> tuple[str, str]:
        captured.append({"chat_id": chat_id, "asset_id": asset_id, "data": data, "mime": mime})
        return "channel-attachments-test", f"assets/chat/{chat_id}/{asset_id}"

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset_bytes", fake_put_bytes)
    return captured


# ----------------------------------------------------------------
# extract_code_fences — parsing rules
# ----------------------------------------------------------------


def test_extract_single_fence_with_lang() -> None:
    text = "Intro.\n\n" + _fence(15) + "\n\nOutro."
    fences = ap.extract_code_fences(text)
    assert len(fences) == 1
    f = fences[0]
    assert f.fence_index == 0
    assert f.lang == "python"
    assert f.line_count == 15
    assert f.body.startswith("line_0") and f.body.endswith("line_14")


def test_fence_index_counts_every_fence_including_mermaid_and_short() -> None:
    text = "\n\n".join(
        [
            _fence(3),  # index 0 — short
            "```mermaid\n" + "\n".join(["a --> b"] * 20) + "\n```",  # index 1 — mermaid
            _fence(20, lang="js"),  # index 2 — qualifying
        ]
    )
    fences = ap.extract_code_fences(text)
    assert [f.fence_index for f in fences] == [0, 1, 2]
    qualifying = ap._qualifying_fences(fences)
    assert [f.fence_index for f in qualifying] == [2]
    assert qualifying[0].lang == "js"


def test_title_from_info_string_extra_tokens() -> None:
    text = _fence(15, lang="python", info_extra="fib.py")
    (fence,) = ap.extract_code_fences(text)
    assert fence.title == "fib.py"


def test_title_from_nearest_preceding_heading() -> None:
    text = "## Solution outline\n\nSome prose.\n\n" + _fence(15)
    (fence,) = ap.extract_code_fences(text)
    assert fence.title == "Solution outline"


def test_title_fallback_code_with_lang() -> None:
    (fence,) = ap.extract_code_fences(_fence(15, lang="rust"))
    assert fence.title == "Code — rust"


def test_title_fallback_bare_code_without_info_string() -> None:
    body = "\n".join(["x"] * 15)
    (fence,) = ap.extract_code_fences(f"```\n{body}\n```")
    assert fence.title == "Code"
    assert fence.lang == ""


def test_heading_inside_fence_body_is_not_a_title_source() -> None:
    body = "# not a heading\n" + "\n".join(["x"] * 15)
    text = f"```python\n{body}\n```\n\n" + _fence(15, lang="go")
    fences = ap.extract_code_fences(text)
    # Second fence must NOT pick up the in-body "# not a heading" line.
    assert fences[1].title == "Code — go"


def test_unclosed_fence_captures_remaining_lines() -> None:
    body = "\n".join(f"row_{i}" for i in range(16))
    (fence,) = ap.extract_code_fences(f"```python\n{body}")
    assert fence.line_count == 16
    assert fence.body.endswith("row_15")


def test_closer_requires_at_least_openers_backticks() -> None:
    # A 4-backtick fence embedding a 3-backtick fence: the inner ```
    # lines are body, only ```` closes.
    inner = "```python\n" + "\n".join(["y"] * 15) + "\n```"
    text = f"````markdown\n{inner}\n````"
    (fence,) = ap.extract_code_fences(text)
    assert fence.lang == "markdown"
    assert "```python" in fence.body
    assert fence.line_count == 17


def test_fence_with_up_to_three_leading_spaces_opens() -> None:
    body = "\n".join(["z"] * 15)
    (fence,) = ap.extract_code_fences(f"   ```python\n{body}\n   ```")
    assert fence.lang == "python"
    assert fence.line_count == 15


def test_no_fences_returns_empty() -> None:
    assert ap.extract_code_fences("Just prose, no code.") == []


# ----------------------------------------------------------------
# _qualifying_fences — threshold + exclusion rules
# ----------------------------------------------------------------


def test_fourteen_line_fence_does_not_qualify() -> None:
    fences = ap.extract_code_fences(_fence(14))
    assert ap._qualifying_fences(fences) == []


def test_fifteen_line_fence_qualifies() -> None:
    fences = ap.extract_code_fences(_fence(15))
    assert len(ap._qualifying_fences(fences)) == 1


def test_mermaid_fence_excluded_even_when_long() -> None:
    text = "```mermaid\n" + "\n".join(["a --> b"] * 30) + "\n```"
    fences = ap.extract_code_fences(text)
    assert len(fences) == 1
    assert ap._qualifying_fences(fences) == []


# ----------------------------------------------------------------
# Kill-switch
# ----------------------------------------------------------------


def test_extraction_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    assert ap.asset_extraction_enabled() is True


def test_extraction_disabled_when_flag_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_ASSET_EXTRACTION_ENABLED", "0")
    assert ap.asset_extraction_enabled() is False


# ----------------------------------------------------------------
# asset_card_descriptor
# ----------------------------------------------------------------


def test_card_descriptor_shape_matches_decision_q4() -> None:
    asset = Asset(
        asset_id="a-1",
        chat_id="c-1",
        owner="u-1",
        kind="code",
        title="fib.py",
        mime="text/plain",
        size_bytes=10,
        origin="generated",
        source={"msg_id": "m-9", "fence_index": 2, "lang": "python"},
        content="x" * 10,
        created_at="2026-07-13T00:00:00.000000+00:00",
        updated_at="2026-07-13T00:00:00.000000+00:00",
    )
    descriptor = ap.asset_card_descriptor(asset)
    assert descriptor == {
        "asset_id": "a-1",
        "chat_id": "c-1",
        "msg_id": "m-9",
        "kind": "code",
        "title": "fib.py",
        "mime": "text/plain",
        "size_bytes": 10,
        "origin": "generated",
        "created_at": "2026-07-13T00:00:00.000000+00:00",
        "source": {"msg_id": "m-9", "fence_index": 2, "lang": "python"},
    }
    # Payload coordinates must never ride the frame.
    assert "content" not in descriptor
    assert "s3_bucket" not in descriptor
    assert "s3_key" not in descriptor


# ----------------------------------------------------------------
# persist_upload_assets
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_projection_reuses_s3_object_and_derives_kind(
    put_asset_capture: list[Asset], metrics: AsyncMock
) -> None:
    atts = [
        _attachment("att-img", mime="image/png"),
        _attachment("att-csv", mime="text/csv"),
        _attachment("att-pdf", mime="application/pdf"),
        _attachment("att-odd", mime="application/x-unknown"),
    ]
    persisted = await ap.persist_upload_assets(
        chat_id="c-1", owner="u-1", msg_id="m-user", attachments=atts
    )
    assert [a.kind for a in persisted] == ["image", "data", "document", "document"]
    assert persisted == put_asset_capture
    for asset, att in zip(persisted, atts, strict=True):
        assert asset.origin == "upload"
        assert asset.s3_bucket == att.s3_bucket
        assert asset.s3_key == att.s3_key  # SAME object — no byte copy
        assert asset.content is None
        assert asset.title == att.name
        assert asset.size_bytes == att.size_bytes
        assert asset.source == {"msg_id": "m-user", "attachment_id": att.id}
        assert asset.owner == "u-1"
    assert metrics.await_count == 4
    metrics.assert_awaited_with(success=True)


@pytest.mark.asyncio
async def test_upload_projection_failure_is_isolated_per_asset(
    monkeypatch: pytest.MonkeyPatch, metrics: AsyncMock
) -> None:
    calls: list[Asset] = []

    def flaky_put(asset: Asset) -> None:
        if asset.source["attachment_id"] == "att-bad":
            raise RuntimeError("ddb down")
        calls.append(asset)

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", flaky_put)
    persisted = await ap.persist_upload_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-user",
        attachments=[_attachment("att-bad"), _attachment("att-ok")],
    )
    assert [a.source["attachment_id"] for a in persisted] == ["att-ok"]
    assert metrics.await_args_list[0].kwargs == {"success": False}
    assert metrics.await_args_list[1].kwargs == {"success": True}


# ----------------------------------------------------------------
# persist_code_exec_image_assets
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_exec_images_persist_to_s3_with_figure_titles(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    png = b"\x89PNG-fake-bytes"
    jpg = b"\xff\xd8fake-jpeg"
    images_by_tool = [
        ("tool-1", [{"mime": "image/png", "b64": base64.b64encode(png).decode()}]),
        ("tool-2", [{"mime": "image/jpeg", "b64": base64.b64encode(jpg).decode()}]),
    ]
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-assistant", images_by_tool=images_by_tool
    )
    assert [a.title for a in persisted] == ["Figure 1", "Figure 2"]
    assert [a.kind for a in persisted] == ["image", "image"]
    assert [a.origin for a in persisted] == ["tool_output", "tool_output"]
    assert persisted[0].source == {"msg_id": "m-assistant", "tool_use_id": "tool-1"}
    assert persisted[1].source == {"msg_id": "m-assistant", "tool_use_id": "tool-2"}
    assert put_bytes_capture[0]["data"] == png
    assert put_bytes_capture[0]["mime"] == "image/png"
    assert persisted[0].s3_key == f"assets/chat/c-1/{persisted[0].asset_id}"
    assert persisted[0].size_bytes == len(png)
    assert persisted[0].content is None
    assert persisted == put_asset_capture
    assert metrics.await_count == 2


@pytest.mark.asyncio
async def test_code_exec_image_bad_base64_is_skipped(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    images_by_tool = [
        (
            "tool-1",
            [
                {"mime": "image/png", "b64": "!!!not-base64!!!"},
                {"mime": "image/png", "b64": base64.b64encode(b"ok").decode()},
            ],
        ),
    ]
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images_by_tool=images_by_tool
    )
    # The bad entry burns Figure 1's ordinal but persists nothing.
    assert [a.title for a in persisted] == ["Figure 2"]
    assert len(put_bytes_capture) == 1
    assert metrics.await_args_list[0].kwargs == {"success": False}
    assert metrics.await_args_list[1].kwargs == {"success": True}


@pytest.mark.asyncio
async def test_code_exec_image_missing_b64_key_is_skipped(
    put_asset_capture: list[Asset], metrics: AsyncMock
) -> None:
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images_by_tool=[("tool-1", [{"mime": "x"}])]
    )
    assert persisted == []
    assert put_asset_capture == []
    metrics.assert_awaited_once_with(success=False)


@pytest.mark.asyncio
async def test_code_exec_image_defaults_mime_when_absent(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images_by_tool=[("tool-1", [{"b64": base64.b64encode(b"raw").decode()}])],
    )
    assert persisted[0].mime == "application/octet-stream"
    assert put_bytes_capture[0]["mime"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_code_exec_image_s3_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch, put_asset_capture: list[Asset], metrics: AsyncMock
) -> None:
    def failing_put_bytes(**_kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("s3 down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset_bytes", failing_put_bytes)
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images_by_tool=[
            ("tool-1", [{"mime": "image/png", "b64": base64.b64encode(b"x").decode()}])
        ],
    )
    assert persisted == []
    assert put_asset_capture == []
    metrics.assert_awaited_once_with(success=False)


# ----------------------------------------------------------------
# persist_generated_image_assets (#279)
# ----------------------------------------------------------------


def _gen_entry(
    *,
    tool_use_id: str = "tu-1",
    data: bytes = b"\x89PNG-gen",
    mime: str = "image/png",
    title: str = "a red bicycle",
) -> dict[str, str]:
    return {
        "tool_use_id": tool_use_id,
        "b64": base64.b64encode(data).decode(),
        "mime": mime,
        "title": title,
    }


@pytest.mark.asyncio
async def test_generated_images_persist_to_s3_as_generated(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    png = b"\x89PNG-gen-bytes"
    images = [_gen_entry(tool_use_id="tu-9", data=png, title="a red bicycle")]
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-assistant", images=images
    )
    (asset,) = persisted
    assert asset.kind == "image"
    assert asset.origin == "generated"
    assert asset.title == "a red bicycle"
    assert asset.mime == "image/png"
    assert asset.source == {"msg_id": "m-assistant", "tool_use_id": "tu-9"}
    assert asset.s3_key == f"assets/chat/c-1/{asset.asset_id}"
    assert asset.size_bytes == len(png)
    assert asset.content is None
    assert put_bytes_capture[0]["data"] == png
    assert persisted == put_asset_capture
    metrics.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_generated_image_bad_base64_is_skipped(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    images = [
        {"tool_use_id": "tu-1", "b64": "!!!not-base64!!!", "mime": "image/png", "title": "bad"},
        _gen_entry(tool_use_id="tu-2", title="good"),
    ]
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images=images
    )
    assert [a.title for a in persisted] == ["good"]
    assert len(put_bytes_capture) == 1
    assert metrics.await_args_list[0].kwargs == {"success": False}
    assert metrics.await_args_list[1].kwargs == {"success": True}


@pytest.mark.asyncio
async def test_generated_image_missing_b64_key_is_skipped(
    put_asset_capture: list[Asset], metrics: AsyncMock
) -> None:
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images=[{"tool_use_id": "tu-1"}]
    )
    assert persisted == []
    assert put_asset_capture == []
    metrics.assert_awaited_once_with(success=False)


@pytest.mark.asyncio
async def test_generated_image_defaults_mime_and_title_when_absent(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    """A sink entry missing ``mime``/``title`` falls back to image/png +
    'Generated image' rather than failing the persist."""
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images=[{"tool_use_id": "tu-1", "b64": base64.b64encode(b"raw").decode()}],
    )
    assert persisted[0].mime == "image/png"
    assert persisted[0].title == "Generated image"
    assert put_bytes_capture[0]["mime"] == "image/png"


@pytest.mark.asyncio
async def test_generated_image_empty_title_falls_back(
    put_asset_capture: list[Asset], put_bytes_capture: list[dict[str, Any]], metrics: AsyncMock
) -> None:
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images=[_gen_entry(title="")]
    )
    assert persisted[0].title == "Generated image"


@pytest.mark.asyncio
async def test_generated_image_s3_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    put_asset_capture: list[Asset],
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    def failing_put_bytes(**_kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("s3 down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset_bytes", failing_put_bytes)
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images=[_gen_entry()]
    )
    assert persisted == []
    assert put_asset_capture == []
    # put_asset_bytes never returned coords, so nothing to compensate-delete.
    assert delete_object_capture == []
    metrics.assert_awaited_once_with(success=False)


@pytest.mark.asyncio
async def test_generated_image_row_write_failure_cleans_up_s3_object(
    monkeypatch: pytest.MonkeyPatch,
    put_bytes_capture: list[dict[str, Any]],
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    """put_asset_bytes succeeded but the row write failed: the produced
    S3 object must be compensating-deleted."""

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    persisted = await ap.persist_generated_image_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", images=[_gen_entry()]
    )
    assert persisted == []
    asset_id = put_bytes_capture[0]["asset_id"]
    assert delete_object_capture == [("channel-attachments-test", f"assets/chat/c-1/{asset_id}")]
    metrics.assert_awaited_once_with(success=False)


# ----------------------------------------------------------------
# persist_fence_assets
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fence_asset_persists_inline_with_fence_index_and_lang(
    put_asset_capture: list[Asset],
    metrics: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    text = "Short one:\n\n" + _fence(3) + "\n\nLong one:\n\n" + _fence(20, lang="python")
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert len(persisted) == 1
    asset = persisted[0]
    assert asset.kind == "code"
    assert asset.origin == "generated"
    assert asset.mime == "text/plain"
    assert asset.source == {"msg_id": "m-a", "fence_index": 1, "lang": "python"}
    assert asset.content is not None and asset.content.startswith("line_0")
    assert asset.s3_bucket is None
    assert asset.size_bytes == len(asset.content.encode("utf-8"))
    metrics.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_fence_asset_over_inline_cap_goes_to_s3(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    # 20 lines whose total UTF-8 size exceeds the 100 KB inline cap.
    line = "x" * ((ASSET_INLINE_CONTENT_MAX_BYTES // 15) + 1)
    body = "\n".join([line] * 15)
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=f"```python\n{body}\n```"
    )
    assert len(persisted) == 1
    asset = persisted[0]
    assert asset.content is None
    assert asset.s3_bucket == "channel-attachments-test"
    assert asset.s3_key == f"assets/chat/c-1/{asset.asset_id}"
    assert put_bytes_capture[0]["data"] == body.encode("utf-8")
    assert put_bytes_capture[0]["mime"] == "text/plain"
    assert asset.size_bytes == len(body.encode("utf-8"))


@pytest.mark.asyncio
async def test_fence_extraction_kill_switch_off_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, metrics: AsyncMock
) -> None:
    monkeypatch.setenv("CHANNEL_ASSET_EXTRACTION_ENABLED", "0")
    called: list[Any] = []
    monkeypatch.setattr(
        "channel.agents.asset_producers.storage.put_asset", lambda a: called.append(a)
    )
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=_fence(20)
    )
    assert persisted == []
    assert called == []
    metrics.assert_not_awaited()


@pytest.mark.asyncio
async def test_fence_persist_failure_is_isolated_per_fence(
    monkeypatch: pytest.MonkeyPatch, metrics: AsyncMock
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    text = _fence(15, lang="python") + "\n\n" + _fence(16, lang="go")
    calls: list[Asset] = []

    def flaky_put(asset: Asset) -> None:
        if asset.source["fence_index"] == 0:
            raise RuntimeError("ddb down")
        calls.append(asset)

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", flaky_put)
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert [a.source["fence_index"] for a in persisted] == [1]
    assert metrics.await_args_list[0].kwargs == {"success": False}
    assert metrics.await_args_list[1].kwargs == {"success": True}


# ----------------------------------------------------------------
# Copilot review round 1 — metric guard + strict base64
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_emission_failure_never_breaks_producer(
    put_asset_capture: list[Asset], monkeypatch: pytest.MonkeyPatch
) -> None:
    """EMF flush failures ride the fail-soft contract too: a raising
    ``record_asset_persist_outcome`` must not abort the producer loop —
    the row is durable, the asset is still returned (so the frame still
    goes out)."""
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)

    async def exploding_metric(*, success: bool) -> None:
        raise RuntimeError("EMF flush failed")

    monkeypatch.setattr(
        "channel.agents.asset_producers.record_asset_persist_outcome", exploding_metric
    )
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=_fence(15)
    )
    assert len(persisted) == 1
    assert persisted == put_asset_capture


@pytest.mark.asyncio
async def test_metric_emission_failure_in_failure_path_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both fail-soft tails compose: put_asset raises AND the failure
    counter raises — the producer still returns cleanly."""
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    async def exploding_metric(*, success: bool) -> None:
        raise RuntimeError("EMF flush failed")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    monkeypatch.setattr(
        "channel.agents.asset_producers.record_asset_persist_outcome", exploding_metric
    )
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=_fence(15)
    )
    assert persisted == []


@pytest.mark.asyncio
async def test_code_exec_image_non_alphabet_b64_rejected_strictly(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    """``validate=True`` pins strict decoding: without it, b64decode
    silently DROPS non-alphabet bytes ("QUJ*RA==" -> b"ABD") and would
    persist garbage instead of counting a failure."""
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images_by_tool=[("tool-1", [{"mime": "image/png", "b64": "QUJ*RA=="}])],
    )
    assert persisted == []
    assert put_bytes_capture == []
    assert put_asset_capture == []
    metrics.assert_awaited_once_with(success=False)


# ----------------------------------------------------------------
# Copilot review round 2 — S3 orphan cleanup on failed row writes
# ----------------------------------------------------------------


@pytest.fixture
def delete_object_capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "channel.agents.asset_producers.storage.delete_asset_object",
        lambda *, bucket, key: captured.append((bucket, key)),
    )
    return captured


@pytest.mark.asyncio
async def test_code_exec_row_write_failure_cleans_up_s3_object(
    monkeypatch: pytest.MonkeyPatch,
    put_bytes_capture: list[dict[str, Any]],
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    """put_asset_bytes succeeded but the row write failed: the produced
    S3 object must be compensating-deleted — a row-less object is
    invisible to the cascade and the reap."""

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images_by_tool=[
            ("tool-1", [{"mime": "image/png", "b64": base64.b64encode(b"x").decode()}])
        ],
    )
    assert persisted == []
    assert len(put_bytes_capture) == 1
    asset_id = put_bytes_capture[0]["asset_id"]
    assert delete_object_capture == [("channel-attachments-test", f"assets/chat/c-1/{asset_id}")]
    metrics.assert_awaited_once_with(success=False)


@pytest.mark.asyncio
async def test_code_exec_s3_put_failure_skips_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    """When put_asset_bytes itself failed nothing landed in S3, so the
    compensating delete must NOT fire."""

    def failing_put_bytes(**_kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("s3 down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset_bytes", failing_put_bytes)
    persisted = await ap.persist_code_exec_image_assets(
        chat_id="c-1",
        owner="u-1",
        msg_id="m-a",
        images_by_tool=[
            ("tool-1", [{"mime": "image/png", "b64": base64.b64encode(b"x").decode()}])
        ],
    )
    assert persisted == []
    assert delete_object_capture == []


@pytest.mark.asyncio
async def test_fence_row_write_failure_cleans_up_oversized_s3_object(
    monkeypatch: pytest.MonkeyPatch,
    put_bytes_capture: list[dict[str, Any]],
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    line = "x" * ((ASSET_INLINE_CONTENT_MAX_BYTES // 15) + 1)
    body = "\n".join([line] * 15)
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=f"```python\n{body}\n```"
    )
    assert persisted == []
    assert len(put_bytes_capture) == 1
    asset_id = put_bytes_capture[0]["asset_id"]
    assert delete_object_capture == [("channel-attachments-test", f"assets/chat/c-1/{asset_id}")]


@pytest.mark.asyncio
async def test_fence_inline_failure_does_not_touch_s3(
    monkeypatch: pytest.MonkeyPatch,
    delete_object_capture: list[tuple[str, str]],
    metrics: AsyncMock,
) -> None:
    """Inline-content fences never wrote to S3, so a failed row write
    must not fire the compensating delete (coords are None)."""
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=_fence(15)
    )
    assert persisted == []
    assert delete_object_capture == []


# ----------------------------------------------------------------
# CSV / TSV fence classification (#383)
# ----------------------------------------------------------------


def _tabular_body(delim: str, rows: int = 16) -> str:
    """A header row + ``rows`` data rows, ``delim``-separated (>= 15 body
    lines so the block qualifies for extraction)."""
    header = delim.join(["name", "age", "city"])
    data = "\n".join(delim.join([f"p{i}", str(20 + i), f"t{i}"]) for i in range(rows))
    return f"{header}\n{data}"


def _code_fence(*, index: int = 0, lang: str, body: str) -> ap.CodeFence:
    return ap.CodeFence(
        fence_index=index,
        lang=lang,
        title="t",
        body=body,
        line_count=body.count("\n") + 1,
    )


# --- _sniff_tabular_mime -----------------------------------------


def test_sniff_comma_tabular_with_header_returns_csv_mime() -> None:
    assert ap._sniff_tabular_mime(_tabular_body(",")) == "text/csv"


def test_sniff_tab_tabular_with_header_returns_tsv_mime() -> None:
    assert ap._sniff_tabular_mime(_tabular_body("\t")) == "text/tab-separated-values"


def test_sniff_blank_lines_ignored_but_still_qualifies() -> None:
    body = "a,b\n\n1,2\n\n3,4"  # blank lines dropped before the count check
    assert ap._sniff_tabular_mime(body) == "text/csv"


def test_sniff_single_line_is_not_tabular() -> None:
    assert ap._sniff_tabular_mime("just one line, with a comma") is None


def test_sniff_single_column_is_not_tabular() -> None:
    # Consistent but zero delimiters per line — one column is not a table.
    assert ap._sniff_tabular_mime("alpha\nbeta\ngamma") is None


def test_sniff_inconsistent_column_count_is_not_tabular() -> None:
    # First line 2 commas, rest 1 — ragged, so not classified as data.
    assert ap._sniff_tabular_mime("a,b,c\n1,2\n3,4") is None


def test_sniff_empty_header_cell_is_not_tabular() -> None:
    # Comma counts are consistent (2 each) but the header has an empty
    # leading cell, and no tab delimiter exists → falls through to None.
    body = "\n".join([",a,b"] * 3)
    assert ap._sniff_tabular_mime(body) is None


# --- _classify_fence ---------------------------------------------


def test_classify_csv_lang_is_data() -> None:
    fence = _code_fence(lang="csv", body=_tabular_body(","))
    assert ap._classify_fence(fence) == ("data", "text/csv")


def test_classify_tsv_lang_is_data() -> None:
    fence = _code_fence(lang="tsv", body=_tabular_body("\t"))
    assert ap._classify_fence(fence) == ("data", "text/tab-separated-values")


def test_classify_python_lang_stays_code() -> None:
    fence = _code_fence(lang="python", body="print('x')\nfor i in range(3):\n    pass")
    assert ap._classify_fence(fence) == ("code", "text/plain")


def test_classify_bare_tabular_fence_is_data() -> None:
    fence = _code_fence(lang="", body=_tabular_body(","))
    assert ap._classify_fence(fence) == ("data", "text/csv")


def test_classify_bare_nontabular_fence_stays_code() -> None:
    # A bare fence whose body isn't delimited data must NOT be reclassified.
    fence = _code_fence(lang="", body="the quick brown fox\njumped over\nthe lazy dog")
    assert ap._classify_fence(fence) == ("code", "text/plain")


# --- persist_fence_assets: data classification end-to-end --------


@pytest.mark.asyncio
async def test_persist_csv_lang_fence_is_kind_data(
    put_asset_capture: list[Asset], metrics: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    text = f"Here is the data:\n\n```csv\n{_tabular_body(',')}\n```"
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert len(persisted) == 1
    asset = persisted[0]
    assert asset.kind == "data"
    assert asset.mime == "text/csv"
    assert asset.origin == "generated"
    assert asset.source == {"msg_id": "m-a", "fence_index": 0, "lang": "csv"}
    assert asset.content is not None and asset.content.startswith("name,age,city")
    metrics.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_persist_tsv_lang_fence_is_kind_data(
    put_asset_capture: list[Asset], metrics: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    text = f"```tsv\n{_tabular_body(chr(9))}\n```"
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert len(persisted) == 1
    assert persisted[0].kind == "data"
    assert persisted[0].mime == "text/tab-separated-values"


@pytest.mark.asyncio
async def test_persist_bare_tabular_fence_is_kind_data(
    put_asset_capture: list[Asset], metrics: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    text = f"```\n{_tabular_body(',')}\n```"
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert len(persisted) == 1
    assert persisted[0].kind == "data"
    assert persisted[0].mime == "text/csv"
    assert persisted[0].source["lang"] == ""


@pytest.mark.asyncio
async def test_persist_python_fence_stays_kind_code(
    put_asset_capture: list[Asset], metrics: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=_fence(20, lang="python")
    )
    assert len(persisted) == 1
    assert persisted[0].kind == "code"
    assert persisted[0].mime == "text/plain"


@pytest.mark.asyncio
async def test_persist_oversized_csv_uses_csv_mime_for_s3(
    put_asset_capture: list[Asset],
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An over-inline-cap CSV fence carries text/csv to the S3 put too, not
    the code default — so the stored object's Content-Type matches."""
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)
    cell = "v" * ((ASSET_INLINE_CONTENT_MAX_BYTES // 15) + 1)
    header = "a,b"
    rows = "\n".join(f"{cell},{i}" for i in range(15))
    text = f"```csv\n{header}\n{rows}\n```"
    persisted = await ap.persist_fence_assets(chat_id="c-1", owner="u-1", msg_id="m-a", text=text)
    assert len(persisted) == 1
    assert persisted[0].kind == "data"
    assert persisted[0].content is None
    assert persisted[0].mime == "text/csv"
    assert put_bytes_capture[0]["mime"] == "text/csv"


@pytest.mark.asyncio
async def test_orphan_cleanup_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    put_bytes_capture: list[dict[str, Any]],
    metrics: AsyncMock,
) -> None:
    """A doubly-failed cleanup (row write AND compensating delete both
    raise) still leaves the producer loop intact."""
    monkeypatch.delenv("CHANNEL_ASSET_EXTRACTION_ENABLED", raising=False)

    def exploding_put(_asset: Asset) -> None:
        raise RuntimeError("ddb down")

    def exploding_delete(*, bucket: str, key: str) -> None:
        raise RuntimeError("s3 also down")

    monkeypatch.setattr("channel.agents.asset_producers.storage.put_asset", exploding_put)
    monkeypatch.setattr(
        "channel.agents.asset_producers.storage.delete_asset_object", exploding_delete
    )
    line = "x" * ((ASSET_INLINE_CONTENT_MAX_BYTES // 15) + 1)
    body = "\n".join([line] * 15)
    persisted = await ap.persist_fence_assets(
        chat_id="c-1", owner="u-1", msg_id="m-a", text=f"```python\n{body}\n```"
    )
    assert persisted == []

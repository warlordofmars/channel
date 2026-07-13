# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for chat domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from channel.models import (
    ASSET_INLINE_CONTENT_MAX_BYTES,
    Asset,
    Attachment,
    Chat,
    ChatCreate,
    ChatMCPMode,
    ChatMCPSettings,
    ChatPatch,
    Citation,
    MCPServer,
    MCPServerAuthStatus,
    MCPToken,
    Message,
    MessageRole,
    Prefs,
    SendMessageRequest,
)


def test_chat_roundtrips_with_required_fields():
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="Hello",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview="",
        model_default="canned-stream-v1",
        message_count=0,
        archived=False,
    )
    assert chat.chat_id == "abc"
    assert chat.archived is False


def test_message_rejects_unknown_role():
    with pytest.raises(ValidationError):
        Message(
            chat_id="abc",
            msg_id="m-1",
            role="system",  # not user|assistant
            text="hi",
            created_at="2026-05-30T00:00:00Z",
        )


def test_message_assistant_carries_token_usage():
    msg = Message(
        chat_id="abc",
        msg_id="m-1",
        role=MessageRole.ASSISTANT,
        text="hello",
        model="canned-stream-v1",
        input_tokens=12,
        output_tokens=3,
        created_at="2026-05-30T00:00:00Z",
    )
    assert msg.role == MessageRole.ASSISTANT
    assert msg.input_tokens == 12


def test_chat_create_defaults_both_optionals_to_none():
    payload = ChatCreate()
    assert payload.title is None  # server will substitute "New chat"
    assert payload.model_default is None


def test_chat_patch_allows_partial_updates():
    patch = ChatPatch(title="Renamed")
    assert patch.title == "Renamed"
    assert patch.archived is None

    # Symmetric case — set only archived, title stays None.
    patch2 = ChatPatch(archived=True)
    assert patch2.archived is True
    assert patch2.title is None


def test_last_user_preview_caps_at_120_chars():
    # Distinguishable prefix so the assertion proves we cap from the head
    # (value[:120]) rather than from the tail (value[-120:]).
    long = "abc" + "x" * 200
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview=long,
        model_default="m",
        message_count=0,
        archived=False,
    )
    assert len(chat.last_user_preview) == 120
    assert chat.last_user_preview.startswith("abc")


def test_send_message_request_accepts_valid_message():
    req = SendMessageRequest(message="hello")
    assert req.message == "hello"


def test_send_message_request_rejects_empty_message():
    with pytest.raises(ValidationError):
        SendMessageRequest(message="")


def test_send_message_request_rejects_oversized_message():
    with pytest.raises(ValidationError):
        SendMessageRequest(message="x" * 100_001)


def test_prefs_defaults():
    p = Prefs()
    assert p.theme == "dark"
    assert p.accent == "42"
    assert p.density == "cozy"
    assert p.shape == "soft"
    assert p.font == "figtree"
    assert p.model == "claude-opus-4-6"
    assert p.effort == "High"
    assert p.send_on_enter is True
    assert p.suggest_followups is True
    assert p.show_reasoning is False


def test_prefs_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Prefs(unknown_key="x")


# ----------------------------------------------------------------
# Attachment + Citation (#174) — file attachments + vision (epic #109)
# ----------------------------------------------------------------


def _att_kwargs(**overrides):
    """Minimal valid kwargs for Attachment construction."""

    base = {
        "id": "att-1",
        "user_id": "u-1",
        "name": "spec.pdf",
        "mime": "application/pdf",
        "size_bytes": 12345,
        "s3_key": "attachments/user/u-1/att-1",
        "s3_bucket": "channel-attachments-dev",
        "checksum_sha256": "abc123",
        "created_at": "2026-06-03T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_attachment_roundtrips_with_required_fields():
    att = Attachment(**_att_kwargs())
    assert att.id == "att-1"
    assert att.referenced_at is None  # optional, defaults to None


def test_attachment_referenced_at_optional():
    att = Attachment(**_att_kwargs(referenced_at="2026-06-03T00:01:00Z"))
    assert att.referenced_at == "2026-06-03T00:01:00Z"


def _cit_kwargs(**overrides):
    """Minimal valid kwargs for Citation (attachment source)."""

    base = {
        "source_type": "attachment",
        "source_id": "att-1",
        "source_name": "spec.pdf",
        "location": {"type": "page", "value": 1},
    }
    base.update(overrides)
    return base


def test_citation_happy_path_attachment_page():
    cit = Citation(**_cit_kwargs())
    assert cit.source_type == "attachment"
    assert cit.location["type"] == "page"
    assert cit.excerpt is None
    assert cit.confidence is None


def test_citation_rejects_unknown_source_type():
    with pytest.raises(ValidationError):
        Citation(**_cit_kwargs(source_type="unknown"))


def test_citation_rejects_attachment_with_url_location():
    """``attachment`` source type only accepts page/cell/region/timestamp."""

    with pytest.raises(ValidationError):
        Citation(**_cit_kwargs(location={"type": "url", "value": "https://x"}))


def test_citation_accepts_attachment_cell_region_timestamp_locations():
    for loc_type in ("cell", "region", "timestamp"):
        cit = Citation(**_cit_kwargs(location={"type": loc_type, "value": "x"}))
        assert cit.location["type"] == loc_type


def test_citation_web_search_requires_url_location():
    cit = Citation(
        source_type="web_search",
        source_id="s-1",
        source_name="search",
        location={"type": "url", "value": "https://example.com"},
    )
    assert cit.source_type == "web_search"

    # Mismatch — web_search with page is rejected
    with pytest.raises(ValidationError):
        Citation(
            source_type="web_search",
            source_id="s-1",
            source_name="search",
            location={"type": "page", "value": 1},
        )


def test_citation_code_output_accepts_any_typed_location():
    """``code_output`` / ``memory_recall`` schemas aren't pinned yet — the
    validator accepts any non-empty location dict with a ``type`` key so
    the recall hook and code-output owners can fill in the shape later."""

    cit = Citation(
        source_type="code_output",
        source_id="tool-1",
        source_name="python",
        location={"type": "stdout", "lines": [1, 2]},
    )
    assert cit.location["type"] == "stdout"


def test_citation_memory_recall_accepts_any_typed_location():
    cit = Citation(
        source_type="memory_recall",
        source_id="evt-1",
        source_name="recall",
        location={"type": "event", "value": "evt-1"},
    )
    assert cit.source_type == "memory_recall"


def test_citation_rejects_location_without_type_key():
    with pytest.raises(ValidationError):
        Citation(**_cit_kwargs(location={"value": 1}))  # missing "type"


def test_citation_confidence_accepts_high_medium_low():
    for value in ("high", "medium", "low"):
        cit = Citation(**_cit_kwargs(confidence=value))
        assert cit.confidence == value


def test_citation_rejects_unknown_confidence():
    with pytest.raises(ValidationError):
        Citation(**_cit_kwargs(confidence="probably"))


def test_message_carries_citations_on_assistant_turn():
    msg = Message(
        chat_id="abc",
        msg_id="m-1",
        role=MessageRole.ASSISTANT,
        text="see [1]",
        created_at="2026-06-03T00:00:00Z",
        citations=[Citation(**_cit_kwargs())],
    )
    assert msg.citations is not None
    assert len(msg.citations) == 1


def test_message_rejects_citations_on_user_turn():
    """Citations are produced by the model — user-role turns must never
    carry them. Per Channel's 2026-06-03 design input (issue body)."""

    with pytest.raises(ValidationError):
        Message(
            chat_id="abc",
            msg_id="m-1",
            role=MessageRole.USER,
            text="hi",
            created_at="2026-06-03T00:00:00Z",
            citations=[Citation(**_cit_kwargs())],
        )


def test_message_user_turn_with_none_citations_is_fine():
    msg = Message(
        chat_id="abc",
        msg_id="m-1",
        role=MessageRole.USER,
        text="hi",
        created_at="2026-06-03T00:00:00Z",
        citations=None,
    )
    assert msg.citations is None


# ----------------------------------------------------------------
# MCP server registry (#207) — MCPServer, MCPToken, ChatMCPSettings
# ----------------------------------------------------------------


def test_mcp_server_defaults_globally_enabled():
    server = MCPServer(
        server_id="11111111-2222-3333-4444-555555555555",
        user_id="user-abc",
        name="Hive",
        url="https://hive.warlordofmars.net/mcp",
        client_id="dcr-xyz",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        created_at="2026-06-09T00:00:00+00:00",
        updated_at="2026-06-09T00:00:00+00:00",
    )
    assert server.globally_enabled is True


def test_mcp_token_carries_encrypted_blob():
    token = MCPToken(
        server_id="srv-1",
        user_id="user-abc",
        access_token_ciphertext=b"opaque",
        refresh_token_ciphertext=b"opaque-refresh",
        expires_at=1_700_000_000,
        granted_scope="read write",
        updated_at="2026-06-09T00:00:00+00:00",
    )
    assert token.access_token_ciphertext == b"opaque"


def test_chat_mcp_settings_inherit_is_default():
    settings = ChatMCPSettings(chat_id="chat-1")
    assert settings.mode == ChatMCPMode.INHERIT
    assert settings.explicit_server_ids == []


def test_new_mcp_models_reject_unknown_keys() -> None:
    base_server = {
        "server_id": "x",
        "user_id": "u",
        "name": "Hive",
        "url": "https://h.example/mcp",
        "client_id": "dcr",
        "tool_prefix": "hive",
        "auth_status": MCPServerAuthStatus.NEVER_AUTHED,
        "created_at": "x",
        "updated_at": "x",
    }
    with pytest.raises(ValidationError):
        MCPServer(**base_server, unknown_field="x")

    with pytest.raises(ValidationError):
        MCPToken(
            server_id="s",
            user_id="u",
            access_token_ciphertext=b"x",
            expires_at=1,
            granted_scope="",
            updated_at="x",
            unknown="y",
        )

    with pytest.raises(ValidationError):
        ChatMCPSettings(chat_id="c", unknown="y")


# ----------------------------------------------------------------
# Asset (#324, epic #321)
# ----------------------------------------------------------------


def _asset_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "asset_id": "a-1",
        "chat_id": "c-1",
        "owner": "u-1",
        "kind": "code",
        "title": "fib.py",
        "mime": "text/x-python",
        "size_bytes": 42,
        "origin": "generated",
        "source": {"msg_id": "m-1"},
        "content": "print('hi')",
        "created_at": "2026-07-13T00:00:00.000000+00:00",
        "updated_at": "2026-07-13T00:00:00.000000+00:00",
    }
    base.update(overrides)
    return base


def test_asset_inline_roundtrip():
    asset = Asset(**_asset_kwargs())
    assert asset.content == "print('hi')"
    assert asset.s3_bucket is None
    assert asset.s3_key is None
    assert asset.kind == "code"
    assert asset.origin == "generated"


def test_asset_s3_backed_roundtrip():
    asset = Asset(
        **_asset_kwargs(
            content=None,
            s3_bucket="channel-attachments",
            s3_key="assets/chat/c-1/a-1",
            kind="image",
            mime="image/png",
            origin="tool_output",
            source={"msg_id": "m-1", "tool_use_id": "t-1"},
        )
    )
    assert asset.content is None
    assert asset.s3_key == "assets/chat/c-1/a-1"


# Note on shape: kwargs are always built OUTSIDE the ``pytest.raises``
# block so the constructor is the only invocation that can raise
# (Sonar python:S5778).


def test_asset_rejects_both_payload_forms():
    kwargs = _asset_kwargs(s3_bucket="channel-attachments", s3_key="assets/chat/c-1/a-1")
    with pytest.raises(ValidationError, match="exactly one"):
        Asset(**kwargs)


def test_asset_rejects_neither_payload_form():
    kwargs = _asset_kwargs(content=None)
    with pytest.raises(ValidationError, match="exactly one"):
        Asset(**kwargs)


@pytest.mark.parametrize(
    ("bucket", "key"),
    [("channel-attachments", None), (None, "assets/chat/c-1/a-1")],
)
def test_asset_rejects_partial_s3_coordinates(bucket: str | None, key: str | None):
    kwargs = _asset_kwargs(content=None, s3_bucket=bucket, s3_key=key)
    with pytest.raises(ValidationError, match="set together"):
        Asset(**kwargs)


def test_asset_inline_content_capped_at_100_kb():
    at_cap = "x" * ASSET_INLINE_CONTENT_MAX_BYTES
    assert Asset(**_asset_kwargs(content=at_cap)).content == at_cap
    over_cap = _asset_kwargs(content=at_cap + "x")
    with pytest.raises(ValidationError, match="inline cap"):
        Asset(**over_cap)


def test_asset_inline_cap_measures_utf8_bytes_not_chars():
    # é is 2 bytes in UTF-8 — a string under the cap in characters but
    # over it in bytes must be rejected (DDB item sizing is byte-based).
    kwargs = _asset_kwargs(content="é" * ((ASSET_INLINE_CONTENT_MAX_BYTES // 2) + 1))
    with pytest.raises(ValidationError, match="inline cap"):
        Asset(**kwargs)


@pytest.mark.parametrize("source", [{}, {"msg_id": ""}, {"msg_id": 7}, {"tool_use_id": "t"}])
def test_asset_requires_source_msg_id(source: dict[str, object]):
    kwargs = _asset_kwargs(source=source)
    with pytest.raises(ValidationError, match="msg_id"):
        Asset(**kwargs)


def test_asset_rejects_unknown_kind():
    kwargs = _asset_kwargs(kind="video")
    with pytest.raises(ValidationError):
        Asset(**kwargs)


def test_asset_rejects_unknown_origin():
    kwargs = _asset_kwargs(origin="imported")
    with pytest.raises(ValidationError):
        Asset(**kwargs)


def test_asset_has_no_pinned_attribute():
    # "Pin to survive chat deletion" is explicitly v2 (John, 2026-07-12).
    # This pin (pun intended) fails if someone pre-bakes the attribute.
    assert "pinned" not in Asset.model_fields

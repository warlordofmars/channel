# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import boto3
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from fastapi.responses import StreamingResponse
from strands.tools.mcp import MCPClient
from strands.types.exceptions import MaxTokensReachedException

from channel import storage
from channel.agents.chat_agent import (
    build_agent,
    build_followups_agent,
    build_titler_agent,
    resolve_model_id,
)
from channel.agents.memory import _sanitize_actor_id, get_or_create_memory
from channel.agents.strands_sse import (
    sse_attachment_error,
    sse_delta,
    sse_done,
    sse_follow_ups_suggested,
    sse_title_suggested,
    sse_tool_error,
    sse_tool_finished,
    sse_tool_progress,
    sse_tool_started,
    sse_user_persisted,
    translate_event,
)
from channel.agents.tool_hooks import clear_cancel_signal, set_cancel_signal
from channel.agents.tools.clock import current_time
from channel.api._auth import require_mgmt_user
from channel.logging_config import fingerprint_id
from channel.mcp import auth as mcp_auth
from channel.mcp.auth import MCPAuthFailedError
from channel.mcp.transports import make_authenticated_transport
from channel.mcp.url_guard import validate_mcp_server_url
from channel.metrics import (
    record_auto_title_outcome,
    record_chat_delete_attachment_wipe_outcome,
    record_chat_delete_memory_wipe_outcome,
    record_followup_outcome,
)
from channel.models import (
    Chat,
    ChatCreate,
    ChatMCPMode,
    ChatPatch,
    FeedbackRequest,
    MCPServerAuthStatus,
    Message,
    MessageRole,
    RegenerateRequest,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"

# Conversation-continuity bound: how many prior turns to feed back into
# the Strands Agent on each request. 100 covers all but the longest
# chats; Bedrock model-context-window caps the real upper bound.
# A future ConversationManager can trim/summarise beyond this.
_HISTORY_TURNS_LIMIT = 100


def _to_strands_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert stored ``Message`` rows into Strands' ``Messages`` shape.

    Strands expects ``[{"role": "user"|"assistant", "content": [{"text": str}]}]``
    in chronological order. ``storage.list_messages`` returns the same
    chronological order (``ScanIndexForward=True``), so no reordering.
    """
    return [{"role": m.role.value, "content": [{"text": m.text}]} for m in messages]


# ----------------------------------------------------------------
# Attachment resolution for the send path (#176)
# ----------------------------------------------------------------


# MIME → Strands DocumentFormat / ImageFormat literal. The two unions
# share no overlap so format also tells us which content-block key
# (``document`` vs. ``image``) to emit.
_MIME_TO_DOC_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/plain": "txt",
    "text/markdown": "md",
}
_MIME_TO_IMG_FORMAT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
# MIME → user-facing family label inside the attachment header text.
_MIME_TO_FAMILY: dict[str, str] = {
    "application/pdf": "PDF",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "text/csv": "CSV",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "text/plain": "text",
    "text/markdown": "markdown",
}


# Bedrock's ConverseStream rejects ``document.name`` values that contain
# anything outside its strict allowlist (alphanumerics, whitespace,
# hyphens, parens, square brackets — see ValidationException message).
# Real user filenames almost always include a period for the extension,
# so the e2e suite (#179) caught every PDF/XLSX upload silently 500-ing
# upstream. This sanitizer keeps the original name human-readable while
# stripping the forbidden bytes — Bedrock only uses the name to label
# the doc block in its prompt anyway, not to fetch anything.
_BEDROCK_DOC_NAME_FORBIDDEN_RE = re.compile(r"[^A-Za-z0-9 \-()\[\]]")
_BEDROCK_DOC_NAME_WS_RE = re.compile(r"\s+")


def _sanitize_document_name(name: str) -> str:
    """Coerce ``name`` into Bedrock's document-name allowlist (#179)."""

    cleaned = _BEDROCK_DOC_NAME_FORBIDDEN_RE.sub(" ", name)
    cleaned = _BEDROCK_DOC_NAME_WS_RE.sub(" ", cleaned).strip()
    return cleaned or "attachment"


def _attachment_label(*, index: int, name: str, size_bytes: int, mime: str) -> str:
    size_mb = f"{size_bytes / 1024 / 1024:.1f}MB"
    family = _MIME_TO_FAMILY.get(mime, "file")
    return f"[attachment_{index}: {name}, {size_mb}, {family}]"


def _attachment_failure_label(*, index: int, name: str, reason: str) -> str:
    return f"[attachment_{index}: {name} — FAILED: {reason}]"


def _resolve_attachments_for_send(
    *,
    user_id: str,
    attachments: list[dict[str, Any]] | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]] | None,
    list[dict[str, Any]],
    list[str],
]:
    """Resolve a request's ``attachments`` list into Strands content blocks
    plus the denormalised message-row snapshots, the SSE error payloads
    for any S3-verify failures, and the list of att_ids that should
    have ``referenced_at`` stamped.

    Returns ``(content_blocks, snapshots, errors, verified_ids)``.

    Raises ``HTTPException(400)`` if any attachment id is missing or
    owned by a different user — the request is structurally invalid
    and no partial persist is allowed (per #176 spec).
    """

    if not attachments:
        return [], None, [], []

    # 1. Resolve canonical rows. Hard-reject on the first miss.
    resolved: list[Any] = []
    for entry in attachments:
        att_id = entry.get("id") if isinstance(entry, dict) else None
        if not att_id:
            raise HTTPException(status_code=400, detail="Attachment entry missing id")
        att = storage.get_attachment(user_id=user_id, att_id=att_id)
        if att is None:
            # Could be: id doesn't exist, OR row exists under another
            # user. Either way the caller has no claim to it; 400.
            raise HTTPException(status_code=400, detail=f"Attachment {att_id} not found")
        resolved.append(att)

    # 2. Snapshot the denormalised metadata for the message row.
    snapshots = [
        {"id": a.id, "name": a.name, "mime": a.mime, "size_bytes": a.size_bytes} for a in resolved
    ]

    # 3. HEAD-verify each S3 object, building content blocks + errors
    #    in user-supplied order. ``index`` is the 1-based attachment
    #    number that appears in the labeled headers.
    content_blocks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    verified_ids: list[str] = []
    for index, att in enumerate(resolved, start=1):
        # Fetch the object bytes inline. Bedrock's Converse API exposes
        # ``s3Location`` references only for select models — Claude
        # Opus 4.6 rejects them with ``ValidationException: This model
        # doesn't support the s3Uri field`` (#201). Inline bytes work
        # everywhere and one GetObject also subsumes the prior HEAD-
        # verify roundtrip (404 surfaces here too).
        att_bytes, reason = storage.get_attachment_bytes(att)
        if att_bytes is None:
            content_blocks.append(
                {
                    "text": _attachment_failure_label(
                        index=index, name=att.name, reason=reason or "unknown"
                    )
                }
            )
            errors.append(
                {
                    "attachment_id": att.id,
                    "filename": att.name,
                    "reason": reason or "unknown",
                }
            )
            continue

        header = _attachment_label(
            index=index, name=att.name, size_bytes=att.size_bytes, mime=att.mime
        )
        content_blocks.append({"text": header})
        bytes_source: dict[str, Any] = {"bytes": att_bytes}
        if att.mime in _MIME_TO_DOC_FORMAT:
            content_blocks.append(
                {
                    "document": {
                        "format": _MIME_TO_DOC_FORMAT[att.mime],
                        "name": _sanitize_document_name(att.name),
                        "source": bytes_source,
                    }
                }
            )
        elif att.mime in _MIME_TO_IMG_FORMAT:
            content_blocks.append(
                {
                    "image": {
                        "format": _MIME_TO_IMG_FORMAT[att.mime],
                        "source": bytes_source,
                    }
                }
            )
        else:
            # MIME outside the v1 allowlist — should have been caught at
            # presign (#175). Fall through with a labeled FAILED marker
            # so the model can voice the gap rather than silently dropping.
            content_blocks[-1] = {
                "text": _attachment_failure_label(
                    index=index,
                    name=att.name,
                    reason=f"unsupported mime {att.mime}",
                )
            }
            errors.append(
                {
                    "attachment_id": att.id,
                    "filename": att.name,
                    "reason": f"unsupported mime {att.mime}",
                }
            )
            continue

        verified_ids.append(att.id)

    return content_blocks, snapshots, errors, verified_ids


router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", status_code=201)
async def create_chat(
    payload: ChatCreate,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Create a new chat for the authenticated user."""

    chat = storage.create_chat(
        user_id=claims["sub"],
        title=payload.title,
        model_default=payload.model_default or _DEFAULT_MODEL,
    )
    return chat.model_dump()


@router.get("")
async def list_chats(
    limit: int = 50,
    cursor: str | None = None,
    include_archived: bool = False,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """List the authenticated user's chats, newest first.

    Archived chats are filtered out server-side by default. The SPA's
    Recents list relies on that. Pass ``?include_archived=1`` to fetch
    everything (used by archive-management surfaces).
    """

    chats, next_cursor = storage.list_chats_for_user(
        claims["sub"],
        limit=limit,
        cursor=cursor,
        include_archived=include_archived,
    )
    return {
        "items": [c.model_dump() for c in chats],
        "next_cursor": next_cursor,
    }


def _postprocess_title(raw: str) -> str:
    """Normalize raw titler output into a usable sidebar title.

    Haiku occasionally emits preamble (e.g. ``"Here's a 3-6 word title:
    Debug pytest fixture"``). We strip leading explanations by taking
    the part after the LAST colon if a colon is present, then split
    into words and cap at 6 (matches the titler's 3-6 word system
    prompt). Strips wrapping quote characters and trailing sentence
    punctuation.

    Returns empty string if no usable text remains — caller treats
    that as a failure outcome (better to leave "New chat" than to
    surface preamble as a title).
    """
    if not raw:
        return ""
    candidate = raw.rsplit(":", 1)[-1] if ":" in raw else raw
    candidate = candidate.strip().strip('"').strip("'").strip()
    words = candidate.split()
    if not words:
        return ""
    return " ".join(words[:6]).rstrip(".,;:!?")


def _agentcore_client() -> Any:
    """Lazy boto3 client construction — patched in unit tests."""
    return boto3.client("bedrock-agentcore")


def _memory_id_for_env() -> str:
    """Resolve the current env's AgentCore memoryId. Cached by
    ``get_or_create_memory``; cheap to call per request."""
    env = os.environ.get("STARTER_ENV", "unknown")
    return get_or_create_memory(env)


async def _wipe_agentcore_session(actor_id: str, chat_id: str) -> None:
    """Best-effort delete of all AgentCore events for one chat session.

    Pages list_events and calls delete_event per event. Wrapped in
    try/except at the endpoint level; this helper just does the work
    and lets exceptions propagate so the caller can record the
    failure metric. Same fail-soft contract as Phase 7c memory writes
    and Phase 8a recall.
    """
    client = _agentcore_client()
    memory_id = _memory_id_for_env()
    sanitized_actor = _sanitize_actor_id(actor_id)
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "memoryId": memory_id,
            "actorId": sanitized_actor,
            "sessionId": chat_id,
            "maxResults": 100,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = await asyncio.to_thread(client.list_events, **kwargs)
        for event in resp.get("events", []):
            await asyncio.to_thread(
                client.delete_event,
                memoryId=memory_id,
                actorId=sanitized_actor,
                sessionId=chat_id,
                eventId=event["eventId"],
            )
        next_token = resp.get("nextToken")
        if not next_token:
            break


async def _load_owned_chat(chat_id: str, user_id: str) -> Chat:
    """Look up a chat by id and assert ownership.  404 on mismatch."""

    chat = storage.get_chat_by_id(chat_id)
    if chat is None or chat.user_id != user_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str = Path(...),
    limit: int = 200,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Get a chat's metadata and a page of messages."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    messages, next_cursor = storage.list_messages(chat_id, limit=limit, cursor=cursor)
    return {
        "chat": chat.model_dump(),
        "messages": [m.model_dump(mode="json") for m in messages],
        "next_cursor": next_cursor,
    }


@router.patch("/{chat_id}", status_code=204)
async def patch_chat(
    payload: ChatPatch,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Rename or archive a chat.  204 No Content on success."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.patch_chat(
        user_id=claims["sub"],
        chat=chat,
        title=payload.title,
        archived=payload.archived,
    )
    return Response(status_code=204)


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Permanently delete a chat: DDB message rows + chat-index row +
    AgentCore session events. AgentCore failure is logged + counted
    but does NOT fail the user's delete (DDB is the source of truth
    for chat existence)."""
    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.delete_chat(user_id=claims["sub"], chat=chat)
    try:
        await _wipe_agentcore_session(actor_id=claims["sub"], chat_id=chat_id)
        await record_chat_delete_memory_wipe_outcome(success=True)
    except Exception as exc:
        logger.warning(
            "agentcore.chat_delete_wipe_failed chat_id=%s",
            chat_id,
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )
        await record_chat_delete_memory_wipe_outcome(success=False)
    # #174 — cascade-delete S3 objects + ATTACHMENT rows referenced by
    # the chat's messages. Best-effort, mirrors the AgentCore wipe
    # pattern: DDB is source of truth for chat existence, so cascade
    # failures are observability noise, not user-visible errors.
    try:
        deleted, failed = await asyncio.to_thread(
            storage.delete_chat_attachments,
            chat_id=chat_id,
            user_id=claims["sub"],
        )
        await record_chat_delete_attachment_wipe_outcome(success=(failed == 0))
        if failed:
            logger.warning(
                "attachment.chat_delete_partial_failure chat_id=%s deleted=%d failed=%d",
                chat_id,
                deleted,
                failed,
            )
    except Exception as exc:
        logger.warning(
            "attachment.chat_delete_wipe_failed chat_id=%s",
            chat_id,
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )
        await record_chat_delete_attachment_wipe_outcome(success=False)
    return Response(status_code=204)


def _build_tool_registry() -> list[Any]:
    """Assemble the per-turn tool registry from env-var kill switches.

    Each tool's registration is gated by a ``STARTER_<NAME>_ENABLED``
    flag — one flag per tool, except ``web_fetch``, which rides
    ``STARTER_WEB_SEARCH_ENABLED`` alongside ``web_search`` (see
    below). The code treats anything other than ``"1"`` as disabled —
    so an unset flag in a stale dev env (e.g. someone running tests
    without the CDK env vars wired up) results in the tool being
    silently omitted rather than crashing on import.

    The deployed envs ship explicit values:
      * ``STARTER_WEB_SEARCH_ENABLED`` — "1" everywhere (kill switch
        only; default-on posture once deployed). Gates BOTH
        ``web_search`` and ``web_fetch`` (#232): the pair is backed by
        the same Exa API key, so one availability signal covers the
        discover/deep-read pair without a second CDK env var.
      * ``STARTER_CODE_EXEC_ENABLED`` — "1" everywhere (kill switch
        only; default-on posture once deployed)
      * ``STARTER_CLOCK_TOOL_ENABLED`` — "1" in dev, "0" in prod
        (strategy spec policy P2: clock is a smoke-test tool, not a
        user-visible capability)

    Order is not significant — Strands collects tools into a name-keyed
    spec for the model.

    Extracted into a helper in #183 so the unit tests can exercise the
    flag matrix without spinning up the streaming coroutine. The
    ``web_search`` / ``web_fetch`` and ``code_exec`` imports stay lazy
    (per-call inside the helper) so a stale dev env without
    ``strands-agents-tools`` / ``boto3`` installed still loads ``chats``
    for a smoke test."""
    registry: list[Any] = []
    if os.environ.get("STARTER_CLOCK_TOOL_ENABLED") == "1":
        registry.append(current_time)
    if os.environ.get("STARTER_WEB_SEARCH_ENABLED") == "1":
        from channel.agents.tools.web_fetch import web_fetch  # noqa: PLC0415
        from channel.agents.tools.web_search import web_search  # noqa: PLC0415

        registry.append(web_search)
        registry.append(web_fetch)
    if os.environ.get("STARTER_CODE_EXEC_ENABLED") == "1":
        from channel.agents.tools.code_exec import code_exec  # noqa: PLC0415

        registry.append(code_exec)
    return registry


async def _build_mcp_clients_for_chat(
    *,
    user_id: str,
    chat_id: str,
) -> list[MCPClient]:
    """Resolve active MCP servers + tokens for one chat turn.

    Per the spike (§Question 2): chats inherit user-level
    ``globally_enabled`` servers by default; ``mode="explicit"`` rows
    capture an exact list at override time. Per-tool-call
    re-instantiation (spike §Question 3 v1 strategy) — every turn
    builds fresh ``MCPClient`` instances bound to the bearer token
    valid at turn start. Mid-chain refresh stays a v2 follow-up.

    Auth-resolution failures are best-effort logged + the MCPSERVER row
    flipped to ``EXPIRED`` so the SPA can surface the Reconnect
    affordance on the next Customize visit — but they do NOT raise into
    the streaming generator. A broken Hive registration must not block
    the chain's other tools.

    Kill switch: ``STARTER_MCP_REGISTRY_ENABLED != "1"`` short-circuits
    to an empty list with no DynamoDB reads. Defaults to enabled when
    unset so dev / test envs that don't provision the env var still see
    MCP behavior; CDK + ``inv dev`` both wire ``"1"`` explicitly.
    """
    if os.environ.get("STARTER_MCP_REGISTRY_ENABLED", "1") != "1":
        return []

    registered = storage.list_mcp_servers_for_user(user_id)
    settings = storage.get_chat_mcp_settings(chat_id)
    if settings.mode == ChatMCPMode.EXPLICIT:
        allowed = set(settings.explicit_server_ids)
        active = [s for s in registered if s.server_id in allowed]
    else:
        active = [s for s in registered if s.globally_enabled]

    # Filter to ACTIVE-only BEFORE token resolution. Without this:
    #   1. A freshly-registered (NEVER_AUTHED) server has no token row,
    #      so the resolver raises MCPAuthFailedError and we'd flip the
    #      row to EXPIRED — losing the user-visible distinction between
    #      "never connected" and "connection went stale".
    #   2. An EXPIRED row's resolver runs the discovery + refresh dance
    #      every single turn until the user reconnects — avoidable work
    #      that won't recover until the user re-completes OAuth.
    # The UI already hides inactive servers from the per-chat picker,
    # so this mirrors that behavior at the runtime layer.
    active = [s for s in active if s.auth_status == MCPServerAuthStatus.ACTIVE]

    clients: list[MCPClient] = []
    for server in active:
        # DNS-rebinding defense: re-validate the persisted URL with a
        # fresh DNS lookup before each turn. A hostname that resolved
        # to a public address at registration can later resolve to
        # loopback / link-local / RFC1918, which would let the Lambda
        # egress to internal infra via the MCP transport. The validator
        # raises HTTPException(400) on failure; we treat that as
        # "skip + log" rather than letting it bubble into the stream.
        try:
            validate_mcp_server_url(server.url)
        except HTTPException:
            logger.warning(
                "mcp.url_revalidation_failed",
                extra={
                    "user_id_hash": fingerprint_id(user_id),
                    "server_id_hash": fingerprint_id(server.server_id),
                },
            )
            continue
        try:
            token = await mcp_auth.get_valid_access_token(
                user_id=user_id,
                server=server,
            )
        except MCPAuthFailedError as exc:
            # user_id (JWT sub) and server_id (DDB UUID) are
            # server-validated identifiers, but Sonar's taint engine
            # treats them as user-controlled because they came in via
            # the request. Logging them via ``extra=`` (structured
            # payload) bypasses the format-string sink the rule flags;
            # ``fingerprint_id`` is a deterministic SHA-256 truncation
            # so the hashes are stable across cold starts (unlike the
            # builtin ``hash()`` which is salted per process).
            logger.warning(
                "mcp.token_resolution_failed",
                extra={
                    "user_id_hash": fingerprint_id(user_id),
                    "server_id_hash": fingerprint_id(server.server_id),
                    "exc_type": type(exc).__name__,
                },
            )
            storage.set_mcp_server_auth_status(
                user_id=user_id,
                server_id=server.server_id,
                status=MCPServerAuthStatus.EXPIRED,
            )
            continue
        clients.append(
            MCPClient(
                make_authenticated_transport(
                    server_url=server.url,
                    access_token=token,
                ),
                prefix=server.tool_prefix,
            )
        )
    return clients


async def _stream_bedrock_reply(
    *,
    chat: Chat,
    user_message: str,
    model: str,
    claims: dict[str, Any],
    state: dict[str, Any] | None = None,
    persist_user: bool = True,
    index_delta_count: int = 2,
    effort: str | None = None,
    attachment_content_blocks: list[dict[str, Any]] | None = None,
    attachment_snapshots: list[dict[str, Any]] | None = None,
    attachment_errors: list[dict[str, Any]] | None = None,
    verified_attachment_ids: list[str] | None = None,
) -> Any:
    """Persist the user turn, stream Strands events, persist the assistant turn.

    ``state`` is an opt-in dict the caller passes in to capture the
    accumulated assistant text, the user/assistant msg ids, and the final
    done-event fields.  ``post_message`` uses it to build the idempotency
    replay payload after the stream completes.  Callers that don't need
    capture (e.g. the ``regenerate`` handler) can omit it.

    ``persist_user`` controls whether the user message is written to
    DynamoDB and announced via ``user_persisted``.  Regenerate sets this
    to ``False`` because the user turn already exists.  ``index_delta_count``
    is the net change to ``message_count`` on the chat-index row — 2 for a
    fresh send (user + assistant), 0 for regenerate (deleted assistant +
    new assistant cancel out).

    ``effort`` overrides the per-turn ``max_tokens`` budget on the
    Strands BedrockModel (issue #154). When omitted, the user's saved
    pref tier applies.
    """

    state = state if state is not None else {}
    resolved_model = resolve_model_id(model)
    state["resolved_model"] = resolved_model
    # Defensive entry-time clear of the per-chat cancel signal. If a previous
    # turn on this chat ended via client disconnect, the except block below
    # left the signal SET (intentionally — see #181 PR-2 Copilot round-3) so
    # the guard could observe it during the dying chain. That stale value
    # would cancel the first tool call of THIS turn if left untouched. The
    # registry is module-level (`channel.agents.tool_hooks`), so even across
    # Lambda invocations on the same warm container we must clear at entry.
    clear_cancel_signal(chat.chat_id)
    # Capture "this was the first round-trip" BEFORE we persist anything.
    # Auto-title block below uses it to fire exactly once per chat
    # (Phase 7d idempotency).
    was_first_round_trip = chat.message_count == 0
    # Load prefs once at stream start. Used by the follow-ups block
    # after the assistant turn lands; defaults are applied at the
    # storage layer when no row exists. Per-request ``effort`` overrides
    # the saved pref so the segmented control in the Composer takes
    # priority over the Customize default.
    prefs = storage.get_prefs(claims["sub"])
    effective_effort = effort if effort is not None else prefs.effort

    # Load the chat's stored history BEFORE persisting the new user
    # message so the loaded list is the true prior context. For
    # regenerate (``persist_user=False``) the trailing message is
    # already the user turn we're about to re-stream; drop it so
    # Strands doesn't see it twice (once in ``messages=`` history,
    # once via ``stream_async(user_message)``).
    prior_msgs, _ = storage.list_messages(chat.chat_id, limit=_HISTORY_TURNS_LIMIT, cursor=None)
    if not persist_user and prior_msgs and prior_msgs[-1].role == MessageRole.USER:
        prior_msgs = prior_msgs[:-1]
    prior_messages = _to_strands_messages(prior_msgs)

    # Attachments are resolved by the route handler BEFORE this
    # generator starts so HTTPException(400) on unowned/missing ids
    # surfaces as a clean 400 instead of mid-stream chaos. By the time
    # we see the resolved tuple here, ownership has already been
    # validated and only S3-verify failures (soft) remain.
    content_blocks = attachment_content_blocks or []
    verified_ids = verified_attachment_ids or []
    errors_list = attachment_errors or []

    if persist_user:
        user_msg = storage.put_message(
            chat_id=chat.chat_id,
            role=MessageRole.USER,
            text=user_message,
            model=None,
            attachments=attachment_snapshots,
        )
        state["user_msg_id"] = user_msg.msg_id
        yield sse_user_persisted(msg_id=user_msg.msg_id, seq=0)

    # Attachment side-effects fire on BOTH the initial send and regenerate.
    # The original send-side ``referenced_at`` stamp is preserved across
    # regenerate (the re-stamp is harmless idempotent under the
    # ``attribute_exists(PK)`` guard in storage). And the SPA needs the
    # ``attachment_error`` event whenever a verify fails — regenerate
    # replays the same attachments, so a freshly-expired S3 object should
    # still prompt the re-upload UI on the second-shot stream.
    for att_id in verified_ids:
        storage.mark_attachment_referenced(user_id=claims["sub"], att_id=att_id)
    for err in errors_list:
        yield sse_attachment_error(**err)

    tool_registry = _build_tool_registry()
    # #207 — append MCPClient instances. Resolution failures are
    # swallowed inside _build_mcp_clients_for_chat (the helper flips
    # the MCPSERVER row to EXPIRED so the SPA can surface Reconnect).
    mcp_clients = await _build_mcp_clients_for_chat(
        user_id=claims["sub"],
        chat_id=chat.chat_id,
    )
    tool_registry = [*tool_registry, *mcp_clients]

    agent = build_agent(
        model_id=model,
        user_id=claims["sub"],
        chat_id=chat.chat_id,
        prior_messages=prior_messages,
        effort=effective_effort,
        tools=tool_registry,
    )
    accumulated: list[str] = []
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0
    # Dedup tool_started — Strands' ``ToolUseStreamEvent`` fires once
    # per input-token while the model is still emitting the call. The
    # SPA only wants a single step row per ``tool_use_id``.
    emitted_tool_starts: set[str] = set()
    # Per-chain count of completed tool calls — used to populate
    # ``partial_result_count`` on ``tool_error`` events per the
    # ``sse_tool_error`` contract: "a chain that fires 3 of 5 steps
    # and fails on 4 still surfaces partial_result_count=3". The
    # translator can't track this — it's per-stream dispatcher state.
    completed_tool_calls = 0

    # When attachments are present, Strands gets the labeled content-block
    # list with the user's text appended; otherwise the bare string keeps
    # the existing happy path.
    user_payload: Any = (
        [*content_blocks, {"text": user_message}] if content_blocks else user_message
    )

    # Cancel-signal lifecycle (#181 PR-2, revised in Copilot round-3):
    # if the SSE client disconnects mid-stream FastAPI raises
    # ``asyncio.CancelledError`` (or closes the generator with
    # ``GeneratorExit``) inside this loop. We surface that by flipping
    # the per-chat cancel signal; ``ToolCallGuardHook`` reads it on the
    # next ``BeforeToolCallEvent`` and aborts the chain (and the guard
    # one-shot-consumes the flag, see ``ToolCallGuardHook`` in
    # ``channel.agents.tool_hooks``).
    #
    # The signal is deliberately NOT cleared in a ``finally`` here: a
    # ``finally`` would fire synchronously when the generator unwinds,
    # so by the time anything else looked at the registry the flag would
    # always be False — the cancel would be unobservable. Instead the
    # signal persists past this dying generator and is cleared by either
    # (a) the guard's one-shot consume if the chain ran another tool
    # call during the cancellation window, or (b) the entry-time clear
    # at the top of this function on the NEXT turn for the same chat.
    try:
        async for event in agent.stream_async(user_payload):
            kind, payload = translate_event(event)
            if kind == "delta":
                accumulated.append(payload)
                yield sse_delta(payload)
            elif kind == "stop":
                stop_reason = payload["stop_reason"]
            elif kind == "usage":
                input_tokens = payload["input_tokens"]
                output_tokens = payload["output_tokens"]
            elif kind == "tool_started":
                tool_use_id = payload["tool_use_id"]
                if tool_use_id and tool_use_id not in emitted_tool_starts:
                    emitted_tool_starts.add(tool_use_id)
                    yield sse_tool_started(**payload)
            elif kind == "tool_progress":
                yield sse_tool_progress(**payload)
            elif kind == "tool_finished":
                completed_tool_calls += 1
                yield sse_tool_finished(**payload)
            elif kind == "tool_error":
                # Override the translator's hardcoded 0 with the
                # actual per-chain completed-call count. See the
                # ``sse_tool_error`` docstring for the contract.
                payload = {**payload, "partial_result_count": completed_tool_calls}
                yield sse_tool_error(**payload)
            elif kind == "tool_results":
                # #183 post-deploy fix: Strands' ``ToolResultMessageEvent``
                # bundles all completed tool results from one cycle into a
                # single message. translate_event splits this into a list
                # of (kind, payload) tuples — one per result. Iterate and
                # emit per the existing tool_finished / tool_error rules.
                for sub_kind, sub_payload in payload:
                    if sub_kind == "tool_finished":
                        completed_tool_calls += 1
                        yield sse_tool_finished(**sub_payload)
                    elif sub_kind == "tool_error":
                        sub_payload = {
                            **sub_payload,
                            "partial_result_count": completed_tool_calls,
                        }
                        yield sse_tool_error(**sub_payload)
    except (asyncio.CancelledError, GeneratorExit):
        set_cancel_signal(chat.chat_id)
        raise
    finally:
        # #207 — Strands' MCPClient holds a background thread + httpx
        # client that must be released when the turn ends. agent.cleanup()
        # is a no-op for natives; safe to call unconditionally on real
        # Agent instances. Without this we rely on the GC finalizer
        # which isn't deterministic in async generators and can leak
        # the MCP background thread across warm Lambda invocations.
        # getattr keeps the call safe under unit-test FakeAgent doubles
        # that don't implement the optional Strands cleanup surface.
        cleanup = getattr(agent, "cleanup", None)
        if cleanup is not None:
            cleanup()  # pragma: no cover - exercised by real Strands Agent only

    assistant_text = "".join(accumulated)
    state["assistant_text"] = assistant_text
    state["input_tokens"] = input_tokens
    state["output_tokens"] = output_tokens
    state["stop_reason"] = stop_reason

    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=assistant_text,
        model=resolved_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    state["assistant_msg_id"] = assistant_msg.msg_id

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=index_delta_count,
        last_message_at=assistant_msg.created_at,
    )

    yield sse_done(
        msg_id=assistant_msg.msg_id,
        seq=1,
        model=resolved_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
    )

    # Phase 7d auto-title: after the FIRST round-trip lands, fire a
    # one-shot Haiku Agent to summarise the exchange in 3-6 words.
    # Inline-emitted in the same SSE stream after ``done`` and BEFORE
    # close — the SPA's readSse loop iterates until reader is done.
    # Fail-soft: titler errors log + EMF + swallow; the user's stream
    # has already completed by this point.
    if was_first_round_trip and os.environ.get("STARTER_AUTO_TITLE_ENABLED", "1") == "1":
        truncated = False
        try:
            titler = build_titler_agent()
            titler_prompt = f"User: {user_message}\nAssistant: {assistant_text[:500]}"
            title_chunks: list[str] = []
            try:
                async for event in titler.stream_async(titler_prompt):
                    kind, payload = translate_event(event)
                    if kind == "delta":
                        title_chunks.append(payload)
            except MaxTokensReachedException:
                # Strands emits deltas BEFORE raising on token-cap, so
                # title_chunks already holds usable partial text.
                # _postprocess_title strips preamble and caps at 6
                # words; empty result → caller records failure.
                truncated = True
            title = _postprocess_title("".join(title_chunks))
            if title:
                storage.patch_chat(
                    user_id=claims["sub"],
                    chat=chat,
                    title=title,
                    archived=None,
                )
                yield sse_title_suggested(chat_id=chat.chat_id, title=title)
                await record_auto_title_outcome(success=True)
            else:
                await record_auto_title_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "auto_title_failed chat_id=%s truncated=%s",
                chat.chat_id,
                truncated,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_auto_title_outcome(success=False)

    # Follow-up suggestions. Runs after auto-title so the SPA gets the
    # title first (sidebar refresh) and the chips appear under the
    # assistant turn. Gated by both an env kill-switch and the user's
    # ``suggest_followups`` pref (default True). Fail-soft: a generation
    # error logs + EMF + swallow; the stream has already completed
    # successfully by this point.
    followups_enabled = os.environ.get("STARTER_FOLLOWUPS_ENABLED", "1") == "1"
    if followups_enabled and prefs.suggest_followups:
        try:
            followups_agent = build_followups_agent()
            followups_prompt = (
                f"User: {user_message}\n\nAssistant: {assistant_text[:1000]}\n\nFollow-up prompts:"
            )
            chunks: list[str] = []
            try:
                async for event in followups_agent.stream_async(followups_prompt):
                    kind, payload = translate_event(event)
                    if kind == "delta":
                        chunks.append(payload)
            except MaxTokensReachedException:
                # Strands emits deltas BEFORE raising on token-cap; any
                # already-accumulated lines below are still usable.
                pass
            suggestions = _parse_followups("".join(chunks))
            if suggestions:
                yield sse_follow_ups_suggested(
                    chat_id=chat.chat_id,
                    message_id=assistant_msg.msg_id,
                    suggestions=suggestions,
                )
                await record_followup_outcome(success=True)
            else:
                await record_followup_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "followups_generation_failed chat_id=%s",
                chat.chat_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_followup_outcome(success=False)


def _parse_followups(raw: str) -> list[str]:
    """Parse a free-form Haiku reply into a list of 2-3 follow-up prompts.

    Trims numbering, list bullets, trailing punctuation, and empty
    lines. Caps at 3 suggestions to keep the chip row visually bounded.
    """
    suggestions: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip().lstrip("-*•0123456789. ").rstrip(".,;:")
        if cleaned:
            suggestions.append(cleaned)
    return suggestions[:3]


@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Send a user message and stream the Bedrock assistant reply via Strands."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or _DEFAULT_MODEL

    # Resolve attachments BEFORE the streaming response begins so the
    # 400-on-unowned-id surfaces cleanly. S3 verify happens here too;
    # individual failures become labeled blocks + SSE errors inside
    # the stream (soft fail), not 400s.
    content_blocks, snapshots, att_errors, verified_ids = _resolve_attachments_for_send(
        user_id=claims["sub"], attachments=payload.attachments
    )

    replay: dict[str, Any] | None = None
    if idempotency_key:
        existing = storage.reserve_idempotency_key(user_id=claims["sub"], key=idempotency_key)
        if existing is not None and existing.get("result"):
            replay = existing["result"]

    if replay is not None:
        replay_payload = replay

        async def _replay() -> Any:
            yield sse_user_persisted(msg_id=replay_payload["user_msg_id"], seq=0)
            yield sse_delta(replay_payload["text"])
            yield sse_done(**replay_payload["done"])

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _produce() -> Any:
        state: dict[str, Any] = {}
        async for chunk in _stream_bedrock_reply(
            chat=chat,
            user_message=payload.message,
            model=model,
            claims=claims,
            state=state,
            effort=payload.effort,
            attachment_content_blocks=content_blocks,
            attachment_snapshots=snapshots,
            attachment_errors=att_errors,
            verified_attachment_ids=verified_ids,
        ):
            yield chunk
        if idempotency_key and state.get("assistant_msg_id"):
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": state["user_msg_id"],
                    "text": state["assistant_text"],
                    "done": {
                        "msg_id": state["assistant_msg_id"],
                        "seq": 1,
                        "model": state["resolved_model"],
                        "input_tokens": state["input_tokens"],
                        "output_tokens": state["output_tokens"],
                        "stop_reason": state["stop_reason"],
                    },
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")


@router.post("/{chat_id}/regenerate")
async def regenerate(
    payload: RegenerateRequest,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Drop the last assistant message and re-stream from the last user message."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or chat.model_default or _DEFAULT_MODEL

    storage.delete_last_assistant_message(chat_id)

    msgs, _ = storage.list_messages(chat_id, limit=50, cursor=None)
    last_user = next((m for m in reversed(msgs) if m.role == MessageRole.USER), None)
    if last_user is None:
        raise HTTPException(
            status_code=400, detail="Cannot regenerate — chat has no user messages."
        )

    # #176 — replay any attachments the prior user turn carried. The
    # snapshot is denormalised so we only need the ids; the resolver
    # re-fetches the canonical rows and rebuilds the content blocks
    # exactly as the original send did.
    replay_attachments = [
        {"id": s.get("id")} for s in (last_user.attachments or []) if s.get("id")
    ] or None
    content_blocks, snapshots, att_errors, verified_ids = _resolve_attachments_for_send(
        user_id=claims["sub"], attachments=replay_attachments
    )

    return StreamingResponse(
        _stream_bedrock_reply(
            chat=chat,
            user_message=last_user.text,
            model=model,
            claims=claims,
            persist_user=False,
            index_delta_count=0,
            effort=payload.effort,
            attachment_content_blocks=content_blocks,
            attachment_snapshots=snapshots,
            attachment_errors=att_errors,
            verified_attachment_ids=verified_ids,
        ),
        media_type="text/event-stream",
    )


@router.post("/{chat_id}/messages/{msg_id}/feedback", status_code=204)
async def submit_feedback(
    payload: FeedbackRequest,
    chat_id: str = Path(...),
    msg_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Persist a thumbs-up / thumbs-down feedback record for a message.

    Idempotent overwrite — submitting again replaces the prior record
    on the same message. ``msg_id`` must belong to ``chat_id`` and the
    chat must be owned by the caller. Mismatches return 404 so chat /
    message existence isn't leaked.
    """

    chat = await _load_owned_chat(chat_id, claims["sub"])
    feedback = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg_id,
        kind=payload.kind,
        note=payload.note,
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=204)

# Copyright (c) 2026 John Carter. All rights reserved.
"""Pydantic models for chat domain — Chat, Message, and request/response shapes.

These are wire-format and storage-format models.  They are intentionally
flat: nested objects (artifacts, attachments) stay as ``list[dict]`` so
schema evolution doesn't require model surgery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MessageRole(str, Enum):
    """Roles for chat turns. ``system`` and other roles are rejected at validation time so they can't accidentally be stored."""

    USER = "user"
    ASSISTANT = "assistant"


class FeedbackKind(str, Enum):
    """Thumbs-up / thumbs-down feedback on an assistant message.

    The wire-format spelling matches what the SPA already uses on its
    UI buttons (``Good`` → ``up``, ``Bad`` → ``down``). Any other value
    fails request validation with 422.
    """

    UP = "up"
    DOWN = "down"


class Feedback(BaseModel):
    """Per-message feedback record persisted on the message DDB row.

    Stored as an inline attribute on the ``MSG#`` row — overwritten on
    each new submission for the same message (issue #146). Future
    follow-ups may surface ``note`` via a modal in the UI.
    """

    kind: FeedbackKind
    note: str | None = None
    created_at: str


class Chat(BaseModel):
    """Chat-index row — one per chat, partition key USER#{user_id}."""

    chat_id: str
    user_id: str
    title: str
    created_at: str
    last_message_at: str
    last_user_preview: str = ""
    model_default: str
    message_count: int = 0
    archived: bool = False

    @field_validator("last_user_preview")
    @classmethod
    def _cap_preview(cls, value: str) -> str:
        """Cap to 120 chars at construction time. Pydantic v2 does not validate on assignment, so direct attribute mutation bypasses this."""
        return value[:120]


class Citation(BaseModel):
    """One source citation on an assistant message turn (#174).

    Polymorphic shape pinned per Channel's 2026-06-03 design input —
    ``source_type`` is an open enum so future sources (web_search,
    code_output, memory_recall) slot in without a schema bump.
    ``location`` stays a free-form dict with per-``source_type``
    validation so DDB persistence is one column, not a discriminated
    union.

    v1 always populates ``Message.citations`` as ``None``; this model
    locks the shape so #128 (calibration), web-search, and the recall
    hook can fill it later.
    """

    source_type: Literal["attachment", "web_search", "code_output", "memory_recall"]
    source_id: str
    source_name: str
    location: dict[str, Any]
    excerpt: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None

    # Compatibility table — keyed by ``source_type``, value is the set
    # of ``location.type`` strings that source type accepts. For source
    # types whose location schema hasn't been pinned yet (code_output,
    # memory_recall — owned by #128 and the recall hook), the empty set
    # means "accept any non-empty location dict with a type key" —
    # forward-compat scaffolding so those sub-issues can lock in their
    # schemas without breaking v1.
    _ALLOWED_LOCATION_TYPES: dict[str, set[str]] = {
        "attachment": {"page", "cell", "region", "timestamp"},
        "web_search": {"url"},
        "code_output": set(),
        "memory_recall": set(),
    }

    @model_validator(mode="after")
    def _validate_location_shape(self) -> Citation:
        loc_type = self.location.get("type")
        if not isinstance(loc_type, str) or not loc_type:
            raise ValueError("Citation.location must include a 'type' string")
        allowed = self._ALLOWED_LOCATION_TYPES.get(self.source_type, set())
        # Empty set = "schema not yet pinned, accept any type" — see class
        # docstring for the rationale.
        if allowed and loc_type not in allowed:
            raise ValueError(
                f"Citation.location.type={loc_type!r} not allowed for "
                f"source_type={self.source_type!r} (allowed: {sorted(allowed)})"
            )
        return self


class Attachment(BaseModel):
    """Canonical attachment row — partition key USER#{user_id} (#174).

    One row per uploaded file, written by the finalize endpoint (#175)
    after the browser PUTs to S3. The S3 object itself is the payload;
    this row carries the metadata (filename, mime, size, checksum) and
    the S3 coordinates so the cascade in
    :func:`storage.delete_chat_attachments` can wipe both sides on
    chat deletion.

    ``referenced_at`` is set the first time a message references this
    attachment (#175 finalize handler or #176's content-block builder).
    Distinct from ``created_at`` so the lifecycle rule on the bucket
    (``unreferenced=1`` tag) can garbage-collect orphans.
    """

    id: str
    user_id: str
    name: str
    mime: str
    size_bytes: int
    s3_key: str
    s3_bucket: str
    checksum_sha256: str
    created_at: str
    referenced_at: str | None = None


# Inline-content ceiling for Asset rows (#324, epic #321 decision Q2).
# Text at or under this many UTF-8 bytes lives in the DDB item's
# ``content`` attribute (comfortably under the 400 KB item cap);
# anything larger — and ALL binary content — goes to S3.
ASSET_INLINE_CONTENT_MAX_BYTES = 100 * 1024


class Asset(BaseModel):
    """Unified chat asset row — partition key CHAT#{chat_id} (#324, epic #321).

    One row per asset (upload projection, generated artifact, or tool
    output) at ``PK=CHAT#{chat_id}, SK=ASSET#{created_at}#{asset_id}``,
    mirroring the ``MSG#`` convention so the chat-scoped lifetime is
    structural (the delete cascade is one Query on the chat partition).
    Every row also projects onto the ``AssetOwnerIndex`` GSI via
    ``owner_pk=ASSETOWNER#{owner}`` / ``owner_sk={created_at}#{asset_id}``
    for the cross-chat browse view.

    ``owner`` is the ownership axis feeding the GSI key — today the
    user_id (JWT ``sub``), later ``{workspace_id}/{user_id}`` when
    workspaces land. Single-attribute migration by design: do NOT add
    a second tenancy field (settled in the #321 design review).

    Payload placement is exclusive: inline text ≤
    :data:`ASSET_INLINE_CONTENT_MAX_BYTES` lives in ``content``;
    everything larger and all binary content lives in S3 under
    ``assets/chat/{chat_id}/{asset_id}`` (``s3_bucket`` + ``s3_key``,
    always set together). Exactly one of the two forms must be present.

    ``source`` stays a free-form dict (``msg_id`` required; optional
    ``tool_use_id`` / ``fence_index``) per this module's flat-model
    philosophy — schema evolution without model surgery. There is
    deliberately no ``pinned`` attribute: "pin to survive chat
    deletion" is explicitly v2 (John, 2026-07-12).
    """

    asset_id: str
    chat_id: str
    owner: str
    kind: Literal["code", "document", "data", "image", "diagram"]
    title: str
    mime: str
    size_bytes: int
    origin: Literal["upload", "generated", "tool_output"]
    source: dict[str, Any]
    content: str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _validate_payload_and_source(self) -> Asset:
        if (self.s3_bucket is None) != (self.s3_key is None):
            raise ValueError("Asset.s3_bucket and Asset.s3_key must be set together")
        has_inline = self.content is not None
        has_s3 = self.s3_bucket is not None
        if has_inline == has_s3:
            raise ValueError("Asset requires exactly one of inline content or s3_bucket+s3_key")
        if self.content is not None and (
            len(self.content.encode("utf-8")) > ASSET_INLINE_CONTENT_MAX_BYTES
        ):
            raise ValueError(
                f"Asset.content exceeds the inline cap of "
                f"{ASSET_INLINE_CONTENT_MAX_BYTES} bytes — store it in S3 instead"
            )
        msg_id = self.source.get("msg_id")
        if not isinstance(msg_id, str) or not msg_id:
            raise ValueError("Asset.source must include a non-empty 'msg_id' string")
        return self


class Message(BaseModel):
    """One turn in a chat — partition key CHAT#{chat_id}."""

    chat_id: str
    msg_id: str
    role: MessageRole
    text: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    artifacts: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    citations: list[Citation] | None = None
    created_at: str
    ttl: int | None = None
    feedback: Feedback | None = None

    @model_validator(mode="after")
    def _citations_assistant_only(self) -> Message:
        # Citations are produced by the model; user turns never carry
        # them. Per the #174 issue body's reading of Channel's
        # 2026-06-03 input — citations point AT sources, and a user
        # turn isn't a source.
        if self.citations and self.role == MessageRole.USER:
            raise ValueError("citations may not be set on user-role messages")
        return self


class ChatCreate(BaseModel):
    """Request body for POST /api/chats."""

    title: str | None = None
    model_default: str | None = None


class ChatPatch(BaseModel):
    """Request body for PATCH /api/chats/{id}."""

    title: str | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    """Request body for POST /api/chats/{id}/messages."""

    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    effort: str | None = None
    attachments: list[dict[str, Any]] | None = None


class RegenerateRequest(BaseModel):
    """Request body for POST /api/chats/{id}/regenerate."""

    model: str | None = None
    effort: str | None = None


class FeedbackRequest(BaseModel):
    """Request body for POST /api/chats/{id}/messages/{msg_id}/feedback.

    ``note`` is reserved for a future note-input modal — v1 always sends
    ``null`` (issue #146). The pydantic validator caps ``note`` at
    1000 chars defensively so a bad client can't smuggle a huge string
    into the row.
    """

    kind: FeedbackKind
    note: str | None = Field(default=None, max_length=1000)


class Prefs(BaseModel):
    """User preferences — single JSON-blob row at ``PK=USER#{u}, SK=PREFS``.

    All fields have defaults so a missing DDB row yields the canonical
    defaults at hydration time. ``extra="forbid"`` rejects unknown keys
    so a stale client can't silently write a typo.
    """

    theme: str = "dark"
    accent: str = "42"
    density: str = "cozy"
    shape: str = "soft"
    font: str = "figtree"
    model: str = "claude-sonnet-4-6"
    effort: str = "High"
    send_on_enter: bool = True
    show_reasoning: bool = False
    suggest_followups: bool = True

    model_config = ConfigDict(extra="forbid")


class MCPServerAuthStatus(str, Enum):
    NEVER_AUTHED = "never_authed"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MCPServerAuthType(str, Enum):
    """How Channel acquires the bearer credential for an MCP server.

    ``oauth_dcr`` is the original flow (discovery → Dynamic Client
    Registration → auth-code → token). ``static_token`` skips all of
    that: the user pastes a pre-issued bearer token (PAT) that Channel
    encrypts and uses directly. See #375.
    """

    OAUTH_DCR = "oauth_dcr"
    STATIC_TOKEN = "static_token"


class MCPServer(BaseModel):
    """A user-registered MCP server. Matches the spike's MCPSERVER row."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    user_id: str
    name: str
    url: str  # e.g. "https://hive.warlordofmars.net/mcp"
    # DCR-issued client id, opaque to Channel. ``None`` for a
    # static-token server — there is no DCR client for a pasted PAT.
    client_id: str | None = None
    tool_prefix: str  # passed to MCPClient(prefix=...) — must be [a-z0-9_]+
    auth_type: MCPServerAuthType = MCPServerAuthType.OAUTH_DCR
    auth_status: MCPServerAuthStatus
    globally_enabled: bool = True  # spike default — see §Question 2 rationale
    created_at: str
    updated_at: str


class MCPToken(BaseModel):
    """Per-(user, server) OAuth token bundle. Encrypted at application layer.

    A static-token (PAT) server stores its bearer token in
    ``access_token_ciphertext`` with ``refresh_token_ciphertext=None``
    and a far-future ``expires_at`` sentinel — a PAT is effectively
    non-expiring, so it must never enter the refresh path (see #375 and
    ``mcp.auth.get_valid_access_token``).
    """

    model_config = ConfigDict(extra="forbid")

    server_id: str
    user_id: str
    # KMS-encrypted blobs — see src/channel/mcp/crypto.py for the round-trip.
    access_token_ciphertext: bytes
    refresh_token_ciphertext: bytes | None = None
    expires_at: int  # Unix seconds — absolute, not relative
    granted_scope: str  # space-separated, as returned by the token endpoint
    updated_at: str


# ----------------------------------------------------------------
# Refresh tokens (#290, epic #241)
# ----------------------------------------------------------------

# Absolute session lifetime. A refresh-token family lives at most this
# long from the login that created it — rotation never extends it, so a
# stolen token can never outlive the original sign-in by more than the
# idle window. Also drives the DynamoDB ``ttl`` on every row in the
# family, so the whole family self-prunes together.
REFRESH_ABSOLUTE_LIFETIME_SECONDS = 30 * 24 * 3600

# Idle timeout. Every successful rotation pushes the new row's
# ``idle_expires_at`` this far into the future; a session that goes
# quiet for longer than this is dead even though the absolute window
# is still open.
REFRESH_IDLE_TIMEOUT_SECONDS = 7 * 24 * 3600


class RefreshRevokeReason(str, Enum):
    """Why a refresh row stopped being usable.

    The distinction is load-bearing, not cosmetic: reuse detection keys
    off :attr:`ROTATED` specifically. A row revoked because it was
    legitimately consumed and superseded is the *only* shape whose
    re-presentation means "someone replayed a token that already did its
    job" — the OAuth 2.1 reuse signal (RFC 9700 §4.14.2). Rows revoked
    by logout or by an earlier reuse cascade belong to a family that is
    already dead, so re-presenting one is a plain rejection, not a fresh
    breach signal, and must not re-trigger the cascade.
    """

    ROTATED = "rotated"
    LOGOUT = "logout"
    REUSE_DETECTED = "reuse_detected"
    USER_REVOKED = "user_revoked"


class RefreshConsumeOutcome(str, Enum):
    """Result of presenting a refresh token to :func:`storage.consume_refresh_token`.

    Deliberately finer-grained than a bare ``RefreshToken | None``: #294
    needs to emit a ``RefreshReuseDetected`` counter separately from
    ``RefreshFailure``, and that is only possible if the storage layer
    tells the caller *which* rejection it hit.
    """

    OK = "ok"
    NOT_FOUND = "not_found"
    REUSED = "reused"
    REVOKED = "revoked"
    EXPIRED_ABSOLUTE = "expired_absolute"
    EXPIRED_IDLE = "expired_idle"


class RefreshToken(BaseModel):
    """One refresh-token row — ``PK=REFRESH#{token_hash}, SK=META`` (#290).

    **The raw token is never persisted.** ``token_hash`` is the SHA-256
    hex digest of the opaque 256-bit token handed to the client; a
    database disclosure therefore yields no usable credential. The same
    reasoning as the MCP-token application-layer encryption, one notch
    stronger — a hash is not reversible even with the key.

    Rows also project onto ``RefreshByUserIndex``
    (``GSI5PK=REFRESH_USER#{user_id}``,
    ``GSI5SK={issued_at}#{token_hash prefix}``) so a user's whole session
    set is one Query — the read path behind per-device revoke and the
    #293 sessions API.

    Two independent expiry columns, both enforced on consume:

    - ``absolute_expires_at`` — fixed at first login
      (:data:`REFRESH_ABSOLUTE_LIFETIME_SECONDS`) and *carried forward
      unchanged* by every rotation.
    - ``idle_expires_at`` — recomputed
      (:data:`REFRESH_IDLE_TIMEOUT_SECONDS` from now) on each rotation.

    ``revoked`` is the live/dead flag; ``revoked_reason`` records why so
    reuse detection can tell a superseded row from a logged-out one.

    ``last_used_at`` equals ``issued_at`` on a live row, and that is
    correct rather than a dead column: under hard rotation a row is
    consumed exactly once and dies doing it, so the live row was itself
    *created* by the device's most recent refresh — its ``issued_at``
    already is "when this device last talked to us", which is what
    #293's session list wants. For a dead row, the moment of use is
    ``revoked_at`` paired with ``revoked_reason == ROTATED``. The column
    is kept distinct because a future sliding-window rotation (epic
    #241 Q1's rejected alternative) would make the two diverge.

    ``display_name`` is the one claim a refreshed access token cannot
    rebuild from ``user_id`` (#292). Google's ``name`` claim reaches us
    exactly once — on the OAuth callback — so without carrying it here a
    refreshed session degrades to the email's local-part, which
    ``Sidebar.jsx`` / ``ChatHome.jsx`` render as the *legacy token*
    fallback rather than a normal state. Optional because rows minted
    before #292 (and any future non-Google issuer that has no name to
    give) simply don't have one; readers fall back to the local-part.
    Carried forward unchanged by rotation, exactly like
    ``absolute_expires_at``, so it survives the family's whole life.
    Deliberately *not* recomputed per refresh the way ``role`` is: a
    renamed Google account keeps the login-time name until the next full
    sign-in, which is the cheaper half of a trade that would otherwise
    put an identity-provider round trip on the refresh path.
    """

    model_config = ConfigDict(extra="forbid")

    token_hash: str
    user_id: str
    device_id: str
    issued_at: str
    last_used_at: str
    absolute_expires_at: str
    idle_expires_at: str
    display_name: str | None = None
    revoked: bool = False
    revoked_reason: RefreshRevokeReason | None = None
    revoked_at: str | None = None

    @model_validator(mode="after")
    def _revocation_fields_agree(self) -> RefreshToken:
        if not self.revoked and (self.revoked_reason is not None or self.revoked_at is not None):
            raise ValueError("RefreshToken.revoked_reason/revoked_at require revoked=True")
        return self


class RefreshConsumeResult(BaseModel):
    """Outcome of a refresh-token consume, plus whatever it produced.

    On :attr:`RefreshConsumeOutcome.OK` the caller gets both the freshly
    minted ``raw_token`` (the only place that plaintext ever exists) and
    the ``token`` row describing it. Every other outcome carries neither.
    ``revoked_count`` is non-zero only on the reuse path, where the
    breach response revokes the rest of the device's family.

    .. warning::
       ``raw_token`` is a real field, so it appears in ``model_dump()``
       and inside ``ValidationError.errors()['input']``. Never return
       this object straight out of a route, log it, or log
       ``exc.errors()`` for it — hand the plaintext to the transport
       and drop the rest. Relevant to #291 / #292, which are the first
       callers with a response body.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: RefreshConsumeOutcome
    raw_token: str | None = None
    token: RefreshToken | None = None
    revoked_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _payload_matches_outcome(self) -> RefreshConsumeResult:
        produced = self.raw_token is not None and self.token is not None
        partial = (self.raw_token is None) != (self.token is None)
        if partial:
            raise ValueError("RefreshConsumeResult.raw_token and .token must be set together")
        if produced != (self.outcome == RefreshConsumeOutcome.OK):
            raise ValueError("RefreshConsumeResult carries a rotated token iff outcome is 'ok'")
        # Only the reuse cascade revokes anything, so a non-zero count on
        # any other outcome means a caller mixed up two code paths — and
        # #294 reads this field to size the breach signal, so a stray
        # count would inflate a security metric.
        if self.revoked_count and self.outcome != RefreshConsumeOutcome.REUSED:
            raise ValueError(
                "RefreshConsumeResult.revoked_count is non-zero only when outcome is 'reused'"
            )
        return self


class ChatMCPMode(str, Enum):
    INHERIT = "inherit"
    EXPLICIT = "explicit"


class ChatMCPSettings(BaseModel):
    """Per-chat MCP override. Lives at PK=CHAT#{chat_id}, SK=MCPSERVERS#META."""

    model_config = ConfigDict(extra="forbid")

    chat_id: str
    mode: ChatMCPMode = ChatMCPMode.INHERIT
    explicit_server_ids: list[str] = Field(default_factory=list)

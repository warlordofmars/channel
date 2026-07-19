# Copyright (c) 2026 John Carter. All rights reserved.
"""First-party / featured MCP server catalog.

A small, curated, server-defined list of MCP servers Channel promotes as
one-click integrations. The catalog is *config*, not user data — it never
touches DynamoDB and carries no secrets. The SPA reads it via
``GET /api/mcp/featured`` to render a one-click "Enable" affordance, and
``POST /api/mcp/servers`` accepts a ``featured_id`` so the actual
registration inherits the catalog's canonical URL, credential type, and
default enablement server-side (never on client trust).

The flagship entry is GitHub's official MCP server
(``https://api.githubcopilot.com/mcp/``). It authenticates with a static
PAT (GitHub's server does **not** support Dynamic Client Registration —
see #375), so it rides the ``static_token`` credential path.

Two safety properties are baked into the GitHub entry per #277:

* ``default_globally_enabled=False`` — GitHub is a read-**write** surface;
  the user must opt in per chat. Registration honours this server-side so
  a one-click enable can never silently make write hands globally live.
* ``write_surface_tools`` — the authoritative list of GitHub MCP tools
  that mutate state (comment, draft/merge, push files, …). It is the
  single source of truth for the deferred write-surface audit wiring
  (#277 scope item 4), which records each ``tool_finished`` whose tool
  name matches this set. That consumer lives in the SSE stream path
  (``api/chats.py`` / ``agents/strands_sse.py``) and is gated by #299
  (shared-memory authorization boundary), so it is intentionally NOT
  wired here — this module only *names* the surface. The names track the
  official ``github/github-mcp-server`` write toolset and should be
  revisited when that audit consumer lands, as GitHub's toolset evolves.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from channel.models import MCPServerAuthType

# GitHub MCP write-surface tool names (unprefixed — Channel surfaces them
# to the model as ``github_<tool>`` via the server's tool_prefix, but the
# audit consumer matches on the underlying GitHub tool name). Mirrors the
# official github/github-mcp-server write toolset.
_GITHUB_WRITE_SURFACE_TOOLS: tuple[str, ...] = (
    "issue_write",
    "add_issue_comment",
    "sub_issue_write",
    "create_pull_request",
    "update_pull_request",
    "merge_pull_request",
    "pull_request_review_write",
    "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment",
    "update_pull_request_branch",
    "request_copilot_review",
    "assign_copilot_to_issue",
    "create_or_update_file",
    "delete_file",
    "create_branch",
    "push_files",
    "create_repository",
    "fork_repository",
    "label_write",
    "discussion_comment_write",
    "create_gist",
    "update_gist",
    "actions_run_trigger",
    "star_repository",
    "unstar_repository",
)


class FeaturedMCPServer(BaseModel):
    """One curated first-party MCP server. Immutable config, never a DDB row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    featured_id: str
    name: str
    url: str
    description: str
    # Where the user mints the credential this server needs (e.g. a GitHub
    # PAT). Surfaced by the SPA next to the one-click enable so the paste
    # step has a destination.
    docs_url: str
    auth_type: MCPServerAuthType
    # Canonical Strands tool prefix — deriving it from the host would yield
    # a useless "api" for api.githubcopilot.com, so the catalog pins it.
    tool_prefix: str
    # Default per-user global enablement at registration time. False for a
    # read-write surface (GitHub) so write hands need an explicit per-chat
    # enable. See #277 scope item 3.
    default_globally_enabled: bool
    # Tools that mutate remote state. Authoritative source for the deferred
    # write-surface audit wiring (#277 item 4 / #299). Empty for a
    # read-only server.
    write_surface_tools: tuple[str, ...] = ()


_FEATURED: tuple[FeaturedMCPServer, ...] = (
    FeaturedMCPServer(
        featured_id="github",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        description=(
            "GitHub's official MCP server — read repositories, issues, and pull "
            "requests, and (with an explicit per-chat enable) comment on issues, "
            "draft issue bodies, and suggest pull-request reviews."
        ),
        docs_url="https://github.com/settings/personal-access-tokens",
        auth_type=MCPServerAuthType.STATIC_TOKEN,
        tool_prefix="github",
        default_globally_enabled=False,
        write_surface_tools=_GITHUB_WRITE_SURFACE_TOOLS,
    ),
)


def list_featured_servers() -> tuple[FeaturedMCPServer, ...]:
    """Return the full featured-server catalog (stable order)."""
    return _FEATURED


def get_featured_server(featured_id: str) -> FeaturedMCPServer | None:
    """Look up a featured server by its slug id, or ``None`` if unknown."""
    for server in _FEATURED:
        if server.featured_id == featured_id:
            return server
    return None

# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the first-party / featured MCP server catalog (#277)."""

from __future__ import annotations

from channel.mcp.featured import (
    FeaturedMCPServer,
    get_featured_server,
    list_featured_servers,
)
from channel.mcp.url_guard import validate_mcp_server_url
from channel.models import MCPServerAuthType


def test_catalog_is_non_empty_and_stably_ordered() -> None:
    servers = list_featured_servers()
    assert len(servers) >= 1
    # Same object each call — the catalog is immutable server config.
    assert list_featured_servers() is servers
    assert all(isinstance(s, FeaturedMCPServer) for s in servers)


def test_featured_ids_are_unique() -> None:
    ids = [s.featured_id for s in list_featured_servers()]
    assert len(ids) == len(set(ids))


def test_get_featured_server_returns_entry() -> None:
    entry = get_featured_server("github")
    assert entry is not None
    assert entry.featured_id == "github"


def test_get_featured_server_unknown_returns_none() -> None:
    assert get_featured_server("does-not-exist") is None


def test_github_entry_is_a_default_off_static_token_write_surface() -> None:
    gh = get_featured_server("github")
    assert gh is not None
    # GitHub MCP does not support DCR — it must ride the static-token path.
    assert gh.auth_type == MCPServerAuthType.STATIC_TOKEN
    # Read-write hands: off until explicitly enabled per chat (#277 item 3).
    assert gh.default_globally_enabled is False
    # Deriving a prefix from the host would yield a useless "api"; pin it.
    assert gh.tool_prefix == "github"
    assert gh.url == "https://api.githubcopilot.com/mcp/"
    assert gh.docs_url.startswith("https://")
    # The write surface is named (authoritative source for the deferred
    # audit wiring) and includes the operations the issue calls out.
    assert gh.write_surface_tools
    assert "add_issue_comment" in gh.write_surface_tools
    assert "merge_pull_request" in gh.write_surface_tools


def test_github_url_passes_the_ssrf_guard() -> None:
    # The canonical featured URL must survive the same validator every
    # registration path runs — otherwise a one-click enable 400s.
    gh = get_featured_server("github")
    assert gh is not None
    validate_mcp_server_url(gh.url)


def test_featured_entry_is_immutable() -> None:
    gh = get_featured_server("github")
    assert gh is not None
    try:
        gh.name = "mutated"  # type: ignore[misc]
    except Exception as exc:  # pydantic raises on frozen-model mutation
        assert "frozen" in str(exc).lower() or "immutable" in str(exc).lower()
    else:  # pragma: no cover - frozen model must reject mutation
        raise AssertionError("FeaturedMCPServer must be immutable")

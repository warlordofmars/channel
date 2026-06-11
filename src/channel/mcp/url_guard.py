# Copyright (c) 2026 John Carter. All rights reserved.
"""SSRF guard for user-supplied MCP-server URLs.

Moved out of ``src/channel/api/mcp.py`` so the chat path can re-validate
the persisted URL at turn time without creating a circular import
(``api.mcp`` imports ``_load_owned_chat`` from ``api.chats``).

Re-validation at turn time closes a DNS-rebinding window: a URL that
resolved to a public IP at registration could later resolve to a
loopback / link-local / private address before the chat path opens
discovery / transport. The validator's DNS lookup runs fresh on every
call, so the rebinding is caught at the moment of egress.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from fastapi import HTTPException


def _is_dangerous_address(addr: str) -> bool:
    """Return True for any address that should never be the target of an
    MCP-server URL — loopback, link-local (incl. cloud metadata at
    169.254.169.254), private RFC1918, multicast, reserved, or unspecified.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def validate_mcp_server_url(url: str) -> None:
    """SSRF defense for the MCP server URL.

    Reject any URL whose scheme isn't ``https`` (except the explicit
    dev-only ``http://localhost[:port]/...`` form, gated by
    ``STARTER_MCP_ALLOW_LOCALHOST=1``), whose userinfo is set, or whose
    hostname resolves to a loopback / link-local / private / multicast
    / reserved address. Raise :class:`HTTPException` 400 on failure.

    Called both at registration / reauth / OAuth callback (initial
    validation) AND at turn time from
    :func:`channel.api.chats._build_mcp_clients_for_chat` (DNS-rebinding
    defense).
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed URL") from exc
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL must not include credentials")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL must include a hostname")

    allow_localhost = os.environ.get("STARTER_MCP_ALLOW_LOCALHOST") == "1"
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and allow_localhost and parsed.hostname == "localhost":
        # Dev-only carve-out — only when the env flag is explicitly set
        # AND the hostname is the literal "localhost" (not "localhost.evil.com").
        return
    else:
        raise HTTPException(status_code=400, detail="MCP server URL must use https")

    # If the hostname IS an IP literal, validate it directly.
    if _is_dangerous_address(parsed.hostname):
        raise HTTPException(status_code=400, detail="URL targets a blocked address range")
    # Otherwise resolve via DNS and reject if ANY resolved address is dangerous.
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="hostname did not resolve") from exc
    for info in infos:
        # getaddrinfo's sockaddr is (host, port[, flow, scope]); the host
        # is always a str for the IPv4/IPv6 address families. mypy types
        # it loosely as ``str | int`` because of the Unix-socket case.
        addr = str(info[4][0])
        if _is_dangerous_address(addr):
            raise HTTPException(status_code=400, detail="URL targets a blocked address range")

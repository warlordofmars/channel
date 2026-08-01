# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared unit-test fixtures.

Unit tests must not touch DynamoDB. ``decode_mgmt_jwt`` performs an
``is_jti_denied`` point-read (#240) on every mgmt token it validates,
which would otherwise reach the real DynamoDB client in suites that drive
the real dependency with real tokens (the auth suites). Following the
repo's "don't mock auth; mock the AWS boundary" rule, this autouse
fixture stubs that single storage seam to "not denied" by default. Tests
that exercise the revoked path override it locally with their own
``monkeypatch``.

The patch target moved from ``channel.api._auth`` to
``channel.auth.tokens`` in #291, when revocation moved down into the
decode path so every mgmt-JWT consumer inherits it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_denied_jtis(monkeypatch: pytest.MonkeyPatch) -> None:
    # No ``raising=False``: ``tokens`` imports ``is_jti_denied``
    # unconditionally, so if that import is ever removed this stub fails
    # loudly instead of silently letting the real DDB read back in.
    monkeypatch.setattr("channel.auth.tokens.is_jti_denied", lambda _jti: False)

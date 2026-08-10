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

Unit tests must not read the email allowlists from SSM either — see
``_no_ssm_allowlist_reads``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_denied_jtis(monkeypatch: pytest.MonkeyPatch) -> None:
    # No ``raising=False``: ``tokens`` imports ``is_jti_denied``
    # unconditionally, so if that import is ever removed this stub fails
    # loudly instead of silently letting the real DDB read back in.
    monkeypatch.setattr("channel.auth.tokens.is_jti_denied", lambda _jti: False)


@pytest.fixture(autouse=True)
def _no_ssm_allowlist_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the **allowlist** SSM boundary — ``channel.auth.google._ssm_param``.

    Scope is exactly that one seam, not SSM generally: ``startup.py``,
    ``auth/tokens.py`` and ``agents/tools/web_search.py`` each read SSM
    through their own helper and are deliberately untouched here. Widen
    this fixture rather than assuming it already covers them.

    Both email allowlists fall back to an SSM read when their env var is
    unset (``ALLOWED_EMAILS`` for sign-in, ``ADMIN_ALLOWED_EMAILS`` for
    the admin role — #600). Those reads fail closed, but only after
    boto3 times out against fake local credentials: slow, and noise in a
    suite whose contract is "no AWS deps".

    Raising here reproduces exactly what a missing/unreadable parameter
    does in production — the caller logs and yields an empty allowlist —
    so a test that hasn't set its env var still exercises the real
    default-deny branch rather than a special test-only shortcut. It can
    therefore only make a test observe *fewer* admins, never more: it
    cannot mask a fail-open. Tests that drive the SSM transport on
    purpose override this with their own ``monkeypatch.setattr``.
    """

    def _refuse(name: str) -> str:
        raise AssertionError(f"unit tests must not read SSM (parameter {name!r})")

    monkeypatch.setattr("channel.auth.google._ssm_param", _refuse)

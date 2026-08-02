# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared integration-test helpers (deduplicated per SonarCloud on PR #340).

``FakeS3`` + the asset factory previously lived as identical copies in
``test_attachments_cascade``, ``test_asset_storage``, and
``test_assets_api`` — one canonical copy here keeps the duplication
density down and the stubs in lock-step. The companion ``fake_s3``
fixture lives in ``conftest.py`` (fixtures must be conftest-hosted for
auto-discovery); this module mirrors the ``tests/e2e/_http_helpers``
pattern for plain helpers.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from channel.models import Asset

# ── Per-run integration table naming (#466) ──────────────────────────────────
#
# Lives here rather than in ``conftest.py`` so the predicate guarding the
# suite's only destructive operation can be unit-tested directly, without
# importing a conftest (and its module-level environment mutation) into
# the unit suite. ``tests/integration/conftest.py`` is outside
# ``--cov=src/channel``, so the 100% gate would otherwise never see this
# logic.
RUN_TABLE_PREFIX = "channel-integration"

# Matches ONLY the names this suite generates. The DynamoDB Local
# container is long-lived and shared — in practice with other projects
# entirely (``hive-*`` tables) as well as legacy Channel tables
# (``channel-test``, ``starter-integration``) and, before this change,
# a bare ``channel-integration``. None of those may ever be swept, so
# the 8-hex suffix is required, not optional.
_RUN_TABLE_RE = re.compile(rf"^{re.escape(RUN_TABLE_PREFIX)}-[0-9a-f]{{8}}$")

# A suite run takes ~20s. The cutoff is deliberately ~4000x that, not a
# tight fit: the age is computed by comparing the *host's* clock against
# a ``CreationDateTime`` reported by the *container's* clock, and Docker
# VM clocks are known to drift behind the host after a sleep/resume. Any
# such skew inflates the apparent age, and an over-estimate past the
# cutoff would sweep a live peer's table — reintroducing #466 through
# the very mechanism meant to prevent it. A day of headroom makes that
# implausible while still bounding table accumulation.
ABANDONED_TABLE_MAX_AGE = timedelta(hours=24)


def is_abandoned_run_table(name: str, created: datetime, now: datetime) -> bool:
    """Is ``name`` a per-run table left behind by a dead session?

    True only when the name matches the generated
    ``channel-integration-<8 hex>`` shape *and* the table predates
    ``now - ABANDONED_TABLE_MAX_AGE``. Both conditions are required:
    the shape check keeps foreign and legacy tables out of reach
    entirely, and the age check keeps a concurrently running peer's
    table out of reach.

    A naive ``created`` is assumed UTC — some DynamoDB Local builds omit
    the offset, and a naive/aware comparison would raise inside the
    sweep's suppression and silently disable cleanup.
    """
    if not _RUN_TABLE_RE.match(name):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created < now - ABANDONED_TABLE_MAX_AGE


class FakeS3:
    """Per-test S3 stub: delete_object is a no-op recorder.

    ``**kwargs`` (rather than boto3's ``Bucket=`` / ``Key=`` keyword
    parameters) keeps the stub signature lint-clean while accepting the
    exact call shape ``storage.delete_asset`` / the attachment cascade
    use.
    """

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.deleted.append((kwargs["Bucket"], kwargs["Key"]))
        return {}


def asset_ts(i: int) -> str:
    """Deterministic per-index timestamp for sortable asset fixtures."""
    return f"2026-07-13T00:00:{i:02d}.000000+00:00"


def make_asset(
    asset_id: str,
    *,
    chat_id: str,
    owner: str,
    created_at: str,
    inline: bool = True,
) -> Asset:
    """Canonical test asset: inline text (``kind=code``) or S3-backed
    binary (``kind=image``), matching the #324 storage-suite fixtures."""
    payload: dict[str, Any] = (
        {"content": f"content of {asset_id}"}
        if inline
        else {
            "s3_bucket": "channel-attachments-test",
            "s3_key": f"assets/chat/{chat_id}/{asset_id}",
        }
    )
    return Asset(
        asset_id=asset_id,
        chat_id=chat_id,
        owner=owner,
        kind="code" if inline else "image",
        title=f"{asset_id}.txt",
        mime="text/plain" if inline else "image/png",
        size_bytes=64,
        origin="generated" if inline else "tool_output",
        source={"msg_id": f"m-{asset_id}"},
        created_at=created_at,
        updated_at=created_at,
        **payload,
    )

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

from typing import Any

from channel.models import Asset


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

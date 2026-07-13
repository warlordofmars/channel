# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the #174 chat-delete attachment cascade.

DynamoDB Local validates the real ATTACHMENT row keys + the message
``attachments`` snapshot parsing. S3 stays mocked — that side is
covered by the unit tests in :mod:`tests.unit.test_storage`.
"""

from __future__ import annotations

import pytest

from channel import storage
from channel.models import Attachment, MessageRole
from tests.integration._helpers import FakeS3 as _FakeS3


def _att(att_id: str, *, user_id: str = "u-cascade") -> Attachment:
    return Attachment(
        id=att_id,
        user_id=user_id,
        name=f"{att_id}.pdf",
        mime="application/pdf",
        size_bytes=1024,
        s3_key=f"attachments/user/{user_id}/{att_id}",
        s3_bucket="channel-attachments-test",
        checksum_sha256="sha-" + att_id,
        created_at="2026-06-03T00:00:00Z",
    )


@pytest.mark.usefixtures("starter_table")
def test_delete_chat_attachments_removes_referenced_rows(fake_s3: _FakeS3) -> None:
    """End-to-end DDB Local — message rows with attachment snapshots
    drive the cascade; canonical ATTACHMENT rows go away; orphaned
    rows (not referenced) stay."""

    user_id = "u-cascade-1"
    referenced = _att("ref-1", user_id=user_id)
    orphan = _att("orphan-1", user_id=user_id)
    storage.put_attachment(referenced)
    storage.put_attachment(orphan)

    chat = storage.create_chat(user_id=user_id, title=None, model_default="m")
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="here's a file",
        model=None,
        attachments=[
            {
                "id": "ref-1",
                "name": "ref-1.pdf",
                "mime": "application/pdf",
                "size_bytes": 1024,
            }
        ],
    )

    deleted, failed = storage.delete_chat_attachments(chat_id=chat.chat_id, user_id=user_id)

    assert (deleted, failed) == (1, 0)
    assert storage.get_attachment(user_id=user_id, att_id="ref-1") is None
    # Orphan untouched — not referenced by any chat message.
    assert storage.get_attachment(user_id=user_id, att_id="orphan-1") is not None
    assert fake_s3.deleted == [
        ("channel-attachments-test", f"attachments/user/{user_id}/ref-1"),
    ]


@pytest.mark.usefixtures("starter_table")
def test_delete_chat_attachments_dedups_across_messages(fake_s3: _FakeS3) -> None:
    """An attachment referenced by two messages in the same chat is
    deleted exactly once."""

    user_id = "u-cascade-2"
    storage.put_attachment(_att("dup-1", user_id=user_id))

    chat = storage.create_chat(user_id=user_id, title=None, model_default="m")
    snap = [
        {
            "id": "dup-1",
            "name": "dup-1.pdf",
            "mime": "application/pdf",
            "size_bytes": 1024,
        }
    ]
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="msg-1",
        model=None,
        attachments=snap,
    )
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="msg-2",
        model=None,
        attachments=snap,
    )

    deleted, failed = storage.delete_chat_attachments(chat_id=chat.chat_id, user_id=user_id)

    assert (deleted, failed) == (1, 0)
    assert storage.get_attachment(user_id=user_id, att_id="dup-1") is None
    assert len(fake_s3.deleted) == 1

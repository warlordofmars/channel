# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test: chat lookup via GSI returns the actual user_id."""

from __future__ import annotations

import pytest

from channel import storage


@pytest.mark.usefixtures("starter_table")
def test_get_chat_by_id_returns_owners_chat() -> None:
    a = storage.create_chat(user_id="u-A", title=None, model_default="m")
    b = storage.create_chat(user_id="u-B", title=None, model_default="m")

    fetched_a = storage.get_chat_by_id(a.chat_id)
    fetched_b = storage.get_chat_by_id(b.chat_id)

    assert fetched_a is not None and fetched_a.user_id == "u-A"
    assert fetched_b is not None and fetched_b.user_id == "u-B"

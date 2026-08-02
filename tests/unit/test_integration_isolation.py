# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the integration suite's per-run table isolation (#466).

The sweep predicate guards the only destructive operation the
integration suite performs against a DynamoDB Local container that is
long-lived and shared — in practice with other projects entirely. A
false positive there deletes a table out from under a running peer,
which is the exact failure #466 exists to remove.

``tests/integration/conftest.py`` is outside ``--cov=src/channel``, so
the coverage gate never sees that logic. These tests are what make the
"cannot sweep anything it shouldn't" guarantee mechanical rather than a
claim in a docstring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.integration._helpers import (
    ABANDONED_TABLE_MAX_AGE,
    RUN_TABLE_PREFIX,
    is_abandoned_run_table,
)

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - ABANDONED_TABLE_MAX_AGE - timedelta(hours=1)

# Every table name observed in the real shared container while #466 was
# being fixed. `hive-*` belong to an entirely different project; the
# `channel-test` / `starter-integration` pair are legacy leftovers from
# before this change; the bare `channel-integration` was a sibling
# worker's in-flight run. None may EVER be eligible, at any age.
FOREIGN_TABLE_NAMES = [
    "hive-integration-apikey-idx",
    "hive-integration-apikey-noidx",
    "hive-integration-test",
    "hive-integration-workspace-scoping",
    "hive-mcp-cross-user",
    "hive-mcp-integration",
    "hive-oauth-user-binding",
    "channel-test",
    "starter-integration",
    "channel-integration",
    "channel",
]


@pytest.mark.parametrize("name", FOREIGN_TABLE_NAMES)
def test_foreign_and_legacy_tables_are_never_eligible(name):
    """Age is irrelevant for a non-matching name — the shape gate is first."""
    assert is_abandoned_run_table(name, LONG_AGO, NOW) is False


def test_generated_name_older_than_the_cutoff_is_eligible():
    assert is_abandoned_run_table(f"{RUN_TABLE_PREFIX}-ab12cd34", LONG_AGO, NOW) is True


def test_generated_name_from_a_live_peer_is_not_eligible():
    """A peer's run is seconds old; the cutoff is a day."""
    fresh = NOW - timedelta(seconds=20)
    assert is_abandoned_run_table(f"{RUN_TABLE_PREFIX}-ab12cd34", fresh, NOW) is False


def test_boundary_exactly_at_the_cutoff_is_not_eligible():
    """Strict `<` — ties are resolved in favour of not deleting."""
    boundary = NOW - ABANDONED_TABLE_MAX_AGE
    assert is_abandoned_run_table(f"{RUN_TABLE_PREFIX}-ab12cd34", boundary, NOW) is False


@pytest.mark.parametrize(
    "suffix",
    [
        "ABCDEF12",  # uppercase — uuid4().hex is lowercase
        "ab12cd3",  # 7 chars
        "ab12cd345",  # 9 chars
        "ghijklmn",  # non-hex letters
        "",  # bare prefix with a trailing dash
    ],
)
def test_near_miss_suffixes_are_not_eligible(suffix):
    """Only the exact 8-lowercase-hex shape this suite generates."""
    assert is_abandoned_run_table(f"{RUN_TABLE_PREFIX}-{suffix}", LONG_AGO, NOW) is False


def test_prefix_must_anchor_at_the_start():
    """A foreign table merely *containing* the prefix stays untouchable."""
    assert is_abandoned_run_table("not-channel-integration-ab12cd34", LONG_AGO, NOW) is False


def test_naive_timestamps_are_treated_as_utc():
    """Some DynamoDB Local builds omit the offset.

    A naive/aware comparison raises TypeError, which the sweep's
    suppression would swallow — silently disabling cleanup forever. The
    predicate normalises instead.
    """
    assert (
        is_abandoned_run_table(f"{RUN_TABLE_PREFIX}-ab12cd34", LONG_AGO.replace(tzinfo=None), NOW)
        is True
    )


def test_cutoff_leaves_room_for_container_clock_skew():
    """The age spans host and container clocks, so it needs real headroom.

    A tight cutoff would let Docker VM clock drift inflate a live peer's
    apparent age past the threshold and get its table swept.
    """
    assert timedelta(hours=12) <= ABANDONED_TABLE_MAX_AGE

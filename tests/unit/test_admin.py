# Copyright (c) 2026 John Carter. All rights reserved.
"""Cross-surface contract tests for the admin metrics surface (#551).

``test_admin_api.py`` covers the user list/detail routes and
``test_admin_metrics_api.py`` covers the metrics routes' request/response
behaviour. This module covers the thing neither can: the **three-way name
agreement** between

1. what ``channel.metrics``' ``record_*`` helpers actually emit,
2. what ``channel.api.admin._METRIC_ALLOWLIST`` lets the dashboard request,
3. what ``ui/src/app/admin/Dashboard.jsx`` reads off the response.

Why this needs its own suite: a break in that chain is **invisible at
runtime**. The summary endpoint returns exactly the allowlisted names, and
the SPA renders whatever key it asks for — so a misspelled name on either
side yields a tile reading `0` / `—`, which is pixel-identical to a tile
reading `0` because nothing failed. On the #476/#477 data-rights counters
that distinction is the entire value of the tile: `MemoryBulkForgetFailures`
sitting at a silent zero is indistinguishable from a forget path that is
quietly failing, and a failing forget path means users have been told their
data is gone when it is not.

**These tests are deliberately non-vacuous.** Neither side's name is
retyped here as a literal to be compared against another literal — that
would pass just as happily with the same typo written twice. Instead each
side is *derived from the real artefact*: the emitted names come from
awaiting the actual ``record_*`` helpers with ``emit_metric`` patched, and
the rendered names come from parsing the actual ``Dashboard.jsx`` source.
The only literals below are the helper functions to drive.

Reading a JSX file from a Python test is unusual, and is the point: the
contract spans two languages, so a test that can see both sides has to
read both. It stays mechanical — no JS is executed, only the metric-name
tokens are extracted.

The no-dimensions discipline on these counters is pinned separately by the
``*_signature_locks_out_dimensions`` tests in ``tests/unit/test_metrics.py``
and by ``_metric_data_queries``' ``{Environment}``-only pin; nothing here
adds or reads a dimension.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from channel.api.admin import _METRIC_ALLOWLIST
from channel.metrics import (
    record_memory_bulk_forget_outcome,
    record_memory_export_outcome,
    record_memory_record_delete_outcome,
)

# The three data-rights recorders #551 is about: account export (#476) and
# the two forget shapes (#477). Each emits a success name and a failure name
# depending on its one ``success`` argument — so driving each helper twice
# yields all six names straight from the emitting code.
_DATA_RIGHTS_RECORDERS: tuple[Callable[..., Awaitable[None]], ...] = (
    record_memory_export_outcome,
    record_memory_record_delete_outcome,
    record_memory_bulk_forget_outcome,
)

_DASHBOARD_JSX = Path(__file__).resolve().parents[2] / "ui/src/app/admin/Dashboard.jsx"

# `m.MemoryExportSuccesses` — a counter read off the summary payload in
# `tilesFor`. PascalCase-anchored so the pattern can't drift onto some other
# single-letter binding's property.
_TILE_METRIC_RE = re.compile(r"\bm\.([A-Z]\w*)")
# `{ metric: "ToolCallSuccesses", ... }` — the timeseries charts' names. Only
# the quoted form matches, so the `metric` prop/parameter of the same name is
# not picked up.
_CHART_METRIC_RE = re.compile(r"\bmetric:\s*\"(\w+)\"")


async def _names_emitted_by(recorder: Callable[..., Awaitable[None]]) -> set[str]:
    """Every metric name ``recorder`` can emit, taken from the real helper.

    This is the load-bearing half of the non-vacuity argument: the names are
    read out of the emitting code path rather than restated in this file, so
    a typo in ``metrics.py`` or in the allowlist cannot be masked by a
    matching typo in the test.
    """
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await recorder(success=True)
        await recorder(success=False)
    names = {call.args[0] for call in mock_emit.await_args_list}
    # Guard the guard: a helper that stopped emitting would otherwise make
    # every assertion below trivially true.
    assert len(names) == 2, f"{recorder.__name__} emitted {names}, expected a success/failure pair"
    return names


def _dashboard_metric_names() -> set[str]:
    """Every CloudWatch counter name ``Dashboard.jsx`` asks the API for."""
    source = _DASHBOARD_JSX.read_text(encoding="utf-8")
    names = set(_TILE_METRIC_RE.findall(source)) | set(_CHART_METRIC_RE.findall(source))
    # Guard the guard: if the component is refactored into a shape these
    # patterns don't match, fail loudly here rather than silently asserting
    # things about an empty set.
    assert names, f"extracted no metric names from {_DASHBOARD_JSX} — the parse shape has drifted"
    return names


@pytest.mark.asyncio
@pytest.mark.parametrize("recorder", _DATA_RIGHTS_RECORDERS, ids=lambda r: r.__name__)
async def test_data_rights_counter_names_are_on_the_admin_allowlist(
    recorder: Callable[..., Awaitable[None]],
) -> None:
    """The names #476/#477 emit are exactly the names the API will serve.

    ``/api/admin/metrics/summary`` returns only allowlisted counters, so a
    name that is emitted but absent from the allowlist is unreadable
    anywhere — which is the bug #551 fixes. Comparing the *emitted* string
    against the allowlist (rather than a literal against a literal) is what
    makes a misspelled allowlist entry fail this test.
    """
    for name in await _names_emitted_by(recorder):
        assert name in _METRIC_ALLOWLIST, (
            f"{recorder.__name__} emits {name!r}, which is not on _METRIC_ALLOWLIST — "
            "the admin metrics endpoints cannot serve it and the dashboard tile "
            "will read a silent zero"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("recorder", _DATA_RIGHTS_RECORDERS, ids=lambda r: r.__name__)
async def test_data_rights_counter_names_are_read_by_the_dashboard(
    recorder: Callable[..., Awaitable[None]],
) -> None:
    """Both halves of each pair reach a tile — the failure half especially.

    Allowlisting a counter makes it *fetchable*; only the SPA reading the
    key makes it *visible*. This closes the other half of #551: the six were
    already emitted, and adding them to the allowlist alone would still have
    rendered them nowhere.
    """
    rendered = _dashboard_metric_names()
    for name in await _names_emitted_by(recorder):
        assert name in rendered, (
            f"{recorder.__name__} emits {name!r}, which {_DASHBOARD_JSX.name} never reads — "
            "the counter is fetchable but invisible"
        )


def test_every_metric_the_dashboard_reads_is_allowlisted() -> None:
    """A name the SPA asks for but the API won't serve renders a silent zero.

    This is the direction that catches a typo made on the *dashboard* side:
    ``m.MemoryBulkForgetFailure`` (singular) is absent from the allowlist, so
    the summary response has no such key, so the tile reads `—` forever while
    looking exactly like a healthy one. The timeseries route is stricter — it
    422s on an unknown metric — but that only degrades a chart to its error
    panel, which is likewise indistinguishable from a transient CloudWatch
    failure.
    """
    unknown = sorted(_dashboard_metric_names() - set(_METRIC_ALLOWLIST))
    assert not unknown, (
        f"{_DASHBOARD_JSX.name} reads metric names that are not on _METRIC_ALLOWLIST: "
        f"{unknown} — these render a silent zero / em dash"
    )


def test_allowlist_has_no_duplicate_entries() -> None:
    """Duplicates would silently double the GetMetricData query fan-out.

    ``_window_sums`` builds one query per allowlist position and zips the
    results back by index, so a name repeated while extending the tuple costs
    a redundant CloudWatch query on every dashboard load without any visible
    symptom.
    """
    duplicates = sorted({n for n in _METRIC_ALLOWLIST if _METRIC_ALLOWLIST.count(n) > 1})
    assert not duplicates, f"_METRIC_ALLOWLIST repeats: {duplicates}"

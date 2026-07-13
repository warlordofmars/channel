# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for ``_infer_next_version`` in tasks.py (#318).

Regression context: the ``dev`` pre-release tag is force-recreated on every
push to ``development`` by CI (ADR-0010), so an unconstrained
``git describe --tags --abbrev=0`` returns ``dev`` and the semver parse
crashed with ``ValueError: invalid literal for int() with base 10: 'dev'``.
The fix constrains describe to ``--match "v*"`` and degrades gracefully on
any tag that still isn't ``vX.Y.Z``-form.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_PATH = ROOT / "tasks.py"

# Load tasks.py as a module — it lives at the repo root, not in a package.
# Registered under a non-clashing name so a hypothetical third-party
# ``tasks`` module is never shadowed.
_spec = importlib.util.spec_from_file_location("channel_tasks", TASKS_PATH)
assert _spec is not None and _spec.loader is not None
channel_tasks = importlib.util.module_from_spec(_spec)
sys.modules["channel_tasks"] = channel_tasks
_spec.loader.exec_module(channel_tasks)

_infer_next_version = channel_tasks._infer_next_version


class FakeCtx:
    """Minimal invoke.Context stand-in with canned per-command responses.

    ``describe`` answers the ``git describe`` call and ``log`` answers the
    ``git log`` call; passing an Exception instance makes that call raise
    (mirroring invoke's UnexpectedExit on a non-zero git exit).
    """

    def __init__(self, describe: str | Exception, log: str | Exception = ""):
        self._describe = describe
        self._log = log
        self.commands: list[str] = []

    def run(self, cmd: str, hide: bool = False) -> SimpleNamespace:
        self.commands.append(cmd)
        result = self._describe if cmd.startswith("git describe") else self._log
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(stdout=result)


# ── describe is constrained to release tags ──────────────────────────────────


def test_describe_only_matches_release_tags():
    """The describe call must carry a --match v* pattern so ``dev`` is never returned."""
    ctx = FakeCtx(describe="v0.20.0")
    _infer_next_version(ctx)
    describe_cmd = ctx.commands[0]
    assert "--match" in describe_cmd
    assert "v*" in describe_cmd


# ── vX.Y.Z tags parse exactly as before ──────────────────────────────────────


def test_patch_bump_from_fix_commit():
    ctx = FakeCtx(describe="v1.2.3", log="fix: repair a thing")
    assert _infer_next_version(ctx) == "1.2.4"


def test_minor_bump_from_feat_commit():
    ctx = FakeCtx(describe="v1.2.3", log="chore: tidy\nfeat(ui): add a thing")
    assert _infer_next_version(ctx) == "1.3.0"


def test_major_bump_from_breaking_change():
    ctx = FakeCtx(describe="v1.2.3", log="feat!: break the world")
    assert _infer_next_version(ctx) == "2.0.0"


def test_major_bump_wins_over_later_feat():
    ctx = FakeCtx(describe="v1.2.3", log="chore: x\nBREAKING CHANGE: y\nfeat: z")
    assert _infer_next_version(ctx) == "2.0.0"


def test_empty_log_defaults_to_patch_bump():
    ctx = FakeCtx(describe="v1.2.3", log="")
    assert _infer_next_version(ctx) == "1.2.4"


# ── no-release-tag fallback is unchanged ─────────────────────────────────────


def test_no_tags_at_all_falls_back_to_v000():
    ctx = FakeCtx(describe=Exception("fatal: No tags can describe"), log=Exception("fatal"))
    assert _infer_next_version(ctx) == "0.0.1"


def test_no_tags_with_feat_commits_bumps_minor():
    ctx = FakeCtx(describe=Exception("fatal: No tags can describe"), log="feat: first feature")
    assert _infer_next_version(ctx) == "0.1.0"


# ── regression: non-semver tag no longer crashes ─────────────────────────────


def test_dev_tag_degrades_gracefully_instead_of_valueerror():
    """The #318 crash shape: a non-semver nearest tag must not raise."""
    ctx = FakeCtx(describe="dev", log=Exception("fatal: bad revision"))
    assert _infer_next_version(ctx) == "0.0.1"


def test_v_glob_tag_that_is_not_semver_degrades_gracefully():
    """A tag like ``vNext`` matches the v* glob but is not vX.Y.Z-form."""
    ctx = FakeCtx(describe="vNext", log=Exception("fatal: bad revision"))
    assert _infer_next_version(ctx) == "0.0.1"


def test_prerelease_v_tag_degrades_gracefully():
    """``v1.2.3-rc.1`` matches the glob but not the strict vX.Y.Z parse."""
    ctx = FakeCtx(describe="v1.2.3-rc.1", log="feat: something")
    assert _infer_next_version(ctx) == "0.1.0"


def test_unparseable_tag_resets_log_range_to_v000():
    """After the parse fallback, the git log range must use v0.0.0, not the bad tag."""
    ctx = FakeCtx(describe="dev", log="")
    _infer_next_version(ctx)
    assert ctx.commands[1].startswith("git log v0.0.0..HEAD")

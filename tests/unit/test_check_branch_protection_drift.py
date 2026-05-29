# Copyright (c) 2026 John Carter. All rights reserved.
"""
Unit tests for scripts/check_branch_protection_drift.py.

The script is the periodic CI guardrail against silent drift between
`infra/branch-protection.expected.json` and the live GitHub repo state
(issue #52). Tests cover:

- normalisation strips volatile fields (`url`, `app_id`, etc.)
- the diff comparator detects every interesting drift shape
- the CLI exits 0 on match, 1 on drift, 2 on usage / fetch errors
- live-state collection composes the right `gh` calls (with mocking)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check_branch_protection_drift.py"

# scripts/ isn't a package; load by file path so tests don't depend on
# PYTHONPATH being set (mirrors the pattern in
# tests/unit/test_check_agent_safe_scope.py).
_spec = importlib.util.spec_from_file_location("check_branch_protection_drift", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
drift = importlib.util.module_from_spec(_spec)
sys.modules["check_branch_protection_drift"] = drift
_spec.loader.exec_module(drift)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_protection(
    *,
    contexts: list[str] | None = None,
    enforce_admins: bool = False,
    allow_force_pushes: bool = False,
) -> dict:
    """Build a branch-protection payload shaped like the live GitHub API."""
    if contexts is None:
        contexts = ["Lint & Type Check", "Unit Tests"]
    return {
        "url": "https://api.github.com/repos/foo/bar/branches/main/protection",
        "required_status_checks": {
            "url": "https://api.github.com/repos/foo/bar/branches/main/protection/required_status_checks",
            "strict": True,
            "contexts": contexts,
            "contexts_url": "https://api.github.com/repos/foo/bar/.../contexts",
            "checks": [{"context": ctx, "app_id": 15368} for ctx in contexts],
        },
        "required_signatures": {
            "url": "https://api.github.com/repos/foo/bar/.../required_signatures",
            "enabled": False,
        },
        "enforce_admins": {
            "url": "https://api.github.com/repos/foo/bar/.../enforce_admins",
            "enabled": enforce_admins,
        },
        "required_linear_history": {"enabled": False},
        "allow_force_pushes": {"enabled": allow_force_pushes},
        "allow_deletions": {"enabled": False},
        "block_creations": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
        "lock_branch": {"enabled": False},
        "allow_fork_syncing": {"enabled": False},
    }


def _make_state(
    *,
    repo_settings: dict | None = None,
    branches: dict | None = None,
) -> dict:
    if repo_settings is None:
        repo_settings = {
            "allow_auto_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "allow_squash_merge": True,
            "default_branch": "development",
            "delete_branch_on_merge": True,
        }
    if branches is None:
        branches = {
            "main": _make_protection(),
            "development": _make_protection(),
        }
    return {"repo_settings": repo_settings, "branches": branches}


# ── Normalisation ─────────────────────────────────────────────────────────────


def test_strip_keys_returns_copy_without_listed_keys():
    src = {"a": 1, "b": 2, "c": 3}
    out = drift._strip_keys(src, ("a", "c"))
    assert out == {"b": 2}
    # Original untouched.
    assert src == {"a": 1, "b": 2, "c": 3}


def test_normalize_branch_protection_drops_url_and_app_id():
    p = _make_protection(contexts=["Lint & Type Check", "Unit Tests"])
    normalized = drift._normalize_branch_protection(p)

    assert "url" not in normalized
    assert "url" not in normalized["required_status_checks"]
    assert "contexts_url" not in normalized["required_status_checks"]
    for check in normalized["required_status_checks"]["checks"]:
        assert "app_id" not in check
        assert "context" in check
    assert "url" not in normalized["required_signatures"]
    assert "url" not in normalized["enforce_admins"]
    # The contract fields (`enabled`, `strict`, etc.) survive.
    assert normalized["enforce_admins"]["enabled"] is False
    assert normalized["required_status_checks"]["strict"] is True


def test_normalize_branch_protection_sorts_checks_and_contexts():
    """Same set in different order must normalise identically."""
    p1 = _make_protection(contexts=["A", "B", "C"])
    p2 = _make_protection(contexts=["C", "A", "B"])
    n1 = drift._normalize_branch_protection(p1)
    n2 = drift._normalize_branch_protection(p2)
    assert n1 == n2


def test_normalize_branch_protection_handles_missing_subobjects():
    """Sparse payload (no required_status_checks, no required_signatures) doesn't crash."""
    p = {
        "url": "https://example/foo",
        "allow_force_pushes": {"enabled": False},
    }
    normalized = drift._normalize_branch_protection(p)
    assert "url" not in normalized
    assert normalized["allow_force_pushes"] == {"enabled": False}


def test_normalize_branch_protection_handles_non_dict_required_status_checks():
    """Defensive: if `required_status_checks` is None/string, leave it alone."""
    p = {"url": "x", "required_status_checks": None}
    normalized = drift._normalize_branch_protection(p)
    assert normalized == {"required_status_checks": None}


def test_normalize_branch_protection_handles_non_list_checks():
    """Defensive: if `checks` is not a list, skip the per-check stripping."""
    p = {
        "url": "x",
        "required_status_checks": {
            "url": "y",
            "strict": True,
            "contexts": ["A"],
            "checks": "not-a-list",
        },
    }
    normalized = drift._normalize_branch_protection(p)
    assert normalized["required_status_checks"]["checks"] == "not-a-list"


def test_normalize_branch_protection_handles_non_dict_check_entry():
    """Defensive: a non-dict entry in `checks` is preserved verbatim and sorts to the front."""
    p = {
        "url": "x",
        "required_status_checks": {
            "strict": True,
            "contexts": ["A"],
            "checks": [{"context": "A", "app_id": 1}, "stray-string"],
        },
    }
    normalized = drift._normalize_branch_protection(p)
    checks = normalized["required_status_checks"]["checks"]
    # `app_id` is stripped from the dict entry; non-dict entry untouched.
    assert {"context": "A"} in checks
    assert "stray-string" in checks


def test_normalize_branch_protection_handles_non_list_contexts():
    """Defensive: if `contexts` is not a list, skip the sort."""
    p = {
        "url": "x",
        "required_status_checks": {"strict": True, "contexts": "not-a-list", "checks": []},
    }
    normalized = drift._normalize_branch_protection(p)
    assert normalized["required_status_checks"]["contexts"] == "not-a-list"


def test_normalize_branch_protection_handles_non_dict_signature_subobject():
    """Defensive: a non-dict `required_signatures` is preserved untouched."""
    p = {"url": "x", "required_signatures": "weird"}
    normalized = drift._normalize_branch_protection(p)
    assert normalized["required_signatures"] == "weird"


def test_normalize_repo_settings_keeps_only_pinned_keys():
    settings = {
        "id": 12345,
        "name": "channel",
        "private": False,
        "allow_auto_merge": True,
        "allow_merge_commit": True,
        "allow_rebase_merge": False,
        "allow_squash_merge": True,
        "default_branch": "development",
        "delete_branch_on_merge": True,
        "fork": False,
        "license": {"key": "mit"},
    }
    normalized = drift._normalize_repo_settings(settings)
    assert set(normalized.keys()) == set(drift._REPO_SETTING_KEYS)
    assert normalized["allow_auto_merge"] is True


def test_normalize_repo_settings_handles_missing_keys():
    """Repo without an explicit setting (e.g. `default_branch`) just omits it."""
    normalized = drift._normalize_repo_settings({"allow_auto_merge": True})
    assert normalized == {"allow_auto_merge": True}


def test_normalize_state_full_roundtrip():
    state = _make_state()
    normalized = drift.normalize_state(state)
    assert "url" not in normalized["branches"]["main"]
    assert "id" not in normalized["repo_settings"]
    # Snapshot's own normalisation should be a fixed point — normalising a
    # second time changes nothing.
    assert drift.normalize_state(normalized) == normalized


def test_normalize_state_handles_non_dict_repo_settings_and_branch_payload():
    """Defensive: malformed top-level state still returns a usable structure."""
    state = {"repo_settings": "weird", "branches": {"main": "also-weird"}}
    normalized = drift.normalize_state(state)
    assert normalized["repo_settings"] == {}
    assert normalized["branches"]["main"] == "also-weird"


def test_normalize_state_defaults_when_keys_missing():
    """Empty state shouldn't crash."""
    normalized = drift.normalize_state({})
    assert normalized == {"repo_settings": {}, "branches": {}}


def test_normalize_state_handles_non_dict_branches_value():
    """Defensive (Copilot finding): `branches: null` or `branches: []`
    must not crash `.items()` — treat as empty.
    """
    assert drift.normalize_state({"branches": None})["branches"] == {}
    assert drift.normalize_state({"branches": []})["branches"] == {}
    assert drift.normalize_state({"branches": "weird"})["branches"] == {}


def test_normalize_state_handles_non_dict_input():
    """Defensive (Copilot finding): a top-level non-dict (list/string/null)
    must not crash `.get(...)` — return the canonical empty shape so
    downstream comparisons see an empty normalized state with missing
    repo settings / branches rather than an exception.
    """
    expected = {"repo_settings": {}, "branches": {}}
    assert drift.normalize_state([]) == expected  # type: ignore[arg-type]
    assert drift.normalize_state("not-a-dict") == expected  # type: ignore[arg-type]
    assert drift.normalize_state(None) == expected  # type: ignore[arg-type]


# ── Diff ──────────────────────────────────────────────────────────────────────


def test_diff_identical_returns_empty():
    a = _make_state()
    b = _make_state()
    assert drift._diff(drift.normalize_state(a), drift.normalize_state(b)) == []


def test_diff_detects_repo_setting_change():
    expected = drift.normalize_state(_make_state())
    live = drift.normalize_state(
        _make_state(
            repo_settings={
                "allow_auto_merge": False,  # drift
                "allow_merge_commit": True,
                "allow_rebase_merge": False,
                "allow_squash_merge": True,
                "default_branch": "development",
                "delete_branch_on_merge": True,
            }
        )
    )
    diffs = drift._diff(expected, live)
    assert any("allow_auto_merge" in d and "False" in d for d in diffs)


def test_diff_detects_added_required_check():
    expected = drift.normalize_state(
        _make_state(
            branches={
                "main": _make_protection(contexts=["A", "B"]),
                "development": _make_protection(contexts=["A", "B"]),
            }
        )
    )
    live = drift.normalize_state(
        _make_state(
            branches={
                "main": _make_protection(contexts=["A", "B", "C"]),
                "development": _make_protection(contexts=["A", "B"]),
            }
        )
    )
    diffs = drift._diff(expected, live)
    # Length difference on contexts AND on checks list, plus per-index drift.
    assert any("branches.main.required_status_checks.contexts" in d for d in diffs)


def test_diff_detects_missing_branch():
    expected = drift.normalize_state(
        _make_state(branches={"main": _make_protection(), "development": _make_protection()})
    )
    live = drift.normalize_state(_make_state(branches={"main": _make_protection()}))
    diffs = drift._diff(expected, live)
    assert any("development" in d and "missing in live state" in d for d in diffs)


def test_diff_detects_extra_branch():
    expected = drift.normalize_state(_make_state(branches={"main": _make_protection()}))
    live = drift.normalize_state(
        _make_state(branches={"main": _make_protection(), "development": _make_protection()})
    )
    diffs = drift._diff(expected, live)
    assert any(
        "development" in d and "present in live state but not in snapshot" in d for d in diffs
    )


def test_diff_detects_type_mismatch():
    diffs = drift._diff({"a": 1}, {"a": "1"}, path="root")
    assert any("type mismatch" in d for d in diffs)


def test_diff_detects_root_type_mismatch_uses_root_label():
    """Type mismatch at the very top emits `<root>` instead of an empty path."""
    diffs = drift._diff({"a": 1}, "not-a-dict")
    assert any("<root>" in d and "type mismatch" in d for d in diffs)


def test_diff_root_missing_key_omits_leading_dot():
    """A missing key at the root level emits `branches: ...`, not `.branches: ...`."""
    diffs = drift._diff({"branches": {}}, {})
    assert any(d.startswith("branches: missing in live state") for d in diffs)
    assert not any(d.startswith(".branches") for d in diffs)


def test_diff_root_extra_key_omits_leading_dot():
    """An extra key at the root level emits `branches: ...`, not `.branches: ...`."""
    diffs = drift._diff({}, {"branches": {}})
    assert any(d.startswith("branches: present in live state but not in snapshot") for d in diffs)
    assert not any(d.startswith(".branches") for d in diffs)


def test_diff_detects_list_length_change():
    diffs = drift._diff([1, 2, 3], [1, 2], path="items")
    assert any("list length differs" in d for d in diffs)


def test_diff_detects_list_element_change():
    diffs = drift._diff([1, 2, 3], [1, 9, 3], path="items")
    assert any("items[1]" in d for d in diffs)


def test_diff_detects_enforce_admins_flip():
    expected = drift.normalize_state(
        _make_state(branches={"main": _make_protection(enforce_admins=False)})
    )
    live = drift.normalize_state(
        _make_state(branches={"main": _make_protection(enforce_admins=True)})
    )
    diffs = drift._diff(expected, live)
    assert any("enforce_admins.enabled" in d for d in diffs)


# ── Live-state collection ─────────────────────────────────────────────────────


def test_gh_json_returns_parsed_stdout():
    fake = mock.Mock(returncode=0, stdout='{"x": 1}', stderr="")
    with mock.patch.object(drift.subprocess, "run", return_value=fake) as run:
        out = drift._gh_json(["api", "/foo"])
    assert out == {"x": 1}
    run.assert_called_once_with(["gh", "api", "/foo"], capture_output=True, text=True, check=False)


def test_gh_json_raises_on_nonzero_exit():
    fake = mock.Mock(returncode=1, stdout="", stderr="boom")
    with (
        mock.patch.object(drift.subprocess, "run", return_value=fake),
        pytest.raises(RuntimeError, match="gh api /foo failed"),
    ):
        drift._gh_json(["api", "/foo"])


def test_gh_json_raises_on_non_json_output():
    """Defensive (Copilot finding): non-JSON stdout from `gh api`
    (transient failure, auth issue, rate-limit page) maps to RuntimeError.
    """
    fake = mock.Mock(returncode=0, stdout="<html>oops</html>", stderr="")
    with (
        mock.patch.object(drift.subprocess, "run", return_value=fake),
        pytest.raises(RuntimeError, match="returned non-JSON output"),
    ):
        drift._gh_json(["api", "/foo"])


def test_collect_live_state_composes_calls(monkeypatch):
    calls: list[list[str]] = []

    def fake_gh_json(args: list[str]):
        calls.append(args)
        if args[1] == "/repos/foo/bar":
            return {"allow_auto_merge": True, "default_branch": "development"}
        return {"contexts": ["x"], "url": "u"}

    monkeypatch.setattr(drift, "_gh_json", fake_gh_json)
    state = drift.collect_live_state("foo", "bar", branches=["main", "development"])

    assert state["repo_settings"]["default_branch"] == "development"
    assert "main" in state["branches"]
    assert "development" in state["branches"]
    assert calls[0] == ["api", "/repos/foo/bar"]
    assert ["api", "/repos/foo/bar/branches/main/protection"] in calls
    assert ["api", "/repos/foo/bar/branches/development/protection"] in calls


# ── CLI ───────────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_main_clean_run_returns_zero(tmp_path, capsys):
    snapshot = tmp_path / "snapshot.json"
    live = tmp_path / "live.json"
    _write_json(snapshot, _make_state())
    _write_json(live, _make_state())

    rc = drift.main(["--snapshot", str(snapshot), "--live-file", str(live)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "OK" in captured.out


def test_main_drift_returns_one_with_diff_in_output(tmp_path, capsys):
    snapshot = tmp_path / "snapshot.json"
    live = tmp_path / "live.json"
    _write_json(snapshot, _make_state())
    drifted = _make_state(
        repo_settings={
            "allow_auto_merge": False,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "allow_squash_merge": True,
            "default_branch": "development",
            "delete_branch_on_merge": True,
        }
    )
    _write_json(live, drifted)

    rc = drift.main(["--snapshot", str(snapshot), "--live-file", str(live)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "DRIFT DETECTED" in captured.out
    assert "allow_auto_merge" in captured.out
    # Recovery hint is in the output.
    assert "refresh the snapshot" in captured.out


def test_main_missing_snapshot_returns_two(tmp_path, capsys):
    rc = drift.main(["--snapshot", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "snapshot file not found" in captured.err


def test_main_missing_live_file_returns_two(tmp_path, capsys):
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, _make_state())
    rc = drift.main(["--snapshot", str(snapshot), "--live-file", str(tmp_path / "missing.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "live-file not found" in captured.err


def test_main_malformed_snapshot_returns_two(tmp_path, capsys):
    """Defensive (Copilot finding): JSON parse error on the snapshot file
    surfaces as exit code 2 instead of an unhandled traceback.
    """
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{not-json", encoding="utf-8")
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "failed to parse" in captured.err


def test_main_malformed_live_file_returns_two(tmp_path, capsys):
    """Defensive (Copilot finding): JSON parse error on the live-file
    surfaces as exit code 2.
    """
    snapshot = tmp_path / "snapshot.json"
    live = tmp_path / "live.json"
    _write_json(snapshot, _make_state())
    live.write_text("not-json", encoding="utf-8")
    rc = drift.main(["--snapshot", str(snapshot), "--live-file", str(live)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "failed to parse" in captured.err


def test_load_json_file_raises_on_oserror(tmp_path):
    """Defensive (Copilot finding): an OSError from `path.open` (e.g. the
    path is a directory) surfaces as `RuntimeError` with the path included.
    """
    not_a_file = tmp_path / "is-a-directory"
    not_a_file.mkdir()
    with pytest.raises(RuntimeError, match=r"failed to read JSON file .*is-a-directory"):
        drift._load_json_file(not_a_file)


def test_main_snapshot_without_branches_returns_two(tmp_path, capsys):
    """Defensive (Copilot finding): a snapshot that lacks a usable
    `branches` map cannot drive the live fetch and would silently report
    OK. Refuse with exit code 2 instead.
    """
    snapshot = tmp_path / "snapshot.json"
    # Repo settings only — no branches key at all.
    _write_json(snapshot, {"repo_settings": {"allow_auto_merge": True}})
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no usable `branches` map" in captured.err


def test_main_snapshot_with_empty_branches_returns_two(tmp_path, capsys):
    """A snapshot with `branches: {}` is also unusable — same exit path."""
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, {"repo_settings": {}, "branches": {}})
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no usable `branches` map" in captured.err


def test_main_snapshot_with_non_dict_branches_returns_two(tmp_path, capsys):
    """A snapshot whose `branches` is the wrong type is also unusable."""
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, {"repo_settings": {}, "branches": ["main"]})
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no usable `branches` map" in captured.err


def test_main_live_fetch_failure_returns_two(tmp_path, capsys, monkeypatch):
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, _make_state())

    def boom(*_a, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(drift, "collect_live_state", boom)
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "failed to fetch live state" in captured.err
    assert "network down" in captured.err


def test_main_live_fetch_success_returns_zero(tmp_path, capsys, monkeypatch):
    """End-to-end: when `gh` is mocked to return matching data, exit clean."""
    snapshot = tmp_path / "snapshot.json"
    state = _make_state()
    _write_json(snapshot, state)

    monkeypatch.setattr(drift, "collect_live_state", lambda *_a, **_kw: state)
    rc = drift.main(["--snapshot", str(snapshot)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "OK" in captured.out

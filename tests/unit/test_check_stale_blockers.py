# Copyright (c) 2026 John Carter. All rights reserved.
"""
Unit tests for scripts/check_stale_blockers.py.

The script is the backlog sweep that catches `Blocked by #N` references which
outlived their blocker (issue #457). Tests cover:

- the `Blocked by` regex across every shape the backlog actually uses
  (single, `and`-joined, comma-joined, lowercase, `&`), and the code-span
  exclusion that stops an issue documenting the syntax from self-reporting
- all four finding kinds: stale-block, missing-block, not-planned-blocker,
  dangling-ref
- reference resolution: GraphQL batching, missing issues, pull-request refs,
  and the NOT_FOUND-vs-real-error split
- the `--fix` label flips, including the one-status-label invariant
- the CLI: `--json`, exit 0 clean / 1 findings / 2 fetch error

Every `gh` invocation is mocked — an autouse fixture makes an unmocked
`subprocess.run` fail loudly, so the suite can never reach the network.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check_stale_blockers.py"

# scripts/ isn't a package; load by file path so tests don't depend on
# PYTHONPATH being set (mirrors tests/unit/test_check_branch_protection_drift.py).
_spec = importlib.util.spec_from_file_location("check_stale_blockers", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
sweep = importlib.util.module_from_spec(_spec)
sys.modules["check_stale_blockers"] = sweep
_spec.loader.exec_module(sweep)


# ── Fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches `subprocess.run` without mocking it."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"unmocked subprocess call: {args!r}")

    monkeypatch.setattr(sweep.subprocess, "run", _boom)


@pytest.fixture(autouse=True)
def _no_repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a real GITHUB_REPOSITORY (CI sets one) out of the resolution tests."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)


def _proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def _issue(
    number: int,
    *,
    title: str = "some issue",
    body: str | None = "",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": name} for name in labels or []],
    }


def _states(*refs: sweep.RefState) -> dict[int, sweep.RefState]:
    return {ref.number: ref for ref in refs}


def _open_issue(number: int) -> sweep.RefState:
    return sweep.RefState(number, "Issue", "OPEN", "")


def _closed_issue(number: int, reason: str = "COMPLETED") -> sweep.RefState:
    return sweep.RefState(number, "Issue", "CLOSED", reason)


def _kinds(findings: list[sweep.Finding]) -> list[str]:
    return [f.kind for f in findings]


# ── gh plumbing ───────────────────────────────────────────────────────────────


def test_gh_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    run = mock.Mock(return_value=_proc(stdout="hello"))
    monkeypatch.setattr(sweep.subprocess, "run", run)

    assert sweep._gh("issue", "list") == "hello"
    assert run.call_args.args[0] == ["gh", "issue", "list"]


def test_gh_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sweep.subprocess, "run", mock.Mock(return_value=_proc(returncode=1, stderr="nope\n"))
    )

    with pytest.raises(RuntimeError, match="gh issue list failed: nope"):
        sweep._gh("issue", "list")


def test_gh_tolerate_exit_returns_partial_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh api graphql` exits 1 on a NOT_FOUND alias but still prints usable data."""
    monkeypatch.setattr(
        sweep.subprocess, "run", mock.Mock(return_value=_proc(stdout="{}", returncode=1))
    )

    assert sweep._gh("api", "graphql", tolerate_exit=True) == "{}"


def test_gh_json_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep.subprocess, "run", mock.Mock(return_value=_proc(stdout='{"a": 1}')))

    assert sweep._gh_json("api", "x") == {"a": 1}


def test_gh_json_raises_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sweep.subprocess, "run", mock.Mock(return_value=_proc(stdout="not json at all"))
    )

    with pytest.raises(RuntimeError, match="returned non-JSON output"):
        sweep._gh_json("api", "x")


# ── Repo resolution ───────────────────────────────────────────────────────────


def test_resolve_repo_prefers_explicit_flag() -> None:
    assert sweep.resolve_repo("acme/widgets") == "acme/widgets"


def test_resolve_repo_falls_back_to_actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/from-env")

    assert sweep.resolve_repo(None) == "acme/from-env"


def test_resolve_repo_asks_gh_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sweep.subprocess, "run", mock.Mock(return_value=_proc(stdout="acme/from-gh\n"))
    )

    assert sweep.resolve_repo(None) == "acme/from-gh"


def test_resolve_repo_raises_when_gh_returns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep.subprocess, "run", mock.Mock(return_value=_proc(stdout="  \n")))

    with pytest.raises(RuntimeError, match="could not resolve the repository"):
        sweep.resolve_repo(None)


# ── Parsing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Blocked by #291", [291]),
        ("Blocked by #259 and #260", [259, 260]),
        ("blocked by #240, #290", [240, 290]),
        ("Blocked by: #1 & #2", [1, 2]),
        ("Blocked by #7 + #8", [7, 8]),
        ("BLOCKED BY #12", [12]),
        ("Blocked  by\n#13", [13]),
        ("Some prose.\n\nBlocked by #5.\n\nMore prose.", [5]),
        ("Blocked by #5 and #6\n\nBlocked by #7", [5, 6, 7]),
        # Duplicates collapse, first-seen order wins.
        ("Blocked by #9, #9 and #4", [9, 4]),
        # No phrase at all.
        ("Part of #300", []),
        ("", []),
        (None, []),
        # A bare newline ends the run — the unrelated ref below is not swallowed.
        ("Blocked by #240\n\n#300 is a different thing", [240]),
        # Prose mentioning a blocker without the phrase is not a declaration.
        ("This was blocked until #99 landed", []),
    ],
)
def test_parse_blockers(body: str | None, expected: list[int]) -> None:
    assert sweep.parse_blockers(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "This is not blocked by #99 anymore",
        "No longer blocked by #99",
        "It isn't blocked by #99",
        "This wasn’t blocked by #99",  # smart apostrophe — GitHub bodies have them
        "Never blocked by #99",
    ],
)
def test_parse_blockers_ignores_negated_phrasing(body: str) -> None:
    """ "not blocked by #99" says the dependency is done — reading it as live
    would let --fix relabel an issue that explicitly says it is unblocked."""
    assert sweep.parse_blockers(body) == []


def test_parse_blockers_keeps_a_real_reference_next_to_a_negated_one() -> None:
    body = "No longer blocked by #99.\n\nBlocked by #100."

    assert sweep.parse_blockers(body) == [100]


def test_parse_blockers_ignores_inline_code_spans() -> None:
    """Issue #457's own body quotes the syntax — it must not self-report."""
    body = "Cover the multi-blocker regex (`Blocked by #259 and #260`, comma-separated)."

    assert sweep.parse_blockers(body) == []


def test_parse_blockers_ignores_fenced_code_blocks() -> None:
    body = "Real one first.\n\nBlocked by #12\n\n```python\n# Blocked by #999\n```\n"

    assert sweep.parse_blockers(body) == [12]


def test_strip_code_replaces_spans_with_a_space() -> None:
    assert sweep.strip_code("a `x` b").strip() == "a   b".strip()
    assert "999" not in sweep.strip_code("``Blocked by #999``")


def test_status_labels_filters_to_the_status_namespace() -> None:
    issue = _issue(1, labels=["priority:p2", "status:blocked", "dx", "status:ready"])

    assert sweep.status_labels(issue) == ["status:blocked", "status:ready"]


def test_status_labels_tolerates_a_null_labels_field() -> None:
    assert sweep.status_labels({"number": 1, "labels": None}) == []


# ── RefState ──────────────────────────────────────────────────────────────────


def test_refstate_describes_an_open_issue() -> None:
    ref = _open_issue(10)

    assert ref.exists and ref.is_open and not ref.is_not_planned
    assert ref.describe() == "OPEN/-"


def test_refstate_describes_a_not_planned_closure() -> None:
    ref = _closed_issue(11, "NOT_PLANNED")

    assert not ref.is_open and ref.is_not_planned
    assert ref.describe() == "CLOSED/NOT_PLANNED"


def test_refstate_describes_a_missing_ref() -> None:
    ref = sweep.RefState(12, "Missing")

    assert not ref.exists and not ref.is_open
    assert ref.describe() == "NOTFOUND"


# ── Reference resolution ──────────────────────────────────────────────────────


def _graphql_payload(nodes: dict[str, Any], errors: list[dict[str, Any]] | None = None) -> str:
    payload: dict[str, Any] = {"data": {"repository": nodes}}
    if errors is not None:
        payload["errors"] = errors
    return json.dumps(payload)


def test_fetch_ref_states_resolves_issues_prs_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock(
        return_value=_proc(
            stdout=_graphql_payload(
                {
                    "n1": {"__typename": "Issue", "state": "OPEN", "stateReason": None},
                    "n2": {"__typename": "Issue", "state": "CLOSED", "stateReason": "NOT_PLANNED"},
                    "n3": {"__typename": "PullRequest", "state": "MERGED"},
                    "n4": None,
                },
                errors=[{"type": "NOT_FOUND", "message": "no #4"}],
            ),
            returncode=1,  # gh exits 1 when any alias is NOT_FOUND
        )
    )
    monkeypatch.setattr(sweep.subprocess, "run", run)

    states = sweep.fetch_ref_states("acme/widgets", [3, 1, 2, 4, 1])

    assert states[1].is_open
    assert states[2].is_not_planned
    assert states[3] == sweep.RefState(3, "PullRequest", "MERGED", "")
    assert not states[4].exists
    # One request for the whole batch, deduped and sorted.
    assert run.call_count == 1
    argv = run.call_args.args[0]
    assert argv[:3] == ["gh", "api", "graphql"]
    assert "owner=acme" in argv and "name=widgets" in argv
    assert argv.count("-F") == 2


def test_fetch_ref_states_batches_large_reference_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    numbers = list(range(1, sweep._GRAPHQL_BATCH * 2 + 2))

    def _fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        query = next(a for a in argv if a.startswith("query="))
        nodes = {
            alias: {"__typename": "Issue", "state": "OPEN", "stateReason": None}
            for alias in (f"n{n}" for n in numbers)
            if f"{alias}: issueOrPullRequest" in query
        }
        return _proc(stdout=_graphql_payload(nodes))

    run = mock.Mock(side_effect=_fake_run)
    monkeypatch.setattr(sweep.subprocess, "run", run)

    states = sweep.fetch_ref_states("acme/widgets", numbers)

    assert run.call_count == 3  # 50 + 50 + 1
    assert len(states) == len(numbers)
    assert all(ref.is_open for ref in states.values())


def test_fetch_ref_states_handles_an_empty_reference_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock()
    monkeypatch.setattr(sweep.subprocess, "run", run)

    assert sweep.fetch_ref_states("acme/widgets", []) == {}
    run.assert_not_called()


def test_fetch_ref_states_treats_a_null_repository_as_all_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sweep.subprocess,
        "run",
        mock.Mock(return_value=_proc(stdout=json.dumps({"data": {"repository": None}}))),
    )

    states = sweep.fetch_ref_states("acme/widgets", [7])

    assert not states[7].exists


def test_fetch_ref_states_raises_on_a_real_graphql_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sweep.subprocess,
        "run",
        mock.Mock(
            return_value=_proc(
                stdout=_graphql_payload(
                    {}, errors=[{"type": "RATE_LIMITED", "message": "slow down"}]
                ),
                returncode=1,
            )
        ),
    )

    with pytest.raises(RuntimeError, match="GraphQL query failed: slow down"):
        sweep.fetch_ref_states("acme/widgets", [1])


def test_check_graphql_errors_stringifies_a_typeless_error() -> None:
    with pytest.raises(RuntimeError, match="weird"):
        sweep._check_graphql_errors({"errors": [{"detail": "weird"}]})


def test_ref_query_aliases_every_number() -> None:
    query = sweep._ref_query([4, 9])

    assert "n4: issueOrPullRequest(number: 4)" in query
    assert "n9: issueOrPullRequest(number: 9)" in query
    assert "... on PullRequest { state }" in query


def test_fetch_open_issues_requests_the_fields_we_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock(return_value=_proc(stdout=json.dumps([_issue(1)])))
    monkeypatch.setattr(sweep.subprocess, "run", run)

    issues = sweep.fetch_open_issues("acme/widgets", limit=5)

    assert [i["number"] for i in issues] == [1]
    argv = run.call_args.args[0]
    assert argv[:2] == ["gh", "issue"]
    assert "number,title,body,labels" in argv
    assert "--state" in argv and "open" in argv


def test_fetch_open_issues_warns_when_truncated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sweep.subprocess,
        "run",
        mock.Mock(return_value=_proc(stdout=json.dumps([_issue(1), _issue(2)]))),
    )

    sweep.fetch_open_issues("acme/widgets", limit=2)

    assert "may be truncated" in capsys.readouterr().err


# ── analyse: the four finding kinds ───────────────────────────────────────────


def test_stale_block_when_every_blocker_is_resolved() -> None:
    issues = [_issue(100, body="Blocked by #10 and #11", labels=["status:blocked"])]
    states = _states(_closed_issue(10), sweep.RefState(11, "PullRequest", "MERGED"))

    findings = sweep.analyse(issues, states)

    assert _kinds(findings) == [sweep.STALE_BLOCK]
    assert "#10, #11" in findings[0].detail
    assert findings[0].statuses == ["status:blocked"]
    assert findings[0].blockers == {10: "CLOSED/COMPLETED", 11: "MERGED/-"}


def test_no_stale_block_while_one_blocker_is_still_open() -> None:
    issues = [_issue(100, body="Blocked by #10, #11", labels=["status:blocked"])]
    states = _states(_closed_issue(10), _open_issue(11))

    assert sweep.analyse(issues, states) == []


def test_missing_block_when_an_open_blocker_is_named_without_the_label() -> None:
    issues = [_issue(101, body="Blocked by #12", labels=["status:ready"])]

    findings = sweep.analyse(issues, _states(_open_issue(12)))

    assert _kinds(findings) == [sweep.MISSING_BLOCK]
    assert "#12" in findings[0].detail


def test_not_planned_blocker_is_flagged_separately() -> None:
    issues = [_issue(102, body="Blocked by #13", labels=["status:blocked"])]

    findings = sweep.analyse(issues, _states(_closed_issue(13, "NOT_PLANNED")))

    # Both fire: the block is stale *and* the dependency probably moved.
    assert _kinds(findings) == [sweep.STALE_BLOCK, sweep.NOT_PLANNED_BLOCKER]
    assert "#13" in findings[1].detail


def test_dangling_ref_when_the_blocker_does_not_exist() -> None:
    issues = [_issue(103, body="Blocked by #99999", labels=["status:blocked"])]

    findings = sweep.analyse(issues, _states(sweep.RefState(99999, "Missing")))

    # Only dangling — a nonexistent ref is no evidence the work is dispatchable.
    assert _kinds(findings) == [sweep.DANGLING_REF]
    assert "#99999" in findings[0].detail


def test_blocked_label_with_no_references_is_not_a_stale_block() -> None:
    """`status:blocked` with an unparseable body is a taxonomy problem, not a stale ref."""
    issues = [_issue(104, body="waiting on the platform team", labels=["status:blocked"])]

    assert sweep.analyse(issues, {}) == []


def test_issues_without_references_or_the_label_are_skipped() -> None:
    issues = [_issue(105, body="Part of #300", labels=["status:ready"])]

    assert sweep.analyse(issues, {}) == []


def test_unresolved_reference_numbers_are_ignored() -> None:
    """A ref absent from the state map (a batch that never resolved) is skipped."""
    issues = [_issue(106, body="Blocked by #14", labels=["status:blocked"])]

    assert sweep.analyse(issues, {}) == []


def test_analyse_reports_every_matching_issue() -> None:
    issues = [
        _issue(200, body="Blocked by #10", labels=["status:blocked"]),
        _issue(201, body="Blocked by #11", labels=["status:ready"]),
    ]
    states = _states(_closed_issue(10), _open_issue(11))

    findings = sweep.analyse(issues, states)

    assert [(f.number, f.kind) for f in findings] == [
        (200, sweep.STALE_BLOCK),
        (201, sweep.MISSING_BLOCK),
    ]


# ── scan ──────────────────────────────────────────────────────────────────────


def test_scan_fetches_issues_then_resolves_their_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issues = [_issue(300, body="Blocked by #20", labels=["status:blocked"])]

    def _fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        if argv[1] == "issue":
            return _proc(stdout=json.dumps(issues))
        return _proc(
            stdout=_graphql_payload(
                {"n20": {"__typename": "Issue", "state": "CLOSED", "stateReason": "COMPLETED"}}
            )
        )

    run = mock.Mock(side_effect=_fake_run)
    monkeypatch.setattr(sweep.subprocess, "run", run)

    findings = sweep.scan("acme/widgets")

    assert _kinds(findings) == [sweep.STALE_BLOCK]
    # Two round-trips total, regardless of how many references were named.
    assert run.call_count == 2


# ── Fixes ─────────────────────────────────────────────────────────────────────


def _finding(kind: str, number: int = 1, statuses: list[str] | None = None) -> sweep.Finding:
    return sweep.Finding(number, "t", kind, "d", {}, statuses if statuses is not None else [])


def test_apply_fixes_swaps_blocked_for_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = mock.Mock(return_value=_proc())
    monkeypatch.setattr(sweep.subprocess, "run", run)

    changed = sweep.apply_fixes(
        "acme/widgets", [_finding(sweep.STALE_BLOCK, 5, ["status:blocked"])]
    )

    assert changed == 1
    argv = run.call_args.args[0]
    assert argv == [
        "gh", "issue", "edit", "5", "--repo", "acme/widgets",
        "--remove-label", "status:blocked", "--add-label", "status:ready",
    ]  # fmt: skip
    assert "fixed #5: status:blocked -> status:ready" in capsys.readouterr().out


def test_apply_fixes_keeps_an_existing_status_instead_of_adding_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The taxonomy allows exactly one status label — don't invent a second."""
    run = mock.Mock(return_value=_proc())
    monkeypatch.setattr(sweep.subprocess, "run", run)

    sweep.apply_fixes(
        "acme/widgets",
        [_finding(sweep.STALE_BLOCK, 6, ["status:blocked", "status:needs-info"])],
    )

    argv = run.call_args.args[0]
    assert "--add-label" not in argv
    assert argv[-2:] == ["--remove-label", "status:blocked"]
    assert "kept status:needs-info" in capsys.readouterr().out


def test_apply_fixes_adds_blocked_and_displaces_the_old_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = mock.Mock(return_value=_proc())
    monkeypatch.setattr(sweep.subprocess, "run", run)

    sweep.apply_fixes("acme/widgets", [_finding(sweep.MISSING_BLOCK, 7, ["status:ready"])])

    argv = run.call_args.args[0]
    assert argv[-4:] == [
        "--add-label", "status:blocked", "--remove-label", "status:ready",
    ]  # fmt: skip
    assert "added status:blocked, removed status:ready" in capsys.readouterr().out


def test_apply_fixes_adds_blocked_when_no_status_label_exists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = mock.Mock(return_value=_proc())
    monkeypatch.setattr(sweep.subprocess, "run", run)

    sweep.apply_fixes("acme/widgets", [_finding(sweep.MISSING_BLOCK, 8)])

    assert "--remove-label" not in run.call_args.args[0]
    assert "added status:blocked" in capsys.readouterr().out


def test_apply_fixes_skips_the_judgement_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """not-planned-blocker and dangling-ref need a human to decide where to re-point."""
    run = mock.Mock(return_value=_proc())
    monkeypatch.setattr(sweep.subprocess, "run", run)

    changed = sweep.apply_fixes(
        "acme/widgets",
        [_finding(sweep.NOT_PLANNED_BLOCKER, 9), _finding(sweep.DANGLING_REF, 10)],
    )

    assert changed == 0
    run.assert_not_called()


# ── Reporting ─────────────────────────────────────────────────────────────────


def test_format_report_says_so_when_clean() -> None:
    assert "No stale blockers" in sweep.format_report([])


def test_format_report_groups_by_kind_in_severity_order() -> None:
    findings = [
        sweep.Finding(2, "second", sweep.MISSING_BLOCK, "detail two", {11: "OPEN/-"}),
        sweep.Finding(1, "first", sweep.STALE_BLOCK, "detail one", {10: "CLOSED/COMPLETED"}),
    ]

    report = sweep.format_report(findings)

    assert report.index("STALE BLOCK") < report.index("MISSING BLOCK")
    assert "  #1 first" in report
    assert "      detail one" in report
    assert "blockers: #10=CLOSED/COMPLETED" in report
    assert "NOT-PLANNED" not in report  # empty groups are omitted


def test_format_report_omits_the_blockers_line_when_there_are_none() -> None:
    report = sweep.format_report([sweep.Finding(1, "t", sweep.STALE_BLOCK, "d", {})])

    assert "blockers:" not in report


def test_format_report_truncates_long_titles() -> None:
    report = sweep.format_report([sweep.Finding(1, "x" * 100, sweep.STALE_BLOCK, "d")])

    assert "x" * 60 in report
    assert "x" * 61 not in report


def test_finding_as_dict_is_json_serializable() -> None:
    finding = sweep.Finding(1, "t", sweep.STALE_BLOCK, "d", {2: "CLOSED/-"}, ["status:blocked"])

    assert json.loads(json.dumps(finding.as_dict()))["blockers"] == {"2": "CLOSED/-"}


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_main_exits_zero_and_says_so_when_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[]))

    assert sweep.main(["--repo", "acme/widgets"]) == 0
    assert "No stale blockers" in capsys.readouterr().out


def test_main_exits_one_on_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[_finding(sweep.STALE_BLOCK, 5)]))

    assert sweep.main(["--repo", "acme/widgets"]) == 1
    assert "STALE BLOCK" in capsys.readouterr().out


def test_main_json_flag_emits_machine_readable_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[_finding(sweep.DANGLING_REF, 6)]))

    assert sweep.main(["--repo", "acme/widgets", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "number": 6,
            "title": "t",
            "kind": sweep.DANGLING_REF,
            "detail": "d",
            "blockers": {},
            "statuses": [],
        }
    ]


def test_main_passes_the_limit_through(monkeypatch: pytest.MonkeyPatch) -> None:
    scan = mock.Mock(return_value=[])
    monkeypatch.setattr(sweep, "scan", scan)

    sweep.main(["--repo", "acme/widgets", "--limit", "7"])

    scan.assert_called_once_with("acme/widgets", 7)


def test_main_fix_flag_applies_and_reports_mutations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[_finding(sweep.STALE_BLOCK, 5)]))
    apply_fixes = mock.Mock(return_value=1)
    monkeypatch.setattr(sweep, "apply_fixes", apply_fixes)

    assert sweep.main(["--repo", "acme/widgets", "--fix"]) == 1
    apply_fixes.assert_called_once()
    assert "1 issue(s) relabelled." in capsys.readouterr().out


def test_main_fix_flag_is_a_no_op_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[]))
    apply_fixes = mock.Mock()
    monkeypatch.setattr(sweep, "apply_fixes", apply_fixes)

    assert sweep.main(["--fix", "--repo", "acme/widgets"]) == 0
    apply_fixes.assert_not_called()


def test_main_exits_two_when_the_sweep_cannot_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(side_effect=RuntimeError("gh exploded")))

    assert sweep.main(["--repo", "acme/widgets"]) == 2
    assert "error: gh exploded" in capsys.readouterr().err


def test_main_exits_two_when_a_fix_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[_finding(sweep.STALE_BLOCK, 5)]))
    monkeypatch.setattr(
        sweep, "apply_fixes", mock.Mock(side_effect=RuntimeError("label edit denied"))
    )

    assert sweep.main(["--repo", "acme/widgets", "--fix"]) == 2
    assert "error: label edit denied" in capsys.readouterr().err


def test_script_runs_as_a_module_without_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `python scripts/check_stale_blockers.py` entry point parses bare argv."""
    monkeypatch.setattr(sys, "argv", ["check_stale_blockers.py"])
    monkeypatch.setattr(sweep, "resolve_repo", mock.Mock(return_value="acme/widgets"))
    monkeypatch.setattr(sweep, "scan", mock.Mock(return_value=[]))

    assert sweep.main() == 0


def test_script_has_no_third_party_imports() -> None:
    """The workflow runs it with plain `uv run python` — stdlib + `gh` only."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "import requests" not in source
    assert "import boto3" not in source
    assert subprocess.__name__ in source

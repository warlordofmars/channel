# Copyright (c) 2026 John Carter. All rights reserved.
"""
Unit tests for scripts/check_action_pins.py.

The script is the CI gate against an action pin's SHA drifting away from the
version named in its trailing comment (issue #592; the #587 incident is the
motivating case). Tests cover:

- the line parser, including the shapes that must NOT match
- every finding class, and the correctly-pinned case that must produce none
- annotated-tag dereference — the case that turns a *correct* pin into a
  false `mismatch` if you skip it
- the exit-1 (drift) vs exit-2 (could-not-check) split, which is the whole
  point of the design: a network blip must read as neither "drift" nor "OK"
- the self-test, both when it passes and when the detector has been broken
- the empty-check guard, so the script can never report OK having evaluated
  nothing
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check_action_pins.py"

# scripts/ isn't a package; load by file path so tests don't depend on
# PYTHONPATH being set (mirrors tests/unit/test_check_branch_protection_drift.py).
_spec = importlib.util.spec_from_file_location("check_action_pins", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
pins_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_action_pins"] = pins_mod
_spec.loader.exec_module(pins_mod)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pin(spec: str, comment: str | None = None, *, line: int = 1):
    return pins_mod.Pin(path="wf.yml", line=line, spec=spec, comment=comment)


def _resolver(table: dict[tuple[str, str], str]):
    """A resolver over a fixed table; unknown tags are definitively absent."""

    def resolve(repo: str, tag: str) -> str:
        try:
            return table[(repo, tag)]
        except KeyError:
            raise pins_mod.TagNotFound(f"{repo} has no tag {tag}") from None

    return resolve


def _write_workflow(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ── Parsing ───────────────────────────────────────────────────────────────────


def test_parses_spec_and_trailing_comment():
    parsed = pins_mod.parse_uses(f"      - uses: actions/checkout@{SHA_A} # v7.0.1\n", "wf.yml")
    assert len(parsed) == 1
    assert parsed[0].spec == f"actions/checkout@{SHA_A}"
    assert parsed[0].comment == "v7.0.1"
    assert parsed[0].line == 1
    assert parsed[0].location == "wf.yml:1"


def test_parses_uses_without_sequence_dash():
    parsed = pins_mod.parse_uses(f"        uses: actions/cache@{SHA_A} # v4.2.0\n", "wf.yml")
    assert [(p.spec, p.comment) for p in parsed] == [(f"actions/cache@{SHA_A}", "v4.2.0")]


def test_quoted_spec_is_unquoted():
    parsed = pins_mod.parse_uses(f'      - uses: "actions/checkout@{SHA_A}" # v7\n', "wf.yml")
    assert parsed[0].spec == f"actions/checkout@{SHA_A}"


def test_absent_and_empty_comments_both_normalise_to_none():
    text = (
        f"      - uses: a/b@{SHA_A}\n      - uses: a/c@{SHA_A} #\n      - uses: a/d@{SHA_A} #   \n"
    )
    assert [p.comment for p in pins_mod.parse_uses(text, "wf.yml")] == [None, None, None]


def test_does_not_match_keys_merely_ending_in_uses_or_inline_prose():
    # `statuses: write` ends in `uses:` textually; a `run:` script mentioning
    # `uses:` mid-line is not a step. Neither may be parsed as a pin.
    text = (
        "      statuses: write\n"
        '        run: echo "uses: actions/checkout@deadbeef"\n'
        "        body: some uses: text\n"
    )
    assert pins_mod.parse_uses(text, "wf.yml") == []


def test_line_numbers_are_one_based_and_track_the_file():
    text = "jobs:\n  a:\n    steps:\n" + f"      - uses: a/b@{SHA_A} # v1\n"
    assert pins_mod.parse_uses(text, "wf.yml")[0].line == 4


# ── Spec splitting / classification helpers ───────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (f"actions/checkout@{SHA_A}", ("actions/checkout", SHA_A)),
        # Subpath actions are tagged in the parent repo.
        (f"github/codeql-action/upload-sarif@{SHA_A}", ("github/codeql-action", SHA_A)),
        # Reusable workflows follow the same rule.
        ("o/r/.github/workflows/w.yml@v1", ("o/r", "v1")),
        ("actions/stale@v9", ("actions/stale", "v9")),
    ],
)
def test_split_spec_extracts_repo_and_ref(spec, expected):
    assert pins_mod.split_spec(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "not-an-action",  # no @ at all
        "@v1",  # no action
        "actions/checkout@",  # no ref
        "checkout@v1",  # single path segment
        "/checkout@v1",  # empty owner segment
    ],
)
def test_split_spec_rejects_unparseable_specs(spec):
    assert pins_mod.split_spec(spec) is None


@pytest.mark.parametrize(
    ("spec", "skippable"),
    [
        ("./.github/actions/local", True),
        ("docker://alpine:3.20", True),
        (f"actions/checkout@{SHA_A}", False),
    ],
)
def test_local_and_docker_references_are_skippable(spec, skippable):
    assert pins_mod.is_skippable(spec) is skippable


def test_tag_from_comment_takes_the_first_token_and_ignores_prose():
    assert pins_mod.tag_from_comment("v4.1.0 (held back for #123)") == "v4.1.0"
    assert pins_mod.tag_from_comment("v4") == "v4"
    assert pins_mod.tag_from_comment("   ") == ""


# ── check_pins: one test per finding class ────────────────────────────────────


def test_matching_pin_produces_no_finding():
    report = pins_mod.check_pins(
        [_pin(f"a/b@{SHA_A}", "v1.0.0")],
        _resolver({("a/b", "v1.0.0"): SHA_A}),
    )
    assert report.findings == []
    assert report.errors == []
    assert report.verified == 1


def test_mismatched_sha_is_reported_as_drift():
    """The #587 shape: comment says one version, SHA belongs to another."""
    report = pins_mod.check_pins(
        [_pin(f"actions/checkout@{SHA_A}", "v4")],
        _resolver({("actions/checkout", "v4"): SHA_B}),
    )
    assert [f.kind for f in report.findings] == [pins_mod.MISMATCH]
    assert SHA_A in report.findings[0].detail
    assert SHA_B in report.findings[0].detail
    assert report.verified == 0


def test_missing_comment_is_a_finding():
    report = pins_mod.check_pins([_pin(f"a/b@{SHA_A}")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.MISSING_COMMENT]


def test_comment_naming_no_tag_is_a_finding_not_an_error():
    """Proven-absent (a successful API call) is drift, not an inability to check."""
    report = pins_mod.check_pins([_pin(f"a/b@{SHA_A}", "nope")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.UNRESOLVABLE_TAG]
    assert report.errors == []


def test_mutable_ref_is_reported_as_unpinned():
    report = pins_mod.check_pins([_pin("actions/stale@v9", "v9")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.UNPINNED]


def test_abbreviated_sha_is_not_accepted_as_a_pin():
    report = pins_mod.check_pins([_pin("a/b@abc1234", "v1")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.UNPINNED]


def test_uppercase_sha_is_not_accepted_as_a_pin():
    report = pins_mod.check_pins([_pin(f"a/b@{'A' * 40}", "v1")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.UNPINNED]


def test_unparseable_spec_is_reported_as_malformed():
    report = pins_mod.check_pins([_pin("not-an-action", "v1")], _resolver({}))
    assert [f.kind for f in report.findings] == [pins_mod.MALFORMED]


def test_skipped_references_are_counted_not_checked():
    report = pins_mod.check_pins(
        [_pin("./.github/actions/local"), _pin("docker://alpine:3.20")],
        _resolver({}),
    )
    assert report.findings == []
    assert report.skipped == 2
    assert report.verified == 0


def test_resolution_failure_becomes_an_error_never_a_finding():
    """A network blip must not masquerade as drift."""

    def boom(repo: str, tag: str) -> str:
        raise pins_mod.ResolutionError("network is down")

    report = pins_mod.check_pins([_pin(f"a/b@{SHA_A}", "v1")], boom)
    assert report.findings == []
    assert len(report.errors) == 1
    assert "network is down" in report.errors[0].detail


def test_mixed_input_classifies_every_pin_independently():
    report = pins_mod.check_pins(
        [
            _pin(f"a/good@{SHA_A}", "v1", line=1),
            _pin(f"a/bad@{SHA_B}", "v1", line=2),
            _pin("./local", line=3),
        ],
        _resolver({("a/good", "v1"): SHA_A, ("a/bad", "v1"): SHA_C}),
    )
    assert report.verified == 1
    assert report.skipped == 1
    assert [f.kind for f in report.findings] == [pins_mod.MISMATCH]


# ── Tag resolution ────────────────────────────────────────────────────────────


def test_lightweight_tag_resolves_in_one_call():
    calls: list[list[str]] = []

    def run(args):
        calls.append(args)
        return [{"ref": "refs/tags/v1.0.0", "object": {"type": "commit", "sha": SHA_A}}]

    assert pins_mod.resolve_tag_sha("a/b", "v1.0.0", run=run) == SHA_A
    assert calls == [["api", "repos/a/b/git/matching-refs/tags/v1.0.0"]]


def test_annotated_tag_is_dereferenced_to_its_commit():
    """
    Skipping the deref reports a *correctly* pinned action as drifted —
    `aquasecurity/trivy-action` is annotated in this repo today.
    """
    calls: list[list[str]] = []

    def run(args):
        calls.append(args)
        if "matching-refs" in args[1]:
            return [{"ref": "refs/tags/v0.36.0", "object": {"type": "tag", "sha": SHA_B}}]
        return {"object": {"type": "commit", "sha": SHA_A}}

    assert pins_mod.resolve_tag_sha("aquasecurity/trivy-action", "v0.36.0", run=run) == SHA_A
    assert calls[1] == ["api", f"repos/aquasecurity/trivy-action/git/tags/{SHA_B}"]


def test_prefix_matches_do_not_satisfy_an_exact_tag():
    """`matching-refs` prefix-matches: `v4` must not be answered by `v4.1.0`."""

    def run(args):
        return [{"ref": "refs/tags/v4.1.0", "object": {"type": "commit", "sha": SHA_A}}]

    with pytest.raises(pins_mod.TagNotFound):
        pins_mod.resolve_tag_sha("a/b", "v4", run=run)


def test_empty_ref_list_means_the_tag_definitively_does_not_exist():
    with pytest.raises(pins_mod.TagNotFound):
        pins_mod.resolve_tag_sha("a/b", "v9", run=lambda args: [])


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [{"ref": "refs/tags/v1", "object": "not-a-dict"}],
        [{"ref": "refs/tags/v1", "object": {"type": "commit"}}],
    ],
)
def test_malformed_ref_payloads_raise_resolution_error(payload):
    with pytest.raises(pins_mod.ResolutionError):
        pins_mod.resolve_tag_sha("a/b", "v1", run=lambda args: payload)


@pytest.mark.parametrize(
    "tag_payload",
    ["not-a-dict", {"object": "not-a-dict"}, {"object": {"no": "sha"}}],
)
def test_malformed_annotated_tag_payloads_raise_resolution_error(tag_payload):
    def run(args):
        if "matching-refs" in args[1]:
            return [{"ref": "refs/tags/v1", "object": {"type": "tag", "sha": SHA_B}}]
        return tag_payload

    with pytest.raises(pins_mod.ResolutionError):
        pins_mod.resolve_tag_sha("a/b", "v1", run=run)


# ── `gh` subprocess wrapper ───────────────────────────────────────────────────


def test_run_gh_parses_json_stdout():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps([1, 2]), stderr=""
    )
    with mock.patch.object(pins_mod.subprocess, "run", return_value=completed):
        assert pins_mod._run_gh(["api", "x"]) == [1, 2]


def test_run_gh_maps_missing_binary_to_resolution_error():
    with (
        mock.patch.object(pins_mod.subprocess, "run", side_effect=OSError("no gh")),
        pytest.raises(pins_mod.ResolutionError, match="could not run"),
    ):
        pins_mod._run_gh(["api", "x"])


def test_run_gh_maps_non_zero_exit_to_resolution_error():
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="rate limited")
    with (
        mock.patch.object(pins_mod.subprocess, "run", return_value=completed),
        pytest.raises(pins_mod.ResolutionError, match="rate limited"),
    ):
        pins_mod._run_gh(["api", "x"])


def test_run_gh_maps_non_json_output_to_resolution_error():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="<html>502</html>", stderr=""
    )
    with (
        mock.patch.object(pins_mod.subprocess, "run", return_value=completed),
        pytest.raises(pins_mod.ResolutionError, match="non-JSON"),
    ):
        pins_mod._run_gh(["api", "x"])


# ── Caching resolver ──────────────────────────────────────────────────────────


def test_cached_resolver_asks_once_per_repo_tag_pair():
    calls: list[tuple[str, str]] = []

    def resolve(repo, tag):
        calls.append((repo, tag))
        return SHA_A

    cached = pins_mod.make_cached_resolver(resolve)
    assert cached("a/b", "v1") == SHA_A
    assert cached("a/b", "v1") == SHA_A
    assert cached("a/b", "v2") == SHA_A
    assert calls == [("a/b", "v1"), ("a/b", "v2")]


def test_cached_resolver_caches_definitive_absence():
    calls: list[tuple[str, str]] = []

    def resolve(repo, tag):
        calls.append((repo, tag))
        raise pins_mod.TagNotFound("nope")

    cached = pins_mod.make_cached_resolver(resolve)
    for _ in range(2):
        with pytest.raises(pins_mod.TagNotFound):
            cached("a/b", "v1")
    assert calls == [("a/b", "v1")]


def test_cached_resolver_does_not_cache_transient_failures():
    """A blip on the first pin must not condemn every later pin needing the same tag."""
    attempts = {"n": 0}

    def resolve(repo, tag):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise pins_mod.ResolutionError("blip")
        return SHA_A

    cached = pins_mod.make_cached_resolver(resolve)
    with pytest.raises(pins_mod.ResolutionError):
        cached("a/b", "v1")
    assert cached("a/b", "v1") == SHA_A


def test_make_cached_resolver_defaults_to_the_gh_backed_resolver():
    with mock.patch.object(pins_mod, "resolve_tag_sha", return_value=SHA_A) as resolver:
        assert pins_mod.make_cached_resolver()("a/b", "v1") == SHA_A
    resolver.assert_called_once_with("a/b", "v1")


# ── Workflow-file discovery ───────────────────────────────────────────────────


def test_discovery_finds_yml_and_yaml_and_ignores_other_files(tmp_path):
    _write_workflow(tmp_path, "a.yml", "")
    _write_workflow(tmp_path, "b.yaml", "")
    _write_workflow(tmp_path, "c.md", "")
    nested = tmp_path / "nested"
    nested.mkdir()
    _write_workflow(nested, "d.yml", "")
    assert [p.name for p in pins_mod.iter_workflow_files(tmp_path)] == ["a.yml", "b.yaml", "d.yml"]


def test_collect_pins_reads_every_file(tmp_path):
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n")
    _write_workflow(tmp_path, "b.yml", f"      - uses: c/d@{SHA_B} # v2\n")
    assert sorted(p.spec for p in pins_mod.collect_pins(tmp_path)) == [
        f"a/b@{SHA_A}",
        f"c/d@{SHA_B}",
    ]


def test_collect_pins_reports_repo_relative_paths_when_inside_the_repo():
    pins = pins_mod.collect_pins(ROOT / ".github" / "workflows")
    assert pins, "the repo's own workflows must contain `uses:` references"
    assert all(p.path.startswith(".github/workflows/") for p in pins)


def test_collect_pins_falls_back_to_absolute_paths_outside_the_repo(tmp_path):
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n")
    assert pins_mod.collect_pins(tmp_path)[0].path == str(tmp_path / "a.yml")


# ── Self-test ─────────────────────────────────────────────────────────────────


def test_self_test_passes_against_an_intact_detector():
    assert pins_mod.run_self_test() == []


def test_self_test_fails_when_a_finding_class_stops_being_detected():
    """The self-test's whole job: notice when the detector has gone silent."""
    with mock.patch.object(pins_mod, "check_pins", return_value=pins_mod.Report([], [], 1, 2)):
        failures = pins_mod.run_self_test()
    assert failures and "expected findings" in failures[0]


def test_self_test_fails_when_a_good_pin_stops_being_verified():
    broken = pins_mod.Report(
        findings=[
            pins_mod.Finding(_pin(f"{action}@{SHA_A}"), kind, "")
            for action, kind in pins_mod._SELF_TEST_EXPECTED.items()
        ],
        errors=[pins_mod.VerificationError(_pin("a/b@x"), "blip")],
        verified=0,
        skipped=0,
    )
    with mock.patch.object(pins_mod, "check_pins", return_value=broken):
        failures = pins_mod.run_self_test()
    assert any("verified" in f for f in failures)
    assert any("skipped" in f for f in failures)
    assert any("verification errors" in f for f in failures)


def test_self_test_resolver_answers_known_tags_and_denies_the_rest():
    assert pins_mod._self_test_resolver("acme/good", "v1.0.0") == pins_mod._SELF_TEST_GOOD_SHA
    with pytest.raises(pins_mod.TagNotFound):
        pins_mod._self_test_resolver("acme/good", "v9")


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_self_test_flag_exits_zero(capsys):
    assert pins_mod.main(["--self-test"]) == 0
    assert "self-test passed" in capsys.readouterr().out


def test_cli_self_test_flag_exits_two_when_the_detector_is_broken(capsys):
    with mock.patch.object(pins_mod, "run_self_test", return_value=["boom"]):
        assert pins_mod.main(["--self-test"]) == 2
    out = capsys.readouterr().out
    assert "SELF-TEST FAILED" in out
    assert "boom" in out


def test_cli_exits_zero_when_every_pin_matches(tmp_path, capsys):
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n      - uses: ./local\n")
    code = pins_mod.main(
        ["--workflow-dir", str(tmp_path)],
        resolve=_resolver({("a/b", "v1"): SHA_A}),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "OK — 1 SHA-pinned action(s)" in out
    assert "1 local/docker reference(s) skipped" in out


def test_cli_exits_one_on_drift_and_names_the_location(tmp_path, capsys):
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n")
    code = pins_mod.main(
        ["--workflow-dir", str(tmp_path)],
        resolve=_resolver({("a/b", "v1"): SHA_B}),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "DRIFT DETECTED (1 finding(s))" in out
    assert "a.yml:1" in out
    assert pins_mod.MISMATCH in out


def test_cli_exits_two_when_pins_could_not_be_verified(tmp_path, capsys):
    """Exit 2, not 1 — and the report says so in as many words."""
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n")

    def boom(repo, tag):
        raise pins_mod.ResolutionError("network is down")

    code = pins_mod.main(["--workflow-dir", str(tmp_path)], resolve=boom)
    out = capsys.readouterr().out
    assert code == 2
    assert "COULD NOT VERIFY 1 pin(s)" in out
    assert "NOT a drift report" in out
    assert "DRIFT DETECTED" not in out


def test_drift_outranks_unverifiable_in_the_exit_code(tmp_path, capsys):
    _write_workflow(
        tmp_path,
        "a.yml",
        f"      - uses: a/drift@{SHA_A} # v1\n      - uses: a/blip@{SHA_A} # v1\n",
    )

    def resolve(repo, tag):
        if repo == "a/drift":
            return SHA_B
        raise pins_mod.ResolutionError("network is down")

    code = pins_mod.main(["--workflow-dir", str(tmp_path)], resolve=resolve)
    out = capsys.readouterr().out
    assert code == 1
    assert "DRIFT DETECTED" in out
    assert "COULD NOT VERIFY" in out  # still reported, just not the exit code


def test_cli_exits_two_when_the_workflow_directory_is_missing(tmp_path, capsys):
    code = pins_mod.main(["--workflow-dir", str(tmp_path / "nope")])
    assert code == 2
    assert "workflow directory not found" in capsys.readouterr().err


def test_cli_refuses_to_report_success_having_checked_nothing(tmp_path, capsys):
    """
    The `#494 / #530 / #568` failure class: a check that is present, green,
    and evaluating nothing. Zero checkable pins is an error, never an OK.
    """
    _write_workflow(tmp_path, "a.yml", "      - uses: ./.github/actions/local\n")
    code = pins_mod.main(["--workflow-dir", str(tmp_path)], resolve=_resolver({}))
    assert code == 2
    assert "refusing to report success on an empty check" in capsys.readouterr().err


def test_cli_defaults_to_the_repos_own_workflow_directory():
    assert pins_mod.DEFAULT_WORKFLOW_DIR == ROOT / ".github" / "workflows"


def test_cli_uses_the_cached_gh_resolver_when_none_is_injected(tmp_path):
    """The default path must go through the cache, not straight to `gh`."""
    _write_workflow(tmp_path, "a.yml", f"      - uses: a/b@{SHA_A} # v1\n")
    with mock.patch.object(
        pins_mod, "make_cached_resolver", return_value=_resolver({("a/b", "v1"): SHA_A})
    ) as factory:
        assert pins_mod.main(["--workflow-dir", str(tmp_path)]) == 0
    factory.assert_called_once_with()


# ── The repo's own workflows ──────────────────────────────────────────────────


def test_every_repo_pin_is_a_full_sha_with_a_version_comment():
    """
    Offline half of the gate, runnable in `inv pre-push`: every third-party
    `uses:` in this repo is SHA-pinned and carries a comment. Whether the SHA
    *is* that tag needs the network, so it stays in CI (security.yml).
    """
    offenders = []
    for pin in pins_mod.collect_pins(ROOT / ".github" / "workflows"):
        if pins_mod.is_skippable(pin.spec):
            continue
        parts = pins_mod.split_spec(pin.spec)
        assert parts is not None, f"{pin.location}: unparseable `uses: {pin.spec}`"
        _repo, ref = parts
        if not pins_mod._SHA40_RE.match(ref) or pin.comment is None:
            offenders.append(f"{pin.location}: {pin.spec} # {pin.comment}")
    assert offenders == []


def test_the_pin_checker_job_is_wired_into_the_security_workflow():
    """
    A detector nobody runs is the failure class this issue exists to close.
    Pin the wiring, not just the script.
    """
    workflow = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
    assert "scripts/check_action_pins.py --self-test" in workflow
    assert "scripts/check_action_pins.py\n" in workflow

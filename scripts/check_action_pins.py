# Copyright (c) 2026 John Carter. All rights reserved.
"""
Detect drift between a GitHub Action's pinned commit SHA and the version
recorded in its trailing comment.

CLAUDE.md's convention is ``uses: owner/repo@<40-hex-sha> # <tag>``: the SHA
is the security control (immutable, unlike a mutable tag) and the comment is
the *only* human-legible record of which version actually runs. Issue #587
found 22 `actions/checkout` pins commented ``# v4`` whose SHA was in fact
``v7.0.1`` — a three-major-version jump introduced by a routine Dependabot
grouped bump (``bb9faf6``) that rewrote 6 of 7 comments and missed the
seventh. The security property held the whole time; **auditability** is what
broke, and a confidently-wrong comment is worse than no comment.

Issue #592: promote the one-off verification loop from #587 into a gate.
This is a *detector*, not a prevention mechanism — it is cause-agnostic and
catches the drift whether it came from Dependabot, a manual bump or a bad
copy-paste.

Usage:
    python scripts/check_action_pins.py
    python scripts/check_action_pins.py --workflow-dir path/to/workflows
    python scripts/check_action_pins.py --self-test   # offline; no `gh` needed

Exit codes:
    0 — every SHA-pinned action matches the tag in its comment
    1 — drift / convention violations found (see the classes below)
    2 — the check could not be *performed*: usage error, nothing to check,
        a self-test failure, or one-or-more pins that could not be verified
        (network, `gh` auth, rate limit, malformed API response)

Exit 1 vs exit 2 is the load-bearing distinction. A network blip must never
read as "drift detected" (that would send someone hunting a nonexistent bad
pin), and it must never silently pass either (that is the "present, green,
evaluating nothing" failure class this repo has been clearing all month —
#494, #530, #537, #568, #570, #588, #598). Both are non-zero, so either way
the workflow fails; the exit code and the report tell a human which kind of
problem they have. When both occur, exit 1 wins: drift is the definitive,
actionable finding.

Finding classes (all exit 1):
    mismatch          — the comment's tag resolves to a different commit
                        than the pinned SHA. The #587 shape.
    missing-comment   — SHA-pinned but no trailing comment at all. Fails by
                        design: an absent comment is the same unauditable
                        state as a wrong one, just less confident about it.
    unresolvable-tag  — the comment names something that is not a tag in
                        that repo (typo, renamed tag, branch name, prose).
                        Definitive: proven absent via a successful API call,
                        never inferred from a failed one.
    unpinned          — the ref is not a full 40-hex commit SHA (a mutable
                        tag or branch). Strictly worse than a wrong comment;
                        CLAUDE.md forbids it outright.
    malformed         — `uses:` value this checker cannot parse as
                        ``owner/repo[/path]@ref``.

Skipped (never a finding): local actions (``./...``) and ``docker://``
references, neither of which carries a GitHub-repo tag to verify. The
summary line reports the skip count so the exclusion stays visible.

Annotated tags:
    ``refs/tags/x`` may point at a *tag object* rather than a commit. Those
    are dereferenced with a second lookup. Skipping that step produces false
    `mismatch` findings on exactly the actions that are pinned *correctly* —
    ``aquasecurity/trivy-action`` is one, live in this repo today.

Why not a `pre-push` task:
    It needs network + `gh` auth, so it is a CI job (see
    `.github/workflows/security.yml`), not part of the local gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW_DIR = ROOT / ".github" / "workflows"

# A full commit SHA. Deliberately exact-length and lowercase-only: an
# abbreviated or mixed-case ref is not what the convention asks for, and
# treating it as pinned would let a 7-char ref pass as a security control.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# `uses:` at the start of a line (optionally after a YAML sequence dash).
# Anchoring matters: it keeps `statuses: write` and any `uses:` text living
# inside an inline `script:` / `body:` string out of the match set.
_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<rest>\S.*?)\s*$")

_WORKFLOW_SUFFIXES = (".yml", ".yaml")

# Finding kinds. Strings rather than an enum so the report format and the
# tests read the same way the CI log does.
MISMATCH = "mismatch"
MISSING_COMMENT = "missing-comment"
UNRESOLVABLE_TAG = "unresolvable-tag"
UNPINNED = "unpinned"
MALFORMED = "malformed"


class TagNotFound(Exception):
    """The repo definitively has no such tag (a successful API call said so)."""


class ResolutionError(Exception):
    """The tag could not be looked up at all — network, auth, rate limit, bad payload."""


@dataclass(frozen=True)
class Pin:
    """One `uses:` reference, as written in a workflow file."""

    path: str
    line: int
    spec: str
    comment: str | None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Finding:
    """A pin that violates the convention. Exit code 1."""

    pin: Pin
    kind: str
    detail: str


@dataclass(frozen=True)
class VerificationError:
    """A pin whose tag could not be looked up. Exit code 2 — not drift."""

    pin: Pin
    detail: str


@dataclass(frozen=True)
class Report:
    findings: list[Finding]
    errors: list[VerificationError]
    verified: int
    skipped: int


# ── Parsing ───────────────────────────────────────────────────────────────────


def parse_uses(text: str, path: str) -> list[Pin]:
    """
    Extract every `uses:` reference from a workflow file's text.

    Deliberately line-based rather than YAML-parsed: the version marker is a
    *comment*, and every YAML parser discards comments. The thing being
    checked only exists in the raw bytes.
    """
    pins: list[Pin] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _USES_RE.match(line)
        if match is None:
            continue
        spec_part, sep, comment_part = match.group("rest").partition("#")
        spec = spec_part.strip().strip("\"'")
        comment = comment_part.strip() if sep else ""
        pins.append(
            Pin(path=path, line=lineno, spec=spec, comment=comment or None),
        )
    return pins


def iter_workflow_files(directory: Path) -> list[Path]:
    """Every `*.yml` / `*.yaml` file under `directory`, sorted for stable output."""
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix in _WORKFLOW_SUFFIXES)


def collect_pins(directory: Path) -> list[Pin]:
    """Parse every workflow file under `directory` into `Pin` records."""
    pins: list[Pin] = []
    for path in iter_workflow_files(directory):
        try:
            display = str(path.relative_to(ROOT))
        except ValueError:  # a --workflow-dir outside the repo (tests, ad-hoc runs)
            display = str(path)
        pins.extend(parse_uses(path.read_text(encoding="utf-8"), display))
    return pins


def is_skippable(spec: str) -> bool:
    """
    Local (`./...`) and container (`docker://...`) actions carry no
    GitHub-repo tag, so there is nothing to cross-check. Not a pass — an
    exclusion, and the summary reports how many were excluded.
    """
    return spec.startswith("./") or spec.startswith("docker://")


def split_spec(spec: str) -> tuple[str, str] | None:
    """
    Split ``owner/repo[/subpath]@ref`` into ``("owner/repo", "ref")``.

    The subpath is dropped: `github/codeql-action/upload-sarif` is tagged in
    the `github/codeql-action` repo. Returns None for anything unparseable.
    """
    action, sep, ref = spec.rpartition("@")
    if not sep or not action or not ref:
        return None
    segments = action.split("/")
    if len(segments) < 2 or not all(segments[:2]):
        return None
    return "/".join(segments[:2]), ref


def tag_from_comment(comment: str) -> str:
    """
    The tag is the first whitespace-delimited token of the trailing comment.

    Lenient on purpose: ``# v4.1.0 (kept back for X)`` should verify the tag
    and ignore the prose. A comment that is only prose yields a token that
    resolves to no tag, which is reported as `unresolvable-tag`.

    Returns "" for a blank comment, which callers coming through
    `parse_uses` never see — an empty comment is normalised to `None` there
    and classified as `missing-comment`.
    """
    tokens = comment.split()
    return tokens[0] if tokens else ""


# ── Checking ──────────────────────────────────────────────────────────────────

Resolver = Callable[[str, str], str]


def check_pins(pins: Iterable[Pin], resolve: Resolver) -> Report:
    """
    Classify every pin, resolving comment tags through `resolve`.

    `resolve(repo, tag)` returns the commit SHA the tag points at, raises
    `TagNotFound` when the repo definitively has no such tag, or
    `ResolutionError` when the question could not be asked.
    """
    findings: list[Finding] = []
    errors: list[VerificationError] = []
    verified = 0
    skipped = 0

    for pin in pins:
        if is_skippable(pin.spec):
            skipped += 1
            continue

        parts = split_spec(pin.spec)
        if parts is None:
            findings.append(
                Finding(pin, MALFORMED, f"cannot parse `uses: {pin.spec}` as owner/repo@ref")
            )
            continue

        repo, ref = parts
        if not _SHA40_RE.match(ref):
            findings.append(
                Finding(
                    pin,
                    UNPINNED,
                    f"{repo} is pinned to `{ref}`, which is not a full 40-character "
                    "commit SHA — mutable refs are forbidden (CLAUDE.md §Conventions)",
                )
            )
            continue

        if pin.comment is None:
            findings.append(
                Finding(
                    pin,
                    MISSING_COMMENT,
                    f"{repo}@{ref[:12]}… has no trailing version comment — the pin is "
                    "unauditable (expected `# <tag>`)",
                )
            )
            continue

        tag = tag_from_comment(pin.comment)
        try:
            real_sha = resolve(repo, tag)
        except TagNotFound:
            findings.append(
                Finding(
                    pin,
                    UNRESOLVABLE_TAG,
                    f"{repo} has no tag `{tag}` — the comment names nothing verifiable",
                )
            )
            continue
        except ResolutionError as exc:
            errors.append(VerificationError(pin, f"{repo}@{tag}: {exc}"))
            continue

        if real_sha != ref:
            findings.append(
                Finding(
                    pin,
                    MISMATCH,
                    f"{repo} is commented `# {tag}` but the pinned SHA is not that tag "
                    f"(pinned {ref}, tag {tag} is {real_sha})",
                )
            )
            continue

        verified += 1

    return Report(findings=findings, errors=errors, verified=verified, skipped=skipped)


# ── Tag resolution via `gh` ───────────────────────────────────────────────────


def _run_gh(args: list[str]) -> Any:
    """
    Run `gh <args>` and return its parsed JSON stdout.

    Every failure mode — `gh` absent, non-zero exit, non-JSON body — raises
    `ResolutionError`, never `TagNotFound`. "I could not ask" and "the answer
    is no" must not collapse into one another; that is the whole exit-1 vs
    exit-2 distinction.
    """
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as exc:  # `gh` not installed / not executable
        raise ResolutionError(f"could not run `gh`: {exc}") from exc
    if result.returncode != 0:
        raise ResolutionError(
            f"`gh {' '.join(args)}` failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ResolutionError(f"`gh {' '.join(args)}` returned non-JSON output: {exc}") from exc


def resolve_tag_sha(repo: str, tag: str, *, run: Callable[[list[str]], Any] = _run_gh) -> str:
    """
    Resolve `tag` in `repo` to the commit SHA it ultimately points at.

    Uses `git/matching-refs/tags/{tag}` rather than `git/ref/tags/{tag}`
    (the endpoint CLAUDE.md names) for one reason: a missing tag comes back
    as an empty array with exit 0, so "no such tag" is a *successful* API
    call rather than a 404 that has to be told apart from every other error
    by scraping stderr. The endpoint prefix-matches, so the exact ref is
    selected explicitly.

    Annotated tags point at a tag object; a second lookup dereferences that
    to its commit. `aquasecurity/trivy-action` is annotated in this repo
    today — skipping the deref would report a correct pin as drifted.
    """
    refs = run(["api", f"repos/{repo}/git/matching-refs/tags/{tag}"])
    if not isinstance(refs, list):
        raise ResolutionError(
            f"expected a list of refs for {repo}@{tag}, got {type(refs).__name__}"
        )

    wanted = f"refs/tags/{tag}"
    match = next(
        (r for r in refs if isinstance(r, dict) and r.get("ref") == wanted),
        None,
    )
    if match is None:
        raise TagNotFound(f"{repo} has no tag {tag}")

    obj = match.get("object")
    if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
        raise ResolutionError(f"malformed ref object for {repo}@{tag}")

    sha = obj["sha"]
    if obj.get("type") != "tag":
        return sha

    # Annotated tag → dereference the tag object to its commit.
    tag_obj = run(["api", f"repos/{repo}/git/tags/{sha}"])
    if not isinstance(tag_obj, dict):
        raise ResolutionError(f"malformed tag object for {repo}@{tag}")
    inner = tag_obj.get("object")
    if not isinstance(inner, dict) or not isinstance(inner.get("sha"), str):
        raise ResolutionError(f"malformed annotated-tag payload for {repo}@{tag}")
    return inner["sha"]


def make_cached_resolver(resolve: Resolver | None = None) -> Resolver:
    """
    Memoise successful and definitively-negative lookups per `(repo, tag)`.

    This repo pins ~60 `uses:` references across ~12 distinct action@tag
    pairs, so caching is the difference between comfortably inside the API
    rate limit and flirting with it. `ResolutionError` is deliberately *not*
    cached — a transient failure should be retried by the next pin that
    needs the same answer.
    """
    # Late-bound rather than a `resolve_tag_sha` default argument: a default
    # captures the function object at definition time, which would make the
    # real resolver unpatchable and silently un-substitutable.
    backend = resolve if resolve is not None else resolve_tag_sha
    cache: dict[tuple[str, str], str | TagNotFound] = {}

    def cached(repo: str, tag: str) -> str:
        key = (repo, tag)
        hit = cache.get(key)
        if isinstance(hit, TagNotFound):
            raise hit
        if hit is not None:
            return hit
        try:
            sha = backend(repo, tag)
        except TagNotFound as exc:
            cache[key] = exc
            raise
        cache[key] = sha
        return sha

    return cached


# ── Self-test ─────────────────────────────────────────────────────────────────

_SELF_TEST_GOOD_SHA = "1" * 40
_SELF_TEST_DRIFTED_SHA = "2" * 40
_SELF_TEST_REAL_SHA = "3" * 40

_SELF_TEST_WORKFLOW = f"""\
jobs:
  demo:
    steps:
      - uses: acme/good@{_SELF_TEST_GOOD_SHA} # v1.0.0
      - uses: acme/drifted@{_SELF_TEST_DRIFTED_SHA} # v1.0.0
      - uses: acme/uncommented@{_SELF_TEST_GOOD_SHA}
      - uses: acme/mutable@v1.0.0
      - uses: acme/typo@{_SELF_TEST_GOOD_SHA} # v1.0.0-nope
      - uses: not-an-action
      - uses: ./.github/actions/local
      - uses: docker://alpine:3.20
      - name: a step whose script mentions uses: nothing
        run: echo "uses: acme/notreal@deadbeef"
"""

_SELF_TEST_TAGS = {
    ("acme/good", "v1.0.0"): _SELF_TEST_GOOD_SHA,
    ("acme/drifted", "v1.0.0"): _SELF_TEST_REAL_SHA,
}

_SELF_TEST_EXPECTED = {
    "acme/drifted": MISMATCH,
    "acme/uncommented": MISSING_COMMENT,
    "acme/mutable": UNPINNED,
    "acme/typo": UNRESOLVABLE_TAG,
    "not-an-action": MALFORMED,
}


def _self_test_resolver(repo: str, tag: str) -> str:
    try:
        return _SELF_TEST_TAGS[(repo, tag)]
    except KeyError:
        raise TagNotFound(f"{repo} has no tag {tag}") from None


def run_self_test() -> list[str]:
    """
    Prove the detector still detects, offline.

    A checker is only worth its green tick if it can be shown to go red. The
    fixture below carries one instance of every finding class plus one
    correctly-pinned action; this asserts the exact set comes back — not
    merely that *something* was reported, and not merely that nothing was.
    Returns a list of failure descriptions; empty means the detector works.
    """
    failures: list[str] = []
    pins = parse_uses(_SELF_TEST_WORKFLOW, "<self-test>")
    report = check_pins(pins, _self_test_resolver)

    actual = {}
    for finding in report.findings:
        action = finding.pin.spec.split("@")[0]
        actual[action] = finding.kind

    if actual != _SELF_TEST_EXPECTED:
        failures.append(f"expected findings {_SELF_TEST_EXPECTED!r}, got {actual!r}")
    if report.verified != 1:
        failures.append(f"expected exactly 1 verified pin, got {report.verified}")
    if report.skipped != 2:
        failures.append(f"expected 2 skipped pins (local + docker), got {report.skipped}")
    if report.errors:
        failures.append(f"expected no verification errors, got {report.errors!r}")
    return failures


# ── CLI ───────────────────────────────────────────────────────────────────────


def _print_report(report: Report, pin_count: int, file_count: int) -> None:
    if report.findings:
        print(f"action pin check: DRIFT DETECTED ({len(report.findings)} finding(s)).\n")
        for finding in sorted(report.findings, key=lambda f: (f.pin.path, f.pin.line)):
            print(f"  {finding.pin.location} [{finding.kind}]")
            print(f"      {finding.detail}")
        print(
            "\nEvery SHA-pinned action must carry a `# <tag>` comment naming the tag "
            "that SHA belongs to.\nResolve the true version with:\n"
            "  gh api repos/{owner}/{repo}/git/matching-refs/tags/{tag}\n"
            "then correct the comment (or the SHA, if the comment is the intended "
            "version)."
        )

    if report.errors:
        if report.findings:
            print()
        print(f"action pin check: COULD NOT VERIFY {len(report.errors)} pin(s).\n")
        for error in sorted(report.errors, key=lambda e: (e.pin.path, e.pin.line)):
            print(f"  {error.pin.location}")
            print(f"      {error.detail}")
        print(
            "\nThis is NOT a drift report — these pins were never checked. Usual "
            "causes are a\nmissing/expired `GH_TOKEN`, an API rate limit, or a "
            "network failure. Re-run once resolved."
        )

    if not report.findings and not report.errors:
        print(
            f"action pin check: OK — {report.verified} SHA-pinned action(s) across "
            f"{file_count} workflow file(s) match their version comments "
            f"({report.skipped} local/docker reference(s) skipped)."
        )
    else:
        print(
            f"\nchecked {pin_count} `uses:` reference(s) across {file_count} file(s): "
            f"{report.verified} verified, {len(report.findings)} finding(s), "
            f"{len(report.errors)} unverifiable, {report.skipped} skipped."
        )


def main(argv: list[str] | None = None, resolve: Resolver | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify each SHA-pinned GitHub Action matches the tag in its comment."
    )
    parser.add_argument(
        "--workflow-dir",
        type=Path,
        default=DEFAULT_WORKFLOW_DIR,
        help="Directory to scan for workflow YAML (default: .github/workflows).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the detector against a known-bad fixture and exit. Offline; no `gh` needed.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        failures = run_self_test()
        if failures:
            print("action pin check: SELF-TEST FAILED — the detector is broken.\n")
            for failure in failures:
                print(f"  {failure}")
            return 2
        print("action pin check: self-test passed — the detector reports known-bad pins.")
        return 0

    if not args.workflow_dir.is_dir():
        print(f"error: workflow directory not found at {args.workflow_dir}", file=sys.stderr)
        return 2

    files = iter_workflow_files(args.workflow_dir)
    pins = collect_pins(args.workflow_dir)
    checkable = [p for p in pins if not is_skippable(p.spec)]

    # Guard against the "present, green, evaluating nothing" failure: if the
    # layout moves or the parser stops matching, a check that found zero pins
    # would otherwise print OK and exit 0 forever.
    if not checkable:
        print(
            f"error: no third-party `uses:` references found under {args.workflow_dir} "
            "— refusing to report success on an empty check.",
            file=sys.stderr,
        )
        return 2

    # Pass every pin, not just `checkable` — `check_pins` counts the skips,
    # and a summary that under-reported them would misdescribe what was
    # actually examined.
    report = check_pins(pins, resolve or make_cached_resolver())
    _print_report(report, pin_count=len(pins), file_count=len(files))

    if report.findings:
        return 1
    if report.errors:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shim
    sys.exit(main())

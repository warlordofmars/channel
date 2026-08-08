# Copyright (c) 2026 John Carter. All rights reserved.
"""Find open issues whose ``Blocked by #N`` references no longer hold.

Three failure modes rot the backlog, and all three hide real work:

* **Stale block** — an issue is labelled ``status:blocked`` but every issue it
  names as a blocker is now resolved. The work is dispatchable and nobody knows.
* **Missing block** — an issue names an *open* blocker in its body but is not
  labelled ``status:blocked``, so the queue will hand an agent work that cannot
  be finished.
* **Unverifiable block** — an issue is labelled ``status:blocked`` but names no
  blocker in the ``Blocked by #N`` form at all. Its dependency cannot be
  checked, so it can never be auto-unblocked and the sweep used to pass over it
  in silence (issue #570).

It also flags two subtler cases: a blocker closed as ``NOT_PLANNED`` (the work
usually moved somewhere else, so the dependency needs re-pointing rather than
deleting), and a reference to an issue that does not exist.

This enforces the CLAUDE.md §"Backlog labels and milestones" definition of
``status:blocked`` — *"depends on another **open** issue in this repo"*, whose
*"body must name the blocker with ``Blocked by #N``"* — mechanically, instead of
relying on someone remembering to re-label every dependent when a blocker
merges.

Why the literal form is *required* rather than the pattern being widened:
    ``status:blocked`` issues used to state their dependency in prose
    (*"Depends on #295 (PR #488) landing first"*, #495) and the sweep saw
    nothing. Widening the regex to guess at ``Depends on`` / ``Requires`` /
    ``After`` / ``Waiting on`` is a losing game against free text — and it fails
    in the worse direction, because a phrase the regex happens to match feeds
    the ``--fix`` label flip. Requiring the literal form instead is complete by
    construction: a ``status:blocked`` issue is either parseable (every check
    below applies to it) or reported as unverifiable. There is no third state,
    and no phrasing can slip between them. Off-repo dependencies have their own
    label — ``status:needs-info`` — which is what the taxonomy already says it
    is for.

Read-only by default. ``--fix`` flips labels for the two unambiguous cases
(stale-block and missing-block) and prints every change it makes.

Usage::

    uv run python scripts/check_stale_blockers.py
    uv run python scripts/check_stale_blockers.py --json
    uv run python scripts/check_stale_blockers.py --fix
    uv run python scripts/check_stale_blockers.py --repo owner/name

Exit codes:
    0 — no findings
    1 — findings reported
    2 — usage / fetch error (matches ``scripts/check_branch_protection_drift.py``,
        so a scheduled job can tell "the backlog is dirty" apart from
        "``gh`` broke")

Why GraphQL and not ``gh issue view`` per reference:
    Resolving each referenced number with its own ``gh issue view`` is one API
    round-trip per reference — enough to contribute to exhausting the REST rate
    limit on a busy day. A single aliased GraphQL query resolves up to
    ``_GRAPHQL_BATCH`` references per request, and ``issueOrPullRequest``
    resolves *pull request* references too (``Blocked by #375`` where #375 is a
    merged PR is a real shape in this backlog) rather than reporting them as
    dangling.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

BLOCKED_LABEL = "status:blocked"
READY_LABEL = "status:ready"
NEEDS_INFO_LABEL = "status:needs-info"
STATUS_PREFIX = "status:"

#: The one phrasing ``status:blocked`` accepts, quoted back at the author in the
#: unverifiable-block finding. Named rather than inlined so the message and
#: :data:`_BLOCKED_RE` can't drift apart silently.
BLOCKED_BY_FORM = "Blocked by #N"

#: Open issues fetched in one ``gh issue list`` call. ``gh`` paginates
#: internally; the value only needs to exceed the open-issue count.
DEFAULT_ISSUE_LIMIT = 1000

#: References resolved per GraphQL request. Aliased scalar selections are
#: cheap, but keeping batches modest avoids tripping query-complexity limits
#: on repos with hundreds of cross-references.
_GRAPHQL_BATCH = 50

# Matches "Blocked by #291", "Blocked by #259 and #260", "blocked by #240, #290",
# "Blocked by: #1 & #2". The repetition only continues across an explicit
# separator (comma / "and" / "&" / "+"), so a bare newline ends the run — an
# unrelated "#300" on a later line is not swallowed into the reference list.
#
# The optional `neg` group captures a preceding negation ("not blocked by #99",
# "no longer blocked by #99", "isn't blocked by #99"). Those phrasings are the
# author saying the dependency is *done*; parse_blockers drops them, because
# reading one as a live blocker is how --fix would relabel an issue that
# explicitly says it is unblocked.
_BLOCKED_RE = re.compile(
    r"(?P<neg>\b(?:\w+n['’]t|not|no\s+longer|never)\s+)?"
    r"blocked\s+by\b[:\s]*"
    r"(?P<refs>(?:#\d+(?:\s*(?:,|and|&|\+)\s*)?)+)",
    re.IGNORECASE,
)
_REF_RE = re.compile(r"#(\d+)")

# Fenced blocks and inline code spans are stripped before parsing: a reference
# inside backticks is documentation *about* the syntax, not a dependency
# declaration. Issue #457 is the proof case — its own body quotes
# "Blocked by #259 and #260" as a regex example, and an unfiltered sweep
# reports the issue as blocked on its own examples.
_CODE_SPAN_RE = re.compile(r"```.*?```|``.*?``|`[^`]*`", re.DOTALL)

#: What a stripped code span leaves behind. Deliberately NOT whitespace:
#: `blocked\s+by` spans newlines, so substituting a space would let
#: "is blocked `x` by #12" collapse into a phrase nobody wrote. A
#: non-whitespace, non-`#`, non-digit sentinel can't join either side.
_CODE_SENTINEL = "\x00"

#: GraphQL error type that means "this number is not an issue or PR here".
#: Expected and handled (it is exactly the dangling-ref finding); every other
#: error type is a genuine failure.
_NOT_FOUND = "NOT_FOUND"

# Finding kinds, in report order.
STALE_BLOCK = "stale-block"
MISSING_BLOCK = "missing-block"
UNVERIFIABLE_BLOCK = "unverifiable-block"
NOT_PLANNED_BLOCKER = "not-planned-blocker"
DANGLING_REF = "dangling-ref"

# The first two are what `--fix` can flip mechanically; everything from
# UNVERIFIABLE_BLOCK down needs a human, so the order also reads as
# "fixable first".
_ORDER = (STALE_BLOCK, MISSING_BLOCK, UNVERIFIABLE_BLOCK, NOT_PLANNED_BLOCKER, DANGLING_REF)
_HEADINGS = {
    STALE_BLOCK: "STALE BLOCK — blocker(s) resolved, work is dispatchable",
    MISSING_BLOCK: "MISSING BLOCK — open blocker but not labelled blocked",
    UNVERIFIABLE_BLOCK: f"UNVERIFIABLE BLOCK — labelled blocked, no '{BLOCKED_BY_FORM}' to check",
    NOT_PLANNED_BLOCKER: "NOT-PLANNED BLOCKER — dependency likely moved",
    DANGLING_REF: "DANGLING REF — referenced issue does not exist",
}


# ── gh plumbing ───────────────────────────────────────────────────────────────


def _gh(*args: str, tolerate_exit: bool = False) -> str:
    """Run ``gh <args>`` and return stdout.

    ``tolerate_exit`` returns stdout even on a non-zero exit, which
    ``gh api graphql`` uses to signal partial results: a query with a
    ``NOT_FOUND`` alias still prints a complete ``data`` payload but exits 1.
    """
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not tolerate_exit:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _gh_json(*args: str, tolerate_exit: bool = False) -> Any:
    """Run ``gh <args>`` and parse its stdout as JSON."""
    raw = _gh(*args, tolerate_exit=tolerate_exit)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh {' '.join(args)} returned non-JSON output: {exc}") from exc


def resolve_repo(explicit: str | None = None) -> str:
    """Resolve the ``owner/name`` to sweep.

    Precedence: ``--repo`` flag, then ``GITHUB_REPOSITORY`` (set by Actions),
    then whatever repo the current directory belongs to. Nothing is hardcoded
    so a fork inherits a working script.
    """
    if explicit:
        return explicit
    from_env = os.environ.get("GITHUB_REPOSITORY")
    if from_env:
        return from_env
    name_with_owner = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    resolved = name_with_owner.strip()
    if not resolved:
        raise RuntimeError("could not resolve the repository — pass --repo owner/name")
    return resolved


# ── Parsing ───────────────────────────────────────────────────────────────────


def strip_code(body: str) -> str:
    """Blank out fenced blocks and inline code spans.

    Each span becomes :data:`_CODE_SENTINEL` rather than being deleted, so the
    prose on either side can't be spliced into a phrase that wasn't written.
    """
    return _CODE_SPAN_RE.sub(_CODE_SENTINEL, body)


def parse_blockers(body: str | None) -> list[int]:
    """Extract issue numbers from every ``Blocked by`` phrase in ``body``."""
    if not body:
        return []
    found: list[int] = []
    for match in _BLOCKED_RE.finditer(strip_code(body)):
        if match.group("neg"):
            continue
        found.extend(int(n) for n in _REF_RE.findall(match.group("refs")))
    # Preserve first-seen order, drop duplicates.
    return list(dict.fromkeys(found))


def status_labels(issue: dict[str, Any]) -> list[str]:
    """Return the ``status:*`` labels on ``issue``, in the order GitHub lists them."""
    return [
        name
        for label in issue.get("labels") or []
        if (name := label.get("name", "")).startswith(STATUS_PREFIX)
    ]


# ── Reference state ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RefState:
    """Resolved state of one referenced ``#N``."""

    number: int
    #: ``Issue``, ``PullRequest``, or ``Missing`` when the number resolves to
    #: nothing in this repo.
    typename: str
    #: ``OPEN`` / ``CLOSED`` / ``MERGED`` (pull requests), or ``""`` when missing.
    state: str = ""
    #: ``COMPLETED`` / ``NOT_PLANNED`` / ``DUPLICATE`` / ``REOPENED`` for issues.
    state_reason: str = ""

    @property
    def exists(self) -> bool:
        return self.typename != "Missing"

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    @property
    def is_not_planned(self) -> bool:
        return self.state_reason == "NOT_PLANNED"

    def describe(self) -> str:
        """One-token summary used in report output, e.g. ``CLOSED/NOT_PLANNED``."""
        if not self.exists:
            return "NOTFOUND"
        return f"{self.state}/{self.state_reason or '-'}"


def _ref_query(numbers: Sequence[int]) -> str:
    """Build an aliased GraphQL query resolving every number in one request."""
    selections = "\n".join(
        f"    n{n}: issueOrPullRequest(number: {n}) {{\n"
        "      __typename\n"
        "      ... on Issue { state stateReason }\n"
        "      ... on PullRequest { state }\n"
        "    }"
        for n in numbers
    )
    return (
        "query($owner: String!, $name: String!) {\n"
        "  repository(owner: $owner, name: $name) {\n"
        f"{selections}\n"
        "  }\n"
        "}"
    )


def _check_graphql_errors(payload: dict[str, Any]) -> None:
    """Raise unless every GraphQL error is an expected *per-alias* ``NOT_FOUND``.

    Only an error pointing at one of the aliases (path ``["repository", "n42"]``)
    is the dangling-ref case. A NOT_FOUND on the repository itself (path
    ``["repository"]``) means the repo name or the token is wrong — that is a
    fetch error, and reporting it as "every reference is missing" would turn an
    auth problem into a backlog-sized dangling-ref report.
    """
    unexpected = [
        err
        for err in payload.get("errors") or []
        if err.get("type") != _NOT_FOUND or len(err.get("path") or []) < 2
    ]
    if unexpected:
        messages = "; ".join(err.get("message", str(err)) for err in unexpected)
        raise RuntimeError(f"GraphQL query failed: {messages}")


def fetch_ref_states(repo: str, numbers: Sequence[int]) -> dict[int, RefState]:
    """Resolve every referenced number to a :class:`RefState`, batched."""
    owner, _, name = repo.partition("/")
    ordered = sorted(set(numbers))
    states: dict[int, RefState] = {}

    for start in range(0, len(ordered), _GRAPHQL_BATCH):
        batch = ordered[start : start + _GRAPHQL_BATCH]
        payload = _gh_json(
            "api",
            "graphql",
            "-f",
            f"query={_ref_query(batch)}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            # A NOT_FOUND alias makes `gh` exit 1 while still printing usable
            # data — that alias *is* the dangling-ref finding.
            tolerate_exit=True,
        )
        _check_graphql_errors(payload)
        repo_data = (payload.get("data") or {}).get("repository")
        if repo_data is None:
            # No repository object at all — the query never reached the backlog.
            # Falling through would mark every reference "Missing" and report a
            # backlog-sized dangling-ref list on exit 1; this is an exit-2 error.
            raise RuntimeError(
                f"could not read {repo} — check the repository name and `gh auth status`"
            )
        for number in batch:
            node = repo_data.get(f"n{number}")
            if not node:
                states[number] = RefState(number, "Missing")
                continue
            states[number] = RefState(
                number,
                node.get("__typename") or "Missing",
                node.get("state") or "",
                node.get("stateReason") or "",
            )
    return states


def fetch_open_issues(repo: str, limit: int = DEFAULT_ISSUE_LIMIT) -> list[dict[str, Any]]:
    """Fetch every open issue's number, title, body and labels in one call."""
    issues: list[dict[str, Any]] = _gh_json(
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,body,labels",
    )
    if len(issues) >= limit:
        print(
            f"warning: hit the --limit of {limit} open issues; results may be "
            "truncated. Re-run with a higher --limit.",
            file=sys.stderr,
        )
    return issues


# ── Findings ──────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    number: int
    title: str
    kind: str
    detail: str
    blockers: dict[int, str] = field(default_factory=dict)
    #: ``status:*`` labels currently on the issue — lets ``--fix`` compute the
    #: label swap without a second round-trip.
    statuses: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "kind": self.kind,
            "detail": self.detail,
            "blockers": self.blockers,
            "statuses": self.statuses,
        }


def _refs(numbers: Iterable[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers)


#: (issue number, title, blocker-state map, status labels) — the per-issue
#: context every finding for that issue shares.
_Context = tuple[int, str, dict[int, str], list[str]]


def _finding(context: _Context, kind: str, detail: str) -> Finding:
    number, title, blockers, statuses = context
    return Finding(number, title, kind, detail, dict(blockers), list(statuses))


def analyse(
    issues: Sequence[dict[str, Any]],
    states: dict[int, RefState],
) -> list[Finding]:
    """Turn fetched issues + resolved reference states into findings.

    Pure: every ``gh`` call happens before this point, which is what makes the
    detection logic testable without a network.
    """
    findings: list[Finding] = []

    for issue in issues:
        blockers = parse_blockers(issue.get("body"))
        statuses = status_labels(issue)
        labelled = BLOCKED_LABEL in statuses
        if not blockers and not labelled:
            continue

        resolved = [states[n] for n in blockers if n in states]
        detail_map = {ref.number: ref.describe() for ref in resolved}
        context = (issue["number"], issue["title"], detail_map, statuses)

        if labelled and not blockers:
            # The label asserts a dependency the body never names in a form
            # anything can check. Every list below derives from `blockers`, so
            # nothing else can fire and the issue would leave the sweep clean —
            # which is exactly how #495 (priority:p1) sat blocked for six days
            # after its blocker merged, its dependency written as prose.
            #
            # Reported, never fixed: the right answer is status:ready if the
            # dependency is done, or status:needs-info if it was never an open
            # issue in this repo, and that is a judgement about where the
            # dependency lives. --fix must not guess between them.
            #
            # The `continue` is a semantic stop, not an optimisation: with no
            # references there is nothing further to say about this issue's
            # dependencies, and a later check that fired here anyway would be
            # reporting on refs it does not have. It is behaviour-neutral
            # against the checks below as they stand today (all of them derive
            # from `blockers`), so no test can distinguish keeping it from
            # dropping it — it is here to keep that true.
            findings.append(
                _finding(
                    context,
                    UNVERIFIABLE_BLOCK,
                    f"labelled {BLOCKED_LABEL} but names no blocker as "
                    f"'{BLOCKED_BY_FORM}' — the dependency cannot be verified, so this "
                    f"issue can never be auto-unblocked. Restate it in that form, or use "
                    f"{NEEDS_INFO_LABEL} if the dependency is not an open issue in this repo",
                )
            )
            continue

        missing = [ref.number for ref in resolved if not ref.exists]
        if missing:
            findings.append(
                _finding(
                    context,
                    DANGLING_REF,
                    f"references non-existent issue(s): {_refs(missing)}",
                )
            )

        real = [ref for ref in resolved if ref.exists]
        open_blockers = [ref.number for ref in real if ref.is_open]
        closed = [ref for ref in real if not ref.is_open]

        if labelled and real and not open_blockers:
            findings.append(
                _finding(
                    context,
                    STALE_BLOCK,
                    f"labelled {BLOCKED_LABEL} but every named blocker is resolved "
                    f"({_refs(ref.number for ref in closed)}) — should be {READY_LABEL}",
                )
            )

        if not labelled and open_blockers:
            findings.append(
                _finding(
                    context,
                    MISSING_BLOCK,
                    f"names open blocker(s) ({_refs(open_blockers)}) but is not labelled "
                    f"{BLOCKED_LABEL} — an agent could pick it up and stall",
                )
            )

        not_planned = [ref.number for ref in closed if ref.is_not_planned]
        if not_planned:
            findings.append(
                _finding(
                    context,
                    NOT_PLANNED_BLOCKER,
                    f"blocker(s) closed as NOT PLANNED ({_refs(not_planned)}) — the work "
                    "usually moved elsewhere; re-point the dependency rather than "
                    "assuming it is done",
                )
            )

    return findings


def scan(repo: str, limit: int = DEFAULT_ISSUE_LIMIT) -> list[Finding]:
    """Fetch the backlog and report every stale-dependency finding."""
    issues = fetch_open_issues(repo, limit)
    refs = {n for issue in issues for n in parse_blockers(issue.get("body"))}
    return analyse(issues, fetch_ref_states(repo, sorted(refs)))


# ── Fixes ─────────────────────────────────────────────────────────────────────


def apply_fixes(repo: str, findings: Sequence[Finding]) -> int:
    """Flip labels for the unambiguous cases. Returns the number of issues changed.

    Only ``stale-block`` and ``missing-block`` are mechanical. ``not-planned-blocker``
    needs a human to decide where the dependency moved, ``dangling-ref`` needs
    someone to work out what the author meant, and ``unverifiable-block`` needs
    someone to decide whether the issue is now ``status:ready`` or was always
    ``status:needs-info`` — a guess there either dispatches genuinely blocked
    work or buries a dependency the label was right about.

    The taxonomy allows exactly one ``status:*`` label, so a fix that adds one
    removes the others it displaces.

    An issue can be *both* stale-blocked and blocked on a ``NOT_PLANNED``
    closure — every blocker is closed, but one of them was abandoned rather
    than delivered. Clearing that block is exactly the "assume it's done"
    mistake ``not-planned-blocker`` exists to catch, so those issues are
    reported and skipped, not fixed.
    """
    needs_judgement = {f.number for f in findings if f.kind == NOT_PLANNED_BLOCKER}
    changed = 0
    for finding in findings:
        others = [s for s in finding.statuses if s != BLOCKED_LABEL]
        if finding.kind == STALE_BLOCK:
            if finding.number in needs_judgement:
                print(
                    f"  skipped #{finding.number}: a blocker was closed NOT PLANNED — "
                    "re-point the dependency by hand"
                )
                continue
            args = ["--remove-label", BLOCKED_LABEL]
            if others:
                # Already carries some other status — removing the stale block
                # is the whole fix; don't invent a second status label.
                summary = f"removed {BLOCKED_LABEL} (kept {', '.join(others)})"
            else:
                args += ["--add-label", READY_LABEL]
                summary = f"{BLOCKED_LABEL} -> {READY_LABEL}"
        elif finding.kind == MISSING_BLOCK:
            args = ["--add-label", BLOCKED_LABEL]
            if others:
                args += ["--remove-label", ",".join(others)]
                summary = f"added {BLOCKED_LABEL}, removed {', '.join(others)}"
            else:
                summary = f"added {BLOCKED_LABEL}"
        else:
            continue

        _gh("issue", "edit", str(finding.number), "--repo", repo, *args)
        print(f"  fixed #{finding.number}: {summary}")
        changed += 1
    return changed


# ── Reporting ─────────────────────────────────────────────────────────────────


def format_report(findings: Sequence[Finding]) -> str:
    """Render findings as the human-readable report."""
    if not findings:
        return "No stale blockers. Backlog dependency labels are consistent."

    lines: list[str] = []
    for kind in _ORDER:
        group = [f for f in findings if f.kind == kind]
        if not group:
            continue
        lines.append(f"{_HEADINGS[kind]}  ({len(group)})")
        for finding in group:
            lines.append(f"  #{finding.number} {finding.title[:60]}")
            lines.append(f"      {finding.detail}")
            if finding.blockers:
                states = ", ".join(f"#{k}={v}" for k, v in finding.blockers.items())
                lines.append(f"      blockers: {states}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect Blocked by references that outlived their blocker, and "
            f"{BLOCKED_LABEL} issues naming no blocker anything can check."
        )
    )
    parser.add_argument(
        "--repo",
        help="owner/name to sweep (default: $GITHUB_REPOSITORY, else the current repo)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ISSUE_LIMIT,
        help=f"max open issues to fetch (default: {DEFAULT_ISSUE_LIMIT})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--fix",
        action="store_true",
        help=f"apply label fixes for {STALE_BLOCK} / {MISSING_BLOCK}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        repo = resolve_repo(args.repo)
        findings = scan(repo, args.limit)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        print(format_report(findings))

    if args.fix and findings:
        print("\nApplying fixes:")
        try:
            changed = apply_fixes(repo, findings)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"{changed} issue(s) relabelled.")

    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

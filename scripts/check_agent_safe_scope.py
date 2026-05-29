# Copyright (c) 2026 John Carter. All rights reserved.
"""
Mechanical scope-boundary check for `agent-safe` PRs.

Issue #77: when an `agent-safe` PR ships, its diff must stay inside the linked
issue's stated scope. This module is the single source of truth for that check;
both `code-reviewer` (pre-merge) and `.github/workflows/agent-safe-scope.yml`
(CI backstop) call into it.

Verdict levels:
- PASS   — every changed file matches the issue's "Files to touch" section or
           a clean area-label glob.
- WARN   — the issue has neither a "Files to touch" section nor a clean
           area-label mapping; the gate cannot make a confident judgement and
           defers (per issue #77 refinements §"Fail-closed → WARN on missing
           scope info"). Issue-creation-time enforcement should ensure this is
           rare.
- FAIL   — the issue has explicit scope info AND the diff strays outside it.

CLI usage:
    python scripts/check_agent_safe_scope.py --pr 123
        # fetches PR diff + linked issue via `gh`, prints verdict, exits non-zero
        # on FAIL.

    python scripts/check_agent_safe_scope.py --issue-body-file body.md \\
        --issue-labels 'ui,priority:p2,agent-safe' --diff-files-file files.txt
        # offline mode for tests / dry runs.

Gating: the CLI resolves the linked issue's labels and only runs the scope
comparison when the issue carries `agent-safe`. Non-agent-safe PRs see a
no-op PASS so the workflow can run unconditionally on every PR (the
`agent-safe` label lives on the **issue**, not the PR, per CLAUDE.md
taxonomy — gating the workflow itself on PR labels would skip nearly every
real agent-safe PR).

The library functions (`evaluate`, `parse_files_to_touch`,
`area_label_paths`, `check_scope`) do not perform the agent-safe gate
themselves — they assume the caller has already decided the check applies.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

# ── Area-label → path-glob map ────────────────────────────────────────────────
#
# Per issue #77 refinements §"Area-label fallback specifics", only bounded
# areas map cleanly to path globs. Meta-areas span the codebase and cannot be
# path-mapped — their presence triggers a WARN fall-through, not a FAIL.
#
# Both maps are ground-truthed to the live label set (issue #90). When a new
# area label lands, refresh from the live taxonomy and extend whichever map
# applies. Canonical refresh procedure:
#
#     gh api /repos/warlordofmars/agentcore-starter/labels --paginate \
#         --jq '.[].name'
#
# Filter out priority:*, size:*, status:*, type labels (`bug`, `enhancement`,
# `chore`), special labels (`agent-safe`, `epic`), and auto-injected GitHub
# labels (`dependencies`, `python`, `javascript`, `github_actions`).

BOUNDED_AREA_GLOBS: dict[str, list[str]] = {
    "api": ["src/channel/api/**", "tests/unit/test_api*.py", "tests/unit/test_agents_api.py"],
    "auth": ["src/channel/auth/**", "tests/unit/test_auth_*.py", "tests/e2e/test_auth_*.py"],
    "infra": ["infra/**"],
    "ui": ["ui/**"],
    "docs": ["docs/**", "docs-site/**", "README.md", "CHANGELOG.md"],
    "ci": [".github/workflows/**", "scripts/**"],
}

META_AREAS: set[str] = {
    "dx",
    "security",
    "reliability",
    "observability",
}


# Always-allowed paths that are universal artifacts of the PR-creation flow
# itself (release notes, changelog churn, etc.). Including them here keeps
# the gate from raising FAIL on housekeeping that every PR is allowed to do.
UNIVERSAL_ALLOWED: list[str] = [
    "CHANGELOG.md",
]


Level = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class Verdict:
    level: Level
    summary: str
    out_of_scope: tuple[str, ...] = ()
    allowed_globs: tuple[str, ...] = ()
    source: str = ""  # "files-to-touch", "area-labels", or "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "summary": self.summary,
            "out_of_scope": list(self.out_of_scope),
            "allowed_globs": list(self.allowed_globs),
            "source": self.source,
        }


# ── Parser ────────────────────────────────────────────────────────────────────


_FILES_TO_TOUCH_HEADING_RE = re.compile(
    r"^#{2,3}\s+Files to touch\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
# Accept ` `text` `, ` *text* `, or bare path tokens inside a markdown bullet.
# We only want the actual path tokens — not the "New:" / "Edit:" prose prefix.
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$", re.MULTILINE)


def _extract_files_to_touch_section(body: str) -> str | None:
    """
    Return the text of the `## Files to touch` / `### Files to touch`
    section (everything between the heading and the next heading of any
    level), or None if no such heading is present.

    Pulled out so both `parse_files_to_touch` and the prose-form shape
    detector in `evaluate` operate on the same bounded slice — neither
    should ever scan the whole issue body, which would risk pulling in
    unrelated backticks from `## Context` or `## Notes`.
    """
    if not body:
        return None

    heading_match = _FILES_TO_TOUCH_HEADING_RE.search(body)
    if not heading_match:
        return None

    section_start = heading_match.end()

    # Find the next heading of any level after our heading — that bounds the
    # section. If none, the section runs to end-of-body.
    next_match = _NEXT_HEADING_RE.search(body, pos=section_start)
    section_end = next_match.start() if next_match else len(body)
    return body[section_start:section_end]


def _section_has_prose_form_paths(section: str) -> bool:
    """
    Return True when the `## Files to touch` section contains backtick-
    quoted path-shaped tokens but no markdown bullets.

    This is the shape-mismatch signal: the issue author wrote the section
    as prose paragraphs (e.g. "Touch `src/foo.py` and `tests/test_foo.py`")
    instead of the required bullet form (`- ` / `* ` / `+ `). The parser
    only walks bullets, so a prose section parses to an empty path list and
    the gate currently fails with the generic "empty or malformed" message
    — which doesn't tell the issue author what to fix. This detector lets
    the gate emit a targeted prose-form message instead.

    "Path-shaped" applies the same heuristic the bullet-fallback path uses
    (contains `/` OR ends in a `.<letter><word-char>+` extension) — so a
    prose section like "Option B: choose `bullets`." doesn't trigger the
    prose-form branch on the bare word `bullets`.
    """
    # If the section has any bullets at all, it is NOT prose-form. The
    # bullet path is the canonical shape; an empty bullet list means the
    # bullets are present but blank, which the existing "empty or malformed"
    # message correctly describes.
    if _BULLET_RE.search(section):
        return False

    for token in _BACKTICK_TOKEN_RE.findall(section):
        token = token.strip()
        if not token:
            continue
        if "/" in token or re.search(r"\.[A-Za-z]\w+$", token):
            return True
    return False


def parse_files_to_touch(body: str) -> list[str] | None:
    """
    Extract the list of allowed paths/globs from an issue body's
    "Files to touch" section.

    Bullets containing backtick-quoted tokens are preferred — backticks are
    the unambiguous, canonical form for path markers. Bullets without
    backticks fall through to a tokenisation pass that accepts a token as a
    path if it (1) consists only of path-safe characters
    (`[\\w./@\\-+]`) AND (2) either contains `/` or ends in a file
    extension matching `\\.[A-Za-z]\\w+$` — first char of extension must be
    a letter, total extension length must be >= 2 chars. The path-safe
    character filter rejects prose fragments like `(e.g.` or `done.`; the
    letter-leading rule rejects version-like tokens like `v1.0` (`\\w`
    includes digits); the >=2-char rule rejects single-letter remnants like
    `e.g` (rstripped from `e.g.`).

    Edge case: bare top-level files with no extension (e.g. `Makefile`) are
    NOT picked up by the no-backtick fallback. The canonical workaround is
    to wrap the token in backticks (e.g. `` `Makefile` ``); these are rare
    enough in practice that requiring backticks is a small cost.

    Returns:
        - A list of path strings (may be empty) if the heading is present.
        - None if no `## Files to touch` / `### Files to touch` heading is
          found (caller falls back to area-label mapping).
    """
    section = _extract_files_to_touch_section(body)
    if section is None:
        return None

    paths: list[str] = []
    for bullet in _BULLET_RE.findall(section):
        # Prefer backtick-quoted tokens (unambiguous path markers).
        tokens = _BACKTICK_TOKEN_RE.findall(bullet)
        if tokens:
            paths.extend(t.strip() for t in tokens if t.strip())
            continue
        # Fall back to the bullet text minus a leading "New:" / "Edit:" /
        # "Modify:" prose prefix. This is best-effort; backticks are the
        # supported convention.
        cleaned = re.sub(
            r"^(New|Edit|Modify|Update|Add):\s*", "", bullet.strip(), flags=re.IGNORECASE
        )
        cleaned = cleaned.strip().rstrip(",.;")
        if not cleaned or cleaned.startswith("`"):
            continue
        # Tokenise the bullet on whitespace + common conjunctions/punctuation
        # so a bullet like `- Edit: src/a.py and src/b.py` produces two paths,
        # not one mashed-together string. Each token is then accepted only if
        # it looks like a path (has a `/` or ends in a file extension) AND
        # consists of path-safe characters only — that filter rejects prose
        # fragments like `(e.g.` or `done.` that would otherwise match the
        # extension regex and produce phantom paths the diff can't satisfy.
        for raw_token in re.split(r"[\s,]+|\band\b", cleaned):
            token = raw_token.strip().rstrip(",.;")
            if not token:
                continue
            # Path-safe character filter: only word chars, dot, slash, hyphen,
            # plus, at-sign, underscore (already in \w). Anything else (parens,
            # quotes, colons, brackets) marks the token as prose, not a path.
            if not re.fullmatch(r"[\w./@\-+]+", token):
                continue
            # A token looks like a path if it contains `/` OR ends in a file
            # extension whose first character is a letter and whose total
            # length is >= 2 characters. The letter-leading rule rejects
            # version-like tokens such as `v1.0` (`\w` includes digits); the
            # >=2-char rule rejects tokens like `e.g` (rstripped from
            # `e.g.`) that would otherwise be misclassified as paths.
            # Tokens with no extension and no `/` (e.g. `Makefile`) are
            # dropped — wrap them in backticks for explicit acceptance.
            # Trade-off: single-letter real extensions (`.c`, `.r`) are also
            # rejected by the no-backtick fallback; backticks remain the
            # canonical workaround. None exist in this repo today.
            looks_like_path = "/" in token or bool(re.search(r"\.[A-Za-z]\w+$", token))
            if looks_like_path:
                paths.append(token)

    return paths


# ── Area-label fallback ───────────────────────────────────────────────────────


def area_label_paths(labels: list[str]) -> list[str] | None:
    """
    Map area labels on the issue to their path globs.

    The repo uses bare area labels (e.g. `ui`, `api`, `auth`) per CLAUDE.md
    §Backlog labels and milestones — NOT prefixed with `area:`. This function
    accepts both forms (`ui` and `area:ui`) for forward-compatibility, but
    the bare form is canonical.

    Identifying which labels are area labels:
        Anything that matches a key in `BOUNDED_AREA_GLOBS` or `META_AREAS`
        is considered an area label. Standard non-area labels (priority:*,
        size:*, status:*, type labels like `enhancement`, special labels
        like `agent-safe`) are filtered out.

    Returns:
        - A list of globs if every area label maps to a bounded area.
        - None if the issue carries no area labels, or if any area label is a
          meta-area (per the BOUNDED_AREA_GLOBS / META_AREAS partition).
          Meta-area presence forces WARN — they cannot be path-mapped.
    """
    # Strip optional `area:` prefix; the repo uses bare names but accept both.
    candidates = [label.removeprefix("area:") for label in labels]

    # Filter to labels that are recognised as areas (bounded or meta). This
    # naturally drops priority:*, size:*, status:*, agent-safe, enhancement,
    # bug, etc.
    area_labels = [c for c in candidates if c in BOUNDED_AREA_GLOBS or c in META_AREAS]
    if not area_labels:
        return None

    globs: list[str] = []
    for area in area_labels:
        if area in META_AREAS:
            return None
        # area is in BOUNDED_AREA_GLOBS by the filter above.
        globs.extend(BOUNDED_AREA_GLOBS[area])

    return globs


# ── Scope comparator ──────────────────────────────────────────────────────────


def _matches_any(path: str, globs: list[str]) -> bool:
    for glob in globs:
        # fnmatch doesn't natively understand `**`. Translate `**` to `*` for
        # an additive match: a glob `src/channel/api/**` should match
        # `src/channel/api/foo/bar.py`. We achieve that by also matching the
        # glob with `**` collapsed to `*` — fnmatch treats `*` as "anything
        # except /" by default? No — in fnmatch `*` matches anything
        # INCLUDING separators. So `src/channel/api/*` will match
        # `src/channel/api/foo/bar.py`. Use that.
        normalised = glob.replace("**", "*")
        if fnmatch.fnmatch(path, normalised):
            return True
        # Also support directory-prefix matches: a glob like `ui/**` should
        # match `ui` itself if it ever appears.
        if glob.endswith("/**") and (path == glob[:-3] or path.startswith(glob[:-2])):
            return True
    return False


# Issue #105: implicit allowlist for co-located test files.
#
# When a source file is listed in `## Files to touch`, the scope check should
# also accept its co-located test file without requiring the issue author to
# enumerate both. Two distinct rule shapes (locked in #105's design comment):
#
# Rule A — JS/TS same-directory `.test` infix:
#     <dir>/<name>.<ext> in scope (ext ∈ {js,jsx,ts,tsx})
#         → <dir>/<name>.test.<ext>                                 implicit
#         → <dir>/__snapshots__/<name>.test.<ext>.snap              implicit
#     Same-directory only — does NOT cross directories.
#
# Rule B — Python basename-keyed, fixed `tests/unit/` root:
#     <anywhere>/<basename>.py in scope
#         → tests/unit/test_<basename>.py                           implicit
#         → tests/unit/<any nested>/test_<basename>.py              implicit
#     Cross-directory by design — pytest convention puts tests in a fixed
#     root regardless of source location (covers src/channel/** and
#     scripts/** without enumerating either).
#
# Forward-direction only: listing source implicitly accepts test, but
# listing test does NOT implicitly accept source. Production code changes
# always require explicit listing — preserves the property that the issue
# body documents the real production-code surface of change.

_JS_TS_EXTS: tuple[str, ...] = ("js", "jsx", "ts", "tsx")
# Source filename pattern for Rule A. The extension alternation is built
# from `_JS_TS_EXTS` so the tuple is the single source of truth — extending
# the rule to a new extension is a one-line change. Must NOT already be a
# test file — `<name>.<ext>` where `<name>` does not end with `.test`.
_JS_TS_SOURCE_RE = re.compile(
    rf"^(?P<name>.+?)\.(?P<ext>{'|'.join(re.escape(ext) for ext in _JS_TS_EXTS)})$"
)
# Glob metacharacters — when present in a `## Files to touch` entry, that
# entry is a wildcard, not a concrete source path; skip implicit derivation
# to avoid widening scope to `tests/unit/test_*.py` (effectively all unit
# tests). Concrete paths are the input contract for the implicit allowlist.
_GLOB_METACHARS = re.compile(r"[*?\[]")


def _implicit_test_paths(source_path: str) -> list[str]:
    """
    Given a concrete source path from `## Files to touch`, return the list
    of co-located test paths (and snapshot paths) that should be implicitly
    accepted alongside it.

    Returns an empty list when:
      - the source path is not a recognised source file (e.g. `.md`,
        `.yml`),
      - the path already looks like a test file (e.g. `foo.test.js` or
        `tests/unit/test_foo.py` — forward-direction only),
      - the entry contains glob metacharacters (`*`, `?`, `[`) — globs are
        not concrete paths and would derive an over-broad implicit set
        (e.g. `src/channel/**/*.py` would map to `tests/unit/test_**.py`).
    """
    derived: list[str] = []

    # Use the path exactly as provided. Path-prefix normalisation (e.g.
    # stripping a leading "./") would diverge from how `check_scope` and
    # `_matches_any` handle `allowed_globs` and `diff_files` — neither
    # normalises — and would create asymmetric matching where the source
    # path itself is rejected as out-of-scope while its derived test path
    # still passes. Keep one consistent semantics.
    path = source_path

    # Glob entries are not concrete paths — skip them to avoid over-broad
    # implicit derivations (a `*.py` entry would otherwise map to
    # `tests/unit/test_*.py`, effectively allowing every unit test).
    if _GLOB_METACHARS.search(path):
        return derived

    # Rule A — JS/TS same-directory `.test` infix. Split the path into a
    # directory prefix (with trailing slash, or "" for bare top-level files)
    # and a filename so we can prepend the prefix back onto derived paths.
    if "/" in path:
        directory, _, filename = path.rpartition("/")
        dir_prefix = f"{directory}/"
    else:
        filename = path
        dir_prefix = ""

    js_match = _JS_TS_SOURCE_RE.match(filename)
    if js_match:
        name = js_match.group("name")
        ext = js_match.group("ext")
        # Skip files that are already test files (e.g. `foo.test.js` —
        # name ends with `.test`). Forward-direction only: tests don't
        # imply more tests.
        if not name.endswith(".test"):
            derived.append(f"{dir_prefix}{name}.test.{ext}")
            derived.append(f"{dir_prefix}__snapshots__/{name}.test.{ext}.snap")

    # Rule B — Python basename-keyed, fixed `tests/unit/` root.
    if path.endswith(".py"):
        basename = path.rpartition("/")[2].removesuffix(".py")
        # Skip files that are already test files (basename starts with
        # `test_`). Forward-direction only.
        if basename and not basename.startswith("test_"):
            # Flat: tests/unit/test_<basename>.py
            derived.append(f"tests/unit/test_{basename}.py")
            # Nested: any path under tests/unit/ ending in
            # test_<basename>.py — modelled with a glob.
            derived.append(f"tests/unit/**/test_{basename}.py")

    return derived


def _expand_implicit_tests(allowed_globs: list[str]) -> list[str]:
    """
    Expand `allowed_globs` with implicit co-located test paths derived from
    each source file in scope. Purely additive — original entries are
    preserved unchanged. Returns the expanded list.
    """
    expanded = list(allowed_globs)
    for entry in allowed_globs:
        expanded.extend(_implicit_test_paths(entry))
    return expanded


def check_scope(diff_files: list[str], allowed_globs: list[str]) -> list[str]:
    """
    Return the list of files in `diff_files` that do NOT match any of the
    `allowed_globs`. Universal-allowed paths are always considered in-scope.

    Implicit co-located test paths are derived from each source entry in
    `allowed_globs` (see `_implicit_test_paths`) and added to the effective
    allowlist before matching. This is forward-direction only — source in
    scope implies test in scope, but not vice versa.
    """
    effective = _expand_implicit_tests(list(allowed_globs)) + UNIVERSAL_ALLOWED
    return [f for f in diff_files if not _matches_any(f, effective)]


# ── Top-level evaluator ───────────────────────────────────────────────────────


def evaluate(
    issue_body: str | None,
    issue_labels: list[str],
    diff_files: list[str],
) -> Verdict:
    """
    Decide whether the PR's diff stays inside the issue's stated scope.

    Resolution order:
    1. If the issue body has a "Files to touch" section, use it.
    2. Else if the issue has bounded area labels (and no meta-areas), use
       their globs.
    3. Else WARN — the gate cannot make a confident judgement.
    """
    files_to_touch = parse_files_to_touch(issue_body or "")

    if files_to_touch is not None:
        if not files_to_touch:
            # Heading present but parser returned no paths — fail closed. The
            # zero-paths condition covers three shapes: (1) bullets present
            # but empty, (2) bullets present with only prose content (no
            # backticks, no path-shaped tokens), (3) prose-form section with
            # backtick-quoted paths but no bullets at all. A section with no
            # entries can't gate a diff; falling back to WARN would let any
            # diff pass scope-check trivially by leaving the section blank.
            #
            # Issue #130: distinguish shape (3) from shapes (1) and (2). When
            # the section contains backtick-quoted path-shaped tokens but no
            # bullets, the author wrote prose instead of bullets — emit a
            # targeted message naming the shape and showing the fix, rather
            # than the generic "empty or malformed" string that doesn't tell
            # them what to do. Shapes (1) and (2) keep the generic message
            # because the bullet shape was correct; the content was the
            # problem.
            section = _extract_files_to_touch_section(issue_body or "") or ""
            if _section_has_prose_form_paths(section):
                return Verdict(
                    level="FAIL",
                    summary=(
                        '"Files to touch" section is present but uses prose form; '
                        "rewrite as a bullet list (each path on its own line "
                        "prefixed with `-`). Bullet form is required so the parser "
                        "can extract path entries unambiguously."
                    ),
                    source="files-to-touch",
                )
            return Verdict(
                level="FAIL",
                summary=(
                    '"Files to touch" section present but empty or malformed; '
                    "refusing to apply soft fallback."
                ),
                source="files-to-touch",
            )
        out = check_scope(diff_files, files_to_touch)
        if out:
            return Verdict(
                level="FAIL",
                summary=(
                    f"PR diff includes {len(out)} file(s) outside the issue's "
                    '"Files to touch" scope.'
                ),
                out_of_scope=tuple(out),
                allowed_globs=tuple(files_to_touch),
                source="files-to-touch",
            )
        return Verdict(
            level="PASS",
            summary='All changed files match the issue\'s "Files to touch" scope.',
            allowed_globs=tuple(files_to_touch),
            source="files-to-touch",
        )

    area_globs = area_label_paths(issue_labels)
    if area_globs is not None:
        out = check_scope(diff_files, area_globs)
        if out:
            return Verdict(
                level="FAIL",
                summary=(f"PR diff includes {len(out)} file(s) outside the area-label path map."),
                out_of_scope=tuple(out),
                allowed_globs=tuple(area_globs),
                source="area-labels",
            )
        return Verdict(
            level="PASS",
            summary="All changed files match the issue's area-label path map.",
            allowed_globs=tuple(area_globs),
            source="area-labels",
        )

    return Verdict(
        level="WARN",
        summary=(
            'Issue body has no "Files to touch" section and no clean area-label '
            "mapping (either no area labels, or area labels include a meta-area "
            "that cannot be path-mapped). Cannot verify scope mechanically."
        ),
        source="none",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _gh(args: list[str]) -> str:
    """Run `gh <args>` and return stdout. Raises on non-zero exit."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _fetch_pr_context(pr: int) -> tuple[str | None, list[str], list[str], int | None]:
    """
    Returns (issue_body, issue_labels, diff_files, issue_number).
    issue_body is None if the PR doesn't link an issue.
    """
    pr_json = _gh(["pr", "view", str(pr), "--json", "body,title"])
    pr_data = json.loads(pr_json)
    pr_text = (pr_data.get("body") or "") + "\n" + (pr_data.get("title") or "")

    # Find the linked issue via Closes/Fixes/Resolves #N (case-insensitive).
    issue_match = re.search(
        r"(?:closes|fixes|resolves)\s+#(\d+)",
        pr_text,
        re.IGNORECASE,
    )
    issue_body: str | None = None
    issue_labels: list[str] = []
    issue_num: int | None = None
    if issue_match:
        issue_num = int(issue_match.group(1))
        issue_json = _gh(["issue", "view", str(issue_num), "--json", "body,labels"])
        issue_data = json.loads(issue_json)
        issue_body = issue_data.get("body") or ""
        issue_labels = [label["name"] for label in issue_data.get("labels", [])]

    diff_text = _gh(["pr", "diff", str(pr), "--name-only"])
    diff_files = [line.strip() for line in diff_text.splitlines() if line.strip()]
    return issue_body, issue_labels, diff_files, issue_num


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr",
        type=int,
        help="PR number — fetches body, labels, and diff via `gh`.",
    )
    parser.add_argument("--issue-body-file", type=str, help="Offline mode: issue body file.")
    parser.add_argument(
        "--issue-labels",
        type=str,
        default="",
        help="Offline mode: comma-separated labels.",
    )
    parser.add_argument(
        "--diff-files-file",
        type=str,
        help="Offline mode: file with one changed-file path per line.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    args = parser.parse_args(argv)

    if args.pr:
        issue_body, issue_labels, diff_files, issue_num = _fetch_pr_context(args.pr)
        context_note = (
            f"PR #{args.pr} (linked issue: #{issue_num})" if issue_num else f"PR #{args.pr}"
        )
    else:
        if not (args.issue_body_file and args.diff_files_file):
            parser.error("provide --pr OR (--issue-body-file AND --diff-files-file)")
        with open(args.issue_body_file, encoding="utf-8") as f:
            issue_body = f.read()
        with open(args.diff_files_file, encoding="utf-8") as f:
            diff_files = [line.strip() for line in f if line.strip()]
        issue_labels = [label.strip() for label in args.issue_labels.split(",") if label.strip()]
        context_note = "(offline mode)"

    # Gate: this check only applies when the linked issue carries `agent-safe`.
    # The label lives on the issue per CLAUDE.md taxonomy; the workflow runs
    # unconditionally on every PR and lets us decide here so we can resolve
    # the linked-issue's labels rather than the PR's.
    if "agent-safe" not in issue_labels:
        if args.json:
            print(
                json.dumps(
                    {
                        "level": "PASS",
                        "summary": "PR's linked issue is not labelled `agent-safe` — scope check skipped.",
                        "out_of_scope": [],
                        "allowed_globs": [],
                        "source": "skip",
                    },
                    indent=2,
                )
            )
        else:
            print(f"agent-safe scope check {context_note}")
            print("  verdict: PASS (skipped)")
            print(
                "  reason : linked issue is not labelled `agent-safe` — scope check does not apply."
            )
        return 0

    verdict = evaluate(issue_body, issue_labels, diff_files)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))
    else:
        print(f"agent-safe scope check {context_note}")
        print(f"  source : {verdict.source or 'n/a'}")
        print(f"  verdict: {verdict.level}")
        print(f"  summary: {verdict.summary}")
        if verdict.allowed_globs:
            print("  allowed scope:")
            for g in verdict.allowed_globs:
                print(f"    - {g}")
        if verdict.out_of_scope:
            print("  out-of-scope files:")
            for f in verdict.out_of_scope:
                print(f"    - {f}")

    return 1 if verdict.level == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())

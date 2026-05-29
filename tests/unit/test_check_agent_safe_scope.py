# Copyright (c) 2026 John Carter. All rights reserved.
"""
Unit tests for scripts/check_agent_safe_scope.py.

Covers all six matrix cases (a)–(f) from issue #77 refinements
§"Validation test matrix":

(a) No Files-to-touch, area:ui, diff in ui/src/        — PASS
(b) No Files-to-touch, area:dx                          — WARN (meta-area)
(c) Files-to-touch listed, diff inside scope            — PASS
(d) Files-to-touch listed, diff strays outside (PR #76) — FAIL
(e) Multiple area labels — verify mapping precedence
(f) Both heading levels (## and ###)
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check_agent_safe_scope.py"

# scripts/ isn't a package; load the module by file path so tests don't rely
# on PYTHONPATH being set.
_spec = importlib.util.spec_from_file_location("check_agent_safe_scope", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
scope_check = importlib.util.module_from_spec(_spec)
sys.modules["check_agent_safe_scope"] = scope_check
_spec.loader.exec_module(scope_check)


# ── Parser tests ──────────────────────────────────────────────────────────────


def test_parse_files_to_touch_h2_heading():
    body = """## Context

Some context.

## Files to touch

- New: `.claude/skills/foo/SKILL.md`
- Edit: `CLAUDE.md`

## Acceptance criteria

- [ ] thing
"""
    assert scope_check.parse_files_to_touch(body) == [
        ".claude/skills/foo/SKILL.md",
        "CLAUDE.md",
    ]


def test_parse_files_to_touch_h3_heading():
    """Case (f) — both ## and ### heading levels parse."""
    body = """### Files to touch

- New: `.claude/skills/bar/SKILL.md`

### Acceptance criteria
- [ ] thing
"""
    assert scope_check.parse_files_to_touch(body) == [
        ".claude/skills/bar/SKILL.md",
    ]


def test_parse_files_to_touch_missing_returns_none():
    body = "## Context\n\nNothing scoped.\n\n## Acceptance criteria\n- [ ] thing"
    assert scope_check.parse_files_to_touch(body) is None


def test_parse_files_to_touch_empty_body_returns_none():
    assert scope_check.parse_files_to_touch("") is None


def test_parse_files_to_touch_multiple_backticks_in_one_bullet():
    body = """## Files to touch

- Edit: `src/channel/api/foo.py` and `tests/unit/test_foo.py`

## Next
"""
    assert scope_check.parse_files_to_touch(body) == [
        "src/channel/api/foo.py",
        "tests/unit/test_foo.py",
    ]


def test_parse_files_to_touch_bare_path_without_backticks():
    body = """## Files to touch

- New: src/channel/foo.py
- Edit: CLAUDE.md

## Next
"""
    # Bare paths should still be picked up — slashes or known top-level files.
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    assert "src/channel/foo.py" in parsed
    assert "CLAUDE.md" in parsed


def test_parse_files_to_touch_bare_paths_multiple_per_bullet():
    """A single bullet containing multiple unquoted paths must yield each
    path separately, not one mashed-together string. Real-world example:
        - Edit: src/a.py and src/b.py
    Without tokenisation this would parse as one phantom path that never
    matches any diff entry, causing false FAILs."""
    body = """## Files to touch

- Edit: src/channel/foo.py and src/channel/bar.py
- New: docs/a.md, docs/b.md

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    assert "src/channel/foo.py" in parsed
    assert "src/channel/bar.py" in parsed
    assert "docs/a.md" in parsed
    assert "docs/b.md" in parsed
    # No mashed-together token should sneak through.
    assert all(" " not in p for p in parsed)


def test_parse_files_to_touch_skips_descriptive_bullets():
    body = """## Files to touch

- Just some prose without paths
- `.claude/skills/qux/SKILL.md`

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed == [".claude/skills/qux/SKILL.md"]


def test_parse_files_to_touch_heading_at_end_of_body():
    body = "## Files to touch\n\n- `foo.py`\n"
    assert scope_check.parse_files_to_touch(body) == ["foo.py"]


def test_parse_files_to_touch_empty_section():
    body = "## Files to touch\n\n## Next\n"
    # Heading present but no bullets → empty list (NOT None).
    assert scope_check.parse_files_to_touch(body) == []


# ── Area-label fallback tests ─────────────────────────────────────────────────


def test_area_label_paths_bounded_area_ui_bare_label():
    """Repo uses bare area labels (no `area:` prefix) per CLAUDE.md.
    Real-world labels look like `ui`, `api`, `auth` — not `area:ui`."""
    globs = scope_check.area_label_paths(["ui", "priority:p2", "size:s"])
    assert globs is not None
    assert any(g.startswith("ui/") for g in globs)


def test_area_label_paths_bounded_area_docs():
    """The live area label is `docs` (per CLAUDE.md taxonomy — issue #46
    reconciled the historical `documentation` label to the canonical
    short-form `docs` matching the rest of the area taxonomy)."""
    globs = scope_check.area_label_paths(["docs", "priority:p2"])
    assert globs is not None
    assert any(g.startswith("docs/") or g.startswith("docs-site/") for g in globs)


def test_area_label_paths_bounded_area_prefixed_form_also_accepted():
    """Forward-compatibility: `area:ui` form works too."""
    globs = scope_check.area_label_paths(["area:ui", "priority:p2"])
    assert globs is not None
    assert any(g.startswith("ui/") for g in globs)


def test_area_label_paths_meta_area_returns_none():
    """Case (b) — `dx` is a meta-area, no clean path map → None → WARN."""
    assert scope_check.area_label_paths(["dx", "priority:p2"]) is None


def test_area_label_paths_no_area_labels_returns_none():
    assert scope_check.area_label_paths(["priority:p1", "size:m"]) is None


def test_area_label_paths_filters_out_non_area_labels():
    """Standard non-area labels (status, type, agent-safe) are filtered out
    before the area check runs. Real issues carry many such labels."""
    globs = scope_check.area_label_paths(
        [
            "ui",
            "agent-safe",
            "enhancement",
            "docs",
            "status:ready",
            "priority:p2",
            "size:s",
        ]
    )
    assert globs is not None
    assert any(g.startswith("ui/") for g in globs)


def test_area_label_paths_multiple_bounded_areas_combine():
    """Case (e) — multiple area labels combine into a union of globs."""
    globs = scope_check.area_label_paths(["api", "auth"])
    assert globs is not None
    assert any(g.startswith("src/channel/api/") for g in globs)
    assert any(g.startswith("src/channel/auth/") for g in globs)


def test_area_label_paths_meta_in_mix_disables_fallback():
    """If any area label is a meta-area, fall through to WARN — partial
    bounded-area coverage isn't reliable enough."""
    assert scope_check.area_label_paths(["ui", "dx"]) is None


def test_area_label_paths_unrecognised_label_is_ignored():
    """A label that's neither in BOUNDED_AREA_GLOBS nor META_AREAS (e.g.
    a custom workflow label) is not treated as an area label at all."""
    assert scope_check.area_label_paths(["custom-label", "priority:p2"]) is None


# ── check_scope tests ─────────────────────────────────────────────────────────


def test_check_scope_all_in_scope():
    out = scope_check.check_scope(
        diff_files=["ui/src/foo.jsx", "ui/src/bar.jsx"],
        allowed_globs=["ui/**"],
    )
    assert out == []


def test_check_scope_some_out_of_scope():
    out = scope_check.check_scope(
        diff_files=["ui/src/foo.jsx", "src/channel/api/bad.py"],
        allowed_globs=["ui/**"],
    )
    assert out == ["src/channel/api/bad.py"]


def test_check_scope_universal_allowed_paths_pass():
    """CHANGELOG.md is universal-allowed regardless of scope."""
    out = scope_check.check_scope(
        diff_files=["ui/src/foo.jsx", "CHANGELOG.md"],
        allowed_globs=["ui/**"],
    )
    assert out == []


def test_check_scope_glob_with_double_star():
    out = scope_check.check_scope(
        diff_files=["src/channel/api/deep/nested/file.py"],
        allowed_globs=["src/channel/api/**"],
    )
    assert out == []


def test_check_scope_exact_path_match():
    out = scope_check.check_scope(
        diff_files=[".claude/skills/foo/SKILL.md"],
        allowed_globs=[".claude/skills/foo/SKILL.md"],
    )
    assert out == []


# ── End-to-end evaluator tests (the six matrix cases) ────────────────────────


def test_case_a_no_files_to_touch_area_ui_diff_in_ui_passes():
    """(a) No Files-to-touch, area `ui`, diff in ui/src/ — PASS."""
    body = "## Context\n\nNothing.\n"
    labels = ["ui", "agent-safe"]  # bare label form, as used in the repo
    diff = ["ui/src/components/Foo.jsx"]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "PASS"
    assert v.source == "area-labels"


def test_case_b_no_files_to_touch_area_dx_warns():
    """(b) No Files-to-touch, area `dx` (meta) — WARN."""
    body = "## Context\n\nNothing.\n"
    labels = ["dx", "agent-safe"]  # bare label form
    diff = ["scripts/foo.py"]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "WARN"
    assert v.source == "none"


def test_case_c_files_to_touch_in_scope_passes():
    """(c) Files-to-touch listed, diff inside scope — PASS."""
    body = """## Files to touch

- New: `.claude/skills/foo/SKILL.md`

## Acceptance criteria
"""
    labels = ["agent-safe"]
    diff = [".claude/skills/foo/SKILL.md"]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "PASS"
    assert v.source == "files-to-touch"


def test_case_d_files_to_touch_strays_fails_pr76_scenario():
    """(d) Files-to-touch listed, diff strays outside (the PR #76 case) — FAIL."""
    body = """## Files to touch

- New: `.claude/skills/react-component/SKILL.md`
- New: `.claude/skills/react-component/example.jsx`

## Acceptance criteria
"""
    labels = ["agent-safe", "ui"]
    diff = [
        ".claude/skills/react-component/SKILL.md",
        ".claude/skills/react-component/example.jsx",
        ".claude/agents/code-reviewer.md",  # ride-along
        "CLAUDE.md",  # ride-along
    ]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "FAIL"
    assert v.source == "files-to-touch"
    assert ".claude/agents/code-reviewer.md" in v.out_of_scope
    assert "CLAUDE.md" in v.out_of_scope
    assert ".claude/skills/react-component/SKILL.md" not in v.out_of_scope


def test_case_e_multiple_area_labels_mapping_precedence():
    """(e) Multiple area labels — globs union, files matching either pass."""
    body = "## Context\n\nNo files-to-touch section.\n"
    labels = ["api", "auth", "agent-safe"]
    diff = [
        "src/channel/api/foo.py",
        "src/channel/auth/oauth.py",
    ]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "PASS"
    assert v.source == "area-labels"


def test_case_e_multiple_areas_with_out_of_scope_file_fails():
    body = "## Context\n\nNo files-to-touch section.\n"
    labels = ["api", "auth", "agent-safe"]
    diff = [
        "src/channel/api/foo.py",
        "ui/src/components/Foo.jsx",  # out of scope for api+auth
    ]
    v = scope_check.evaluate(body, labels, diff)
    assert v.level == "FAIL"
    assert "ui/src/components/Foo.jsx" in v.out_of_scope


def test_case_f_h2_heading_in_evaluator():
    """(f) H2 heading → parsed correctly by the full evaluator."""
    body = """## Files to touch

- `src/channel/foo.py`

## More
"""
    v = scope_check.evaluate(body, ["agent-safe"], ["src/channel/foo.py"])
    assert v.level == "PASS"


def test_case_f_h3_heading_in_evaluator():
    """(f) H3 heading → parsed correctly by the full evaluator."""
    body = """### Files to touch

- `src/channel/foo.py`

### More
"""
    v = scope_check.evaluate(body, ["agent-safe"], ["src/channel/foo.py"])
    assert v.level == "PASS"


# ── Additional safety-net tests ───────────────────────────────────────────────


def test_evaluate_warns_when_no_scope_info_anywhere():
    body = "## Context\n\nNo info.\n"
    v = scope_check.evaluate(body, ["priority:p2"], ["src/channel/foo.py"])
    assert v.level == "WARN"
    assert v.source == "none"


def test_evaluate_fails_when_files_to_touch_section_is_empty():
    """Empty Files-to-touch section fails closed — falling back to WARN
    would let any diff pass by leaving the section blank."""
    body = "## Files to touch\n\n## Next\n"
    v = scope_check.evaluate(body, ["agent-safe"], ["foo.py"])
    assert v.level == "FAIL"
    assert v.source == "files-to-touch"


def test_evaluate_handles_none_body():
    """A PR linked to no issue → body=None → WARN."""
    v = scope_check.evaluate(None, [], ["foo.py"])
    assert v.level == "WARN"


def test_verdict_to_dict_serialises_cleanly():
    body = """## Files to touch

- `foo.py`
"""
    v = scope_check.evaluate(body, [], ["foo.py", "bar.py"])
    d = v.to_dict()
    assert d["level"] == "FAIL"
    assert d["out_of_scope"] == ["bar.py"]
    assert d["source"] == "files-to-touch"
    assert d["allowed_globs"] == ["foo.py"]


def test_universal_allowed_changelog_passes_in_files_to_touch_mode():
    body = """## Files to touch

- `src/channel/foo.py`
"""
    v = scope_check.evaluate(body, [], ["src/channel/foo.py", "CHANGELOG.md"])
    assert v.level == "PASS"


# ── Issue #90 edge-case regression tests ──────────────────────────────────────


def test_fix1_bare_top_level_file_with_extension_is_accepted():
    """Issue #90 Fix 1: a bare token (no slash, no backticks) with a file
    extension is accepted as a path. Previously only a hard-coded allowlist
    of top-level files (CLAUDE.md, README.md, CHANGELOG.md) passed; new
    top-level files like `tasks.py` or `pyproject.toml` were silently
    dropped, causing false FAILs for legitimate diffs."""
    body = """## Files to touch

- Edit: tasks.py
- New: pyproject.toml

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    assert "tasks.py" in parsed
    assert "pyproject.toml" in parsed


def test_fix1_prose_fragments_with_dots_are_not_treated_as_paths():
    """Issue #90 Fix 1 hardening: tokens with non-path characters (parens,
    quotes, etc.) must NOT pass the heuristic, even if they end in
    `\\.\\w+`. Without the path-safe character filter, fragments like
    `(e.g.` or `v1.0)` would be accepted as phantom paths, then force a
    false FAIL because no diff file matches them. Realistic example:
        - Edit: src/foo.py (e.g. for handling auth)"""
    body = """## Files to touch

- Edit: src/foo.py (e.g. for handling auth)
- Edit: src/bar.py and similar files (v1.0)

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    # Real paths still picked up.
    assert "src/foo.py" in parsed
    assert "src/bar.py" in parsed
    # Prose fragments must NOT appear.
    for token in parsed:
        assert "(" not in token, f"prose fragment {token!r} leaked through"
        assert ")" not in token, f"prose fragment {token!r} leaked through"
    # Specific common abbreviations that previously slipped through.
    assert not any("e.g" in t for t in parsed)
    assert not any(t.startswith("v1.") for t in parsed)


def test_fix1_version_like_tokens_are_not_treated_as_paths():
    """Issue #90 Fix 1 hardening (Copilot iter 2): tokens like `v1.0`,
    `v1.2`, or `2.10` would pass the path-safe character filter (only word
    chars + dot) AND match `\\.\\w+$` because `\\w` includes digits. The
    extension regex is tightened to `\\.[A-Za-z]\\w+$` — the first char
    after the dot must be a letter and the extension total length must be
    >=2 chars — rejecting version-like tokens.

    Real paths (`tasks.py`, `pyproject.toml`, `package-lock.json`) still
    pass — every realistic file extension starts with a letter."""
    body = """## Files to touch

- Edit: src/foo.py for v1.0 release
- New: pyproject.toml bumped to 2.10

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    # Real paths picked up.
    assert "src/foo.py" in parsed
    assert "pyproject.toml" in parsed
    # Version-like tokens must NOT be classified as paths.
    assert "v1.0" not in parsed
    assert "2.10" not in parsed


def test_fix1_unparenthesised_abbreviations_are_not_treated_as_paths():
    """Issue #90 Fix 1 hardening (Copilot iter 3): tokens like `e.g.` in
    prose without parens get rstripped to `e.g`, which would pass both the
    path-safe filter AND the letter-leading extension regex (`.g` starts
    with `g`). The extension rule is tightened to require >=2 chars after
    the dot, rejecting `.g` while still accepting `.py`, `.md`, `.js`."""
    body = """## Files to touch

- Edit: src/foo.py e.g. for handling auth
- Edit: src/bar.py i.e. the main entry point

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    # Real paths picked up.
    assert "src/foo.py" in parsed
    assert "src/bar.py" in parsed
    # Abbreviations must NOT be classified as paths.
    assert "e.g" not in parsed
    assert "i.e" not in parsed


def test_fix1_bare_top_level_file_without_extension_is_rejected():
    """Issue #90 Fix 1 documented edge case: tokens without `/` and without
    a file extension (e.g. `Makefile`) still drop. Backticks are the
    canonical workaround; this test pins the documented behaviour so the
    docstring stays honest."""
    body = """## Files to touch

- Edit: Makefile

## Next
"""
    parsed = scope_check.parse_files_to_touch(body)
    assert parsed is not None
    assert "Makefile" not in parsed
    # Backticks remain the canonical workaround for extension-less files.
    body_with_backticks = """## Files to touch

- Edit: `Makefile`

## Next
"""
    parsed_bt = scope_check.parse_files_to_touch(body_with_backticks)
    assert parsed_bt == ["Makefile"]


def test_fix2_pruned_areas_no_longer_mapped():
    """Issue #90 Fix 2: ground-truth taxonomy. Pruned BOUNDED_AREA_GLOBS
    keys (`mcp`, `sdk`) and pruned META_AREAS members (`compliance`,
    `marketing`, etc.) must no longer resolve. An issue carrying only a
    pruned label falls through to WARN, exposing it to issue-creation-time
    enforcement instead of being silently swept under a dead mapping."""
    # Pruned bounded areas
    assert "mcp" not in scope_check.BOUNDED_AREA_GLOBS
    assert "sdk" not in scope_check.BOUNDED_AREA_GLOBS
    # Pruned meta areas
    for pruned in (
        "compliance",
        "marketing",
        "growth",
        "seo",
        "design",
        "ops",
        "performance",
        "ux",
        "a11y",
    ):
        assert pruned not in scope_check.META_AREAS, f"{pruned!r} should be pruned"
    # An issue with only a pruned area label falls through to None (WARN).
    assert scope_check.area_label_paths(["mcp", "priority:p2"]) is None
    assert scope_check.area_label_paths(["compliance", "priority:p2"]) is None


# ── Issue #105: implicit allowlist for co-located test files ─────────────────
#
# Two distinct rule shapes (locked in #105's design comment):
#   Rule A — JS/TS same-directory `.test` infix.
#   Rule B — Python basename-keyed, fixed `tests/unit/` root.
# Forward-direction only: source in scope implies test in scope, but not
# vice versa.


def test_implicit_test_rule_a_js_co_located_test_passes():
    """Rule A — JS source listed → JS co-located test implicitly accepted.
    AC4 regression for PR #104: ui/src/api.js + ui/src/api.test.js touched
    together passes scope check without listing the test explicitly."""
    body = """## Files to touch

- `ui/src/api.js`

## Next
"""
    diff = ["ui/src/api.js", "ui/src/api.test.js"]
    v = scope_check.evaluate(body, ["agent-safe", "ui"], diff)
    assert v.level == "PASS"
    assert v.source == "files-to-touch"


def test_implicit_test_rule_a_tsx_co_located_test_passes():
    """Rule A — TSX source listed → TSX co-located test implicitly accepted."""
    body = """## Files to touch

- `ui/src/components/Button.tsx`

## Next
"""
    diff = ["ui/src/components/Button.tsx", "ui/src/components/Button.test.tsx"]
    v = scope_check.evaluate(body, ["agent-safe", "ui"], diff)
    assert v.level == "PASS"


def test_implicit_test_rule_b_python_nested_test_passes():
    """Rule B — Python source listed → nested test under tests/unit/
    implicitly accepted. tests/unit/api/test_main.py for src/channel/api/main.py."""
    body = """## Files to touch

- `src/channel/api/main.py`

## Next
"""
    diff = ["src/channel/api/main.py", "tests/unit/api/test_main.py"]
    v = scope_check.evaluate(body, ["agent-safe", "api"], diff)
    assert v.level == "PASS"


def test_implicit_test_rule_b_python_flat_test_passes():
    """Rule B — Python source listed → flat tests/unit/test_<name>.py
    implicitly accepted. Cross-directory: scripts/check_agent_safe_scope.py
    → tests/unit/test_check_agent_safe_scope.py."""
    body = """## Files to touch

- `scripts/check_agent_safe_scope.py`

## Next
"""
    diff = [
        "scripts/check_agent_safe_scope.py",
        "tests/unit/test_check_agent_safe_scope.py",
    ]
    v = scope_check.evaluate(body, ["agent-safe", "ci"], diff)
    assert v.level == "PASS"


def test_implicit_test_forward_direction_only_js():
    """Negative — Rule A is forward-direction only. Listing the test alone
    does NOT implicitly accept the source. Production code changes always
    require explicit listing."""
    body = """## Files to touch

- `ui/src/api.test.js`

## Next
"""
    diff = ["ui/src/api.test.js", "ui/src/api.js"]
    v = scope_check.evaluate(body, ["agent-safe", "ui"], diff)
    assert v.level == "FAIL"
    assert "ui/src/api.js" in v.out_of_scope


def test_implicit_test_forward_direction_only_python():
    """Negative — Rule B is forward-direction only. Listing the test alone
    does NOT implicitly accept the source. Production code changes always
    require explicit listing."""
    body = """## Files to touch

- `tests/unit/test_main.py`

## Next
"""
    diff = ["tests/unit/test_main.py", "src/channel/api/main.py"]
    v = scope_check.evaluate(body, ["agent-safe", "api"], diff)
    assert v.level == "FAIL"
    assert "src/channel/api/main.py" in v.out_of_scope


def test_implicit_test_rule_a_does_not_cross_directories():
    """Negative — Rule A is same-directory only. ui/src/api.js in scope
    does NOT implicitly accept tests/api.test.js (different directory)."""
    body = """## Files to touch

- `ui/src/api.js`

## Next
"""
    diff = ["ui/src/api.js", "tests/api.test.js"]
    v = scope_check.evaluate(body, ["agent-safe", "ui"], diff)
    assert v.level == "FAIL"
    assert "tests/api.test.js" in v.out_of_scope


def test_implicit_test_helper_returns_expected_paths_for_js():
    """Direct test of `_implicit_test_paths` for JS — exposes Rule A's
    derived paths so future maintainers can see the full surface."""
    derived = scope_check._implicit_test_paths("ui/src/api.js")
    assert "ui/src/api.test.js" in derived
    assert "ui/src/__snapshots__/api.test.js.snap" in derived


def test_implicit_test_helper_returns_expected_paths_for_python():
    """Direct test of `_implicit_test_paths` for Python — exposes Rule B's
    derived paths so future maintainers can see the full surface."""
    derived = scope_check._implicit_test_paths("src/channel/api/main.py")
    assert "tests/unit/test_main.py" in derived
    assert "tests/unit/**/test_main.py" in derived


def test_implicit_test_helper_skips_non_source_paths():
    """Non-source paths (markdown, yml, paths that already look like tests)
    yield no implicit derivations."""
    assert scope_check._implicit_test_paths("CLAUDE.md") == []
    assert scope_check._implicit_test_paths(".github/workflows/ci.yml") == []
    # Already a test file — JS form.
    assert scope_check._implicit_test_paths("ui/src/api.test.js") == []
    # Already a test file — Python form.
    assert scope_check._implicit_test_paths("tests/unit/test_main.py") == []


def test_implicit_test_helper_skips_glob_entries():
    """Glob entries in `## Files to touch` must not derive implicit tests
    — `src/channel/api/*.py` would otherwise map to `tests/unit/test_*.py`
    (effectively every unit test). The implicit allowlist keys off
    concrete paths only."""
    assert scope_check._implicit_test_paths("src/channel/api/*.py") == []
    assert scope_check._implicit_test_paths("ui/src/components/*.jsx") == []
    assert scope_check._implicit_test_paths("scripts/?.py") == []
    assert scope_check._implicit_test_paths("ui/src/[abc].js") == []


def test_implicit_test_glob_in_files_to_touch_does_not_widen_scope():
    """End-to-end: a glob entry in `## Files to touch` must not silently
    widen scope to all unit tests via implicit derivation. Only the literal
    glob is in scope; an unrelated test file that happens to match the
    derived `tests/unit/test_*.py` pattern is rejected."""
    body = """## Files to touch

- `src/channel/api/*.py`

## Next
"""
    diff = ["src/channel/api/users.py", "tests/unit/test_unrelated.py"]
    v = scope_check.evaluate(body, ["agent-safe", "api"], diff)
    assert v.level == "FAIL"
    assert "tests/unit/test_unrelated.py" in v.out_of_scope


def test_fix2_kept_areas_still_resolve():
    """Issue #90 Fix 2: the kept area labels (`api`, `auth`, `infra`, `ui`,
    `docs`, `ci`) must still resolve to their globs after the
    prune. Live meta-areas (`dx`, `security`, `reliability`,
    `observability`) must still trigger the meta-area WARN fall-through.

    Note: `documentation` was renamed to `docs` per issue #46 to align with
    the CLAUDE.md taxonomy."""
    # Bounded areas still map.
    for area in ("api", "auth", "infra", "ui", "docs", "ci"):
        assert area in scope_check.BOUNDED_AREA_GLOBS, f"{area!r} should still map"
    api_globs = scope_check.area_label_paths(["api", "priority:p2"])
    assert api_globs is not None
    assert any(g.startswith("src/channel/api/") for g in api_globs)
    # Live meta-areas still WARN-fall-through.
    for meta in ("dx", "security", "reliability", "observability"):
        assert meta in scope_check.META_AREAS, f"{meta!r} should still be a meta-area"
        assert scope_check.area_label_paths([meta, "priority:p2"]) is None


# ── Issue #130: prose-form shape-mismatch detection ──────────────────────────
#
# When `## Files to touch` is present but the author wrote it as prose
# paragraphs (no markdown bullets) containing backtick-quoted paths, the
# parser returns []. Pre-#130 the gate emitted the generic "empty or
# malformed" message — which doesn't tell the issue author what to fix.
# After #130 the gate detects this specific shape and emits a targeted
# message naming "prose form" and showing the bullet-form fix.


def test_evaluate_emits_prose_form_message_when_section_uses_prose():
    """The exact shape that bit PR #129 (issue #46): a `## Files to touch`
    section written as prose paragraphs containing backtick-quoted paths,
    no bullets. The gate must emit the prose-form-specific message instead
    of the generic 'empty or malformed' string."""
    body = """## Files to touch

Option A: rename `scripts/check_agent_safe_scope.py` and update
`tests/unit/test_check_agent_safe_scope.py` to match. Option B:
update `CLAUDE.md` only.

## Acceptance criteria
"""
    v = scope_check.evaluate(body, ["agent-safe"], ["scripts/check_agent_safe_scope.py"])
    assert v.level == "FAIL"
    assert v.source == "files-to-touch"
    assert "prose form" in v.summary
    # The fix instruction must name the bullet character so the issue
    # author can act on the message without leaving the CI log.
    assert "bullet list" in v.summary
    assert "`-`" in v.summary
    # The generic "empty or malformed" string must NOT appear — that is
    # the message we are explicitly replacing for this shape.
    assert "empty or malformed" not in v.summary


def test_evaluate_keeps_generic_empty_message_when_section_truly_empty():
    """When `## Files to touch` is present but the section body is whitespace-
    only (no bullets, no backticks, no prose paths), the original generic
    'empty or malformed' message is the right one — there's nothing to point
    at as 'prose form'."""
    body = "## Files to touch\n\n## Acceptance criteria\n"
    v = scope_check.evaluate(body, ["agent-safe"], ["foo.py"])
    assert v.level == "FAIL"
    assert v.source == "files-to-touch"
    assert "empty or malformed" in v.summary
    assert "prose form" not in v.summary


def test_evaluate_keeps_generic_message_when_section_has_bullets_but_no_paths():
    """A bullet section that fails to yield any paths (e.g. bullets contain
    only prose, no backticks and no path-shaped tokens) is the
    'empty-after-parse' case, not the prose-form case. The detector requires
    NO bullets to fire — bullets-but-no-paths keeps the generic message so
    we don't mislead the author into thinking they used the wrong shape
    when they actually used the right shape but no parseable content."""
    body = """## Files to touch

- This is just prose with no real paths
- Another bullet without any path-shaped tokens

## Acceptance criteria
"""
    v = scope_check.evaluate(body, ["agent-safe"], ["foo.py"])
    assert v.level == "FAIL"
    assert v.source == "files-to-touch"
    assert "empty or malformed" in v.summary
    assert "prose form" not in v.summary


def test_section_has_prose_form_paths_returns_true_for_prose_with_backtick_paths():
    section = "Touch `src/foo.py` and `tests/test_foo.py` to make this work.\n"
    assert scope_check._section_has_prose_form_paths(section) is True


def test_section_has_prose_form_paths_returns_false_when_bullets_present():
    """Even if bullets contain no paths, the presence of bullets means the
    author used the canonical shape — not the prose-form failure mode."""
    section = "\n- some bullet without paths\n"
    assert scope_check._section_has_prose_form_paths(section) is False


def test_section_has_prose_form_paths_returns_false_for_prose_without_paths():
    """Backtick-quoted tokens that aren't path-shaped (no `/`, no extension)
    don't count — e.g. `bullets`, `prose`, `option A`. Without this filter
    the detector would fire on any section that mentions a backtick-quoted
    word."""
    section = "Choose `bullets` over `prose` for clarity.\n"
    assert scope_check._section_has_prose_form_paths(section) is False


def test_section_has_prose_form_paths_returns_false_for_empty_section():
    assert scope_check._section_has_prose_form_paths("") is False
    assert scope_check._section_has_prose_form_paths("   \n\n  ") is False


def test_section_has_prose_form_paths_recognises_extension_only_paths():
    """Top-level files like `tasks.py` (extension, no slash) count as
    path-shaped — same heuristic as the bullet-fallback parser."""
    section = "Edit `tasks.py` to add a new invoke task.\n"
    assert scope_check._section_has_prose_form_paths(section) is True


def test_extract_files_to_touch_section_returns_section_text():
    body = """## Context

intro

## Files to touch

- `foo.py`

## Acceptance criteria
"""
    section = scope_check._extract_files_to_touch_section(body)
    assert section is not None
    assert "- `foo.py`" in section
    # Must NOT bleed into the next section.
    assert "Acceptance criteria" not in section
    # Must NOT include the heading line itself. Test by line — a section
    # body could legitimately mention the words "Files to touch" inside its
    # content (e.g. an issue note), so substring-checking the whole section
    # would be brittle. Instead, assert no non-empty line in the returned
    # text is the heading itself.
    for line in section.splitlines():
        assert not re.match(r"^#{2,3}\s+Files to touch\s*$", line, re.IGNORECASE), (
            f"heading line leaked into extracted section: {line!r}"
        )


def test_extract_files_to_touch_section_returns_none_when_heading_absent():
    assert scope_check._extract_files_to_touch_section("## Context\n\nstuff\n") is None
    assert scope_check._extract_files_to_touch_section("") is None
    assert scope_check._extract_files_to_touch_section(None) is None  # type: ignore[arg-type]

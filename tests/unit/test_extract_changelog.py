# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for scripts/extract_changelog.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "extract_changelog.py"

# Load the script as a module — scripts/ is not a package.
_spec = importlib.util.spec_from_file_location("extract_changelog", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
extract_changelog = importlib.util.module_from_spec(_spec)
sys.modules["extract_changelog"] = extract_changelog
_spec.loader.exec_module(extract_changelog)


SAMPLE = """# Changelog

## [Unreleased]

### Added

- A new thing that's in flight.

## 0.20.0 — 2026-05-12

### Added

- Conversation header pinning (#100).
- Sidebar rename + delete dialogs (#101).

### Fixed

- Recall hook cache invalidation on JWT swap (#102).

## 0.19.1 — 2026-04-30

### Fixed

- CSP nonce reuse in static error pages (#88).
"""


def test_extract_returns_section_body():
    body = extract_changelog.extract("0.20.0", SAMPLE)
    assert "### Added" in body
    assert "Conversation header pinning" in body
    assert "Recall hook cache" in body
    # No content from neighbouring sections
    assert "CSP nonce reuse" not in body
    assert "A new thing that's in flight" not in body
    # Heading itself not included
    assert "## 0.20.0" not in body


def test_extract_tolerates_leading_v():
    bare_body = extract_changelog.extract("0.20.0", SAMPLE)
    v_body = extract_changelog.extract("v0.20.0", SAMPLE)
    assert v_body == bare_body


def test_extract_last_section_runs_to_eof():
    body = extract_changelog.extract("0.19.1", SAMPLE)
    assert "CSP nonce reuse" in body


def test_extract_missing_version_returns_empty():
    assert extract_changelog.extract("9.9.9", SAMPLE) == ""


def test_extract_unreleased_section():
    # The matcher tolerates non-version text in the heading as long as the
    # version literal appears — but a bare "[Unreleased]" heading has no
    # version, so requesting "Unreleased" should miss.
    assert extract_changelog.extract("Unreleased", SAMPLE) == ""


def test_extract_partial_version_does_not_match_longer():
    # Word-boundary anchor: requesting "0.20" must NOT match "0.20.0".
    assert extract_changelog.extract("0.20", SAMPLE) == ""

# Copyright (c) 2026 John Carter. All rights reserved.
"""Extract a single version section from CHANGELOG.md.

Used by the ``release`` GitHub Actions job to populate the body of the
GitHub release with the curated CHANGELOG section instead of an
auto-generated commit dump. Falls back to printing nothing (exit 0)
when no section matches the requested version — the workflow then
defaults to ``--generate-notes`` so the release still gets a body.

Usage:
    python scripts/extract_changelog.py 0.20.0
    python scripts/extract_changelog.py 0.20.0 > release-notes.md

The matcher looks for a level-2 heading whose text contains the
literal version string (with or without a leading ``v``). Sections
end at the next level-2 heading or end-of-file. The matched section
is printed *without* the heading line itself, since the GitHub
release UI renders its own version title.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def extract(version: str, changelog_text: str) -> str:
    """Return the body of the section for ``version`` (without its heading).

    Empty string if no matching section. ``version`` may include or
    omit a leading ``v`` — the matcher tolerates either form.

    The matcher requires the version literal to start with a digit and
    end with no trailing digit or dot, so e.g. ``0.20`` does NOT match a
    ``## 0.20.0`` heading. ``Unreleased`` is rejected (no leading digit)
    so it can't be promoted to a release accidentally.
    """
    bare = version.lstrip("v")
    if not bare or not bare[0].isdigit():
        return ""
    # Stricter than \b: forbid an adjacent digit or dot on either side
    # so 0.20 doesn't match 0.20.0.
    heading_re = re.compile(
        r"^## .*?(?<![\d.])" + re.escape(bare) + r"(?![\d.]).*$",
        re.MULTILINE,
    )
    match = heading_re.search(changelog_text)
    if match is None:
        return ""
    start = match.end()
    next_heading = re.search(r"^## ", changelog_text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(changelog_text)
    return changelog_text[start:end].strip("\n")


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: extract_changelog.py <version>",
            file=sys.stderr,
        )
        return 2
    version = sys.argv[1]
    if not CHANGELOG.exists():
        return 0
    text = CHANGELOG.read_text(encoding="utf-8")
    section = extract(version, text)
    if section:
        sys.stdout.write(section)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

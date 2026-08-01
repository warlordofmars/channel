# Copyright (c) 2026 John Carter. All rights reserved.
"""
Detect drift between the checked-in branch-protection snapshot and the
live GitHub repo state.

Issue #52: `infra/branch-protection.expected.json` is the canonical record
of the template repo's branch protection (`main`, `development`) and merge
settings. If someone edits protection in the GitHub UI without updating
the snapshot, onboarding's Phase 1.5 verification step starts producing
false negatives for forks. This script is the periodic CI guardrail that
catches that drift.

Usage:
    # Live mode — fetch from GitHub via `gh` and compare against the snapshot
    python scripts/check_branch_protection_drift.py
    python scripts/check_branch_protection_drift.py --owner foo --repo bar

    # Offline / test mode — read live state from a JSON file
    python scripts/check_branch_protection_drift.py --live-file live.json

    # Compare against an alternative snapshot path
    python scripts/check_branch_protection_drift.py \\
        --snapshot infra/branch-protection.expected.json

Exit codes:
    0 — no drift
    1 — drift detected (diff printed to stdout)
    2 — usage / fetch error

Why a custom comparator (and not `diff`):
    The snapshot includes derived/volatile fields — `url`, `contexts_url`,
    and per-check `app_id` — that aren't part of the protection contract.
    Comparing raw JSON would false-trigger on every fork (different `url`
    prefix) and on legitimate GitHub-side rotations (e.g. app_id changes).
    The `_normalize_*` helpers strip these fields so the diff reflects
    only the things a fork operator can act on.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
DEFAULT_SNAPSHOT = ROOT / "infra" / "branch-protection.expected.json"

# Fields stripped from the snapshot and the live response before comparison.
# These are derived / volatile and not part of the protection contract.
_VOLATILE_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "url",
    "contexts_url",
)
# Fields stripped from each entry in `required_status_checks.checks`.
# `app_id` rotates on the GitHub side without operator action.
_VOLATILE_CHECK_FIELDS: tuple[str, ...] = ("app_id",)

# Repo-settings keys we care about. The `gh api repos/{owner}/{repo}` endpoint
# returns ~80 fields; we only diff the merge-policy subset that the protection
# contract pins (per issue #50).
_REPO_SETTING_KEYS: tuple[str, ...] = (
    "allow_auto_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_squash_merge",
    "default_branch",
    "delete_branch_on_merge",
)


# ── Normalisation ─────────────────────────────────────────────────────────────


def _strip_keys(obj: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return a shallow copy of `obj` with `keys` removed."""
    return {k: v for k, v in obj.items() if k not in keys}


def _normalize_branch_protection(protection: dict[str, Any]) -> dict[str, Any]:
    """
    Strip volatile/derived fields from a branch-protection payload so the
    comparison only reflects fields a repo operator can act on.

    Mutations:
    - Top-level `url` removed.
    - `required_status_checks.url` and `required_status_checks.contexts_url`
      removed.
    - Each entry in `required_status_checks.checks` has `app_id` removed.
    - `required_signatures.url` and `enforce_admins.url` removed (these
      sub-objects only have `enabled` + `url`; the `enabled` flag is the
      contract).
    - `required_status_checks.checks` is sorted by `context` so the diff
      doesn't false-trigger on GitHub returning the same set in different
      order (e.g. after an app re-install).
    """
    out = _strip_keys(protection, _VOLATILE_TOP_LEVEL_FIELDS)

    rsc = out.get("required_status_checks")
    if isinstance(rsc, dict):
        rsc_clean = _strip_keys(rsc, _VOLATILE_TOP_LEVEL_FIELDS)
        checks = rsc_clean.get("checks")
        if isinstance(checks, list):
            cleaned_checks = [
                _strip_keys(c, _VOLATILE_CHECK_FIELDS) if isinstance(c, dict) else c
                for c in checks
            ]
            cleaned_checks.sort(
                key=lambda c: c.get("context", "") if isinstance(c, dict) else ""
            )
            rsc_clean["checks"] = cleaned_checks
        # Sort `contexts` for the same reason — GitHub may reorder.
        contexts = rsc_clean.get("contexts")
        if isinstance(contexts, list):
            rsc_clean["contexts"] = sorted(contexts)
        out["required_status_checks"] = rsc_clean

    # Sub-objects with the `{enabled, url}` shape: drop `url`.
    for sub_key in ("required_signatures", "enforce_admins"):
        sub = out.get(sub_key)
        if isinstance(sub, dict):
            out[sub_key] = _strip_keys(sub, _VOLATILE_TOP_LEVEL_FIELDS)

    return out


def _normalize_repo_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce a full repo-settings payload to the keys we pin
    (`_REPO_SETTING_KEYS`). The live repos endpoint returns ~80 fields;
    keeping only what we contract on prevents diff noise from features the
    snapshot doesn't track.
    """
    return {k: settings[k] for k in _REPO_SETTING_KEYS if k in settings}


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a full state payload (snapshot or live-collected) into the
    canonical comparable shape. Both sides of the diff go through this so
    the comparator can treat them as plain dicts.

    Expected shape (matches `infra/branch-protection.expected.json`):
        {
          "repo_settings": { ... },
          "branches": {
            "main": { ...protection... },
            "development": { ...protection... }
          }
        }
    """
    # Defensive: `_load_json_file` will happily parse a top-level list or
    # string, and `_gh_json` could in principle return a non-dict on a
    # weird API edge case. Treat any non-dict input as the canonical
    # empty state shape so the rest of the function can safely use
    # `.get(...)`; the diff will then report missing repo settings /
    # branches relative to the other side instead of crashing with an
    # AttributeError.
    if not isinstance(state, dict):
        return {"repo_settings": {}, "branches": {}}
    repo_settings = state.get("repo_settings", {})
    branches = state.get("branches", {})
    # Defensive: a malformed snapshot (e.g. `branches: null` or `branches: []`)
    # would crash `.items()` here. Treat anything that isn't a dict as an
    # empty branches map — the diff comparator will then surface the
    # missing branches as "missing in live state" / "present in live state
    # but not in snapshot" entries rather than blowing up the script.
    branches_iter = branches.items() if isinstance(branches, dict) else ()
    return {
        "repo_settings": _normalize_repo_settings(repo_settings)
        if isinstance(repo_settings, dict)
        else {},
        "branches": {
            name: _normalize_branch_protection(payload)
            if isinstance(payload, dict)
            else payload
            for name, payload in branches_iter
        },
    }


# ── Diff ──────────────────────────────────────────────────────────────────────


def _diff(expected: Any, actual: Any, path: str = "") -> list[str]:
    """
    Walk two JSON-shaped values and return a list of human-readable
    drift descriptions. Empty list means the inputs are equal.

    The format intentionally mirrors `jq`-style paths so a CI log reader
    can locate the field in the snapshot file directly.
    """
    diffs: list[str] = []

    if type(expected) is not type(actual):
        diffs.append(
            f"{path or '<root>'}: type mismatch — expected "
            f"{type(expected).__name__}, got {type(actual).__name__}"
        )
        return diffs

    if isinstance(expected, dict):
        # Type-narrow `actual` for static checkers; the runtime check above
        # (`type(expected) is not type(actual)`) already enforces this.
        actual_dict: dict[str, Any] = actual
        expected_keys = set(expected.keys())
        actual_keys = set(actual_dict.keys())

        for missing in sorted(expected_keys - actual_keys):
            missing_path = f"{path}.{missing}" if path else missing
            diffs.append(f"{missing_path}: missing in live state")
        for extra in sorted(actual_keys - expected_keys):
            extra_path = f"{path}.{extra}" if path else extra
            diffs.append(f"{extra_path}: present in live state but not in snapshot")
        for shared in sorted(expected_keys & actual_keys):
            sub_path = f"{path}.{shared}" if path else shared
            diffs.extend(_diff(expected[shared], actual_dict[shared], sub_path))
        return diffs

    if isinstance(expected, list):
        actual_list: list[Any] = actual
        if len(expected) != len(actual_list):
            diffs.append(
                f"{path}: list length differs — expected {len(expected)} items, "
                f"got {len(actual_list)}"
            )
            return diffs
        for idx, (e, a) in enumerate(zip(expected, actual_list, strict=False)):
            diffs.extend(_diff(e, a, f"{path}[{idx}]"))
        return diffs

    if expected != actual:
        diffs.append(f"{path}: expected {expected!r}, got {actual!r}")
    return diffs


# ── Live-state collection ─────────────────────────────────────────────────────


def _gh_json(args: list[str]) -> Any:
    """Run `gh <args>` and return its parsed JSON stdout."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        # `gh api` can return non-JSON on transient failures, auth issues,
        # or rate-limit responses. Surface as RuntimeError so the CLI's
        # outer `except RuntimeError` branch maps it to exit code 2.
        raise RuntimeError(
            f"gh {' '.join(args)} returned non-JSON output: {exc}"
        ) from exc


def collect_live_state(
    owner: str,
    repo: str,
    branches: list[str],
) -> dict[str, Any]:
    """
    Build the live-state payload by hitting GitHub for each branch's
    protection plus the repo-settings endpoint. Output shape matches
    `infra/branch-protection.expected.json` so `normalize_state` accepts
    it unchanged.
    """
    state: dict[str, Any] = {
        "repo_settings": _gh_json(["api", f"/repos/{owner}/{repo}"]),
        "branches": {},
    }
    for branch in branches:
        state["branches"][branch] = _gh_json(
            ["api", f"/repos/{owner}/{repo}/branches/{branch}/protection"]
        )
    return state


# ── CLI ───────────────────────────────────────────────────────────────────────


def _load_json_file(path: Path) -> Any:
    """
    Read a JSON file from disk. File-access errors (path is a directory,
    permission denied, etc.) and JSON parse errors are surfaced as
    `RuntimeError` so `main` can map them to the documented exit code 2
    alongside other usage errors.
    """
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse {path} as JSON: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"failed to read JSON file {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="Path to the expected snapshot JSON (default: infra/branch-protection.expected.json).",
    )
    parser.add_argument(
        "--owner",
        default="warlordofmars",
        help="GitHub repo owner (default: warlordofmars).",
    )
    parser.add_argument(
        "--repo",
        default="channel",
        help="GitHub repo name (default: channel).",
    )
    parser.add_argument(
        "--live-file",
        type=Path,
        help="Read live state from a JSON file instead of calling `gh` "
        "(useful for offline tests).",
    )
    args = parser.parse_args(argv)

    if not args.snapshot.exists():
        print(f"error: snapshot file not found at {args.snapshot}", file=sys.stderr)
        return 2

    try:
        expected = _load_json_file(args.snapshot)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # The script's central decision — which branches to fetch from the
    # live API — is keyed off the snapshot's `branches` map. If that map
    # is missing or shaped wrong, drift would be silently undetectable
    # (we'd fetch zero branches and report "OK"). Refuse to proceed.
    expected_branches = expected.get("branches") if isinstance(expected, dict) else None
    if not isinstance(expected_branches, dict) or not expected_branches:
        print(
            f"error: snapshot at {args.snapshot} has no usable `branches` map "
            "— refusing to proceed (without a branch list, drift cannot be checked).",
            file=sys.stderr,
        )
        return 2

    if args.live_file is not None:
        if not args.live_file.exists():
            print(f"error: live-file not found at {args.live_file}", file=sys.stderr)
            return 2
        try:
            live = _load_json_file(args.live_file)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            live = collect_live_state(
                args.owner,
                args.repo,
                branches=sorted(expected_branches.keys()),
            )
        except RuntimeError as exc:
            print(f"error: failed to fetch live state — {exc}", file=sys.stderr)
            return 2

    normalized_expected = normalize_state(expected)
    normalized_live = normalize_state(live)

    diffs = _diff(normalized_expected, normalized_live)

    if not diffs:
        # Display the snapshot path relative to the repo root when possible
        # (cleaner CI log) and absolute otherwise (paths under tmp dirs in
        # tests, or arbitrary `--snapshot` paths from operators).
        try:
            display_path = args.snapshot.relative_to(ROOT)
        except ValueError:
            display_path = args.snapshot
        print(
            "branch-protection drift check: OK — "
            f"snapshot at {display_path} matches live state."
        )
        return 0

    print(
        f"branch-protection drift check: DRIFT DETECTED ({len(diffs)} difference(s)).\n"
    )
    for d in diffs:
        print(f"  {d}")
    print(
        "\nIf this drift is intentional (you changed protection or merge "
        "settings on purpose), refresh the snapshot:\n"
        "  gh api /repos/{owner}/{repo} > /tmp/repo.json\n"
        "  gh api /repos/{owner}/{repo}/branches/main/protection > /tmp/main.json\n"
        "  gh api /repos/{owner}/{repo}/branches/development/protection > /tmp/dev.json\n"
        "  # then merge the relevant fields into infra/branch-protection.expected.json"
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - thin CLI shim
    sys.exit(main())

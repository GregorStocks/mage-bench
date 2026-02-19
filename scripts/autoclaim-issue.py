#!/usr/bin/env python3
"""Automatically pick and claim the highest-priority unclaimed issue.

Merges origin/master, lists issues, filters out claimed and non-autoclaimable
ones, picks the highest-priority unclaimed issue, and claims it.

Usage:
    autoclaim-issue.py

Exit codes:
    0  Claimed successfully (prints issue filename and PR number)
    1  No claimable issues available
    2  Failed after max retries (all picks were race-lost)
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ISSUES_DIR = Path("issues")
MAX_RETRIES = 5


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def merge_master() -> None:
    """Fetch and merge origin/master into the current branch."""
    subprocess.run(["git", "fetch", "origin"], check=True)
    subprocess.run(["git", "merge", "origin/master", "--no-edit"], check=True)


def load_issues() -> list[tuple[str, int, str]]:
    """Load issues sorted by priority. Returns (stem, priority, title)."""
    assert ISSUES_DIR.is_dir(), f"Issues directory not found: {ISSUES_DIR}"
    issues = []
    for f in sorted(ISSUES_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        if data.get("not_autoclaimable"):
            continue
        issues.append((f.stem, data.get("priority", 999), data["title"]))
    issues.sort(key=lambda i: i[1])
    return issues


def get_claimed() -> set[str]:
    """Get set of already-claimed issue filenames from open PRs."""
    result = run(
        ["gh", "pr", "list", "--state", "open", "--json", "body", "--jq", ".[].body"]
    )
    assert result.returncode == 0, f"gh pr list failed: {result.stderr}"
    claimed = set()
    for line in result.stdout.splitlines():
        m = re.search(r"<!-- claim: (.+?) -->", line.strip().replace("\r", ""))
        if m:
            claimed.add(m.group(1))
    return claimed


def claim(issue_stem: str) -> bool:
    """Attempt to claim an issue. Returns True on success, False on race loss.

    Raises SystemExit on bad input (exit code 2) — no point retrying.
    """
    result = subprocess.run(
        ["uv", "run", "python", "scripts/claim-issue.py", issue_stem],
    )
    if result.returncode == 2:
        sys.exit(2)
    return result.returncode == 0


def pick_unclaimed(issues: list[tuple[str, int, str]], claimed: set[str]) -> str | None:
    """Pick the first unclaimed issue from highest priority tier."""
    for stem, _priority, _title in issues:
        if stem not in claimed:
            return stem
    return None


def main() -> None:
    merge_master()

    for attempt in range(MAX_RETRIES):
        issues = load_issues()
        claimed = get_claimed()
        pick = pick_unclaimed(issues, claimed)

        if pick is None:
            print("No claimable issues available.", file=sys.stderr)
            sys.exit(1)

        print(f"Attempt {attempt + 1}/{MAX_RETRIES}: claiming {pick}")
        if claim(pick):
            print(f"Claimed: {pick}")
            sys.exit(0)

        print(f"Lost race for {pick}, retrying...", file=sys.stderr)

    print(f"Failed after {MAX_RETRIES} attempts.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

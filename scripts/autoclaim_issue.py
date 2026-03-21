#!/usr/bin/env python3
"""Claim an issue — either a specific one or the highest-priority unclaimed one.

Merges origin/master first, then either claims the named issue or picks the
highest-priority unclaimed non-blocked issue.

Usage:
    autoclaim_issue.py                  Auto-pick highest priority issue
    autoclaim_issue.py <issue-name>     Claim a specific issue (bypasses blocked)

Exit codes:
    0  Claimed successfully (prints issue filename and PR number)
    1  No claimable issues available / named issue already claimed
    2  Failed after max retries (all picks were race-lost)
"""

import re
import subprocess
import sys
from pathlib import Path

from scripts.issue_files import issue_path, issue_stem, iter_issue_files, load_issue

ISSUES_DIR = Path("issues")
MAX_RETRIES = 5


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def merge_master() -> None:
    """Fetch and merge origin/master into the current branch."""
    subprocess.run(["git", "fetch", "origin"], check=True)
    subprocess.run(["git", "merge", "origin/master", "--no-edit"], check=True)


def load_issues() -> list[tuple[str, int, str]]:
    """Load issues sorted by priority. Returns (stem, priority, title)."""
    assert ISSUES_DIR.is_dir(), f"Issues directory not found: {ISSUES_DIR}"
    issues = []
    for f in iter_issue_files(ISSUES_DIR):
        data = load_issue(f)
        if data.get("blocked"):
            continue
        issues.append((f.stem, data.get("priority", 999), data["title"]))
    issues.sort(key=lambda i: i[1])
    return issues


def get_claimed() -> set[str]:
    """Get set of already-claimed issue filenames from open PRs.

    Merged and closed PRs are excluded — merging a PR that intentionally
    leaves an issue file open (for follow-up work) should not block the
    issue from being re-claimed. Closing a PR without merging likewise
    releases the claim.
    """
    claimed = set()
    result = run(
        ["gh", "pr", "list", "--state", "open", "--json", "body", "--jq", ".[].body"]
    )
    assert result.returncode == 0, f"gh pr list failed: {result.stderr}"
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
        ["uv", "run", "python", "scripts/claim_issue.py", issue_stem],
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


def claim_specific(issue_name: str) -> None:
    """Claim a specific issue by name, bypassing blocked."""
    stem = issue_stem(issue_name)
    path = issue_path(ISSUES_DIR, issue_name)
    assert path.exists(), f"Issue file not found: {path}"

    claimed = get_claimed()
    if stem in claimed:
        print(f"Issue {stem} is already claimed by another PR.", file=sys.stderr)
        sys.exit(1)

    if claim(stem):
        print(f"Claimed: {stem}")
        sys.exit(0)

    print(f"Failed to claim {stem}.", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    merge_master()

    if len(sys.argv) > 1:
        claim_specific(sys.argv[1])
        return

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

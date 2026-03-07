#!/usr/bin/env python3
"""Claim an issue by creating a draft PR. Lowest PR number wins ties.

Usage:
    claim-issue.py <issue-filename>   Claim an issue
    claim-issue.py --list             List already-claimed issues

Exit codes:
    0  Claimed successfully (or --list succeeded)
    1  Already claimed or lost race
    2  Bad input
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ISSUES_DIR = Path("issues")
RACE_SETTLE_SECONDS = 5


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def list_claimed() -> list[str]:
    """Return list of claimed issue filenames from open and merged PRs.

    Closed (abandoned) PRs are intentionally excluded — closing a PR
    without merging releases the claim so another agent can pick it up.
    """
    claimed = []
    for state in ("open", "merged"):
        result = run(
            ["gh", "pr", "list", "--state", state, "--json", "body", "--jq", ".[].body"]
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip().replace("\r", "")
            m = re.search(r"<!-- claim: (.+?) -->", line)
            if m:
                claimed.append(m.group(1))
    return sorted(set(claimed))


def _race_winner(issue: str) -> str | None:
    """Return the lowest PR number claiming *issue*, or None."""
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,body",
            "--jq",
            f'[.[] | select(.body | test("<!-- claim: {issue} -->")) | .number] | sort | .[0]',
        ]
    )
    winner = result.stdout.strip()
    if winner and winner != "null":
        return winner
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: claim-issue.py <issue-filename>", file=sys.stderr)
        print("       claim-issue.py --list", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "--list":
        for name in list_claimed():
            print(name)
        sys.exit(0)

    issue = sys.argv[1].removesuffix(".json")

    issue_path = ISSUES_DIR / f"{issue}.json"
    if not issue_path.exists():
        print(f"Error: {issue_path} not found", file=sys.stderr)
        sys.exit(2)

    data = json.loads(issue_path.read_text())
    title = data["title"]

    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch == "master":
        print(
            "Error: can't claim an issue from master — switch to a feature branch first",
            file=sys.stderr,
        )
        sys.exit(2)

    # Ensure at least one commit ahead of master so the PR can be created
    log_result = run(["git", "log", "origin/master..HEAD", "--oneline"])
    if not log_result.stdout.strip():
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"Claim: {title}"], check=True
        )

    # Push current branch
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)

    # Reuse existing PR for this branch if one exists, otherwise create a new one
    existing = run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        ]
    )
    existing_pr = existing.stdout.strip()

    if existing_pr:
        our_pr = existing_pr
        subprocess.run(
            [
                "gh",
                "pr",
                "edit",
                our_pr,
                "--title",
                f"Solve: {title}",
                "--body",
                f"<!-- claim: {issue} -->",
            ],
            check=True,
        )
        print(f"Updated existing PR #{our_pr} for {issue}")
    else:
        result = run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                "master",
                "--title",
                f"Solve: {title}",
                "--body",
                f"<!-- claim: {issue} -->",
            ]
        )
        assert result.returncode == 0, f"gh pr create failed: {result.stderr}"
        pr_url = result.stdout.strip()
        m = re.search(r"(\d+)$", pr_url)
        assert m, f"Could not extract PR number from: {pr_url}"
        our_pr = m.group(1)
        print(f"Created draft PR #{our_pr}: {pr_url}")

    # Race resolution: lowest PR number claiming this issue wins.
    # Two-phase check with a settle window to close the TOCTOU gap —
    # concurrent claims created within seconds of each other need time
    # to propagate through the GitHub API before both are visible.
    winner = _race_winner(issue)
    if winner and winner != our_pr:
        print(f"Lost race: PR #{winner} already claims {issue}", file=sys.stderr)
        sys.exit(1)

    time.sleep(RACE_SETTLE_SECONDS)

    winner = _race_winner(issue)
    if winner and winner != our_pr:
        print(
            f"Lost race (re-check): PR #{winner} already claims {issue}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Claimed {issue} (PR #{our_pr})")
    print(f"Branch: {branch}")
    print(f"Push: git push origin {branch}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Abandon a claimed issue by closing its PR and deleting the branch.

Usage:
    abandon-issue.py

Exit codes:
    0  PR closed and branch deleted
    1  No open PR found for current branch
"""

import subprocess
import sys


def main() -> None:
    # Get current branch name
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch and branch != "master", (
        "Must be on a feature branch to abandon an issue"
    )

    result = subprocess.run(
        ["gh", "pr", "close", branch, "--delete-branch"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to close PR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()

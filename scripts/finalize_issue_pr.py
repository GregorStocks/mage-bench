#!/usr/bin/env python3
"""Push, create/update a PR for the current branch, and mark it ready.

Usage:
    finalize_issue_pr.py --title "PR title" --body "PR body"

Exit codes:
    0  Success
    1  No local issue claim found for the current worktree
"""

import argparse
import json
import subprocess

from magebench.common.local_claims import current_owner_claims

ISSUE_NAMESPACE = "issues"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _current_branch() -> str:
    result = run(["git", "branch", "--show-current"])
    assert result.returncode == 0, f"git branch --show-current failed: {result.stderr}"
    branch = result.stdout.strip()
    assert branch, "Expected current branch"
    return branch


def _open_branch_pr(branch: str) -> dict[str, object] | None:
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,isDraft",
        ]
    )
    assert result.returncode == 0, f"gh pr list --head {branch} failed: {result.stderr}"
    prs = json.loads(result.stdout)
    assert isinstance(prs, list), (
        f"gh pr list returned non-list payload: {type(prs).__name__}"
    )
    if not prs:
        return None
    assert len(prs) == 1, (
        f"Expected at most one open PR for branch {branch}, got {len(prs)}"
    )
    pr = prs[0]
    assert isinstance(pr, dict), f"gh pr list returned non-object PR entry: {pr!r}"
    return pr


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize an issue PR")
    parser.add_argument("--title", required=True, help="PR title")
    parser.add_argument("--body", required=True, help="PR body")
    args = parser.parse_args()

    claims = current_owner_claims(ISSUE_NAMESPACE)
    if not claims:
        raise SystemExit(1)

    subprocess.run(["git", "push", "origin", "HEAD"], check=True)

    branch = _current_branch()
    pr = _open_branch_pr(branch)

    if pr is None:
        subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                "master",
                "--title",
                args.title,
                "--body",
                args.body,
            ],
            check=True,
        )
        pr = _open_branch_pr(branch)
        assert pr is not None, f"Open PR for {branch} not found after creation"
    else:
        subprocess.run(
            [
                "gh",
                "pr",
                "edit",
                str(pr["number"]),
                "--title",
                args.title,
                "--body",
                args.body,
            ],
            check=True,
        )

    if bool(pr["isDraft"]):
        subprocess.run(["gh", "pr", "ready"], check=True)

    print(f"PR finalized: {args.title}")


if __name__ == "__main__":
    main()

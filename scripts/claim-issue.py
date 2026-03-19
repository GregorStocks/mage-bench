#!/usr/bin/env python3
"""Claim an issue by creating a draft PR. Earliest claim timestamp wins ties.

Usage:
    claim-issue.py <issue-filename>   Claim an issue
    claim-issue.py --list             List already-claimed issues

Exit codes:
    0  Claimed successfully (or --list succeeded)
    1  Already claimed or lost race
    2  Bad input / branch already tied to a different open PR
"""

import re
import subprocess
import sys
import time
from datetime import datetime
import json
from pathlib import Path

from scripts.issue_files import issue_path, issue_stem, load_issue

ISSUES_DIR = Path("issues")
RACE_SETTLE_SECONDS = 5
CLAIM_TS_RE = re.compile(r"<!-- claim-ts: (\d+) -->")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def extract_claim_tag(body: str) -> str | None:
    m = re.search(r"<!-- claim: (.+?) -->", body.replace("\r", ""))
    return m.group(1) if m else None


def extract_claim_time_ns(body: str) -> int | None:
    m = CLAIM_TS_RE.search(body.replace("\r", ""))
    return int(m.group(1)) if m else None


def claim_metadata(issue: str, claim_time_ns: int) -> str:
    return f"<!-- claim: {issue} -->\n<!-- claim-ts: {claim_time_ns} -->"


def _github_time_to_ns(created_at: str) -> int:
    return int(
        datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        * 1_000_000_000
    )


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
            claim = extract_claim_tag(line.strip())
            if claim:
                claimed.append(claim)
    return sorted(set(claimed))


def get_open_branch_pr(branch: str) -> dict[str, object] | None:
    """Return the current branch's open PR, if any."""
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
            "number,body,url",
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


def _open_claims() -> list[dict[str, int | str]]:
    """Return open PR claim records with explicit claim timestamps."""
    result = run(
        ["gh", "pr", "list", "--state", "open", "--json", "number,body,createdAt"]
    )
    assert result.returncode == 0, f"gh pr list --state open failed: {result.stderr}"
    prs = json.loads(result.stdout)
    assert isinstance(prs, list), (
        f"gh pr list returned non-list payload: {type(prs).__name__}"
    )

    claims: list[dict[str, int | str]] = []
    for pr in prs:
        assert isinstance(pr, dict), f"gh pr list returned non-object PR entry: {pr!r}"
        body = str(pr["body"])
        issue = extract_claim_tag(body)
        if issue is None:
            continue
        claim_time_ns = extract_claim_time_ns(body)
        if claim_time_ns is None:
            claim_time_ns = _github_time_to_ns(str(pr["createdAt"]))
        claims.append(
            {
                "issue": issue,
                "number": int(pr["number"]),
                "claim_time_ns": claim_time_ns,
            }
        )
    return claims


def _race_winner(issue: str) -> str | None:
    """Return the earliest claim for *issue*, tie-broken by PR number."""
    matches = [claim for claim in _open_claims() if claim["issue"] == issue]
    if not matches:
        return None
    winner = min(
        matches,
        key=lambda claim: (int(claim["claim_time_ns"]), int(claim["number"])),
    )
    return str(winner["number"])


def _branch_pr_lost_race(issue: str, pr_number: str) -> bool:
    """Return whether another open PR already owns this issue claim."""
    winner = _race_winner(issue)
    return winner is not None and winner != pr_number


def replace_claim_metadata(
    body: str, old_issue: str, new_issue: str, claim_time_ns: int
) -> str:
    updated_body = re.sub(
        rf"<!-- claim: {re.escape(old_issue)} -->",
        f"<!-- claim: {new_issue} -->",
        body,
        count=1,
    )
    assert updated_body != body, f"PR body missing claim tag for {old_issue}:\n{body}"
    updated_ts = CLAIM_TS_RE.sub(
        f"<!-- claim-ts: {claim_time_ns} -->", updated_body, count=1
    )
    if updated_ts != updated_body:
        return updated_ts
    return updated_body.replace(
        f"<!-- claim: {new_issue} -->",
        claim_metadata(new_issue, claim_time_ns),
        1,
    )


def repurpose_branch_pr(
    pr_number: str,
    body: str,
    old_issue: str,
    new_issue: str,
    new_title: str,
    claim_time_ns: int,
) -> None:
    """Update the current branch PR in place to claim a different issue."""
    subprocess.run(
        [
            "gh",
            "pr",
            "edit",
            pr_number,
            "--title",
            f"Solve: {new_title}",
            "--body",
            replace_claim_metadata(body, old_issue, new_issue, claim_time_ns),
        ],
        check=True,
    )
    print(
        f"Updated PR #{pr_number} claim from {old_issue} to {new_issue}",
        file=sys.stderr,
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: claim-issue.py <issue-filename>", file=sys.stderr)
        print("       claim-issue.py --list", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "--list":
        for name in list_claimed():
            print(name)
        sys.exit(0)

    issue = issue_stem(sys.argv[1])
    claim_time_ns = time.time_ns()

    path = issue_path(ISSUES_DIR, issue)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(2)

    data = load_issue(path)
    title = data["title"]

    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch == "master":
        print(
            "Error: can't claim an issue from master — switch to a feature branch first",
            file=sys.stderr,
        )
        sys.exit(2)

    branch_pr = get_open_branch_pr(branch)
    if branch_pr is not None:
        branch_pr_body = str(branch_pr["body"])
        existing_claim = extract_claim_tag(branch_pr_body)
        pr_number = str(branch_pr["number"])
        pr_url = str(branch_pr["url"])
        if existing_claim is None:
            print(
                f"Error: branch {branch} already has open PR #{pr_number} without a claim tag "
                f"({pr_url}); refusing to repurpose it for {issue}",
                file=sys.stderr,
            )
            sys.exit(2)
        if existing_claim != issue:
            if _branch_pr_lost_race(existing_claim, pr_number):
                repurpose_branch_pr(
                    pr_number,
                    branch_pr_body,
                    existing_claim,
                    issue,
                    title,
                    claim_time_ns,
                )
                branch_pr = get_open_branch_pr(branch)
                assert branch_pr is not None, (
                    f"PR #{pr_number} disappeared while repurposing branch {branch} to {issue}"
                )
            else:
                print(
                    f"Error: branch {branch} already has open PR #{pr_number} claiming "
                    f"{existing_claim}; refusing to also claim {issue} on the same branch",
                    file=sys.stderr,
                )
                sys.exit(2)

    if branch_pr is not None:
        print(f"Branch {branch} already has open PR #{pr_number} claiming {issue}")
        our_pr = pr_number
        subprocess.run(["git", "push", "-u", "origin", branch], check=True)
    else:
        # Ensure at least one commit ahead of master so the PR can be created
        log_result = run(["git", "log", "origin/master..HEAD", "--oneline"])
        if not log_result.stdout.strip():
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", f"Claim: {title}"], check=True
            )

        # Push current branch
        subprocess.run(["git", "push", "-u", "origin", branch], check=True)

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
                claim_metadata(issue, claim_time_ns),
            ]
        )
        assert result.returncode == 0, f"gh pr create failed: {result.stderr}"
        pr_url = result.stdout.strip()
        m = re.search(r"(\d+)$", pr_url)
        assert m, f"Could not extract PR number from: {pr_url}"
        our_pr = m.group(1)
        print(f"Created draft PR #{our_pr}: {pr_url}")

    # Race resolution: earliest claim timestamp wins.
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

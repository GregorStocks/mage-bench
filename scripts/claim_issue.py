#!/usr/bin/env python3
"""Claim an issue using the shared local claim store.

Usage:
    claim_issue.py <issue-filename>  Claim an issue
    claim_issue.py --current         Print this worktree's claimed issue
    claim_issue.py --list            List active claimed issues

Exit codes:
    0  Claimed successfully (or query succeeded)
    1  Already claimed by another worktree / no current claim
    2  Bad input / branch already tied to a different claimed issue
"""

import sys
from pathlib import Path

from magebench.common.issue_files import issue_path, issue_stem, load_issue
from magebench.common.local_claims import (
    ClaimConflictError,
    canonical_issue_key,
    claim_exact_keys,
    current_owner_claims,
    current_worktree_context,
    list_claims,
    resolve_issue_stem_for_key,
)

ISSUES_DIR = Path("issues")
ISSUE_NAMESPACE = "issues"


def list_claimed() -> list[str]:
    return sorted(record.key for record in list_claims(ISSUE_NAMESPACE))


def current_claimed_issue_stem() -> str | None:
    claims = current_owner_claims(ISSUE_NAMESPACE)
    if not claims:
        return None
    assert len(claims) == 1, (
        f"Expected at most one issue claim for this worktree, got {claims}"
    )
    resolved = resolve_issue_stem_for_key(ISSUES_DIR, claims[0].key)
    assert resolved is not None, (
        f"Current issue claim {claims[0].key!r} does not resolve to an issue file"
    )
    return resolved


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: claim_issue.py <issue-filename>", file=sys.stderr)
        print("       claim_issue.py --current", file=sys.stderr)
        print("       claim_issue.py --list", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "--list":
        for name in list_claimed():
            print(name)
        return

    if sys.argv[1] == "--current":
        current = current_claimed_issue_stem()
        if current is None:
            sys.exit(1)
        print(current)
        return

    issue = issue_stem(sys.argv[1])
    path = issue_path(ISSUES_DIR, issue)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(2)

    context = current_worktree_context()
    if context.branch == "master":
        print(
            "Error: can't claim an issue from master — switch to a feature branch first",
            file=sys.stderr,
        )
        sys.exit(2)

    existing_claims = current_owner_claims(ISSUE_NAMESPACE)
    if existing_claims:
        assert len(existing_claims) == 1, (
            f"Expected at most one issue claim for this worktree, got {existing_claims}"
        )
        existing_claim = existing_claims[0]
        if existing_claim.key != canonical_issue_key(issue):
            print(
                f"Error: worktree {context.worktree_name} already claims {existing_claim.key}; "
                f"refusing to also claim {issue}",
                file=sys.stderr,
            )
            sys.exit(2)

    title = load_issue(path)["title"]
    metadata = {
        canonical_issue_key(issue): {
            "issue_stem_at_claim": issue,
            "issue_title": title,
        }
    }
    try:
        claim_exact_keys(
            ISSUE_NAMESPACE,
            [canonical_issue_key(issue)],
            metadata_by_key=metadata,
        )
    except ClaimConflictError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    resolved = current_claimed_issue_stem()
    assert resolved is not None, (
        "Issue claim succeeded but current claim could not be resolved"
    )
    print(f"Claimed {resolved}")
    print(f"Branch: {context.branch}")


if __name__ == "__main__":
    main()

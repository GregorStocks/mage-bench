#!/usr/bin/env python3
"""Claim an issue — either a specific one or the highest-priority unclaimed one.

Merges origin/master first, then either claims the named issue or atomically
picks the highest-priority locally unclaimed non-blocked issue.

Usage:
    autoclaim_issue.py                  Auto-pick highest priority issue
    autoclaim_issue.py <issue-name>     Claim a specific issue (bypasses blocked)

Exit codes:
    0  Claimed successfully
    1  No claimable issues available / named issue already claimed
    2  Bad input
"""

import subprocess
import sys
from pathlib import Path

from magebench.common.issue_files import (
    issue_path,
    issue_stem,
    iter_issue_files,
    load_issue,
)
from magebench.common.local_claims import (
    ClaimConflictError,
    canonical_issue_key,
    claim_exact_keys,
    claim_first_available_keys,
    current_owner_claims,
    current_worktree_context,
    resolve_issue_stem_for_key,
)

ISSUES_DIR = Path("issues")
ISSUE_NAMESPACE = "issues"


def merge_master() -> None:
    subprocess.run(["git", "fetch", "origin"], check=True)
    subprocess.run(["git", "merge", "origin/master", "--no-edit"], check=True)


def load_issues() -> list[tuple[str, int, str]]:
    assert ISSUES_DIR.is_dir(), f"Issues directory not found: {ISSUES_DIR}"
    issues = []
    for path in iter_issue_files(ISSUES_DIR):
        data = load_issue(path)
        if data.get("blocked"):
            continue
        issues.append((path.stem, data.get("priority", 999), data["title"]))
    issues.sort(key=lambda issue: (issue[1], issue[0]))
    return issues


def _claimed_issue_stem(key: str) -> str:
    return resolve_issue_stem_for_key(ISSUES_DIR, key) or key


def _existing_owner_issue_claim() -> str | None:
    existing_claims = current_owner_claims(ISSUE_NAMESPACE)
    if not existing_claims:
        return None
    assert len(existing_claims) == 1, (
        f"Expected at most one issue claim for this worktree, got {existing_claims}"
    )
    return existing_claims[0].key


def _refuse_if_already_claiming(*, target_key: str | None) -> None:
    existing_key = _existing_owner_issue_claim()
    if existing_key is None or existing_key == target_key:
        return

    existing_stem = _claimed_issue_stem(existing_key)
    if target_key is None:
        print(
            f"Error: worktree already claims {existing_stem}; refusing to auto-claim another issue",
            file=sys.stderr,
        )
    else:
        print(
            f"Error: worktree already claims {existing_stem}; refusing to also claim "
            f"{_claimed_issue_stem(target_key)}",
            file=sys.stderr,
        )
    sys.exit(2)


def _ensure_not_on_master() -> None:
    context = current_worktree_context()
    if context.branch != "master":
        return
    print(
        "Error: can't claim an issue from master — switch to a feature branch first",
        file=sys.stderr,
    )
    sys.exit(2)


def claim_specific(issue_name: str) -> None:
    stem = issue_stem(issue_name)
    path = issue_path(ISSUES_DIR, issue_name)
    assert path.exists(), f"Issue file not found: {path}"
    issue_key = canonical_issue_key(stem)
    _refuse_if_already_claiming(target_key=issue_key)

    metadata = {
        issue_key: {
            "issue_stem_at_claim": stem,
            "issue_title": load_issue(path)["title"],
        }
    }
    try:
        records = claim_exact_keys(
            ISSUE_NAMESPACE,
            [issue_key],
            metadata_by_key=metadata,
        )
    except ClaimConflictError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Claimed: {_claimed_issue_stem(records[0].key)}")


def main() -> None:
    merge_master()
    _ensure_not_on_master()

    if len(sys.argv) > 2:
        print("Usage: autoclaim_issue.py [issue-name]", file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) == 2:
        claim_specific(sys.argv[1])
        return

    existing_key = _existing_owner_issue_claim()
    if existing_key is not None:
        print(
            f"Error: worktree already claims {_claimed_issue_stem(existing_key)}; "
            "refusing to auto-claim another issue",
            file=sys.stderr,
        )
        sys.exit(2)

    issues = load_issues()
    candidate_keys = [canonical_issue_key(stem) for stem, _priority, _title in issues]
    metadata = {
        canonical_issue_key(stem): {
            "issue_stem_at_claim": stem,
            "issue_title": title,
            "issue_priority": priority,
        }
        for stem, priority, title in issues
    }
    records = claim_first_available_keys(
        ISSUE_NAMESPACE,
        candidate_keys,
        1,
        metadata_by_key=metadata,
    )
    if not records:
        print("No claimable issues available.", file=sys.stderr)
        sys.exit(1)

    print(f"Claimed: {_claimed_issue_stem(records[0].key)}")


if __name__ == "__main__":
    main()

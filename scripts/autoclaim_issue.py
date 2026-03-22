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


def claim_specific(issue_name: str) -> None:
    stem = issue_stem(issue_name)
    path = issue_path(ISSUES_DIR, issue_name)
    assert path.exists(), f"Issue file not found: {path}"

    metadata = {
        canonical_issue_key(stem): {
            "issue_stem_at_claim": stem,
            "issue_title": load_issue(path)["title"],
        }
    }
    try:
        records = claim_exact_keys(
            ISSUE_NAMESPACE,
            [canonical_issue_key(stem)],
            metadata_by_key=metadata,
        )
    except ClaimConflictError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Claimed: {_claimed_issue_stem(records[0].key)}")


def main() -> None:
    merge_master()

    if len(sys.argv) > 2:
        print("Usage: autoclaim_issue.py [issue-name]", file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) == 2:
        claim_specific(sys.argv[1])
        return

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

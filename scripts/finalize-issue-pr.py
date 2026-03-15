#!/usr/bin/env python3
"""Push, update PR with claim tag preserved, and mark ready.

Usage:
    finalize-issue-pr.py --title "PR title" --body "PR body"

Extracts the <!-- claim: ... --> tag from the current PR body,
appends it to the new body, pushes, edits the PR, and marks it ready.

Exit codes:
    0  Success
    1  No open PR found for current branch
    2  No claim tag found in PR body
"""

import argparse
import re
import subprocess


CLAIM_TS_RE = re.compile(r"<!-- claim-ts: \d+ -->")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def extract_claim_metadata(body: str) -> str:
    claim_match = re.search(r"<!-- claim: .+? -->", body)
    assert claim_match, f"No claim tag found in PR body:\n{body}"
    claim_parts = [claim_match.group(0)]
    claim_ts_match = CLAIM_TS_RE.search(body)
    if claim_ts_match:
        claim_parts.append(claim_ts_match.group(0))
    return "\n".join(claim_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize an issue PR")
    parser.add_argument("--title", required=True, help="PR title")
    parser.add_argument(
        "--body", required=True, help="PR body (claim tag is appended automatically)"
    )
    args = parser.parse_args()

    # Push
    subprocess.run(["git", "push", "origin", "HEAD"], check=True)

    # Get current PR body
    result = run(["gh", "pr", "view", "--json", "body", "--jq", ".body"])
    assert result.returncode == 0, (
        f"No open PR found for current branch: {result.stderr}"
    )

    # Extract claim metadata
    claim_metadata = extract_claim_metadata(result.stdout)

    # Update PR
    body_with_claim = f"{args.body}\n\n{claim_metadata}"
    subprocess.run(
        ["gh", "pr", "edit", "--title", args.title, "--body", body_with_claim],
        check=True,
    )

    # Mark ready
    subprocess.run(["gh", "pr", "ready"], check=True)

    print(f"PR finalized: {args.title}")


if __name__ == "__main__":
    main()

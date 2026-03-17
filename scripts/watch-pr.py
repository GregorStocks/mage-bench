#!/usr/bin/env python3
"""Watch a PR until CI completes, then report results and review feedback.

Usage:
    watch-pr.py [<pr-number>]

Polls CI checks every 30s. Once all checks finish, also reports any
review comments or change requests that appeared while waiting.

Exit codes:
    0  All checks passed, no review feedback
    1  One or more checks failed
    2  Review feedback found
    3  Both failures and feedback
    4  Timed out waiting for checks
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone

POLL_INTERVAL = 30  # seconds
TIMEOUT = 1800  # 30 minutes
STARTUP_GRACE = 120  # wait up to 2min for checks to appear


def run_gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def get_pr_number() -> str:
    result = run_gh("pr", "view", "--json", "number", "--jq", ".number")
    assert result.returncode == 0, f"No open PR for current branch: {result.stderr}"
    return result.stdout.strip()


def get_repo_nwo() -> str:
    """Get owner/repo for API calls."""
    result = run_gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    assert result.returncode == 0, f"Failed to get repo info: {result.stderr}"
    return result.stdout.strip()


def get_checks(pr: str) -> list[dict]:
    result = run_gh("pr", "checks", pr, "--json", "bucket,name,link,workflow")
    assert result.returncode in (0, 1, 8), (
        f"gh pr checks failed (exit {result.returncode}): {result.stderr}"
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def should_stop_polling(checks: list[dict]) -> bool:
    """Return True if we have enough info to report: any failure or all done."""
    if not checks:
        return False
    if any(c.get("bucket") == "fail" for c in checks):
        return True
    return all(c.get("bucket") not in ("pending", None) for c in checks)


def get_review_feedback(pr: str, nwo: str, since: datetime) -> list[str]:
    """Get review comments, change requests, and inline comments newer than `since`."""
    result = run_gh("pr", "view", pr, "--json", "reviews,comments")
    assert result.returncode == 0, f"Failed to fetch PR details: {result.stderr}"
    data = json.loads(result.stdout)

    feedback = []

    for review in data.get("reviews", []):
        state = review.get("state", "")
        if state in ("APPROVED", "PENDING", "DISMISSED"):
            continue
        submitted = review.get("submittedAt")
        if submitted:
            ts = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
            if ts < since:
                continue
        author = review["author"]["login"]
        body = review.get("body", "").strip()
        if body:
            feedback.append(f"[{state}] @{author}: {body}")
        else:
            # CHANGES_REQUESTED with no body means inline-only review
            feedback.append(f"[{state}] @{author} (see inline comments)")

    for comment in data.get("comments", []):
        author = comment["author"]["login"]
        if author.endswith("[bot]"):
            continue
        created = comment.get("createdAt")
        if created:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if ts < since:
                continue
        body = comment.get("body", "").strip()
        if not body:
            continue
        feedback.append(f"[COMMENT] @{author}: {body}")

    # Inline review comments (diff-level) are a separate API endpoint
    inline_result = run_gh(
        "api",
        f"repos/{nwo}/pulls/{pr}/comments",
        "--jq",
        ".[] | [.user.login, .path, (.line | tostring), .created_at, .body] | @tsv",
    )
    if inline_result.returncode == 0 and inline_result.stdout.strip():
        for line in inline_result.stdout.strip().split("\n"):
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            author, path, line_no, created, body = parts
            if created:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if ts < since:
                    continue
            feedback.append(f"[INLINE] @{author} on {path}:{line_no}: {body.strip()}")

    return feedback


def main() -> None:
    pr = sys.argv[1] if len(sys.argv) > 1 else get_pr_number()
    nwo = get_repo_nwo()
    since = datetime.now(timezone.utc)
    print(f"Watching PR #{pr}...", flush=True)

    start = time.monotonic()

    # Wait for checks to appear
    checks = get_checks(pr)
    while not checks:
        if time.monotonic() - start > STARTUP_GRACE:
            print("No CI checks found after 2 minutes.", flush=True)
            break
        print("Waiting for checks to start...", flush=True)
        time.sleep(POLL_INTERVAL)
        checks = get_checks(pr)

    # Poll until all checks finish
    while not should_stop_polling(checks):
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT:
            pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
            print(
                f"\nTimed out after {TIMEOUT}s. Still pending: {', '.join(pending)}",
                flush=True,
            )
            sys.exit(4)

        pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
        mins = int(elapsed // 60)
        print(
            f"  [{mins}m] {len(pending)} pending: {', '.join(pending[:5])}",
            flush=True,
        )
        time.sleep(POLL_INTERVAL)
        checks = get_checks(pr)

    # Collect results
    failed = [c for c in checks if c.get("bucket") == "fail"]
    feedback = get_review_feedback(pr, nwo, since)

    exit_code = 0

    if failed:
        exit_code |= 1
        print(f"\n{len(failed)} check(s) FAILED:")
        for c in failed:
            print(f"  - {c['name']}: {c.get('link', 'no link')}")

    if feedback:
        exit_code |= 2
        print(f"\n{len(feedback)} review comment(s):")
        for fb in feedback:
            print(f"  {fb}")

    if exit_code == 0:
        passed = [c for c in checks if c.get("bucket") == "pass"]
        skipped = [c for c in checks if c.get("bucket") == "skipping"]
        print(
            f"\nAll checks passed ({len(passed)} passed, {len(skipped)} skipped). "
            "No review feedback.",
            flush=True,
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

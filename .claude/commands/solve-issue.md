# Solve an Issue

Pick and solve exactly **one** issue, then create a PR.

## Workflow

0. **Preflight check** — before doing anything else, verify the branch is clean and up to date:
   ```bash
   git fetch origin
   ```
   Then check that:
   - `git status --porcelain` is empty (no uncommitted changes)
   - `git rev-parse HEAD` equals `git rev-parse origin/master` (no extra commits)

   If either check fails, **stop immediately** and tell the user. Do not proceed — solve-issue must start from a clean branch that matches `origin/master` exactly.

1. **Claim an issue** by running:
   ```bash
   # Auto-pick the highest-priority unclaimed issue:
   uv run python scripts/autoclaim-issue.py

   # Or claim a specific issue by name (bypasses not_autoclaimable):
   uv run python scripts/autoclaim-issue.py <issue-name>
   ```
   If the user passed an argument to `/solve-issue` (e.g. `/solve-issue populate-deck-strategies`), use it as the issue name. Otherwise, auto-pick.

   Auto-pick mode skips issues with `"not_autoclaimable": true` — those have preconditions that need manual review. Naming a specific issue bypasses that filter, but still fails if the issue is already claimed by another PR.

   - If the script **succeeds** (exit 0): you claimed it. Continue to step 2.
   - If the script **fails** (exit 1 or 2): **stop immediately**. Tell the user no issue was claimed and do NOT proceed. You must not work on any issue you haven't successfully claimed — no exceptions. The claiming system prevents multiple Claudes from working on the same issue; bypassing it causes wasted work and merge conflicts.
2. **Check if already fixed** — before planning anything, check whether the issue was already resolved and the issue file just wasn't cleaned up. Do this by:
   - Finding when the issue file was created (`git log --diff-filter=A -- issues/<filename>.json`)
   - Reviewing git history since that date for commits that look like they address the issue
   - Reading the relevant code to see if the described bug/problem still exists

   If the issue **is already fixed**: skip the planning/implementation steps entirely. Just delete the issue file, commit it, push, and finalize the PR as a cleanup. The PR title should be something like "Clean up outdated issue: \<title\>" and the body should briefly explain that the issue was already resolved (mention the commit or change that fixed it). Conceptually this is a zero-line fix — the only change is removing the stale issue file.

   If the issue **is NOT fixed**: continue to step 3.
3. **Enter plan mode** — explore the codebase, design your approach, and present it to the user for feedback before writing any code. This is the user's chance to redirect you if the approach is wrong. **Your plan must end with this checklist** (copy it verbatim into your plan):

   ```
   ## Post-implementation checklist
   - [ ] Implement the changes described above
   - [ ] Add/update tests
   - [ ] Run `make check` (lint, typecheck, tests)
   - [ ] Delete the issue file and include deletion in the commit
   - [ ] Push final changes: `git push origin HEAD`
   - [ ] Finalize PR: `uv run python scripts/finalize-issue-pr.py --title "..." --body "..."`
   ```

   This checklist survives the plan mode boundary and ensures no steps are skipped even if earlier context is compressed.
4. After the plan is approved, **create tasks** from the checklist using `TaskCreate`. Mark each task in_progress when you start it and completed when you finish it.
5. Implement the fix. Push progress:
   ```bash
   git push origin HEAD
   ```
6. Update tests to expect the correct behavior
7. Run `make check` to verify lint, typecheck, and tests pass
8. Delete the issue file (e.g., `rm issues/<issue-filename>.json`) and **include the deletion in the commit** — the issue removal must ship with the fix
9. **Document ALL issues you discover** during exploration, even if you're only fixing one. Future Claudes benefit from this documentation!
10. Push final changes and finalize the PR. The script extracts the `<!-- claim: ... -->` tag from the current PR body and appends it to your new body automatically:
    ```bash
    uv run python scripts/finalize-issue-pr.py --title "<concise PR title>" --body "<PR description with summary, test plan>"
    ```
    Then stop — leave remaining issues for the next Claude.

## Abandoning an Issue

If you determine an issue isn't worth fixing after claiming it, clean up your claim:
```bash
uv run python scripts/abandon-issue.py
```
Then restart from step 1 to pick a different issue.

## Is It Worth Fixing?

Not every quirk deserves a fix. For issues that seem one-in-a-million or where it's not realistically possible to determine the original author's intent, it's fine to give up and handle it gracefully. Being correct on fewer things is better than being _wrong_.

## Important

- One issue per PR — keeps PRs small and reviewable
- Stop after creating the PR — don't chain multiple fixes

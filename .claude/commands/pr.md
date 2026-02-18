# Make a PR

Create a pull request for the current branch's changes.

## Workflow

1. **Commit any uncommitted work.** Check `git status` — if there are staged or unstaged changes, commit them before proceeding. Everything that's part of this PR should be in a commit.

2. **Understand the full scope of changes.** Run these in parallel:
   ```bash
   git fetch origin
   git log --oneline origin/master..HEAD
   git diff origin/master..HEAD --stat
   ```
   Read through the actual diffs and changed files — don't just look at filenames. You need to understand what changed and why to write a good PR.

3. **Merge in master** so you're testing against the latest code:
   ```bash
   git merge origin/master
   ```
   Fix any merge conflicts before proceeding.

4. **Run `make check`** (lint, typecheck, tests). Fix any failures before proceeding. Do not create a PR with failing checks.

5. **Write the PR title and body.** The PR description must explain **why** these changes exist, not just what they do. A reviewer can read the diff to see *what* changed — the PR body should tell them *why* it changed, what problem it solves, and any context they'd need to evaluate the approach.

   Bad (just restates the diff):
   > - Add `timeout` parameter to `fetch_game_data()`
   > - Update `config.json` to include `timeout_secs` field
   > - Add test for timeout behavior

   Good (explains the motivation):
   > Grok 4 base has a 32% timeout rate at the current 45s limit because it's
   > a slow model. Increase the LLM request timeout to 120s so slower models
   > can finish reasoning without getting cut off.

   The summary bullets should be a mix of what and why — lead with the motivation, then mention key implementation details only when they're non-obvious.

6. **Push and create the PR:**
   ```bash
   git push -u origin HEAD
   gh pr create --title "<concise title>" --body "$(cat <<'EOF'
   ## Summary
   <2-5 bullets mixing why and what>

   ## Test plan
   <bulleted checklist — what you verified>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

7. **Report the PR URL** to the user.

## Guidelines

- **Title**: Short, imperative, under 70 characters. Describes the outcome, not the mechanism (e.g., "Fix timeout for slow models" not "Add timeout_secs config parameter").
- **Summary**: Start with the *problem* or *motivation*, then describe the solution. A reader should understand why this PR exists from the first bullet alone.
- **Test plan**: List what you actually verified — `make check`, manual testing, screenshots, specific scenarios. Don't list things you didn't do.
- **One logical change per PR** — don't bundle unrelated work.
- If the branch has many commits, the PR description should synthesize the overall change, not enumerate every commit.

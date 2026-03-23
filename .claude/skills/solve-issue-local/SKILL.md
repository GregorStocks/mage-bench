---
name: solve-issue-local
description: Repo-specific build, test, and PR instructions for mage-bench issue solving.
---

# Mage-Bench: Solve Issue Local Instructions

This file provides repo-specific instructions for the global `/solve-issue` skill.

## Build & Test Commands

```bash
make check          # Full lint + typecheck + tests (the one command to rule them all)
make build          # Java build only
make test           # Python tests only
make lint           # Python lint only
```

- If you need live progress or a concrete failing sub-target, prefer `make check VERBOSE=1` over launching a second blind `make check`.
- For large Java refactors, especially under `Mage.Client.Bridge/`, use a module-scoped Maven loop for fast feedback while iterating (e.g. `mvn -pl Mage.Client.Bridge -DskipTests compile`). Still finish with the full `make check` before finalizing.

## Code Review

After implementation, run `/simplify` to review the changed code for reuse, quality, and efficiency. Fix any issues found.

Also inspect `website/package-lock.json` before committing. `make check` / website tooling can add incidental `"peer": true` lockfile churn even when you did not intentionally change website dependencies; drop unrelated lockfile noise so the issue PR stays scoped.

If you run targeted Ruff fixes or formatting on "changed files only", remember that `git diff --name-only` omits newly added untracked files. Include untracked paths from `git status` too, or lint can still fail on the brand-new files you just created.

## Test Considerations

- If your code changes prompt rendering, bridge responses, MCP tool output, replay behavior, or exported game data, proactively search existing goldens for the affected prompt fragment or behavior and regenerate every impacted golden before moving on.
- If you move a canonical file or schema path, search `conftest.py` and shared test fixtures for hardcoded repo paths before relying on the full test suite.
- If you move Python tests across roots (for example out of `puppeteer/` into top-level `tests/`), also update repo plumbing that discovers or consumes them: `Makefile` lint/test targets, `scripts/checks/quiet_check.py` triggers, pytest config location, and any website/utilities that read golden assets by path. The file move alone is not enough to keep `make check` green.

## Harness Epoch

If MCP tools, pilot logic, or priority semantics change enough to make game results non-comparable, bump `HARNESS_EPOCH` in `puppeteer/src/puppeteer/harness_epoch.py`.

## Post-Implementation Checklist

```markdown
- [ ] Implement the changes
- [ ] Add/update tests
- [ ] Run `make check` (lint, typecheck, tests)
- [ ] Delete the issue file and include deletion in the commit
- [ ] Run `/simplify` to review changed code
- [ ] Push final changes: `git push origin HEAD`
- [ ] Finalize PR: `issue-finalize-pr --title "..." --body "..."`
- [ ] Watch CI: `issue-watch-pr`
```

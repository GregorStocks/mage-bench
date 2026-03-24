---
name: create-pr-local
description: Repo-specific validation and PR instructions for mage-bench.
---

# Mage-Bench: Create PR Local Instructions

This file provides repo-specific instructions for the global `/create-pr` skill.

## Pre-Validation (before step 5)

If the diff changes prompt rendering, bridge responses, MCP tool output, replay behavior, or exported game data, search existing goldens for the affected behavior and regenerate every stale prompt/export now. Do not assume only newly added tests need updates, and do not wait for CI to remind you.

## Validation Command

```bash
make check          # Full lint + typecheck + tests
```

- If you need live progress or a concrete failing sub-target, prefer `make check VERBOSE=1` over launching a second blind `make check`.
- The quiet wrapper (`scripts/checks/quiet_check.py`) can stay silent for minutes while long subchecks run. If it looks hung, inspect the process tree (e.g. `pstree -ap <quiet_check_pid>`) to see which subcheck is active before assuming it's stuck.
- If `make check` fails in website-related targets with `npm ERR! EEXIST` symlink errors, or `verify-schema-types` claims `website/src/types/game-export.d.ts` is stale on an otherwise clean tree, check whether parallel `npm install` runs are racing inside the website targets before assuming the generated types actually need regeneration.

## Post-Validation Cleanup

Check `website/package-lock.json` before pushing. `make check` / website tooling can add incidental `"peer": true` lockfile churn even when you did not intentionally change website dependencies; drop unrelated lockfile noise so the PR stays scoped.

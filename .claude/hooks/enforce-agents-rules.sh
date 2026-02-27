#!/usr/bin/env bash
# Pre-hook that enforces AGENTS.md rules by blocking prohibited Bash commands.
# Reads JSON from stdin with tool_input.command, exits 2 to block, 0 to allow.
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Strip quoted strings so tool names in commit messages, echo args, etc. don't
# trigger false positives. Join lines first to handle heredocs.
STRIPPED=$(echo "$COMMAND" | tr '\n' ' ' | sed "s/'[^']*'//g" | sed 's/"[^"]*"//g')

# --- Rule 1: Never rebase ---
if echo "$COMMAND" | grep -qE '^\s*git\s+rebase\b'; then
  echo "Blocked: git rebase is not allowed. Use 'git merge origin/master' instead." >&2
  exit 2
fi

# --- Rule 2: Never amend commits ---
if echo "$COMMAND" | grep -qE '^\s*git\s+commit\b.*--amend\b'; then
  echo "Blocked: git commit --amend is not allowed. Create a new commit instead." >&2
  exit 2
fi

# --- Rule 3: Never force-push ---
if echo "$COMMAND" | grep -qE '^\s*git\s+push\b.*(--force-with-lease|--force|-f)\b'; then
  echo "Blocked: force-push is not allowed. Create new commits and push normally." >&2
  exit 2
fi

# Rules 4-8 check STRIPPED (quotes removed) to avoid false positives on
# tool names appearing inside commit messages or string arguments.

# --- Rule 4: Never invoke mvn directly ---
if echo "$STRIPPED" | grep -qE '(^|\s|;|\||&&)\s*mvn\b'; then
  echo "Blocked: never invoke mvn directly. Use 'make build' or other make targets." >&2
  exit 2
fi

# --- Rule 5: Never invoke npm/npx directly ---
if echo "$STRIPPED" | grep -qE '(^|\s|;|\||&&)\s*np[mx]\b'; then
  echo "Blocked: never invoke npm/npx directly. Use 'make website' or other make targets." >&2
  exit 2
fi

# --- Rule 6: Never use python3/pip/pip3 directly ---
if echo "$STRIPPED" | grep -qE '(^|\s|;|\||&&)\s*(python3|pip3?)\b'; then
  echo "Blocked: never use python3/pip/pip3 directly. Use 'uv run' instead." >&2
  exit 2
fi

# --- Rule 7: Never use pkill/killall/lsof-to-kill ---
if echo "$STRIPPED" | grep -qE '(^|\s|;|\||&&)\s*(pkill|killall)\b'; then
  echo "Blocked: pkill/killall can kill other Claudes' dev servers in other worktrees. Kill specific PIDs instead." >&2
  exit 2
fi
if echo "$STRIPPED" | grep -qE 'lsof\b.*\|.*kill\b'; then
  echo "Blocked: lsof piped to kill can kill other Claudes' dev servers. Kill specific PIDs instead." >&2
  exit 2
fi

# --- Rule 8: Never run LLM configs that consume API tokens ---
if echo "$STRIPPED" | grep -qE 'make\s+run\b.*CONFIG\s*=\s*(commander-gauntlet|commander-1v3|standard-gauntlet|modern-gauntlet|legacy-gauntlet|round-robin-1v1|round-robin-commander|yente-1v1|yente-commander)\b'; then
  echo "Blocked: this config consumes API tokens. Use free configs only (e.g. standard-dumb, modern-staller)." >&2
  exit 2
fi

# All checks passed
exit 0

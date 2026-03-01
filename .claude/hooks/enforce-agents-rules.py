#!/usr/bin/env python3
"""Pre-hook that enforces AGENTS.md rules by blocking prohibited Bash commands.

Reads JSON from stdin with tool_input.command. Exits 2 to block, 0 to allow.
"""

import json
import re
import sys


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def strip_quotes(command: str) -> str:
    """Remove quoted strings to avoid false positives on tool names in
    commit messages, echo args, heredoc bodies, etc."""
    oneline = command.replace("\n", " ")
    oneline = re.sub(r"'[^']*'", "", oneline)
    oneline = re.sub(r'"[^"]*"', "", oneline)
    return oneline


def check(command: str) -> None:
    stripped = strip_quotes(command)

    # --- Git rules (check raw command — these are always first token) ---

    if re.search(r"^\s*git\s+rebase\b", command):
        block("Blocked: git rebase is not allowed. Use 'git merge origin/master' instead.")

    if re.search(r"^\s*git\s+commit\b.*--amend\b", command):
        block("Blocked: git commit --amend is not allowed. Create a new commit instead.")

    if re.search(r"^\s*git\s+push\b.*(--force-with-lease|--force|-f)\b", command):
        block("Blocked: force-push is not allowed. Create new commits and push normally.")

    # --- Tool rules (check stripped to ignore quoted strings) ---

    if re.search(r"(?:^|\s|[;&|])\s*mvn\b", stripped):
        block("Blocked: never invoke mvn directly. Use 'make build' or other make targets.")

    if re.search(r"(?:^|\s|[;&|])\s*np[mx]\b", stripped):
        block("Blocked: never invoke npm/npx directly. Use 'make website' or other make targets.")

    if re.search(r"(?:^|\s|[;&|])\s*(?:python3|pip3?)\b", stripped):
        block("Blocked: never use python3/pip/pip3 directly. Use 'uv run' instead.")

    if re.search(r"(?:^|\s|[;&|])\s*(?:pkill|killall)\b", stripped):
        block("Blocked: pkill/killall can kill other Claudes' dev servers in other worktrees. Kill specific PIDs instead.")

    if re.search(r"lsof\b.*\|.*kill\b", stripped):
        block("Blocked: lsof piped to kill can kill other Claudes' dev servers. Kill specific PIDs instead.")

    # --- Expensive configs ---

    paid_configs = (
        "commander-1v3",
        "round-robin-1v1", "round-robin-commander", "round-robin-jumpstart",
    )
    paid_pattern = "|".join(re.escape(c) for c in paid_configs)
    if re.search(rf"make\s+run\b.*CONFIG\s*=\s*({paid_pattern})\b", stripped):
        block("Blocked: this config consumes API tokens. Use free configs only (e.g. standard-dumb, modern-staller).")

    # --- Golden tests — use make targets ---
    if re.search(r"GOLDEN_INTEGRATION\s*=\s*1", stripped) or re.search(r"-m\s+golden", stripped):
        block(
            "Blocked: don't run golden tests directly. Use make targets instead:\n"
            "  make test-golden              # run all golden tests\n"
            "  make test-golden K=bolt       # run golden tests matching 'bolt'\n"
            "  make update-golden            # regenerate all golden files\n"
            "  make update-golden K=bolt     # regenerate golden files matching 'bolt'"
        )

    if re.search(r"UPDATE_(?:BLUNDER_)?GOLDEN\s*=\s*1", stripped):
        block(
            "Blocked: don't pass UPDATE_GOLDEN / UPDATE_BLUNDER_GOLDEN as env vars. Use make targets instead:\n"
            "  make update-golden            # regenerate all golden files\n"
            "  make update-blunder-golden    # regenerate blunder prompt golden files"
        )

    # --- CI re-runs — never re-run CI, fix the root cause ---
    if re.search(r"\bgh\s+run\s+(?:rerun|retry)\b", stripped):
        block(
            "Blocked: never re-run CI to work around failures. Find and fix the root cause.\n"
            "If you believe it's a GitHub infrastructure issue, ask Gregor to re-run it."
        )

    # --- Maven build cache — use make clean ---
    if re.search(r"\.m2/build-cache\b", stripped) or re.search(
        r"maven\.build\.cache", stripped
    ):
        block(
            "Blocked: don't manipulate the Maven build cache directly. Use 'make clean' instead."
        )

    # --- /tmp/ — use tmp/ (repo-local) instead ---
    # Match /tmp as an absolute path, not as a component inside another path
    # (e.g. "dale-dragon-lily/tmp/foo" is fine, "/tmp/foo" is not).
    # The negative lookbehind rejects /tmp when preceded by path characters.
    if re.search(r"(?<![a-zA-Z0-9._-])/tmp(?:/|\s|$)", command):
        block("Blocked: never use /tmp/. Use tmp/ (repo-local scratch directory) instead.")


def main() -> None:
    hook_input = json.load(sys.stdin)
    command = hook_input["tool_input"]["command"]
    check(command)


if __name__ == "__main__":
    main()

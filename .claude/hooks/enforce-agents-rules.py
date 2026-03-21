#!/usr/bin/env python3
"""Pre-hook that enforces project rules by blocking prohibited Bash commands.

Reads JSON from stdin with tool_input.command. Exits 2 to block, 0 to allow.
"""

import json
import os
import re
import subprocess
import sys
import time


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


def check_generated_files(command: str) -> None:
    """Block commits where source files are staged but generated outputs are stale."""
    if not re.search(r"^\s*git\s+commit\b", command):
        return
    try:
        staged_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        staged = set(staged_result.stdout.strip().split("\n")) if staged_result.stdout.strip() else set()

        dirty_result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        dirty = set(dirty_result.stdout.strip().split("\n")) if dirty_result.stdout.strip() else set()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return

    # McpServer.java -> mcp-tools.json
    mcp_source = "Mage.Client.Bridge/src/main/java/mage/client/bridge/McpServer.java"
    mcp_output = "website/src/data/mcp-tools.json"
    if mcp_source in staged and mcp_output not in staged and mcp_output in dirty:
        block(
            "Blocked: McpServer.java is staged but mcp-tools.json has unstaged changes.\n"
            "Run: make regen-mcp-tools && git add website/src/data/mcp-tools.json"
        )

    # game-export-v*.schema.json -> game-export.d.ts
    # TypeScript types are generated from the latest schema version.
    schema_sources = {f for f in staged if f.startswith("schemas/game-export-v") and f.endswith(".schema.json")}
    schema_output = "website/src/types/game-export.d.ts"
    if schema_sources and schema_output not in staged and schema_output in dirty:
        block(
            "Blocked: game-export schema is staged but game-export.d.ts has unstaged changes.\n"
            "Run: make regen-schema-types && git add website/src/types/game-export.d.ts"
        )


def check_pr_preconditions(stripped: str) -> None:
    """Block gh pr create if make check hasn't been run recently."""
    if not re.search(r"\bgh\s+pr\s+create\b", stripped):
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    stamp = os.path.join(project_dir, "tmp", ".check-passed")

    if not os.path.exists(stamp):
        block(
            "Blocked: run 'make check' before creating a PR.\n"
            "No record of a passing 'make check' found."
        )

    stamp_mtime = os.path.getmtime(stamp)

    stamp_age = time.time() - stamp_mtime
    if stamp_age > 1800:  # 30 minutes
        block(
            "Blocked: run 'make check' before creating a PR.\n"
            f"Last 'make check' was {int(stamp_age // 60)} minutes ago — run it again."
        )

    # Check stamp is newer than HEAD commit
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        head_time = int(result.stdout.strip())
        if stamp_mtime < head_time:
            block(
                "Blocked: run 'make check' before creating a PR.\n"
                "New commits were made after the last 'make check' run."
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass


def check(command: str) -> None:
    stripped = strip_quotes(command)

    # --- Git rules (check stripped to ignore quoted strings, e.g. commit messages) ---

    if re.search(r"\bgit\s+rebase\b", stripped):
        block(
            "Blocked: we never rebase — always merge.\n"
            "Use 'git fetch origin && git merge origin/master'."
        )

    if re.search(r"\bgit\s+commit\b.*--amend\b", stripped):
        block("Blocked: never amend commits. Create a new commit instead.")

    if re.search(r"\bgit\s+push\b.*(--force-with-lease|--force|-f)\b", stripped):
        block("Blocked: never force-push. Create new commits and push normally.")

    if re.search(r"\bgit\s+reset\b", stripped):
        block(
            "Blocked: git reset rewrites history or destroys work.\n"
            "To unstage files, use 'git restore --staged <file>' instead."
        )

    # --- Generated file checks at commit time ---
    check_generated_files(command)

    # --- PR preconditions ---
    check_pr_preconditions(stripped)

    # --- Tool rules (check stripped to ignore quoted strings) ---

    if re.search(r"(?:^|\s|[;&|])\s*mvn\b", stripped):
        block(
            "Blocked: the Makefile handles classpaths and caching correctly.\n"
            "Use 'make build' or other make targets."
        )

    if re.search(r"(?:^|\s|[;&|])\s*np[mx]\b", stripped):
        block(
            "Blocked: each worktree has its own port and config.\n"
            "Use 'make website' or other make targets."
        )

    if re.search(r"(?:^|\s|[;&|])\s*(?:python3|pip3?)\b", stripped):
        block(
            "Blocked: all Python must go through uv for dependency management.\n"
            "Use 'uv run python ...', 'uv run --project puppeteer python -m ...', or 'uv add <pkg>'."
        )

    if re.search(r"(?:^|\s|[;&|])\s*(?:pkill|killall)\b", stripped):
        block(
            "Blocked: pkill/killall can kill other Claudes' dev servers in other worktrees.\n"
            "Kill specific PIDs instead."
        )

    if re.search(r"lsof\b.*\|.*kill\b", stripped):
        block(
            "Blocked: lsof piped to kill can kill other Claudes' dev servers.\n"
            "Kill specific PIDs instead."
        )

    # --- Expensive configs ---

    paid_configs = (
        "round-robin-1v1", "round-robin-commander", "round-robin-jumpstart",
    )
    paid_pattern = "|".join(re.escape(c) for c in paid_configs)
    if re.search(rf"make\s+run\b.*CONFIG\s*=\s*({paid_pattern})\b", stripped):
        block(
            "Blocked: this config consumes real API tokens and costs money.\n"
            "For testing, use free configs: 'make run' or 'make run CONFIG=jumpstart-dumb'."
        )

    # --- Golden tests — use make targets ---
    if re.search(r"(?:^|\s|[;&|])\s*make\s+regen-golden\b", stripped) and re.search(
        r"\bK\s*=", stripped
    ):
        block(
            "Blocked: never regenerate a single golden fixture.\n"
            "Run 'make regen-golden' to regenerate all golden files together."
        )

    if re.search(r"GOLDEN_INTEGRATION\s*=\s*1", stripped) or re.search(r"-m\s+golden", stripped):
        block(
            "Blocked: don't run golden tests directly. Use make targets instead:\n"
            "  make test-golden              # run all golden tests\n"
            "  make test-golden K=bolt       # run golden tests matching 'bolt'\n"
            "  make regen-golden             # regenerate all golden files\n"
        )

    if re.search(r"UPDATE_(?:BLUNDER_)?GOLDEN\s*=\s*1", stripped):
        block(
            "Blocked: don't pass UPDATE_GOLDEN / UPDATE_BLUNDER_GOLDEN as env vars. Use make targets instead:\n"
            "  make regen-golden             # regenerate all golden files (export + blunder prompts)"
        )

    # --- Expensive make targets ---
    if re.search(r"(?:^|\s|[;&|])\s*make\s+blunder-eval\b", stripped):
        block(
            "Blocked: 'make blunder-eval' costs money (LLM calls).\n"
            "Ask Gregor before running it."
        )

    if re.search(r"(?:^|\s|[;&|])\s*make\s+tournament-draft\b", stripped) or re.search(
        r"\bpython\b.*tournament_draft\.py\b", stripped
    ):
        block(
            "Blocked: tournament draft costs money (LLM API calls for every player).\n"
            "Ask Gregor before running it."
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
        block(
            "Blocked: use tmp/ (repo-local, gitignored, per-worktree) instead of /tmp/."
        )


def main() -> None:
    hook_input = json.load(sys.stdin)
    command = hook_input["tool_input"]["command"]
    check(command)


if __name__ == "__main__":
    main()

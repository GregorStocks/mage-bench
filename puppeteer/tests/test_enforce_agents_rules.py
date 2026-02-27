"""Tests for .claude/hooks/enforce-agents-rules.sh hook script."""

import json
import subprocess
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "enforce-agents-rules.sh"


def _run_hook(command: str) -> subprocess.CompletedProcess[str]:
    """Run the hook script with a simulated tool_input JSON on stdin."""
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        [str(HOOK_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
    )


class TestBlockedCommands:
    """Commands that must be blocked (exit 2)."""

    @pytest.mark.parametrize(
        "command",
        [
            "git rebase main",
            "git rebase origin/master",
            "git rebase -i HEAD~3",
        ],
    )
    def test_rebase_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "rebase" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "git commit --amend",
            "git commit --amend -m 'fix'",
            "git commit -a --amend",
        ],
    )
    def test_amend_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "amend" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "git push --force",
            "git push -f origin main",
            "git push --force-with-lease",
            "git push origin HEAD --force",
        ],
    )
    def test_force_push_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "force" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "mvn clean install",
            "mvn compile",
            "mvn -pl Mage.Server compile",
        ],
    )
    def test_mvn_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "mvn" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "npm install",
            "npm run dev",
            "npx astro dev",
            "npx tsc --noEmit",
        ],
    )
    def test_npm_npx_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "npm" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "python3 script.py",
            "pip install requests",
            "pip3 install requests",
        ],
    )
    def test_python3_pip_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "uv" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "pkill -f astro",
            "pkill node",
            "killall node",
        ],
    )
    def test_pkill_killall_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "kill" in result.stderr.lower()

    def test_lsof_pipe_kill_blocked(self) -> None:
        result = _run_hook("lsof -i :4321 | awk '{print $2}' | xargs kill")
        assert result.returncode == 2
        assert "kill" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "make run CONFIG=commander-gauntlet",
            "make run CONFIG=standard-gauntlet",
            "make run CONFIG=modern-gauntlet",
            "make run CONFIG=legacy-gauntlet",
            "make run CONFIG=commander-1v3",
            "make run CONFIG=round-robin-1v1",
            "make run CONFIG=round-robin-commander",
            "make run CONFIG=yente-1v1",
            "make run CONFIG=yente-commander",
        ],
    )
    def test_llm_configs_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "api tokens" in result.stderr.lower()


class TestAllowedCommands:
    """Commands that must be allowed (exit 0)."""

    @pytest.mark.parametrize(
        "command",
        [
            "make build",
            "make check",
            "make run",
            "make run CONFIG=standard-dumb",
            "make run CONFIG=modern-staller",
            "make website",
            "make mcp-tools",
            "git commit -m 'fix bug'",
            "git push origin HEAD",
            "git merge origin/master",
            "git status",
            "git diff",
            "git log --oneline -5",
            "uv run python script.py",
            "uv run --project puppeteer python -m pytest",
            "ls -la",
            "cat foo.txt",
            "grep -r pattern .",
            "echo hello",
        ],
    )
    def test_allowed(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 0, f"Unexpected block: {result.stderr}"

    @pytest.mark.parametrize(
        "command",
        [
            'git commit -m "Removed direct mvn and python3 from allow list"',
            "git commit -m 'Use npm/npx via make targets, not directly'",
            'echo "never use pip3 or python3 directly"',
            # Heredoc-style commit message mentioning tool names
            "git commit -m \"$(cat <<'EOF'\nNo direct mvn/npm/npx/python3/pip\nEOF\n)\"",
        ],
    )
    def test_tool_names_in_quotes_allowed(self, command: str) -> None:
        """Tool names inside quoted strings (commit messages, echo) must not trigger blocks."""
        result = _run_hook(command)
        assert result.returncode == 0, f"Unexpected block: {result.stderr}"

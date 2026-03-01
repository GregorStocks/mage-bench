"""Tests for .claude/hooks/enforce-agents-rules.py hook script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "enforce-agents-rules.py"


def _run_hook(command: str) -> subprocess.CompletedProcess[str]:
    """Run the hook script with a simulated tool_input JSON on stdin."""
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
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
        assert "make" in result.stderr.lower()

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
        assert "make" in result.stderr.lower()

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
            "make run CONFIG=commander-1v3",
            "make run CONFIG=round-robin-1v1",
            "make run CONFIG=round-robin-commander",
            "make run CONFIG=round-robin-jumpstart",
        ],
    )
    def test_llm_configs_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "api tokens" in result.stderr.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "cd puppeteer && GOLDEN_INTEGRATION=1 uv run pytest -m golden -v",
            "cd puppeteer && GOLDEN_INTEGRATION=1 UPDATE_GOLDEN=1 uv run pytest -m golden -v",
            'GOLDEN_INTEGRATION=1 uv run pytest -m golden -k "bolt"',
            "uv run pytest -m golden -v",
        ],
    )
    def test_golden_direct_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "make" in result.stderr.lower()


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
            "make test-golden",
            "make test-golden K=bolt",
            "make update-golden",
            "make update-golden K=dark_depths",
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

    @pytest.mark.parametrize(
        "command",
        [
            # Repo-local tmp/ directory is fine
            "ls tmp/",
            "cat > tmp/foo.md << 'EOF'\nstuff\nEOF",
            # /tmp/ as a path component inside a longer path is fine
            "rm dale-dragon-lily/tmp/test_hook.py",
            "rm /home/gregor/code/worktrees/dale-dragon-lily/tmp/foo.py",
        ],
    )
    def test_repo_local_tmp_allowed(self, command: str) -> None:
        """Repo-local tmp/ paths must not be blocked."""
        result = _run_hook(command)
        assert result.returncode == 0, f"Unexpected block: {result.stderr}"


class TestTmpBlocked:
    """System /tmp/ paths must be blocked."""

    @pytest.mark.parametrize(
        "command",
        [
            "cat > /tmp/replay.md << 'EOF'\nstuff\nEOF",
            "echo hello > /tmp/foo.txt",
            "ls /tmp/",
            "cat /tmp/foo.txt",
            "cp foo.txt /tmp/bar.txt",
            "tee /tmp/output.log",
            "ls /tmp",
        ],
    )
    def test_system_tmp_blocked(self, command: str) -> None:
        result = _run_hook(command)
        assert result.returncode == 2
        assert "/tmp/" in result.stderr.lower() or "tmp" in result.stderr.lower()

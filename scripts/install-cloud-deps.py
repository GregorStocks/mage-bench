"""Install dependencies needed for Claude Code cloud environments.

Called automatically via SessionStart hook. Fast no-op (~10ms) when
everything is already installed; only does real work on first run.
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKER = PROJECT_ROOT / "tmp" / ".cloud-deps-installed"
GH_VERSION = "2.67.0"


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, cwd=cwd)


def main() -> None:
    # Fast path: if we've already run successfully, skip everything.
    if MARKER.exists():
        sys.exit(0)

    print("Installing cloud environment dependencies...")

    # Ensure scratch directory exists (needed before worktree-setup.py runs)
    (PROJECT_ROOT / "tmp").mkdir(exist_ok=True)

    # Maven (needed for make build / make regen-golden)
    if not shutil.which("mvn"):
        print("Installing Maven...")
        run(["sudo", "apt-get", "update", "-qq"])
        run(["sudo", "apt-get", "install", "-y", "-qq", "maven"])

    # GitHub CLI (needed for claim-issue.py)
    if not shutil.which("gh"):
        print("Installing GitHub CLI...")
        gh_archive = f"gh_{GH_VERSION}_linux_amd64.tar.gz"
        tmp_dir = PROJECT_ROOT / "tmp"
        run(
            [
                "curl",
                "-fsSL",
                f"https://github.com/cli/cli/releases/download/v{GH_VERSION}/{gh_archive}",
                "-o",
                str(tmp_dir / gh_archive),
            ]
        )
        run(["tar", "xzf", str(tmp_dir / gh_archive), "-C", str(tmp_dir)])
        run(
            [
                "sudo",
                "cp",
                str(tmp_dir / f"gh_{GH_VERSION}_linux_amd64" / "bin" / "gh"),
                "/usr/local/bin/gh",
            ]
        )
        # Clean up
        (tmp_dir / gh_archive).unlink(missing_ok=True)
        gh_extracted = tmp_dir / f"gh_{GH_VERSION}_linux_amd64"
        if gh_extracted.exists():
            shutil.rmtree(gh_extracted)

    # Run workspace setup (creates tmp/, symlinks, .env, etc.)
    run(["uv", "run", "python", "scripts/worktree-setup.py"], cwd=PROJECT_ROOT)

    # Mark as done so subsequent sessions are instant.
    MARKER.touch()

    print("Cloud dependencies installed.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Conductor workspace setup script.

Creates shared directories and symlinks plugins/images for client modules.
Sets up per-worktree Maven local repository to prevent cross-worktree
artifact corruption.

Usage:
    worktree-setup.py
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SHARED_IMAGES = Path.home() / ".mage-bench" / "images"
CLIENT_MODULES = ["Mage.Client", "Mage.Client.Observer"]

# Port range for per-worktree dev servers (4321 is Astro's default)
PORT_RANGE_START = 4321
PORT_RANGE_SIZE = 200


def _find_main_worktree_root() -> Path | None:
    """Return the root of the main git worktree, or None if detection fails."""
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).parent


def _seed_m2_repo(m2_repo: Path) -> str | None:
    """Seed the per-worktree Maven local repository via CoW reflink copy.

    Returns a description of the seed source, or None if no seed was used.
    """
    main_root = _find_main_worktree_root()
    main_m2_repo = main_root / ".m2-repo" if main_root else None
    global_m2_repo = Path.home() / ".m2" / "repository"

    # Pick the best seed source
    if main_m2_repo and main_m2_repo != m2_repo and main_m2_repo.is_dir():
        source = main_m2_repo
        label = f"main worktree ({main_root})"
    elif global_m2_repo.is_dir():
        source = global_m2_repo
        label = "~/.m2/repository"
    else:
        m2_repo.mkdir(parents=True, exist_ok=True)
        return None

    # CoW reflink copy: near-instant on btrfs, falls back to regular copy elsewhere.
    # Resolve source to follow symlinks (e.g. ~/.m2/repository -> /other/disk),
    # otherwise cp -a copies the symlink itself instead of an isolated directory.
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(source.resolve()), str(m2_repo)],
        check=True,
    )
    return label


def main() -> None:
    # Create scratch directory (avoids /tmp permission issues in Claude Code)
    (PROJECT_ROOT / "tmp").mkdir(exist_ok=True)

    # Ensure shared Maven build cache directory exists
    (Path.home() / ".m2" / "build-cache").mkdir(parents=True, exist_ok=True)

    # Ensure shared images directory exists
    SHARED_IMAGES.mkdir(parents=True, exist_ok=True)

    # Per-worktree Maven local repository (prevents cross-worktree artifact corruption)
    m2_repo = PROJECT_ROOT / ".m2-repo"
    seed_source: str | None = None
    if not m2_repo.exists():
        seed_source = _seed_m2_repo(m2_repo)

    # Write .mvn/maven.config so ALL Maven invocations use the per-worktree repo
    # (Maven reads this from the project root's .mvn/ dir, even from subdirectories)
    maven_config = PROJECT_ROOT / ".mvn" / "maven.config"
    maven_config_content = f"-Dmaven.repo.local={m2_repo.resolve()}\n"
    if not maven_config.exists() or maven_config.read_text() != maven_config_content:
        maven_config.write_text(maven_config_content)

    # Symlink plugins/images to shared location for each client module
    for module in CLIENT_MODULES:
        plugins_dir = PROJECT_ROOT / module / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        images_link = plugins_dir / "images"

        if (
            images_link.is_symlink()
            and images_link.resolve() == SHARED_IMAGES.resolve()
        ):
            # Already a correct symlink, we're good
            pass
        elif images_link.is_symlink():
            # Symlink pointing to wrong target, fix it
            images_link.unlink()
            images_link.symlink_to(SHARED_IMAGES)
        elif images_link.is_dir():
            # Existing directory - move contents to shared location, then symlink
            print(f"Moving existing images from {module} to shared location...")
            for item in images_link.iterdir():
                dest = SHARED_IMAGES / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            shutil.rmtree(images_link)
            images_link.symlink_to(SHARED_IMAGES)
        else:
            # No existing directory, just create symlink
            images_link.symlink_to(SHARED_IMAGES)

    # Assign a stable, unique port for this worktree's dev server.
    # Each worktree gets a deterministic port derived from its directory name,
    # so multiple Claudes can run `make website` without colliding.
    worktree_name = PROJECT_ROOT.name
    port_hash = int(hashlib.sha256(worktree_name.encode()).hexdigest(), 16)
    port = PORT_RANGE_START + (port_hash % PORT_RANGE_SIZE)

    env_file = PROJECT_ROOT / ".env"
    port_line = f"WEBSITE_PORT={port}"
    existing_lines: list[str] = []
    if env_file.exists():
        existing_lines = env_file.read_text().splitlines()

    # Skip rewrite if port is already set correctly (idempotent fast path)
    if port_line not in existing_lines:
        env_lines = [
            line for line in existing_lines if not line.startswith("WEBSITE_PORT=")
        ]
        env_lines.append(port_line)
        env_file.write_text("\n".join(env_lines) + "\n")

    print("mage-bench workspace ready.")
    print(f"  Website port: {port} (for worktree '{worktree_name}')")
    print(f"  Maven repo: {m2_repo} (per-worktree)")
    if seed_source:
        print(f"    Seeded from {seed_source} (CoW reflink)")
    print("  Build cache: ~/.m2/build-cache")
    print("  Images: ~/.mage-bench/images (symlinked from */plugins/images)")


if __name__ == "__main__":
    main()

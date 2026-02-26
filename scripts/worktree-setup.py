#!/usr/bin/env python3
"""Conductor workspace setup script.

Creates shared directories and symlinks plugins/images for client modules.

Usage:
    worktree-setup.py
"""

import hashlib
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SHARED_IMAGES = Path.home() / ".mage-bench" / "images"
CLIENT_MODULES = ["Mage.Client", "Mage.Client.Observer"]

# Port range for per-worktree dev servers (4321 is Astro's default)
PORT_RANGE_START = 4321
PORT_RANGE_SIZE = 200


def main() -> None:
    # Create scratch directory (avoids /tmp permission issues in Claude Code)
    (PROJECT_ROOT / "tmp").mkdir(exist_ok=True)

    # Ensure shared Maven build cache directory exists
    (Path.home() / ".m2" / "build-cache").mkdir(parents=True, exist_ok=True)

    # Ensure shared images directory exists
    SHARED_IMAGES.mkdir(parents=True, exist_ok=True)

    # Symlink plugins/images to shared location for each client module
    for module in CLIENT_MODULES:
        plugins_dir = PROJECT_ROOT / module / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        images_link = plugins_dir / "images"

        if images_link.is_symlink():
            # Already a symlink, we're good
            pass
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
    env_lines: list[str] = []
    if env_file.exists():
        env_lines = [
            line
            for line in env_file.read_text().splitlines()
            if not line.startswith("WEBSITE_PORT=")
        ]
    env_lines.append(f"WEBSITE_PORT={port}")
    env_file.write_text("\n".join(env_lines) + "\n")

    print("mage-bench workspace ready.")
    print(f"  Website port: {port} (for worktree '{worktree_name}')")
    print("  Build cache: ~/.m2/build-cache")
    print("  Images: ~/.mage-bench/images (symlinked from */plugins/images)")


if __name__ == "__main__":
    main()

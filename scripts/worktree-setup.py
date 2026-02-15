#!/usr/bin/env python3
"""Conductor workspace setup script.

Creates shared directories and symlinks plugins/images for client modules.

Usage:
    worktree-setup.py
"""

import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SHARED_IMAGES = Path.home() / ".mage-bench" / "images"
CLIENT_MODULES = ["Mage.Client", "Mage.Client.Streaming"]


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

    print("mage-bench workspace ready.")
    print("  Build cache: ~/.m2/build-cache")
    print("  Images: ~/.mage-bench/images (symlinked from */plugins/images)")


if __name__ == "__main__":
    main()

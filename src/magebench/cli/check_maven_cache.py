#!/usr/bin/env python3
"""Check Maven build cache effectiveness.

Usage:
    check_maven_cache.py [maven-args]

Examples:
    check_maven_cache.py                    # Run default install
    check_maven_cache.py -pl Mage           # Build specific module
    check_maven_cache.py -DskipTests        # Skip tests
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(__file__).resolve().parents[3]
CACHE_DIR = Path.home() / ".m2" / "build-cache"

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
NC = "\033[0m"


def main() -> None:
    extra_args = sys.argv[1:]

    print("=== Maven Build Cache Check ===")
    print()

    # Show cache directory stats
    print(f"Cache directory: {CACHE_DIR}")
    if CACHE_DIR.is_dir():
        result = subprocess.run(["du", "-sh", str(CACHE_DIR)], capture_output=True, text=True)
        cache_size = result.stdout.split()[0] if result.stdout else "?"
        cache_modules = len(list(CACHE_DIR.rglob("buildinfo.xml")))
        print(f"  Size: {cache_size}")
        print(f"  Cached builds: {cache_modules}")
    else:
        print("  (directory does not exist yet)")
    print()

    cmd = ["mvn", "install", "-X", *extra_args]
    print(f"Running: {' '.join(cmd)}")
    print("(capturing output for analysis...)")
    print()

    # Run maven with debug output, capture lines while streaming to stdout
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        lines.append(line)
    proc.wait()
    build_success = proc.returncode == 0

    print()
    print("=== Cache Analysis ===")
    print()

    output = "".join(lines)
    cache_hits = output.count("Found cached build, restoring")
    cache_misses = output.count("Saved Build to local file")

    print(f"Cache hits:   {GREEN}{cache_hits}{NC}")
    print(f"Cache misses: {YELLOW}{cache_misses}{NC}")

    total = cache_hits + cache_misses
    if total > 0:
        hit_rate = cache_hits * 100 // total
        print(f"Hit rate:     {hit_rate}%")

    print()

    if cache_hits > 0:
        print("Modules restored from cache:")
        for line in output.splitlines():
            if "Found cached build, restoring" in line:
                print(f"  {line}")
        print()

    if cache_misses > 0:
        print("Modules saved to cache (cache miss):")
        count = 0
        for line in output.splitlines():
            if "Saved Build to local file" in line:
                print(f"  {line}")
                count += 1
                if count >= 10:
                    break
        print()

    if total == 0:
        print(f"{YELLOW}No cache activity detected in build output.{NC}")
        print("This could mean:")
        print("  - The build cache extension isn't active")
        print("  - The grep patterns don't match this Maven version's output")
        print("  - Check the raw output with: mvn install -X 2>&1 | grep -i cache")
        print()

    if build_success:
        print(f"{GREEN}Build completed successfully{NC}")
    else:
        print(f"{RED}Build failed{NC}")
        sys.exit(1)


if __name__ == "__main__":
    main()

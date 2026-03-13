"""Run each check target. Quiet by default (one line per step).

Pass -v for verbose (sequential) output.

All targets run in parallel by default. Wall-clock time is dominated
by the slowest target (usually `test`) rather than the sum of all targets.
"""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TARGETS = [
    "lint",
    "lint-website",
    "lint-md",
    "astro-check",
    "format-check",
    "typecheck",
    "test",
    "test-js",
    "verify-decks",
    "verify-schema-types",
    "verify-mcp-tools",
]


def _run_target(target: str) -> tuple[str, subprocess.CompletedProcess]:
    result = subprocess.run(["make", target], capture_output=True, text=True)
    return target, result


def main():
    verbose = "-v" in sys.argv[1:]

    if verbose:
        # Sequential with live output (for debugging)
        for target in TARGETS:
            result = subprocess.run(["make", target])
            if result.returncode != 0:
                sys.exit(result.returncode)
        return

    # Parallel execution
    t0 = time.monotonic()
    futures = {}
    with ThreadPoolExecutor(max_workers=len(TARGETS)) as pool:
        for target in TARGETS:
            futures[pool.submit(_run_target, target)] = target

        failed = []
        for future in as_completed(futures):
            target, result = future.result()
            elapsed = time.monotonic() - t0
            if result.returncode == 0:
                print(f"  {target:<25} ok  ({elapsed:.1f}s)")
            else:
                print(f"  {target:<25} FAIL ({elapsed:.1f}s)")
                failed.append((target, result))

    if failed:
        for target, result in failed:
            print(f"\n--- {target} ---")
            print(result.stdout, end="")
            print(result.stderr, end="")
        sys.exit(1)

    elapsed = time.monotonic() - t0
    print(f"\n  all checks passed in {elapsed:.1f}s")


if __name__ == "__main__":
    main()

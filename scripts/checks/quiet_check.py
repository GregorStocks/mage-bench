"""Run each check target. Quiet by default (one line per step).

Pass -v for verbose output.
"""

import subprocess
import sys

TARGETS = [
    "lint",
    "format-check",
    "typecheck",
    "test",
    "test-js",
    "verify-decks",
    "verify-schema-types",
    "verify-mcp-tools",
]


def main():
    verbose = "-v" in sys.argv[1:]

    for target in TARGETS:
        if verbose:
            result = subprocess.run(["make", target])
            if result.returncode != 0:
                sys.exit(result.returncode)
        else:
            print(f"  {target:<25}", end="", flush=True)
            result = subprocess.run(["make", target], capture_output=True, text=True)
            if result.returncode == 0:
                print("ok")
            else:
                print("FAIL")
                print()
                print(result.stdout, end="")
                print(result.stderr, end="")
                sys.exit(result.returncode)


if __name__ == "__main__":
    main()

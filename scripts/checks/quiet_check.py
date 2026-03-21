"""Run each check target. Quiet by default (one line per step).

Pass -v for verbose (sequential) output.

Independent targets run in parallel by default. Website/npm-backed targets run
sequentially to avoid contending over `website/node_modules`.
"""

import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TARGETS = [
    "lint",
    "lint-java",
    "lint-website",
    "lint-md",
    "astro-check",
    "format-check",
    "typecheck",
    "test",
    "test-js",
    "verify-decks",
    "verify-schema-types",
]

SERIAL_TARGETS = [
    "lint-website",
    "lint-md",
    "astro-check",
    "test-js",
    "verify-schema-types",
]

PARALLEL_TARGETS = [target for target in TARGETS if target not in SERIAL_TARGETS]

RECURSIVE_MAKE_ENV_VARS = (
    "GNUMAKEFLAGS",
    "MAKEFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "MAKE_TERMERR",
    "MAKE_TERMOUT",
    "MFLAGS",
)


def _make_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in RECURSIVE_MAKE_ENV_VARS:
        env.pop(key, None)
    return env


def _run_command_with_captured_output(
    command: list[str], *, env: dict[str, str]
) -> subprocess.CompletedProcess:
    # `subprocess.run(..., capture_output=True)` waits for pipe EOF, which can
    # hang if the target exits while a descendant still has stdout/stderr open.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_file:
        process = subprocess.Popen(
            command,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        returncode = process.wait()
        output_file.seek(0)
        output = output_file.read()
    return subprocess.CompletedProcess(command, returncode, output, "")


def _run_make(target: str, *, capture_output: bool) -> subprocess.CompletedProcess:
    command = ["make", target]
    env = _make_env()
    if capture_output:
        return _run_command_with_captured_output(command, env=env)
    return subprocess.run(
        command,
        capture_output=False,
        text=True,
        env=env,
    )


def _run_target(target: str) -> tuple[str, subprocess.CompletedProcess]:
    return target, _run_make(target, capture_output=True)


def _report_result(
    target: str,
    result: subprocess.CompletedProcess,
    *,
    elapsed: float,
    failed: list[tuple[str, subprocess.CompletedProcess]],
) -> None:
    if result.returncode == 0:
        print(f"  {target:<25} ok  ({elapsed:.1f}s)")
    else:
        print(f"  {target:<25} FAIL ({elapsed:.1f}s)")
        failed.append((target, result))


def main() -> None:
    verbose = "-v" in sys.argv[1:]

    if verbose:
        # Sequential with live output (for debugging)
        for target in TARGETS:
            result = _run_make(target, capture_output=False)
            if result.returncode != 0:
                sys.exit(result.returncode)
        return

    # Run independent targets in parallel, then website/npm-backed targets
    # sequentially so `npm install` does not race with itself in node_modules.
    t0 = time.monotonic()
    futures = {}
    with ThreadPoolExecutor(max_workers=len(PARALLEL_TARGETS)) as pool:
        for target in PARALLEL_TARGETS:
            futures[pool.submit(_run_target, target)] = target

        failed: list[tuple[str, subprocess.CompletedProcess]] = []
        for future in as_completed(futures):
            target, result = future.result()
            elapsed = time.monotonic() - t0
            _report_result(target, result, elapsed=elapsed, failed=failed)

    for target in SERIAL_TARGETS:
        result = _run_make(target, capture_output=True)
        elapsed = time.monotonic() - t0
        _report_result(target, result, elapsed=elapsed, failed=failed)

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

"""Run each check target. Quiet by default (one line per step).

Pass -v for verbose (sequential) output.

Independent targets run in parallel by default. Website/npm-backed targets run
sequentially to avoid contending over ``website/node_modules``.

On non-master branches, targets are skipped when their input files haven't
changed relative to origin/master. Set CHECK_ALL=1 to force all targets.
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
    "test-java",
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

# Maps each target to path prefixes and suffixes that should trigger it.
# A target runs if ANY changed file matches ANY of its triggers.
# Prefix matches use startswith; suffix matches (starting with *) use endswith.
TARGET_TRIGGERS: dict[str, list[str]] = {
    "lint": ["src/", "tests/", "issues/"],
    # "Mage." covers the Mage.* modules; "Mage/" covers the root Mage module
    # (prefix matching means "Mage." does not match "Mage/src/...").
    "lint-java": [
        "Mage.",
        "Mage/",
        "pom.xml",
        "src/magebench/cli/mcp_tools_json5.py",
        "website/src/data/mcp-tools.json5",
    ],
    "lint-website": ["website/"],
    "lint-md": ["*.md", ".markdownlint"],
    "astro-check": ["website/"],
    "format-check": ["src/", "tests/"],
    "typecheck": ["src/"],
    "test": [
        "src/",
        "tests/",
        "website/public/games/",
        "configs/",
    ],
    # only these modules' sources reach the TEST_JAVA_MODULES test classpaths
    "test-java": [
        "Mage.Server/",
        "Mage/",
        "Mage.Common/",
        "Mage.Sets/",
        "Mage.Client/",
        "Mage.Client.Bridge/",
        "Mage.Client.Observer/",
        "pom.xml",
    ],
    "test-js": ["website/"],
    "verify-decks": ["Mage.", "Mage/", "pom.xml"],
    "verify-schema-types": ["src/magebench/game/", "website/src/types/"],
}

# Files that, if changed, force all targets to run.
ALWAYS_RUN_TRIGGERS = [
    "Makefile",
    "src/magebench/cli/checks/",
    "pyproject.toml",
    "ruff-lint.toml",
    "uv.lock",
    ".mvn/",
]


def _file_matches(path: str, triggers: list[str]) -> bool:
    for trigger in triggers:
        if trigger.startswith("*"):
            if path.endswith(trigger[1:]):
                return True
        elif path.startswith(trigger):
            return True
    return False


def _changed_files_vs_master() -> list[str] | None:
    """Return files changed on this branch vs origin/master, or None to run all."""
    if os.environ.get("CHECK_ALL") == "1":
        return None

    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        master = subprocess.check_output(
            ["git", "rev-parse", "origin/master"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    if head == master:
        return None

    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", "origin/master", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    # Include both committed and uncommitted changes
    committed = subprocess.check_output(["git", "diff", "--name-only", merge_base, "HEAD"], text=True).strip()
    uncommitted = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], text=True).strip()
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True).strip()

    files: set[str] = set()
    for block in (committed, uncommitted, untracked):
        if block:
            files.update(block.splitlines())

    return sorted(files)


def _targets_to_run(targets: list[str]) -> tuple[list[str], list[str]]:
    """Return (targets_to_run, targets_to_skip)."""
    changed = _changed_files_vs_master()
    if changed is None:
        return targets, []

    if not changed:
        return [], targets

    # Check if any always-run trigger files changed
    for path in changed:
        if _file_matches(path, ALWAYS_RUN_TRIGGERS):
            return targets, []

    to_run = []
    to_skip = []
    for target in targets:
        triggers = TARGET_TRIGGERS.get(target)
        assert triggers is not None, f"no triggers defined for target {target!r}"
        if any(_file_matches(path, triggers) for path in changed):
            to_run.append(target)
        else:
            to_skip.append(target)

    return to_run, to_skip


def _make_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in RECURSIVE_MAKE_ENV_VARS:
        env.pop(key, None)
    return env


def _run_command_with_captured_output(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
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


def _run_target(target: str) -> tuple[str, subprocess.CompletedProcess, float]:
    t = time.monotonic()
    result = _run_make(target, capture_output=True)
    return target, result, time.monotonic() - t


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

    to_run, to_skip = _targets_to_run(TARGETS)

    if to_skip:
        for target in to_skip:
            print(f"  {target:<25} skip (no changes)")

    if not to_run:
        print("\n  all targets skipped — no relevant changes on this branch")
        return

    parallel = [t for t in to_run if t in PARALLEL_TARGETS]
    serial = [t for t in to_run if t in SERIAL_TARGETS]

    # Run independent targets in parallel, then website/npm-backed targets
    # sequentially so `npm install` does not race with itself in node_modules.
    t0 = time.monotonic()
    futures = {}
    failed: list[tuple[str, subprocess.CompletedProcess]] = []
    timings: list[tuple[str, float]] = []

    with ThreadPoolExecutor(max_workers=max(len(parallel), 1)) as pool:
        for target in parallel:
            futures[pool.submit(_run_target, target)] = target

        for future in as_completed(futures):
            target, result, duration = future.result()
            timings.append((target, duration))
            _report_result(target, result, elapsed=duration, failed=failed)

    for target in serial:
        t_serial = time.monotonic()
        result = _run_make(target, capture_output=True)
        duration = time.monotonic() - t_serial
        timings.append((target, duration))
        _report_result(target, result, elapsed=duration, failed=failed)

    if failed:
        for target, result in failed:
            print(f"\n--- {target} ---")
            print(result.stdout, end="")
            print(result.stderr, end="")
        sys.exit(1)

    elapsed = time.monotonic() - t0
    timings.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  all checks passed in {elapsed:.1f}s")
    print("\n  timing breakdown (slowest first):")
    total_cpu = 0.0
    for name, dur in timings:
        total_cpu += dur
        bar = "\u2588" * int(dur / 2)
        print(f"    {name:<25} {dur:6.1f}s  {bar}")
    parallel_wall = max(
        (dur for name, dur in timings if name in PARALLEL_TARGETS),
        default=0,
    )
    serial_wall = sum(dur for name, dur in timings if name in SERIAL_TARGETS)
    print(f"\n  parallel phase wall:  {parallel_wall:.1f}s")
    print(f"  serial phase wall:   {serial_wall:.1f}s")
    print(f"  sum of all targets:  {total_cpu:.1f}s")


if __name__ == "__main__":
    main()

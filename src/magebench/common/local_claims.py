import fcntl
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from magebench.common.issue_files import issue_stem, iter_issue_files

CLAIMS_ROOT_RELATIVE = Path("coordination") / "claims-v1"
ISSUE_PREFIXES = ("p1-", "p2-", "p3-", "p4-", "blocked-")


@dataclass(frozen=True)
class WorktreeContext:
    repo_root: Path
    git_common_dir: Path
    worktree_path: Path
    worktree_name: str
    branch: str


@dataclass(frozen=True)
class ClaimRecord:
    namespace: str
    key: str
    claim_path: Path
    worktree_path: Path
    worktree_name: str
    branch: str
    payload: dict[str, Any]


class ClaimConflictError(RuntimeError):
    pass


def _run_git(repo_root: Path | None, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    return result.stdout.strip()


def current_worktree_context(repo_root: Path | None = None) -> WorktreeContext:
    worktree_path = Path(_run_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    git_common_dir = Path(_run_git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    branch = _run_git(repo_root, "branch", "--show-current")
    assert branch, f"Expected current branch for worktree {worktree_path}"
    return WorktreeContext(
        repo_root=worktree_path,
        git_common_dir=git_common_dir,
        worktree_path=worktree_path,
        worktree_name=worktree_path.name,
        branch=branch,
    )


def claims_root(repo_root: Path | None = None) -> Path:
    return current_worktree_context(repo_root).git_common_dir / CLAIMS_ROOT_RELATIVE


def canonical_issue_key(issue_name: str) -> str:
    stem = issue_stem(issue_name)
    for prefix in ISSUE_PREFIXES:
        if stem.startswith(prefix):
            return stem.removeprefix(prefix)
    return stem


def resolve_issue_stem_for_key(issues_dir: Path, key: str) -> str | None:
    matches = [path.stem for path in iter_issue_files(issues_dir) if canonical_issue_key(path.stem) == key]
    if not matches:
        return None
    assert len(matches) == 1, f"Multiple issue files resolve to {key}: {matches}"
    return matches[0]


def _validate_key(key: str) -> str:
    assert key, "Claim key must not be empty"
    assert "/" not in key, f"Claim key must not contain '/': {key!r}"
    assert "\x00" not in key, "Claim key must not contain NUL"
    return key


def _namespace_dir(context: WorktreeContext, namespace: str) -> Path:
    return context.git_common_dir / CLAIMS_ROOT_RELATIVE / Path(namespace)


def _active_dir(context: WorktreeContext, namespace: str) -> Path:
    return _namespace_dir(context, namespace) / "active"


def _claim_path(context: WorktreeContext, namespace: str, key: str) -> Path:
    return _active_dir(context, namespace) / f"{_validate_key(key)}.json"


def _load_claim(path: Path, namespace: str) -> ClaimRecord:
    data = json.loads(path.read_text())
    assert isinstance(data, dict), f"Claim file must contain an object: {path}"
    key = str(data["key"])
    worktree_path = Path(str(data["worktree_path"]))
    worktree_name = str(data["worktree_name"])
    branch = str(data["branch"])
    return ClaimRecord(
        namespace=namespace,
        key=key,
        claim_path=path,
        worktree_path=worktree_path,
        worktree_name=worktree_name,
        branch=branch,
        payload=data,
    )


def _same_owner(record: ClaimRecord, context: WorktreeContext) -> bool:
    return record.worktree_path == context.worktree_path and record.branch == context.branch


def _active_worktree_branches(context: WorktreeContext) -> dict[Path, str | None]:
    output = _run_git(context.repo_root, "worktree", "list", "--porcelain")
    active: dict[Path, str | None] = {}
    current_path: Path | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
            active[current_path] = None
            continue
        if current_path is None:
            continue
        if line.startswith("branch "):
            branch_ref = line.removeprefix("branch ")
            active[current_path] = branch_ref.removeprefix("refs/heads/")
            continue
        if line == "detached":
            active[current_path] = None
    return active


def _cleanup_stale_claims_locked(context: WorktreeContext, namespace: str) -> None:
    active_dir = _active_dir(context, namespace)
    active_worktrees = _active_worktree_branches(context)
    for claim_file in active_dir.glob("*.json"):
        record = _load_claim(claim_file, namespace)
        current_branch = active_worktrees.get(record.worktree_path)
        if current_branch != record.branch:
            claim_file.unlink(missing_ok=True)


@contextmanager
def _locked_namespace(context: WorktreeContext, namespace: str) -> Iterator[Path]:
    namespace_dir = _namespace_dir(context, namespace)
    active_dir = namespace_dir / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    lock_path = namespace_dir / "allocator.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _cleanup_stale_claims_locked(context, namespace)
        yield active_dir
    finally:
        os.close(fd)


def _load_claims_locked(context: WorktreeContext, namespace: str) -> dict[str, ClaimRecord]:
    return {
        claim_file.stem: _load_claim(claim_file, namespace)
        for claim_file in sorted(_active_dir(context, namespace).glob("*.json"))
    }


def list_claims(namespace: str, repo_root: Path | None = None) -> list[ClaimRecord]:
    context = current_worktree_context(repo_root)
    with _locked_namespace(context, namespace):
        return list(_load_claims_locked(context, namespace).values())


def current_owner_claims(namespace: str, repo_root: Path | None = None) -> list[ClaimRecord]:
    context = current_worktree_context(repo_root)
    with _locked_namespace(context, namespace):
        claims = _load_claims_locked(context, namespace)
        return [record for record in claims.values() if _same_owner(record, context)]


def _claim_payload(
    context: WorktreeContext,
    namespace: str,
    key: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "namespace": namespace,
        "key": key,
        "worktree_path": str(context.worktree_path),
        "worktree_name": context.worktree_name,
        "branch": context.branch,
        "claimed_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
    }
    if metadata:
        payload.update(metadata)
    return payload


def _write_claim_locked(path: Path, payload: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def claim_exact_keys(
    namespace: str,
    requested_keys: list[str],
    *,
    repo_root: Path | None = None,
    metadata_by_key: dict[str, dict[str, Any]] | None = None,
) -> list[ClaimRecord]:
    context = current_worktree_context(repo_root)
    keys = [_validate_key(key) for key in requested_keys]
    with _locked_namespace(context, namespace):
        claims = _load_claims_locked(context, namespace)
        for key in keys:
            record = claims.get(key)
            if record is not None and not _same_owner(record, context):
                raise ClaimConflictError(
                    f"{namespace} claim {key} is already owned by {record.worktree_name} ({record.branch})"
                )

        selected: list[ClaimRecord] = []
        for key in keys:
            existing = claims.get(key)
            if existing is not None:
                selected.append(existing)
                continue
            path = _claim_path(context, namespace, key)
            payload = _claim_payload(
                context,
                namespace,
                key,
                None if metadata_by_key is None else metadata_by_key.get(key),
            )
            _write_claim_locked(path, payload)
            record = _load_claim(path, namespace)
            claims[key] = record
            selected.append(record)
        return selected


def claim_first_available_keys(
    namespace: str,
    candidate_keys: list[str],
    count: int,
    *,
    repo_root: Path | None = None,
    metadata_by_key: dict[str, dict[str, Any]] | None = None,
) -> list[ClaimRecord]:
    assert count > 0, f"count must be positive, got {count}"
    context = current_worktree_context(repo_root)
    deduped_keys = list(dict.fromkeys(_validate_key(key) for key in candidate_keys))
    with _locked_namespace(context, namespace):
        claims = _load_claims_locked(context, namespace)
        selected: list[ClaimRecord] = []
        for key in deduped_keys:
            if len(selected) >= count:
                break
            record = claims.get(key)
            if record is not None:
                if _same_owner(record, context):
                    selected.append(record)
                continue
            path = _claim_path(context, namespace, key)
            payload = _claim_payload(
                context,
                namespace,
                key,
                None if metadata_by_key is None else metadata_by_key.get(key),
            )
            _write_claim_locked(path, payload)
            record = _load_claim(path, namespace)
            claims[key] = record
            selected.append(record)
        return selected

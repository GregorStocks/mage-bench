from pathlib import Path
from unittest.mock import patch

import pytest

from magebench.common import local_claims


def _context(tmp_path: Path, name: str, branch: str) -> local_claims.WorktreeContext:
    worktree_path = tmp_path / name
    worktree_path.mkdir(parents=True, exist_ok=True)
    git_common_dir = tmp_path / "git-common"
    git_common_dir.mkdir(parents=True, exist_ok=True)
    return local_claims.WorktreeContext(
        repo_root=worktree_path,
        git_common_dir=git_common_dir,
        worktree_path=worktree_path,
        worktree_name=name,
        branch=branch,
    )


def test_claim_exact_keys_is_idempotent_for_same_worktree(tmp_path: Path) -> None:
    context = _context(tmp_path, "wt-one", "feature")

    with (
        patch.object(local_claims, "current_worktree_context", return_value=context),
        patch.object(
            local_claims,
            "_active_worktree_branches",
            return_value={context.worktree_path: context.branch},
        ),
    ):
        first = local_claims.claim_exact_keys("issues", ["bug-a"])
        second = local_claims.claim_exact_keys("issues", ["bug-a"])
        listed = local_claims.list_claims("issues")

    assert [record.key for record in first] == ["bug-a"]
    assert [record.key for record in second] == ["bug-a"]
    assert [record.key for record in listed] == ["bug-a"]


def test_claim_exact_keys_conflicts_for_other_worktree(tmp_path: Path) -> None:
    first_context = _context(tmp_path, "wt-one", "feature-a")
    second_context = _context(tmp_path, "wt-two", "feature-b")
    active = {
        first_context.worktree_path: first_context.branch,
        second_context.worktree_path: second_context.branch,
    }

    with (
        patch.object(local_claims, "current_worktree_context", return_value=first_context),
        patch.object(local_claims, "_active_worktree_branches", return_value=active),
    ):
        local_claims.claim_exact_keys("issues", ["bug-a"])

    with (
        patch.object(local_claims, "current_worktree_context", return_value=second_context),
        patch.object(local_claims, "_active_worktree_branches", return_value=active),
        pytest.raises(local_claims.ClaimConflictError, match="bug-a"),
    ):
        local_claims.claim_exact_keys("issues", ["bug-a"])


def test_claim_first_available_keys_skips_other_owner_and_claims_next(
    tmp_path: Path,
) -> None:
    first_context = _context(tmp_path, "wt-one", "feature-a")
    second_context = _context(tmp_path, "wt-two", "feature-b")
    active = {
        first_context.worktree_path: first_context.branch,
        second_context.worktree_path: second_context.branch,
    }

    with (
        patch.object(local_claims, "current_worktree_context", return_value=first_context),
        patch.object(local_claims, "_active_worktree_branches", return_value=active),
    ):
        local_claims.claim_exact_keys("games/fast", ["game_20260301_010101"])

    with (
        patch.object(local_claims, "current_worktree_context", return_value=second_context),
        patch.object(local_claims, "_active_worktree_branches", return_value=active),
    ):
        claimed = local_claims.claim_first_available_keys(
            "games/fast",
            ["game_20260301_010101", "game_20260301_020202", "game_20260301_030303"],
            2,
        )

    assert [record.key for record in claimed] == [
        "game_20260301_020202",
        "game_20260301_030303",
    ]


def test_list_claims_drops_stale_branch_reuse(tmp_path: Path) -> None:
    context = _context(tmp_path, "wt-one", "feature-a")

    with (
        patch.object(local_claims, "current_worktree_context", return_value=context),
        patch.object(
            local_claims,
            "_active_worktree_branches",
            return_value={context.worktree_path: context.branch},
        ),
    ):
        local_claims.claim_exact_keys("issues", ["bug-a"])

    with (
        patch.object(local_claims, "current_worktree_context", return_value=context),
        patch.object(
            local_claims,
            "_active_worktree_branches",
            return_value={context.worktree_path: "other-branch"},
        ),
    ):
        listed = local_claims.list_claims("issues")

    assert listed == []
    assert not (context.git_common_dir / "coordination" / "claims-v1" / "issues" / "active" / "bug-a.json").exists()

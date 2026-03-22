import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from magebench.common.local_claims import ClaimConflictError, ClaimRecord

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_script(path: str):
    spec = importlib.util.spec_from_file_location(path.replace("/", "_"), SCRIPTS_DIR / path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


claim_games = _import_script("analysis/claim_games.py")


def _record(game_id: str, namespace: str) -> ClaimRecord:
    return ClaimRecord(
        namespace=namespace,
        key=game_id,
        claim_path=Path(f"/tmp/{game_id}.json"),
        worktree_path=Path("/tmp/wt"),
        worktree_name="wt",
        branch="feature",
        payload={"key": game_id},
    )


def test_claim_games_exact_ids(capsys: pytest.CaptureFixture[str]) -> None:
    game_ids = ["game_20260301_010101", "game_20260301_020202"]

    with (
        patch.object(sys, "argv", ["claim_games.py", "--type", "fast", *game_ids]),
        patch.object(claim_games, "claim_exact_keys") as mock_claim_exact,
        patch.object(claim_games, "game_path_for_id", side_effect=lambda game_id: Path(f"/games/{game_id}.json5")),
    ):
        claim_games.main()

    mock_claim_exact.assert_called_once()
    assert mock_claim_exact.call_args.args == ("games/fast", game_ids)
    assert capsys.readouterr().out == "".join(f"/games/{game_id}.json5\n" for game_id in game_ids)


def test_claim_games_exact_ids_conflict_exits_1() -> None:
    with (
        patch.object(sys, "argv", ["claim_games.py", "--type", "deep", "game_20260301_010101"]),
        patch.object(
            claim_games,
            "claim_exact_keys",
            side_effect=ClaimConflictError("already claimed"),
        ),
        pytest.raises(SystemExit, match="1"),
    ):
        claim_games.main()


def test_claim_games_exact_ids_raise_when_export_missing() -> None:
    game_id = "game_20260301_010101"

    with (
        patch.object(sys, "argv", ["claim_games.py", "--type", "deep", game_id]),
        patch.object(claim_games, "claim_exact_keys"),
        patch.object(
            claim_games,
            "game_path_for_id",
            side_effect=AssertionError("missing export"),
        ),
        pytest.raises(AssertionError, match="missing export"),
    ):
        claim_games.main()


def test_claim_games_auto_mode_claims_requested_count(capsys: pytest.CaptureFixture[str]) -> None:
    candidates = [
        Path("/games/game_20260301_010101.json5"),
        Path("/games/game_20260301_020202.json5"),
        Path("/games/game_20260301_030303.json5"),
    ]
    claimed = [
        _record("game_20260301_010101", "games/fast"),
        _record("game_20260301_020202", "games/fast"),
    ]

    with (
        patch.object(sys, "argv", ["claim_games.py", "--type", "fast", "--count", "2"]),
        patch.object(claim_games, "find_unanalyzed", return_value=candidates) as mock_find,
        patch.object(claim_games, "claim_first_available_keys", return_value=claimed) as mock_claim,
        patch.object(claim_games, "game_path_for_id", side_effect=lambda game_id: Path(f"/games/{game_id}.json5")),
    ):
        claim_games.main()

    mock_find.assert_called_once_with("fast", None, 30, include_claimed=True)
    assert mock_claim.call_args.args == (
        "games/fast",
        ["game_20260301_010101", "game_20260301_020202", "game_20260301_030303"],
        2,
    )
    assert capsys.readouterr().out == "/games/game_20260301_010101.json5\n/games/game_20260301_020202.json5\n"

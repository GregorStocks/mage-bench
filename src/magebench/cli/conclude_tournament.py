#!/usr/bin/env python3
"""Advance from a crowned champion into the next regular season.

Usage:
    python -m magebench.cli.conclude_tournament
"""

import json
from pathlib import Path

from magebench.cli.tournament_game import (
    BETWEEN_SEASONS_PHASE,
    REGULAR_SEASON_PHASE,
    get_tournament_champion_seed,
)
from magebench.leaderboard.website_data import copy_season_data

_ROOT = Path(__file__).resolve().parents[3]
_SEASON_FILE = _ROOT / "data" / "season.json"


def _load_season() -> dict:
    assert _SEASON_FILE.exists(), f"Season file not found: {_SEASON_FILE}"
    season_data = json.loads(_SEASON_FILE.read_text())
    assert isinstance(season_data, dict), f"{_SEASON_FILE}: expected JSON object"
    assert season_data["phase"] == BETWEEN_SEASONS_PHASE, (
        f"Season {season_data['current_season']} is in phase "
        f"'{season_data['phase']}', expected '{BETWEEN_SEASONS_PHASE}'"
    )
    return season_data


def _save_season(season_data: dict) -> None:
    _SEASON_FILE.write_text(json.dumps(season_data, indent=2) + "\n")


def main() -> int:
    season_data = _load_season()
    season_num = season_data["current_season"]
    tournament_rel = season_data.get("tournament")
    assert tournament_rel is not None, f"Season {season_num} has no active tournament pointer"
    tournament_path = _ROOT / tournament_rel
    assert tournament_path.exists(), f"Tournament file not found: {tournament_path}"
    tournament = json.loads(tournament_path.read_text())
    assert tournament["season"] == season_num, (
        f"Tournament season {tournament['season']} does not match current season {season_num}"
    )

    champion_seed = tournament.get("champion_seed")
    assert champion_seed is not None, (
        "Tournament champion has not been recorded yet. Run 'make tournament-game' after the finals complete."
    )
    computed_champion_seed = get_tournament_champion_seed(tournament)
    assert computed_champion_seed == champion_seed, (
        f"Tournament champion_seed {champion_seed} does not match finals winner {computed_champion_seed}"
    )

    entrants_by_seed = {entrant["seed"]: entrant for entrant in tournament["entrants"]}
    champion = entrants_by_seed[champion_seed]

    season_data["current_season"] = season_num + 1
    season_data["phase"] = REGULAR_SEASON_PHASE
    season_data["tournament"] = None
    _save_season(season_data)
    copy_season_data(
        data_dir=_ROOT / "website" / "src" / "data",
        season_json=_SEASON_FILE,
    )

    print(f"Season {season_num} champion: #{champion_seed} {champion['display_name']}")
    print(f"Season {season_num + 1} moved to regular season -> {_SEASON_FILE}")
    print(f"Tournament record preserved at {tournament_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

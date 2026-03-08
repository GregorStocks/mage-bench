#!/usr/bin/env python3
"""Run tournament bracket matches.

Reads the current tournament from data/season.json -> data/tournaments/season-N.json,
determines the next match to play, creates a game config on the fly, runs it via
the orchestrator, and records the result back to the tournament JSON.

Usage:
    python scripts/tournament_game.py             # play the next match
    python scripts/tournament_game.py --games 3   # play the next 3 matches
"""

import argparse
import json
import subprocess
from pathlib import Path

from puppeteer.config import (
    _generate_player_name,
    load_models,
    load_personalities,
)

_ROOT = Path(__file__).resolve().parent.parent
_SEASON_FILE = _ROOT / "data" / "season.json"


# -- Tournament loading --


def load_tournament() -> tuple[dict, Path]:
    """Load the current tournament JSON. Returns (tournament_data, file_path)."""
    assert _SEASON_FILE.exists(), f"Season file not found: {_SEASON_FILE}"
    season_data = json.loads(_SEASON_FILE.read_text())
    assert season_data["phase"] == "tournament", (
        f"Season {season_data['current_season']} is in phase "
        f"'{season_data['phase']}', expected 'tournament'"
    )
    tournament_path = _ROOT / season_data["tournament"]
    assert tournament_path.exists(), f"Tournament file not found: {tournament_path}"
    tournament = json.loads(tournament_path.read_text())
    return tournament, tournament_path


# -- Bracket generation --


def generate_bracket(size: int) -> list[tuple[int, int]]:
    """Generate first-round matchups for a seeded single-elimination bracket.

    Uses the standard "fold" algorithm to ensure top seeds are maximally separated:
      [1, 2] -> [1, 4, 2, 3] -> [1, 8, 4, 5, 2, 7, 3, 6]
    Then pairs adjacent entries: (1,8), (4,5), (2,7), (3,6).
    """
    assert size >= 2 and (size & (size - 1)) == 0, (
        f"Bracket size must be a power of 2, got {size}"
    )
    positions = [1, 2]
    while len(positions) < size:
        next_size = len(positions) * 2
        expanded = []
        for seed in positions:
            expanded.append(seed)
            expanded.append(next_size + 1 - seed)
        positions = expanded

    return [(positions[i], positions[i + 1]) for i in range(0, len(positions), 2)]


def round_name(num_matches: int) -> str:
    """Return a human-readable name for a round based on match count."""
    names = {1: "Finals", 2: "Semifinals", 4: "Quarterfinals"}
    return names.get(num_matches, f"Round of {num_matches * 2}")


# -- Bracket state management --


def _build_round(round_num: int, matchups: list[tuple[int, int]]) -> dict:
    """Build a round dict from a list of (seed_a, seed_b) matchups."""
    return {
        "round": round_num,
        "name": round_name(len(matchups)),
        "matches": [
            {
                "match": i + 1,
                "seed_a": a,
                "seed_b": b,
                "winner_seed": None,
                "game_id": None,
            }
            for i, (a, b) in enumerate(matchups)
        ],
    }


def find_next_match(tournament: dict) -> tuple[dict, dict] | None:
    """Find the next unplayed match in the tournament bracket.

    Generates rounds on demand:
    - If rounds is empty, creates round 1 from the bracket seedings.
    - If the current round is complete, creates the next round from winners.

    Returns (round_dict, match_dict) for the next match to play, or None if
    the tournament is complete.

    Modifies tournament["rounds"] in place when generating new rounds.
    """
    rounds = tournament["rounds"]
    size = tournament["size"]

    # Generate round 1 if needed
    if not rounds:
        bracket = generate_bracket(size)
        rounds.append(_build_round(1, bracket))

    while True:
        current_round = rounds[-1]
        matches = current_round["matches"]

        # Find first unplayed match in current round
        for match in matches:
            if match["winner_seed"] is None:
                return current_round, match

        # Current round is complete — generate next round if possible
        if len(matches) == 1:
            # Finals are done — tournament is complete
            return None

        # Pair adjacent winners for next round
        winners = [m["winner_seed"] for m in matches]
        next_matchups = [
            (winners[i], winners[i + 1]) for i in range(0, len(winners), 2)
        ]
        next_round_num = current_round["round"] + 1
        rounds.append(_build_round(next_round_num, next_matchups))


# -- Deck file management --


def write_tournament_deck(
    project_root: Path, seed: int, card_lines: list[str], half_decks: list[str]
) -> Path:
    """Write a tournament decklist to a .dck file. Returns path relative to project root."""
    tmp_dir = project_root / "tmp" / "tournament-decks"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    deck_name = " + ".join(half_decks)
    content = f"NAME:{deck_name}\n" + "\n".join(card_lines) + "\n"
    dck_path = tmp_dir / f"seed-{seed}.dck"
    dck_path.write_text(content)
    return dck_path.relative_to(project_root)


# -- Game config generation --


def build_game_config(
    tournament: dict,
    seed_a: int,
    seed_b: int,
    project_root: Path,
) -> Path:
    """Build a game config JSON for a tournament match. Returns config file path."""
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    draft = tournament["draft"]
    decklists = draft["decklists"]

    players = []
    for seed in (seed_a, seed_b):
        entrant = entrants_by_seed[seed]
        decklist = decklists[str(seed)]

        deck_path = write_tournament_deck(
            project_root, seed, decklist["cards"], decklist["half_decks"]
        )

        player: dict = {
            "type": "pilot",
            "preset": entrant["preset"],
            "personality": entrant["personality"],
            "deck": str(deck_path),
        }
        players.append(player)

    config = {
        "tournamentGame": True,
        "gameType": "Two Player Duel",
        "deckType": "Limited",
        "matchTimeLimit": "MIN__60",
        "matchBufferTime": "NONE",
        "players": players,
    }

    config_dir = project_root / "tmp" / "tournament-configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"seed{seed_a}-vs-seed{seed_b}.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path


# -- Game result extraction --


def find_latest_game_dir() -> Path:
    """Find the most recently created game directory."""
    logs_dir = Path.home() / ".mage-bench" / "logs"
    assert logs_dir.exists(), f"Logs directory not found: {logs_dir}"
    game_dirs = list(logs_dir.glob("game_*"))
    assert game_dirs, f"No game directories found in {logs_dir}"
    return max(game_dirs, key=lambda p: p.name)


def read_game_winner(game_dir: Path) -> str | None:
    """Read the winner from server_game_events.jsonl in the game directory."""
    events_file = game_dir / "server_game_events.jsonl"
    assert events_file.exists(), f"No server_game_events.jsonl in {game_dir}"
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        if event.get("type") == "game_end":
            return event.get("winner")
    return None


def map_winner_to_seed(
    winner_name: str,
    seed_a: int,
    seed_b: int,
    tournament: dict,
) -> int:
    """Map a winner's XMage player name back to their tournament seed."""
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    models_data = load_models(None)
    personalities = load_personalities(None)

    name_to_seed: dict[str, int] = {}
    for seed in (seed_a, seed_b):
        entrant = entrants_by_seed[seed]
        name = _generate_player_name(
            entrant["model"], entrant["personality"], models_data, personalities
        )
        name_to_seed[name] = seed

    assert winner_name in name_to_seed, (
        f"Winner name {winner_name!r} doesn't match any entrant. "
        f"Expected one of: {name_to_seed}"
    )
    return name_to_seed[winner_name]


# -- Main logic --


def run_match(tournament: dict, tournament_path: Path) -> bool:
    """Run the next tournament match. Returns True if a match was played, False if tournament is complete."""
    result = find_next_match(tournament)
    if result is None:
        # Tournament is complete — find the champion
        final_round = tournament["rounds"][-1]
        champion_seed = final_round["matches"][0]["winner_seed"]
        entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
        champion = entrants_by_seed[champion_seed]
        print(
            f"Tournament is complete! Champion: #{champion_seed} {champion['display_name']}"
        )
        return False

    round_dict, match = result
    seed_a = match["seed_a"]
    seed_b = match["seed_b"]
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    name_a = entrants_by_seed[seed_a]["display_name"]
    name_b = entrants_by_seed[seed_b]["display_name"]

    print(f"\n{'=' * 60}")
    print(f"{round_dict['name']} — Match {match['match']}")
    print(f"  #{seed_a} {name_a}  vs  #{seed_b} {name_b}")
    print(f"{'=' * 60}\n")

    # Save rounds state before running (in case of crash, bracket structure is preserved)
    tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")

    # Build config and run the game
    config_path = build_game_config(tournament, seed_a, seed_b, _ROOT)

    rc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "puppeteer",
            "python",
            "-m",
            "puppeteer",
            "--observer",
            "--record",
            "--config",
            str(config_path),
        ],
        cwd=_ROOT,
    ).returncode

    assert rc == 0, f"Orchestrator exited with code {rc}"

    # Determine winner
    game_dir = find_latest_game_dir()
    winner_name = read_game_winner(game_dir)
    assert winner_name is not None, (
        f"No winner found in {game_dir}. Check server_game_events.jsonl for details."
    )

    winner_seed = map_winner_to_seed(winner_name, seed_a, seed_b, tournament)
    winner_display = entrants_by_seed[winner_seed]["display_name"]
    loser_seed = seed_b if winner_seed == seed_a else seed_a
    loser_display = entrants_by_seed[loser_seed]["display_name"]

    # Record result
    match["winner_seed"] = winner_seed
    match["game_id"] = game_dir.name
    tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")

    print(f"\n{'=' * 60}")
    print(
        f"RESULT: #{winner_seed} {winner_display} defeats #{loser_seed} {loser_display}"
    )
    print(f"Game: {game_dir.name}")
    print(f"{'=' * 60}\n")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tournament bracket matches")
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Number of sequential matches to play (default: 1)",
    )
    args = parser.parse_args()
    assert args.games >= 1, f"--games must be >= 1, got {args.games}"

    tournament, tournament_path = load_tournament()
    assert "draft" in tournament, (
        "Tournament has no draft results. Run 'make tournament-draft' first."
    )

    total_matches = tournament["size"] - 1
    played = sum(
        1
        for r in tournament.get("rounds", [])
        for m in r["matches"]
        if m["winner_seed"] is not None
    )
    remaining = total_matches - played

    print(f"Tournament: Season {tournament['season']}, {tournament['size']} players")
    print(
        f"Format: best-of-{tournament['best_of']}, {tournament['elimination']} elimination"
    )
    print(f"Progress: {played}/{total_matches} matches played, {remaining} remaining")

    games_to_play = min(args.games, remaining)
    if games_to_play == 0:
        # Tournament might be complete — call run_match to print champion
        run_match(tournament, tournament_path)
        return 0

    for i in range(games_to_play):
        if games_to_play > 1:
            print(f"\n--- Match {i + 1} of {games_to_play} ---")
        if not run_match(tournament, tournament_path):
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

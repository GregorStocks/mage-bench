#!/usr/bin/env python3
"""Run tournament bracket matches.

Reads the current tournament from data/season.json -> data/tournaments/season-N.json,
determines the next match to play, creates a game config on the fly, runs it via
the orchestrator, and records the result back to the tournament JSON.

Usage:
    python scripts/tournament_game.py            # play the next match
    python scripts/tournament_game.py --games 8  # play up to 8 matches in parallel
"""

import argparse
import concurrent.futures
import json
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from puppeteer.config import (
    _generate_player_name,
    load_models,
    load_personalities,
)
from puppeteer.orchestrator import (
    AnnotationFailure,
    clean_stale_h2_locks,
    compile_project,
    refresh_observer_resources,
    resolve_annotation_failures,
    upload_and_export,
)
from scripts.export_game import read_game_winner
from scripts.generate_leaderboard import generate_all_website_data

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


def _build_round(
    round_num: int, matchups: Sequence[tuple[int | None, int | None]]
) -> dict:
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
                "games": [],
            }
            for i, (a, b) in enumerate(matchups)
        ],
    }


def _init_bracket(tournament: dict) -> None:
    """Initialize all bracket rounds upfront with null seeds for future rounds.

    The website expects all rounds to be present (not generated on demand),
    so we create the full bracket structure immediately.
    """
    rounds = tournament["rounds"]
    size = tournament["size"]
    bracket = generate_bracket(size)
    rounds.append(_build_round(1, bracket))
    # Pre-generate placeholder rounds (null seeds until matchups are known)
    num_matches = len(bracket) // 2
    round_num = 2
    while num_matches >= 1:
        rounds.append(
            _build_round(
                round_num,
                [(None, None)] * num_matches,
            )
        )
        num_matches //= 2
        round_num += 1


def _advance_round(rounds: list[dict], round_idx: int) -> None:
    """Fill in the next round's matchups from the completed round's winners."""
    completed = rounds[round_idx]
    next_round = rounds[round_idx + 1]
    winners = [m["winner_seed"] for m in completed["matches"]]
    for i, match in enumerate(next_round["matches"]):
        match["seed_a"] = winners[i * 2]
        match["seed_b"] = winners[i * 2 + 1]


def find_ready_matches(tournament: dict) -> list[tuple[dict, dict]]:
    """Find all playable matches in the current round.

    Returns a list of (round_dict, match_dict) tuples for matches
    that have known seeds but no winner yet. Advances completed rounds
    and initializes brackets as needed.

    Modifies tournament["rounds"] in place.
    """
    rounds = tournament["rounds"]

    if not rounds:
        _init_bracket(tournament)

    for i, current_round in enumerate(rounds):
        matches = current_round["matches"]

        all_complete = all(m["winner_seed"] is not None for m in matches)
        if all_complete:
            if i + 1 < len(rounds) and rounds[i + 1]["matches"][0]["seed_a"] is None:
                _advance_round(rounds, i)
            continue

        ready = []
        for match in matches:
            if match["winner_seed"] is None and match["seed_a"] is not None:
                ready.append((current_round, match))
        return ready

    return []


def find_next_match(tournament: dict) -> tuple[dict, dict] | None:
    """Find the next unplayed match. Returns (round_dict, match_dict) or None."""
    ready = find_ready_matches(tournament)
    return ready[0] if ready else None


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
        "skipPostGamePrompts": True,
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

_write_lock = threading.Lock()


def _save_tournament(tournament: dict, tournament_path: Path) -> None:
    """Thread-safe tournament JSON save."""
    with _write_lock:
        tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")


def _parse_game_dir(output: str) -> Path:
    """Parse the game directory path from orchestrator output."""
    for line in output.splitlines():
        if "Game logs:" in line:
            return Path(line.split("Game logs:")[-1].strip())
    raise AssertionError(
        "Could not find 'Game logs:' in orchestrator output.\n"
        f"Output (last 2000 chars):\n{output[-2000:]}"
    )


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


def _run_single_game(
    tournament: dict,
    seed_a: int,
    seed_b: int,
    quiet: bool = False,
    skip_compile: bool = False,
) -> tuple[Path, int]:
    """Run a single game between two seeds. Returns (game_dir, winner_seed).

    Args:
        quiet: If True, suppress live output (used for parallel matches).
        skip_compile: If True, pass --skip-compile to orchestrator (caller already compiled).
    """
    config_path = build_game_config(tournament, seed_a, seed_b, _ROOT)

    cmd = [
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
    ]
    if skip_compile:
        cmd.append("--skip-compile")

    proc = subprocess.Popen(
        cmd,
        cwd=_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    game_dir = None
    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        if not quiet:
            print(line, end="", flush=True)
        if game_dir is None and "Game logs:" in line:
            game_dir = Path(line.split("Game logs:")[-1].strip())

    rc = proc.wait()
    output = "".join(output_lines)
    assert rc == 0, (
        f"Orchestrator exited with code {rc}\n"
        f"Output (last 2000 chars):\n{output[-2000:]}"
    )
    if game_dir is None:
        game_dir = _parse_game_dir(output)

    winner_name = read_game_winner(game_dir)
    assert winner_name is not None, (
        f"No winner found in {game_dir}. Check server_game_events.jsonl for details."
    )

    winner_seed = map_winner_to_seed(winner_name, seed_a, seed_b, tournament)
    return game_dir, winner_seed


def _run_match_on(
    tournament: dict,
    tournament_path: Path,
    round_dict: dict,
    match: dict,
    quiet: bool = False,
    skip_compile: bool = False,
    deferred_failures: list[AnnotationFailure] | None = None,
) -> None:
    """Run a specific match (best-of-N series)."""
    seed_a = match["seed_a"]
    seed_b = match["seed_b"]
    best_of = tournament["best_of"]
    wins_needed = best_of // 2 + 1
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    name_a = entrants_by_seed[seed_a]["display_name"]
    name_b = entrants_by_seed[seed_b]["display_name"]

    print(f"\n{'=' * 60}")
    print(f"{round_dict['name']} — Match {match['match']} (best of {best_of})")
    print(f"  #{seed_a} {name_a}  vs  #{seed_b} {name_b}")
    print(f"{'=' * 60}\n")

    # Play games until one player reaches wins_needed
    wins = {seed_a: 0, seed_b: 0}

    for game_num in range(1, best_of + 1):
        if best_of > 1:
            print(
                f"\n--- Game {game_num} of {best_of} (series: {wins[seed_a]}-{wins[seed_b]}) ---"
            )

        game_dir, winner_seed = _run_single_game(
            tournament, seed_a, seed_b, quiet=quiet, skip_compile=skip_compile
        )
        wins[winner_seed] += 1

        match["games"].append(
            {
                "game_id": game_dir.name,
                "winner_seed": winner_seed,
            }
        )

        # Save after each game so partial series survive crashes
        _save_tournament(tournament, tournament_path)

        # Upload to YouTube + export + blunder analysis (orchestrator skips
        # these for tournament games; we handle it here after the bracket
        # JSON is updated so the export finds the tournament context)
        upload_and_export(
            game_dir,
            _ROOT,
            deferred_failures=deferred_failures,
        )

        winner_display = entrants_by_seed[winner_seed]["display_name"]
        print(
            f"  Game {game_num}: #{winner_seed} {winner_display} wins ({wins[seed_a]}-{wins[seed_b]})"
        )

        if wins[winner_seed] >= wins_needed:
            break

    # Record match winner
    match_winner = seed_a if wins[seed_a] >= wins_needed else seed_b
    match_loser = seed_b if match_winner == seed_a else seed_a
    match["winner_seed"] = match_winner
    _save_tournament(tournament, tournament_path)

    winner_display = entrants_by_seed[match_winner]["display_name"]
    loser_display = entrants_by_seed[match_loser]["display_name"]

    print(f"\n{'=' * 60}")
    print(
        f"RESULT: #{match_winner} {winner_display} defeats "
        f"#{match_loser} {loser_display} ({wins[seed_a]}-{wins[seed_b]})"
    )
    print(f"{'=' * 60}\n")


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

    # Save rounds state before running (in case of crash, bracket structure is preserved)
    _save_tournament(tournament, tournament_path)

    _run_match_on(tournament, tournament_path, round_dict, match)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tournament bracket matches")
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Number of matches to play in parallel (default: 1)",
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

    # Pre-compile once so parallel subprocesses can skip compilation.
    # This avoids 4 concurrent Maven builds fighting over the same build dir,
    # and prevents H2 lock file races between sibling processes.
    parallel = games_to_play > 1
    if parallel:
        print("\nPre-compiling before parallel execution...")
        assert compile_project(_ROOT, observer=True), "Compilation failed"
        assert refresh_observer_resources(_ROOT), "Observer resource refresh failed"

        # Clean stale H2 lock files once before spawning parallel servers
        clean_stale_h2_locks(_ROOT)

    deferred_failures: list[AnnotationFailure] = []
    matches_played = 0
    while matches_played < games_to_play:
        ready = find_ready_matches(tournament)
        if not ready:
            run_match(tournament, tournament_path)  # prints champion
            break

        batch_size = min(len(ready), games_to_play - matches_played)
        batch = ready[:batch_size]

        if batch_size == 1:
            # Single match — run with live output
            round_dict, match = batch[0]
            _save_tournament(tournament, tournament_path)
            _run_match_on(
                tournament,
                tournament_path,
                round_dict,
                match,
                skip_compile=parallel,
                deferred_failures=deferred_failures,
            )
        else:
            # Multiple matches — run in parallel
            _save_tournament(tournament, tournament_path)
            print(f"\nStarting {batch_size} matches in parallel...\n")
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=batch_size
            ) as executor:
                futures = {
                    executor.submit(
                        _run_match_on,
                        tournament,
                        tournament_path,
                        round_dict,
                        match,
                        quiet=True,
                        skip_compile=True,
                        deferred_failures=deferred_failures,
                    ): match
                    for round_dict, match in batch
                }
                for future in concurrent.futures.as_completed(futures):
                    future.result()  # re-raise any exceptions

        matches_played += batch_size

    # Resolve any deferred annotation failures
    resolve_annotation_failures(deferred_failures, _ROOT)

    generate_all_website_data()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

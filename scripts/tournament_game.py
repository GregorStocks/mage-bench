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
import json
import threading
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from puppeteer.config import (
    Config,
    generate_player_name,
    load_models,
    load_personalities,
)
from puppeteer.log import setup_logging
from puppeteer.orchestrator import (
    clean_stale_h2_locks,
    compile_project,
    refresh_observer_resources,
    run_orchestrator,
)
from puppeteer.post_game_analysis import (
    AnnotationFailure,
    resolve_annotation_failures,
    upload_and_export,
)
from scripts.export_game import read_game_winner
from scripts.generate_leaderboard import generate_all_website_data

_ROOT = Path(__file__).resolve().parent.parent
_SEASON_FILE = _ROOT / "data" / "season.json"

REGULAR_SEASON_PHASE = "regular-season"
TOURNAMENT_PHASE = "tournament"
BETWEEN_SEASONS_PHASE = "between-seasons"
ACTIVE_TOURNAMENT_PHASES = frozenset({TOURNAMENT_PHASE, BETWEEN_SEASONS_PHASE})


# -- Tournament loading --


def load_season(allowed_phases: Collection[str] | None = None) -> dict:
    """Load data/season.json, optionally asserting the current phase."""
    assert _SEASON_FILE.exists(), f"Season file not found: {_SEASON_FILE}"
    season_data = json.loads(_SEASON_FILE.read_text())
    assert isinstance(season_data, dict), f"{_SEASON_FILE}: expected JSON object"
    if allowed_phases is not None:
        phase = season_data["phase"]
        assert phase in allowed_phases, (
            f"Season {season_data['current_season']} is in phase '{phase}', expected one of {sorted(allowed_phases)}"
        )
    return season_data


def _save_season(season_data: dict) -> None:
    """Persist data/season.json."""
    _SEASON_FILE.write_text(json.dumps(season_data, indent=2) + "\n")


def load_tournament(
    allowed_phases: Collection[str] | None = None,
) -> tuple[dict, Path]:
    """Load the current tournament JSON. Returns (tournament_data, file_path)."""
    season_data = load_season(allowed_phases or (TOURNAMENT_PHASE,))
    tournament_rel = season_data.get("tournament")
    assert tournament_rel is not None, (
        f"Season {season_data['current_season']} has no active tournament pointer"
    )
    tournament_path = _ROOT / tournament_rel
    assert tournament_path.exists(), f"Tournament file not found: {tournament_path}"
    tournament = json.loads(tournament_path.read_text())
    assert isinstance(tournament, dict), f"{tournament_path}: expected JSON object"
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

        return [
            (current_round, match)
            for match in matches
            if match["winner_seed"] is None and match["seed_a"] is not None
        ]

    return []


def find_next_match(tournament: dict) -> tuple[dict, dict] | None:
    """Find the next unplayed match. Returns (round_dict, match_dict) or None."""
    ready = find_ready_matches(tournament)
    return ready[0] if ready else None


def tournament_is_complete(tournament: dict) -> bool:
    """Return True once every bracket match has a recorded winner."""
    rounds = tournament["rounds"]
    if not rounds:
        return False
    matches = [match for round_dict in rounds for match in round_dict["matches"]]
    expected_matches = tournament["size"] - 1
    assert len(matches) == expected_matches, (
        f"Expected {expected_matches} bracket matches, found {len(matches)}"
    )
    return all(match["winner_seed"] is not None for match in matches)


def get_tournament_champion_seed(tournament: dict) -> int | None:
    """Return the champion seed once the bracket is fully complete."""
    if not tournament_is_complete(tournament):
        return None
    final_round = tournament["rounds"][-1]
    assert len(final_round["matches"]) == 1, (
        f"Final round must contain exactly one match, found {len(final_round['matches'])}"
    )
    champion_seed = final_round["matches"][0]["winner_seed"]
    assert champion_seed is not None, "Complete tournament is missing a finals winner"
    assert isinstance(champion_seed, int) and not isinstance(champion_seed, bool), (
        f"Champion seed must be an int, got {champion_seed!r}"
    )
    return champion_seed


def _finalize_completed_tournament_state(
    tournament: dict, tournament_path: Path
) -> bool:
    """Persist champion data and switch the season into the between-seasons phase."""
    champion_seed = get_tournament_champion_seed(tournament)
    if champion_seed is None:
        return False

    entrants_by_seed = {entrant["seed"]: entrant for entrant in tournament["entrants"]}
    assert champion_seed in entrants_by_seed, (
        f"Champion seed {champion_seed} not found in entrants"
    )
    champion = entrants_by_seed[champion_seed]
    changed = False

    if tournament.get("champion_seed") != champion_seed:
        tournament["champion_seed"] = champion_seed
        changed = True
    if tournament.get("completed_at") is None:
        tournament["completed_at"] = datetime.now(UTC).isoformat()
        changed = True
    if changed:
        _save_tournament(tournament, tournament_path)

    season_data = load_season(ACTIVE_TOURNAMENT_PHASES)
    tournament_rel = season_data.get("tournament")
    assert tournament_rel is not None, (
        f"Season {season_data['current_season']} has no active tournament pointer"
    )
    expected_tournament_path = (_ROOT / tournament_rel).resolve()
    assert expected_tournament_path == tournament_path.resolve(), (
        f"Season points at {expected_tournament_path}, not {tournament_path.resolve()}"
    )
    if season_data["phase"] == TOURNAMENT_PHASE:
        season_data["phase"] = BETWEEN_SEASONS_PHASE
        _save_season(season_data)
        print(
            f"Season {season_data['current_season']} championship is complete. "
            "Champion crowned; regular-season games remain blocked until "
            "you run 'make conclude-tournament'."
        )

    print(
        f"Tournament is complete! Champion: #{champion_seed} {champion['display_name']}"
    )
    return True


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


@dataclass
class MatchSeries:
    """Mutable state for an in-progress best-of-N match."""

    match: dict
    wins: dict[int, int]


def _load_match_wins(match: dict, seed_a: int, seed_b: int) -> dict[int, int]:
    """Reconstruct current series score from already-recorded games."""
    wins = {seed_a: 0, seed_b: 0}
    for game in match["games"]:
        winner_seed = game["winner_seed"]
        assert winner_seed in wins, (
            f"Recorded game winner {winner_seed} is not part of match {seed_a} vs {seed_b}"
        )
        wins[winner_seed] += 1
    return wins


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
        name = generate_player_name(
            entrant["model"], entrant["personality"], models_data, personalities
        )
        name_to_seed[name] = seed

    assert winner_name in name_to_seed, (
        f"Winner name {winner_name!r} doesn't match any entrant. Expected one of: {name_to_seed}"
    )
    return name_to_seed[winner_name]


# -- Main logic --


def _make_runner_config(
    config_paths: list[Path],
    *,
    skip_compile: bool,
) -> Config:
    """Build an orchestrator Config for one or more tournament games."""
    assert config_paths, "Tournament runner requires at least one config file"
    return Config(
        config_file=config_paths[0],
        batch_config_files=config_paths if len(config_paths) > 1 else [],
        observer=True,
        record=True,
        num_games=len(config_paths),
        skip_compile=skip_compile,
    )


def _print_match_header(
    round_dict: dict, match: dict, entrants_by_seed: dict[int, dict], best_of: int
) -> None:
    """Print the standard per-match banner."""
    seed_a = match["seed_a"]
    seed_b = match["seed_b"]
    name_a = entrants_by_seed[seed_a]["display_name"]
    name_b = entrants_by_seed[seed_b]["display_name"]
    print(f"\n{'=' * 60}")
    print(f"{round_dict['name']} — Match {match['match']} (best of {best_of})")
    print(f"  #{seed_a} {name_a}  vs  #{seed_b} {name_b}")
    print(f"{'=' * 60}\n")


def _complete_match_if_decided(
    tournament: dict,
    tournament_path: Path,
    match: dict,
    wins: dict[int, int],
    wins_needed: int,
    entrants_by_seed: dict[int, dict],
) -> bool:
    """Set and print the match winner once a player has enough game wins."""
    for seed, count in wins.items():
        if count < wins_needed:
            continue
        match_winner = seed
        match_loser = (
            match["seed_b"] if match_winner == match["seed_a"] else match["seed_a"]
        )
        match["winner_seed"] = match_winner
        _save_tournament(tournament, tournament_path)
        winner_display = entrants_by_seed[match_winner]["display_name"]
        loser_display = entrants_by_seed[match_loser]["display_name"]
        print(f"\n{'=' * 60}")
        print(
            f"RESULT: #{match_winner} {winner_display} defeats "
            f"#{match_loser} {loser_display} ({wins[match['seed_a']]}-{wins[match['seed_b']]})"
        )
        print(f"{'=' * 60}\n")
        return True
    return False


def _read_game_result(
    game_dir: Path,
    seed_a: int,
    seed_b: int,
    tournament: dict,
) -> tuple[Path, int]:
    """Read a finished game's winner and map it back to a tournament seed."""
    winner_name = read_game_winner(game_dir)
    assert winner_name is not None, (
        f"No winner found in {game_dir}. Check server_game_events.jsonl for details."
    )
    winner_seed = map_winner_to_seed(winner_name, seed_a, seed_b, tournament)
    return game_dir, winner_seed


def _run_games(
    tournament: dict,
    matchups: list[tuple[int, int]],
    *,
    skip_compile: bool = False,
) -> list[tuple[Path, int]]:
    """Run one game for each matchup and return the winner for each."""
    assert matchups, "_run_games requires at least one matchup"
    config_paths = [
        build_game_config(tournament, seed_a, seed_b, _ROOT)
        for seed_a, seed_b in matchups
    ]
    result = run_orchestrator(
        _make_runner_config(config_paths, skip_compile=skip_compile),
        project_root=_ROOT,
    )
    assert result.exit_code == 0, f"Orchestrator exited with code {result.exit_code}"
    assert len(result.sessions) == len(matchups), (
        f"Expected {len(matchups)} game sessions, got {len(result.sessions)}"
    )
    return [
        _read_game_result(session.game_dir, seed_a, seed_b, tournament)
        for session, (seed_a, seed_b) in zip(result.sessions, matchups, strict=False)
    ]


def _record_match_game(
    tournament: dict,
    tournament_path: Path,
    match: dict,
    wins: dict[int, int],
    wins_needed: int,
    entrants_by_seed: dict[int, dict],
    game_dir: Path,
    winner_seed: int,
    result_label: str,
    deferred_failures: list[AnnotationFailure] | None = None,
) -> bool:
    """Persist a finished game and update the surrounding match state."""
    wins[winner_seed] += 1
    match["games"].append(
        {
            "game_id": game_dir.name,
            "winner_seed": winner_seed,
        }
    )
    _save_tournament(tournament, tournament_path)
    upload_and_export(
        game_dir,
        _ROOT,
        deferred_failures=deferred_failures,
    )

    winner_display = entrants_by_seed[winner_seed]["display_name"]
    print(
        f"  {result_label}: #{winner_seed} {winner_display} wins ({wins[match['seed_a']]}-{wins[match['seed_b']]})"
    )
    return _complete_match_if_decided(
        tournament,
        tournament_path,
        match,
        wins,
        wins_needed,
        entrants_by_seed,
    )


def _run_match_on(
    tournament: dict,
    tournament_path: Path,
    round_dict: dict,
    match: dict,
    *,
    skip_compile: bool = False,
    deferred_failures: list[AnnotationFailure] | None = None,
) -> None:
    """Run a specific match (best-of-N series)."""
    seed_a = match["seed_a"]
    seed_b = match["seed_b"]
    best_of = tournament["best_of"]
    wins_needed = best_of // 2 + 1
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    _print_match_header(round_dict, match, entrants_by_seed, best_of)

    # Play games until one player reaches wins_needed
    wins = _load_match_wins(match, seed_a, seed_b)
    if _complete_match_if_decided(
        tournament, tournament_path, match, wins, wins_needed, entrants_by_seed
    ):
        return

    for game_num in range(len(match["games"]) + 1, best_of + 1):
        if best_of > 1:
            print(
                f"\n--- Game {game_num} of {best_of} (series: {wins[seed_a]}-{wins[seed_b]}) ---"
            )

        game_dir, winner_seed = _run_games(
            tournament,
            [(seed_a, seed_b)],
            skip_compile=skip_compile,
        )[0]
        if _record_match_game(
            tournament,
            tournament_path,
            match,
            wins,
            wins_needed,
            entrants_by_seed,
            game_dir,
            winner_seed,
            f"Game {game_num}",
            deferred_failures,
        ):
            break


def _run_match_batch(
    tournament: dict,
    tournament_path: Path,
    batch: list[tuple[dict, dict]],
    *,
    skip_compile: bool = False,
    deferred_failures: list[AnnotationFailure] | None = None,
) -> None:
    """Run multiple tournament matches in parallel on one XMage server per round."""
    best_of = tournament["best_of"]
    wins_needed = best_of // 2 + 1
    entrants_by_seed = {e["seed"]: e for e in tournament["entrants"]}
    active_series: list[MatchSeries] = []

    for round_dict, match in batch:
        _print_match_header(round_dict, match, entrants_by_seed, best_of)
        wins = _load_match_wins(match, match["seed_a"], match["seed_b"])
        if not _complete_match_if_decided(
            tournament, tournament_path, match, wins, wins_needed, entrants_by_seed
        ):
            active_series.append(MatchSeries(match, wins))

    while active_series:
        for series in active_series:
            game_num = len(series.match["games"]) + 1
            if best_of > 1:
                print(
                    f"\n--- Match {series.match['match']} Game {game_num} of {best_of} "
                    f"(series: {series.wins[series.match['seed_a']]}-{series.wins[series.match['seed_b']]}) ---"
                )

        results = _run_games(
            tournament,
            [
                (series.match["seed_a"], series.match["seed_b"])
                for series in active_series
            ],
            skip_compile=skip_compile,
        )

        next_active: list[MatchSeries] = []
        for series, (game_dir, winner_seed) in zip(
            active_series, results, strict=False
        ):
            if not _record_match_game(
                tournament,
                tournament_path,
                series.match,
                series.wins,
                wins_needed,
                entrants_by_seed,
                game_dir,
                winner_seed,
                f"Match {series.match['match']} Game {len(series.match['games']) + 1}",
                deferred_failures,
            ):
                next_active.append(series)

        active_series = next_active


def run_match(tournament: dict, tournament_path: Path) -> bool:
    """Run the next tournament match. Returns True if a match was played, False if tournament is complete."""
    result = find_next_match(tournament)
    if result is None:
        _finalize_completed_tournament_state(tournament, tournament_path)
        return False

    round_dict, match = result

    # Save rounds state before running (in case of crash, bracket structure is preserved)
    _save_tournament(tournament, tournament_path)

    _run_match_on(tournament, tournament_path, round_dict, match)
    return True


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Run tournament bracket matches")
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Number of matches to play in parallel (default: 1)",
    )
    args = parser.parse_args()
    assert args.games >= 1, f"--games must be >= 1, got {args.games}"

    tournament, tournament_path = load_tournament(ACTIVE_TOURNAMENT_PHASES)
    assert "draft" in tournament, (
        "Tournament has no draft results. Run 'make tournament-draft' first."
    )

    total_matches = tournament["size"] - 1
    played = sum(
        1
        for r in tournament["rounds"]
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

    # Pre-compile once so repeated orchestrator batches can skip compilation.
    parallel = games_to_play > 1
    if parallel:
        print("\nPre-compiling before parallel execution...")
        assert compile_project(_ROOT, observer=True), "Compilation failed"
        assert refresh_observer_resources(_ROOT), "Observer resource refresh failed"

        # Clean stale H2 lock files once before starting the first shared server
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
            # Multiple matches — run one game per match on a shared server batch
            _save_tournament(tournament, tournament_path)
            print(
                f"\nStarting {batch_size} matches in parallel on one XMage server...\n"
            )
            _run_match_batch(
                tournament,
                tournament_path,
                batch,
                skip_compile=True,
                deferred_failures=deferred_failures,
            )

        matches_played += batch_size

    _finalize_completed_tournament_state(tournament, tournament_path)

    # Resolve any deferred annotation failures
    resolve_annotation_failures(deferred_failures)

    generate_all_website_data()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

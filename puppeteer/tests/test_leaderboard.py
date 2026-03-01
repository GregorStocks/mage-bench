"""Tests for leaderboard generation: OpenSkill ratings, placement, aggregation."""

import gzip
import json
import tempfile
from pathlib import Path

import pytest

from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.leaderboard import (
    _player_key,
    _split_key,
    capitalize_provider,
    compute_openskill_ratings,
    compute_thinking_time,
    derive_display_name,
    derive_format,
    extract_placements,
    generate_all_leaderboards,
    generate_internals_data,
    generate_leaderboard,
    generate_leaderboard_file,
    generate_model_stats,
    load_model_registry,
)


def _make_game(
    game_id: str,
    timestamp: str,
    winner: str | None,
    players: list[dict],
) -> dict:
    return {
        "id": game_id,
        "timestamp": timestamp,
        "totalTurns": 10,
        "winner": winner,
        "players": players,
        "annotations": [],
    }


def _pilot(
    name: str,
    model: str,
    cost: float = 1.0,
    placement: int | None = None,
    tool_calls_ok: int | None = None,
    tool_calls_failed: int | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    d: dict = {"name": name, "type": "pilot", "model": model, "totalCostUsd": cost}
    if placement is not None:
        d["placement"] = placement
    if tool_calls_ok is not None:
        d["toolCallsOk"] = tool_calls_ok
    if tool_calls_failed is not None:
        d["toolCallsFailed"] = tool_calls_failed
    if reasoning_effort is not None:
        d["reasoningEffort"] = reasoning_effort
    return d


def _cpu(name: str) -> dict:
    return {"name": name, "type": "cpu", "commander": "Some Commander"}


# --- capitalize_provider ---


def test_capitalize_provider_known():
    assert capitalize_provider("anthropic") == "Anthropic"
    assert capitalize_provider("google") == "Google"
    assert capitalize_provider("openai") == "OpenAI"
    assert capitalize_provider("mistralai") == "Mistral AI"
    assert capitalize_provider("deepseek") == "DeepSeek"


def test_capitalize_provider_fallback():
    assert capitalize_provider("newprovider") == "Newprovider"


# --- derive_display_name ---


def test_derive_display_name():
    assert derive_display_name("mistralai/devstral-small") == "Devstral Small"
    assert derive_display_name("openai/gpt-4.1-mini") == "Gpt 4.1 Mini"


def test_derive_display_name_no_slash():
    assert derive_display_name("standalone") == "Standalone"


# --- load_model_registry ---


def test_load_model_registry():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "models": [
                    {"id": "anthropic/claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
                    {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
                ]
            },
            f,
        )
        path = Path(f.name)

    try:
        registry = load_model_registry(path)
        assert registry == {
            "anthropic/claude-sonnet-4.5": "Claude Sonnet 4.5",
            "google/gemini-2.5-flash": "Gemini 2.5 Flash",
        }
    finally:
        path.unlink()


def test_load_model_registry_missing_file():
    assert load_model_registry(Path("/nonexistent/models.json")) == {}


# --- extract_placements ---


def test_extract_placements_from_player_field():
    game = _make_game(
        "g1",
        "20260101_000000",
        "Alice",
        [
            _pilot("Alice", "a/x", placement=1),
            _pilot("Bob", "b/y", placement=2),
            _pilot("Carol", "c/z", placement=3),
        ],
    )
    result = extract_placements(game)
    assert result == {"Alice": 1, "Bob": 2, "Carol": 3}


def test_extract_placements_from_winner_only():
    """When no placement field and no game files, uses winner field."""
    game = _make_game(
        "g1",
        "20260101_000000",
        "Alice",
        [_pilot("Alice", "a/x"), _pilot("Bob", "b/y")],
    )
    result = extract_placements(game)
    assert result["Alice"] == 1
    assert result["Bob"] == 2


def test_extract_placements_no_winner():
    game = _make_game(
        "g1",
        "20260101_000000",
        None,
        [_pilot("Alice", "a/x"), _pilot("Bob", "b/y")],
    )
    result = extract_placements(game)
    assert result == {}


def test_extract_placements_from_game_file():
    """Falls back to reading full game JSON for elimination order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        games_dir = Path(tmpdir)
        game_data = {
            "actions": [
                {"seq": 100, "message": "Carol has lost the game."},
                {"seq": 200, "message": "Bob has lost the game."},
            ]
        }
        (games_dir / "g1.json.gz").write_bytes(gzip.compress(json.dumps(game_data).encode()))

        game = _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x"), _pilot("Bob", "b/y"), _pilot("Carol", "c/z")],
        )
        result = extract_placements(game, games_dir)
        assert result == {"Alice": 1, "Bob": 2, "Carol": 3}


# --- compute_ratings ---


def test_ratings_no_games():
    ratings, per_game = compute_openskill_ratings([])
    assert ratings == {}
    assert per_game == []


def test_ratings_winner_gains():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1),
                _pilot("Bob", "b/model-b", placement=2),
                _pilot("Carol", "c/model-c", placement=3),
                _pilot("Dave", "d/model-d", placement=4),
            ],
        )
    ]
    ratings, _per_game = compute_openskill_ratings(games)
    # Winner should have highest rating (winner-takes-all: all losers are tied)
    assert ratings["a/model-a"] > ratings["b/model-b"]
    assert ratings["b/model-b"] == ratings["c/model-c"]
    assert ratings["c/model-c"] == ratings["d/model-d"]


def test_ratings_no_placements_no_change():
    """Games with no placement data should not change ratings."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            None,
            [_pilot("Alice", "a/model-a"), _pilot("Bob", "b/model-b")],
        )
    ]
    _ratings, per_game = compute_openskill_ratings(games)
    assert len(per_game) == 1
    # Ratings should be equal (both start the same, no update)
    assert per_game[0]["players"][0]["ratingBefore"] == per_game[0]["players"][0]["ratingAfter"]


def test_ratings_chronological_order():
    """Ratings should build up across games processed chronologically."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        ),
        _make_game(
            "g2",
            "20260102_000000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        ),
    ]
    _ratings, per_game = compute_openskill_ratings(games)
    # After 2 wins, Alice should be higher than after 1 win
    assert per_game[1]["players"][0]["ratingBefore"] > per_game[0]["players"][0]["ratingBefore"]


def test_ratings_per_game_snapshots():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        )
    ]
    _, per_game = compute_openskill_ratings(games)
    assert len(per_game) == 1
    assert per_game[0]["id"] == "g1"
    assert len(per_game[0]["players"]) == 2

    alice = next(p for p in per_game[0]["players"] if p["key"] == "a/x")
    assert alice["ratingAfter"] > alice["ratingBefore"]

    bob = next(p for p in per_game[0]["players"] if p["key"] == "b/y")
    assert bob["ratingAfter"] < bob["ratingBefore"]


def test_ratings_skips_non_pilots():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "CPU1",
            [_cpu("CPU1"), _pilot("Alice", "a/x")],
        )
    ]
    ratings, per_game = compute_openskill_ratings(games)
    assert "a/x" in ratings
    assert len(per_game[0]["players"]) == 1


def test_ratings_full_ordering():
    """Winner-takes-all: winner is rated highest, all losers are tied."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/a", placement=1),
                _pilot("Bob", "b/b", placement=2),
                _pilot("Carol", "c/c", placement=3),
                _pilot("Dave", "d/d", placement=4),
            ],
        )
    ]
    ratings, _ = compute_openskill_ratings(games)
    # Winner is rated highest
    assert ratings["a/a"] > ratings["b/b"]
    # All losers are tied (winner-takes-all scoring)
    assert ratings["b/b"] == ratings["c/c"] == ratings["d/d"]


# --- compute_openskill_ratings ---


def test_openskill_ratings_no_games():
    ratings, per_game = compute_openskill_ratings([])
    assert ratings == {}
    assert per_game == []


def test_openskill_ratings_winner_gains():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1),
                _pilot("Bob", "b/model-b", placement=2),
                _pilot("Carol", "c/model-c", placement=3),
                _pilot("Dave", "d/model-d", placement=4),
            ],
        )
    ]
    ratings, _per_game = compute_openskill_ratings(games)
    # Winner gains rating
    assert ratings["a/model-a"] > 1600
    # All losers get the same rating (winner-takes-all, no placement ordering)
    assert ratings["b/model-b"] == ratings["c/model-c"] == ratings["d/model-d"]
    # Losers lose rating
    assert ratings["b/model-b"] < 1600


def test_openskill_ratings_per_game_snapshots():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/x", placement=1),
                _pilot("Bob", "b/y", placement=2),
            ],
        )
    ]
    _, per_game = compute_openskill_ratings(games)
    assert len(per_game) == 1
    assert per_game[0]["id"] == "g1"

    alice = next(p for p in per_game[0]["players"] if p["key"] == "a/x")
    assert alice["ratingAfter"] > alice["ratingBefore"]

    bob = next(p for p in per_game[0]["players"] if p["key"] == "b/y")
    assert bob["ratingAfter"] < bob["ratingBefore"]


def test_openskill_ratings_start_at_1600():
    """OpenSkill display ratings should start at 1600."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            None,
            [_pilot("Alice", "a/x"), _pilot("Bob", "b/y")],
        )
    ]
    _, per_game = compute_openskill_ratings(games)
    assert per_game[0]["players"][0]["ratingBefore"] == 1600


# --- generate_leaderboard ---


def test_generate_leaderboard_basic():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=5.0, placement=1),
                _pilot("Bob", "b/model-b", cost=2.0, placement=2),
            ],
        ),
        _make_game(
            "g2",
            "20260102_000000",
            "Bob",
            [
                _pilot("Alice", "a/model-a", cost=3.0, placement=2),
                _pilot("Bob", "b/model-b", cost=1.0, placement=1),
            ],
        ),
    ]
    result, ratings_by_game = generate_leaderboard(
        games,
        {},
    )
    assert result["totalGames"] == 2
    assert len(result["models"]) == 2

    # Both have 1 win in 2 games
    for m in result["models"]:
        assert m["gamesPlayed"] == 2
        assert m["winRate"] == 0.5

    assert "g1" in ratings_by_game
    assert "g2" in ratings_by_game


def test_generate_leaderboard_no_games():
    result, ratings_by_game = generate_leaderboard(
        [],
        {},
    )
    assert result["totalGames"] == 0
    assert result["models"] == []
    assert ratings_by_game == {}


def test_generate_leaderboard_no_pilots():
    games = [_make_game("g1", "20260101_000000", "CPU1", [_cpu("CPU1"), _cpu("CPU2")])]
    result, _ = generate_leaderboard(
        games,
        {},
    )
    assert result["totalGames"] == 1
    assert result["models"] == []


def test_generate_leaderboard_skips_non_pilot():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/model-a", placement=1), _cpu("CPU1")],
        )
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )
    assert len(result["models"]) == 1
    assert result["models"][0]["modelName"] == "Model A"


def test_generate_leaderboard_uses_registry():
    registry = {"a/model-a": "Fancy Model Name"}
    games = [_make_game("g1", "20260101_000000", "Alice", [_pilot("Alice", "a/model-a", placement=1)])]
    result, _ = generate_leaderboard(
        games,
        registry,
    )
    assert result["models"][0]["modelName"] == "Fancy Model Name"


def test_generate_leaderboard_sorted_by_rating():
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/winner", placement=1),
                _pilot("Bob", "b/loser", placement=2),
            ],
        )
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )
    assert result["models"][0]["modelName"] == "Winner"
    assert result["models"][1]["modelName"] == "Loser"


def test_generate_leaderboard_missing_cost():
    player = {"name": "Alice", "type": "pilot", "model": "a/x", "placement": 1}
    games = [_make_game("g1", "20260101_000000", "Alice", [player])]
    result, _ = generate_leaderboard(
        games,
        {},
    )
    assert result["models"][0]["avgApiCost"] == 0.0


def test_generate_leaderboard_avg_cost():
    games = [
        _make_game("g1", "20260101_000000", "A", [_pilot("A", "a/x", cost=10.0, placement=1)]),
        _make_game("g2", "20260102_000000", "A", [_pilot("A", "a/x", cost=20.0, placement=1)]),
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )
    assert result["models"][0]["avgApiCost"] == 15.0


def test_generate_leaderboard_excludes_no_winner():
    """Games without a winner should be excluded from leaderboard stats."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=5.0, placement=1),
                _pilot("Bob", "b/model-b", cost=2.0, placement=2),
            ],
        ),
        # No winner — should be excluded
        _make_game(
            "g2",
            "20260102_000000",
            None,
            [_pilot("Alice", "a/model-a", cost=3.0), _pilot("Bob", "b/model-b", cost=1.0)],
        ),
    ]
    result, ratings_by_game = generate_leaderboard(
        games,
        {},
    )
    assert result["totalGames"] == 1
    for m in result["models"]:
        assert m["gamesPlayed"] == 1

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    assert alice["winRate"] == 1.0
    assert alice["avgApiCost"] == 5.0

    # No-winner game should not appear in ratings
    assert "g1" in ratings_by_game
    assert "g2" not in ratings_by_game


def test_generate_leaderboard_all_no_winner():
    """If all games lack a winner, leaderboard should be empty."""
    games = [
        _make_game("g1", "20260101_000000", None, [_pilot("A", "a/x"), _pilot("B", "b/y")]),
        _make_game("g2", "20260102_000000", None, [_pilot("A", "a/x"), _pilot("B", "b/y")]),
    ]
    result, ratings_by_game = generate_leaderboard(
        games,
        {},
    )
    assert result["totalGames"] == 0
    assert result["models"] == []
    assert ratings_by_game == {}


def test_generate_leaderboard_tool_calls():
    """Tool call counts should be averaged across games."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1, tool_calls_ok=40, tool_calls_failed=2),
                _pilot("Bob", "b/model-b", placement=2, tool_calls_ok=30, tool_calls_failed=5),
            ],
        ),
        _make_game(
            "g2",
            "20260102_000000",
            "Bob",
            [
                _pilot("Alice", "a/model-a", placement=2, tool_calls_ok=50, tool_calls_failed=4),
                _pilot("Bob", "b/model-b", placement=1, tool_calls_ok=20, tool_calls_failed=1),
            ],
        ),
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    bob = next(m for m in result["models"] if m["modelName"] == "Model B")

    # Alice: (40+50)/2 = 45.0 ok, (2+4)/2 = 3.0 failed
    assert alice["avgToolCallsOk"] == 45.0
    assert alice["avgToolCallsFailed"] == 3.0

    # Bob: (30+20)/2 = 25.0 ok, (5+1)/2 = 3.0 failed
    assert bob["avgToolCallsOk"] == 25.0
    assert bob["avgToolCallsFailed"] == 3.0


def test_generate_leaderboard_missing_tool_calls():
    """Old games without tool call fields should default to 0."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/model-a", placement=1), _pilot("Bob", "b/model-b", placement=2)],
        ),
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    assert alice["avgToolCallsOk"] == 0.0
    assert alice["avgToolCallsFailed"] == 0.0


# --- generate_leaderboard_file ---


def test_generate_leaderboard_file_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"
        data_dir.mkdir()

        game = _make_game(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "anthropic/claude-sonnet-4.5", cost=5.0, placement=1),
                _pilot("Bob", "google/gemini-2.5-flash", cost=1.0, placement=2),
            ],
        )
        game["deckType"] = "Constructed - Standard"
        game["harnessEpoch"] = HARNESS_EPOCH
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(
            json.dumps(
                {
                    "models": [
                        {"id": "anthropic/claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
                        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
                    ]
                }
            )
        )

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            models_json,
        )

        # Verify benchmark-results.json
        assert output_path.exists()
        result = json.loads(output_path.read_text())
        assert result["totalGames"] == 1
        # Top-level models come from jumpstart (empty here), check format pool instead
        assert "formats" in result
        assert result["formats"]["standard"]["totalGames"] == 1
        standard_models = result["formats"]["standard"]["models"]
        assert len(standard_models) == 2
        assert standard_models[0]["modelName"] == "Claude Sonnet 4.5"
        assert standard_models[0]["rating"] > standard_models[1]["rating"]

        # Verify ratings.json
        ratings_path = games_dir.parent / "data" / "ratings.json"
        assert ratings_path.exists()
        ratings_data = json.loads(ratings_path.read_text())
        assert "game_20260101_000000" in ratings_data
        assert "anthropic/claude-sonnet-4.5" in ratings_data["game_20260101_000000"]
        claude_rating = ratings_data["game_20260101_000000"]["anthropic/claude-sonnet-4.5"]
        assert claude_rating["after"] > claude_rating["before"]


def test_generate_leaderboard_file_no_games():
    """When no game files exist, should produce empty results."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            root / "models.json",
        )

        result = json.loads(output_path.read_text())
        assert result["totalGames"] == 0
        assert result["models"] == []


def test_generate_leaderboard_file_with_game_fallback():
    """When game files lack placement, reads elimination order from actions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        # Game without placement fields, but with elimination actions
        game = _make_game(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x"), _pilot("Bob", "b/y"), _pilot("Carol", "c/z")],
        )
        game["deckType"] = "Constructed - Standard"
        game["harnessEpoch"] = HARNESS_EPOCH
        game["actions"] = [
            {"seq": 100, "message": "Carol has lost the game."},
            {"seq": 200, "message": "Bob has lost the game."},
        ]
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            models_json,
        )
        result = json.loads(output_path.read_text())

        # Alice won (1st), Bob and Carol are losers (winner-takes-all: tied)
        standard_models = result["formats"]["standard"]["models"]
        models_by_name = {m["modelName"]: m for m in standard_models}
        assert models_by_name["X"]["rating"] > models_by_name["Y"]["rating"]
        assert models_by_name["Y"]["rating"] == models_by_name["Z"]["rating"]


# --- derive_format ---


def test_derive_format_legacy():
    assert derive_format({"deckType": "Constructed - Legacy"}) == "legacy"


def test_derive_format_modern():
    assert derive_format({"deckType": "Constructed - Modern"}) == "modern"


def test_derive_format_standard():
    assert derive_format({"deckType": "Constructed - Standard"}) == "standard"


def test_derive_format_commander():
    assert derive_format({"deckType": "Variant Magic - Freeform Commander"}) == "commander"


def test_derive_format_commander_default():
    """Empty deckType defaults to 'commander' for backward compat."""
    assert derive_format({}) == "commander"
    assert derive_format({"deckType": ""}) == "commander"


def test_derive_format_commander_from_game_type():
    """Commander gameType with unknown deckType -> commander."""
    assert derive_format({"gameType": "Commander Free For All", "deckType": "something"}) == "commander"


# --- generate_all_leaderboards ---


def test_generate_all_leaderboards_legacy_and_commander():
    legacy_game = _make_game(
        "g1",
        "20260101_000000",
        "Alice",
        [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
    )
    legacy_game["deckType"] = "Constructed - Legacy"

    commander_game = _make_game(
        "g2",
        "20260102_000000",
        "Carol",
        [
            _pilot("Carol", "c/z", placement=1),
            _pilot("Dave", "d/w", placement=2),
            _pilot("Eve", "e/v", placement=3),
            _pilot("Frank", "f/u", placement=4),
        ],
    )
    commander_game["deckType"] = "Variant Magic - Freeform Commander"

    format_results, _ = generate_all_leaderboards(
        [legacy_game, commander_game],
        {},
    )

    assert "legacy" in format_results
    assert "commander" in format_results
    assert "combined" in format_results

    assert format_results["legacy"]["totalGames"] == 1
    assert format_results["commander"]["totalGames"] == 1
    assert format_results["combined"]["totalGames"] == 2


def test_generate_all_leaderboards_separate_format_pools():
    """Each constructed format gets its own independent rating pool."""
    games = []
    for i, fmt in enumerate(["Constructed - Legacy", "Constructed - Modern", "Constructed - Standard"]):
        g = _make_game(
            f"g{i}",
            f"2026010{i}_000000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        )
        g["deckType"] = fmt
        games.append(g)

    format_results, _ = generate_all_leaderboards(
        games,
        {},
    )
    assert format_results["legacy"]["totalGames"] == 1
    assert format_results["modern"]["totalGames"] == 1
    assert format_results["standard"]["totalGames"] == 1
    assert format_results["combined"]["totalGames"] == 3


def test_generate_all_leaderboards_commander_winner_rated_highest():
    """Commander ratings should produce differentiated ratings."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/a", placement=1),
                _pilot("Bob", "b/b", placement=2),
                _pilot("Carol", "c/c", placement=3),
                _pilot("Dave", "d/d", placement=4),
            ],
        )
    ]
    games[0]["deckType"] = "Variant Magic - Freeform Commander"

    format_results, ratings_by_game = generate_all_leaderboards(games, {})
    models = format_results["commander"]["models"]
    # Winner should be rated highest
    sorted_models = sorted(models, key=lambda m: -m["rating"])
    assert sorted_models[0]["modelId"] == "a/a"
    # All 4 should appear
    assert len(models) == 4
    # Ratings in ratings.json
    assert "g1" in ratings_by_game


def test_generate_leaderboard_file_has_formats_key():
    """benchmark-results.json should have top-level fields AND formats key."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x", cost=5.0, placement=1), _pilot("Bob", "b/y", cost=2.0, placement=2)],
        )
        game["deckType"] = "Constructed - Legacy"
        game["harnessEpoch"] = HARNESS_EPOCH
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            models_json,
        )
        result = json.loads(output_path.read_text())

        # Top-level fields (from jumpstart pool — empty here since only legacy game)
        assert "totalGames" in result
        assert "models" in result
        assert result["totalGames"] == 1

        # Per-pool data
        assert "formats" in result
        assert "legacy" in result["formats"]
        assert "commander" in result["formats"]
        assert "combined" in result["formats"]
        assert result["formats"]["legacy"]["totalGames"] == 1


# --- _player_key / _split_key ---


def test_player_key_without_effort():
    assert _player_key({"model": "a/x"}) == "a/x"


def test_player_key_with_effort():
    assert _player_key({"model": "a/x", "reasoningEffort": "medium"}) == "a/x::medium"


def test_player_key_with_snake_case_effort():
    assert _player_key({"model": "a/x", "reasoning_effort": "low"}) == "a/x::low"


def test_split_key_without_effort():
    assert _split_key("a/x") == ("a/x", None)


def test_split_key_with_effort():
    assert _split_key("a/x::medium") == ("a/x", "medium")


# --- reasoning effort in leaderboard ---


def test_generate_leaderboard_splits_by_reasoning_effort():
    """Same model at different effort levels should produce separate entries."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/haiku", cost=1.0, placement=1, reasoning_effort="low"),
                _pilot("Bob", "a/haiku", cost=2.0, placement=2, reasoning_effort="medium"),
            ],
        ),
    ]
    result, _ = generate_leaderboard(
        games,
        {"a/haiku": "Haiku"},
    )
    assert len(result["models"]) == 2
    names = {m["modelName"] for m in result["models"]}
    assert "Haiku (low)" in names
    assert "Haiku (medium)" in names

    # Both should have the same modelId
    for m in result["models"]:
        assert m["modelId"] == "a/haiku"

    # Winner should have reasoningEffort field
    winner = next(m for m in result["models"] if m["modelName"] == "Haiku (low)")
    assert winner["reasoningEffort"] == "low"


def test_generate_leaderboard_no_effort_no_suffix():
    """Players without reasoningEffort should have no suffix in display name."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=1.0, placement=1),
                _pilot("Bob", "b/model-b", cost=2.0, placement=2),
            ],
        ),
    ]
    result, _ = generate_leaderboard(
        games,
        {"a/model-a": "Model A"},
    )
    model_a = next(m for m in result["models"] if m["modelId"] == "a/model-a")
    assert model_a["modelName"] == "Model A"
    assert "reasoningEffort" not in model_a


def test_generate_leaderboard_blunder_score():
    """Blunder score should be severity-weighted blunders per turn."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1),
                _pilot("Bob", "b/model-b", placement=2),
            ],
        ),
        _make_game(
            "g2",
            "20260102_000000",
            "Bob",
            [
                _pilot("Alice", "a/model-a", placement=2),
                _pilot("Bob", "b/model-b", placement=1),
            ],
        ),
    ]
    # totalTurns=10 per game (from _make_game)
    games[0]["annotations"] = [
        {"type": "blunder", "player": "Alice", "severity": "major"},  # weight 4
        {"type": "blunder", "player": "Alice", "severity": "minor"},  # weight 1
        {"type": "blunder", "player": "Bob", "severity": "moderate"},  # weight 2
    ]
    games[1]["annotations"] = [
        {"type": "blunder", "player": "Bob", "severity": "major"},  # weight 4
    ]
    result, _ = generate_leaderboard(
        games,
        {},
    )

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    bob = next(m for m in result["models"] if m["modelName"] == "Model B")

    # Alice: game1 = (4+1)/10 = 0.5, game2 = 0/10 = 0.0 -> total 5/20 = 0.25
    assert alice["blunderScore"] == 0.25
    # Bob: game1 = 2/10 = 0.2, game2 = 4/10 = 0.4 -> total 6/20 = 0.3
    assert bob["blunderScore"] == 0.3


def test_generate_leaderboard_blunder_score_excludes_questionable():
    """Questionable severity should not count toward the blunder index."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1),
                _pilot("Bob", "b/model-b", placement=2),
            ],
        ),
    ]
    # totalTurns=10
    games[0]["annotations"] = [
        {"type": "blunder", "player": "Alice", "severity": "major"},  # weight 4
        {"type": "blunder", "player": "Alice", "severity": "questionable"},  # weight 0
        {"type": "blunder", "player": "Alice", "severity": "questionable"},  # weight 0
    ]
    result, _ = generate_leaderboard(games, {})
    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    # (4 + 0 + 0) / 10 = 0.4
    assert alice["blunderScore"] == 0.4


def test_generate_leaderboard_blunder_score_no_annotations():
    """Games without annotations should crash."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/model-a", placement=1), _pilot("Bob", "b/model-b", placement=2)],
        ),
    ]
    del games[0]["annotations"]
    with pytest.raises(AssertionError, match="no annotations"):
        generate_leaderboard(games, {})


def test_generate_leaderboard_blunder_score_zero_turns():
    """Games with totalTurns=0 should crash."""
    game = _make_game(
        "g1",
        "20260101_000000",
        "Alice",
        [_pilot("Alice", "a/model-a", placement=1), _pilot("Bob", "b/model-b", placement=2)],
    )
    game["totalTurns"] = 0
    game["annotations"] = [
        {"type": "blunder", "player": "Alice", "severity": "major"},
    ]
    with pytest.raises(AssertionError, match="no turns"):
        generate_leaderboard([game], {})


def test_ratings_separate_by_effort():
    """Same model at different efforts should have independent ratings."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/x", placement=1, reasoning_effort="medium"),
                _pilot("Bob", "a/x", placement=2, reasoning_effort="low"),
            ],
        ),
    ]
    ratings, _ = compute_openskill_ratings(games)
    assert "a/x::medium" in ratings
    assert "a/x::low" in ratings
    assert ratings["a/x::medium"] > ratings["a/x::low"]


# --- epoch filtering ---


def test_generate_leaderboard_file_excludes_old_epochs():
    """Games from old epochs should be excluded from ratings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        # Epoch 1 game (old, should be excluded)
        old_game = _make_game(
            "game_20260210_090000",
            "20260210_090000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        )
        old_game["harnessEpoch"] = 2
        old_game["deckType"] = "Constructed - Standard"
        (games_dir / "game_20260210_090000.json.gz").write_bytes(gzip.compress(json.dumps(old_game).encode()))

        # Current epoch game (should be included)
        new_game = _make_game(
            "game_20260215_090000",
            "20260215_090000",
            "Carol",
            [_pilot("Carol", "c/z", placement=1), _pilot("Dave", "d/w", placement=2)],
        )
        new_game["harnessEpoch"] = HARNESS_EPOCH
        new_game["deckType"] = "Constructed - Standard"
        (games_dir / "game_20260215_090000.json.gz").write_bytes(gzip.compress(json.dumps(new_game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            models_json,
        )
        result = json.loads(output_path.read_text())

        # Only the current-epoch game should be in ratings (epoch 2 excluded, min is 3)
        assert result["totalGames"] == 1
        assert result["excludedGames"] == 1
        assert result["minEpoch"] == 11
        assert result["epochCounts"] == {"2": 1, str(HARNESS_EPOCH): 1}

        # Only current-epoch models should appear in the standard pool
        standard_models = result["formats"]["standard"]["models"]
        model_ids = {m["modelId"] for m in standard_models}
        assert "c/z" in model_ids
        assert "a/x" not in model_ids


def test_generate_leaderboard_file_explicit_epoch():
    """Game with explicit harnessEpoch should be included when above minimum."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        # Old timestamp but explicit current epoch — should be included
        game = _make_game(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/x", placement=1), _pilot("Bob", "b/y", placement=2)],
        )
        game["deckType"] = "Constructed - Standard"
        game["harnessEpoch"] = HARNESS_EPOCH
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_leaderboard_file(
            games_dir,
            data_dir,
            models_json,
        )
        result = json.loads(output_path.read_text())

        assert result["totalGames"] == 1
        assert result["excludedGames"] == 0


# --- compute_thinking_time ---


def test_compute_thinking_time_basic():
    events = [
        {"ts": "2026-02-14T10:00:00-08:00", "player": "Alice", "type": "llm_response"},
        {"ts": "2026-02-14T10:00:05-08:00", "player": "Alice", "type": "tool_call"},
        {"ts": "2026-02-14T10:00:10-08:00", "player": "Bob", "type": "llm_response"},
        {"ts": "2026-02-14T10:00:12-08:00", "player": "Bob", "type": "tool_call"},
    ]
    result = compute_thinking_time(events)
    # Alice: 5s (own event) + 5s (gap to Bob) = 10s
    assert result["Alice"] == 10.0
    # Bob: 2s (own event, last event has no following gap)
    assert result["Bob"] == 2.0


def test_compute_thinking_time_empty():
    assert compute_thinking_time([]) == {}


def test_compute_thinking_time_single_event():
    events = [{"ts": "2026-02-14T10:00:00-08:00", "player": "Alice", "type": "tool_call"}]
    assert compute_thinking_time(events) == {}


def test_compute_thinking_time_missing_player():
    events = [
        {"ts": "2026-02-14T10:00:00-08:00", "player": "", "type": "tool_call"},
        {"ts": "2026-02-14T10:00:05-08:00", "player": "Alice", "type": "tool_call"},
    ]
    result = compute_thinking_time(events)
    assert "" not in result


# --- thinking time in leaderboard ---


def test_generate_leaderboard_thinking_time():
    """Thinking time should be averaged across games."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", placement=1),
                _pilot("Bob", "b/model-b", placement=2),
            ],
        ),
        _make_game(
            "g2",
            "20260102_000000",
            "Bob",
            [
                _pilot("Alice", "a/model-a", placement=2),
                _pilot("Bob", "b/model-b", placement=1),
            ],
        ),
    ]
    # Add thinking time to players
    games[0]["players"][0]["thinkingTimeSecs"] = 120.0
    games[0]["players"][1]["thinkingTimeSecs"] = 90.0
    games[1]["players"][0]["thinkingTimeSecs"] = 80.0
    games[1]["players"][1]["thinkingTimeSecs"] = 110.0

    result, _ = generate_leaderboard(games, {})

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    bob = next(m for m in result["models"] if m["modelName"] == "Model B")

    # Alice: (120 + 80) / 2 = 100.0
    assert alice["avgThinkingTimeSecs"] == 100.0
    # Bob: (90 + 110) / 2 = 100.0
    assert bob["avgThinkingTimeSecs"] == 100.0


def test_generate_leaderboard_missing_thinking_time():
    """Old games without thinkingTimeSecs should default to 0."""
    games = [
        _make_game(
            "g1",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/model-a", placement=1), _pilot("Bob", "b/model-b", placement=2)],
        ),
    ]
    result, _ = generate_leaderboard(games, {})

    alice = next(m for m in result["models"] if m["modelName"] == "Model A")
    assert alice["avgThinkingTimeSecs"] == 0.0


# --- generate_model_stats ---


def _make_game_with_events(
    game_id: str,
    timestamp: str,
    winner: str | None,
    players: list[dict],
    llm_events: list[dict],
    epoch: int = HARNESS_EPOCH,
) -> dict:
    return {
        "id": game_id,
        "timestamp": timestamp,
        "totalTurns": 10,
        "winner": winner,
        "players": players,
        "llmEvents": llm_events,
        "harnessEpoch": epoch,
    }


def test_generate_model_stats_basic():
    """Basic aggregation of timeouts, responses, and token counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=5.0, placement=1, tool_calls_ok=10, tool_calls_failed=1),
                _pilot("Bob", "b/model-b", cost=2.0, placement=2, tool_calls_ok=8, tool_calls_failed=0),
            ],
            [
                {
                    "ts": "T1",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {"promptTokens": 1000, "completionTokens": 200, "cachedTokens": 400},
                },
                {
                    "ts": "T2",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {
                        "promptTokens": 2000,
                        "completionTokens": 300,
                        "cachedTokens": 800,
                        "reasoningTokens": 100,
                    },
                },
                {
                    "ts": "T3",
                    "player": "Alice",
                    "type": "llm_error",
                    "errorType": "timeout",
                    "errorMessage": "Timed out",
                },
                {
                    "ts": "T4",
                    "player": "Bob",
                    "type": "llm_response",
                    "usage": {"promptTokens": 500, "completionTokens": 100},
                },
                {"ts": "T5", "player": "Alice", "type": "context_reset", "reason": "repeated_timeouts"},
            ],
            epoch=10,
        )
        # Add thinkingTimeSecs so backfill doesn't need real timestamps
        game["players"][0]["thinkingTimeSecs"] = 60.0
        game["players"][1]["thinkingTimeSecs"] = 30.0
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert "a/model-a" in result["models"]
        alice = result["models"]["a/model-a"]
        assert alice["modelName"] == "Model A"
        assert alice["provider"] == "A"

        bucket = alice["epochs"]["10"]
        assert bucket["gamesPlayed"] == 1
        assert bucket["wins"] == 1
        assert bucket["successfulResponses"] == 2
        assert bucket["totalPromptTokens"] == 3000
        assert bucket["totalCompletionTokens"] == 500
        assert bucket["errors"] == {"timeout": 1}
        assert bucket["contextResets"] == 1
        assert bucket["totalToolCallsOk"] == 10
        assert bucket["totalToolCallsFailed"] == 1
        assert bucket["totalThinkingTimeSecs"] == 60.0
        assert bucket["totalCachedTokens"] == 1200
        assert bucket["totalReasoningTokens"] == 100

        bob = result["models"]["b/model-b"]
        bob_bucket = bob["epochs"]["10"]
        assert bob_bucket["successfulResponses"] == 1
        assert bob_bucket["errors"] == {}
        assert bob_bucket["contextResets"] == 0
        # Bob had no cache/reasoning data — should default to 0
        assert bob_bucket["totalCachedTokens"] == 0
        assert bob_bucket["totalReasoningTokens"] == 0


def test_generate_model_stats_epoch_bucketing():
    """Games at different epochs produce separate buckets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        for epoch, game_id in [(3, "game_20260101_000000"), (5, "game_20260102_000000")]:
            game = _make_game_with_events(
                game_id,
                game_id.replace("game_", ""),
                "Alice",
                [
                    _pilot("Alice", "a/model-a", cost=1.0, placement=1, tool_calls_ok=5, tool_calls_failed=0),
                ],
                [
                    {
                        "ts": "T1",
                        "player": "Alice",
                        "type": "llm_response",
                        "usage": {"promptTokens": 100, "completionTokens": 50},
                    },
                ],
                epoch=epoch,
            )
            game["players"][0]["thinkingTimeSecs"] = 10.0
            (games_dir / f"{game_id}.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        alice = result["models"]["a/model-a"]
        assert "3" in alice["epochs"]
        assert "5" in alice["epochs"]
        assert alice["epochs"]["3"]["gamesPlayed"] == 1
        assert alice["epochs"]["5"]["gamesPlayed"] == 1


def test_generate_model_stats_error_types():
    """Multiple error types in one game are bucketed correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [_pilot("Alice", "a/model-a", cost=1.0, placement=1, tool_calls_ok=5, tool_calls_failed=0)],
            [
                {"ts": "T1", "player": "Alice", "type": "llm_error", "errorType": "timeout", "errorMessage": "t1"},
                {"ts": "T2", "player": "Alice", "type": "llm_error", "errorType": "timeout", "errorMessage": "t2"},
                {
                    "ts": "T3",
                    "player": "Alice",
                    "type": "llm_error",
                    "errorType": "BadRequestError",
                    "errorMessage": "bad",
                },
                {
                    "ts": "T4",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {"promptTokens": 100, "completionTokens": 50},
                },
            ],
            epoch=10,
        )
        game["players"][0]["thinkingTimeSecs"] = 10.0
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        bucket = result["models"]["a/model-a"]["epochs"]["10"]
        assert bucket["errors"] == {"timeout": 2, "BadRequestError": 1}
        assert bucket["successfulResponses"] == 1


def test_generate_model_stats_includes_no_winner_games():
    """Games without a winner are included (unlike leaderboard)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260101_000000",
            "20260101_000000",
            None,  # no winner
            [
                _pilot("Alice", "a/model-a", cost=1.0, tool_calls_ok=5, tool_calls_failed=0),
            ],
            [
                {
                    "ts": "T1",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {"promptTokens": 100, "completionTokens": 50},
                },
            ],
            epoch=10,
        )
        game["players"][0]["thinkingTimeSecs"] = 10.0
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        bucket = result["models"]["a/model-a"]["epochs"]["10"]
        assert bucket["gamesPlayed"] == 1
        assert bucket["wins"] == 0


def test_generate_model_stats_no_games():
    """Empty games directory produces empty output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert result["models"] == {}


def test_generate_model_stats_reasoning_effort():
    """Same model at different efforts produces separate entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260101_000000",
            "20260101_000000",
            "Alice",
            [
                _pilot(
                    "Alice",
                    "a/model-a",
                    cost=1.0,
                    placement=1,
                    tool_calls_ok=5,
                    tool_calls_failed=0,
                    reasoning_effort="medium",
                ),
                _pilot(
                    "Bob",
                    "a/model-a",
                    cost=2.0,
                    placement=2,
                    tool_calls_ok=3,
                    tool_calls_failed=0,
                    reasoning_effort="low",
                ),
            ],
            [
                {
                    "ts": "T1",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {"promptTokens": 100, "completionTokens": 50},
                },
                {
                    "ts": "T2",
                    "player": "Bob",
                    "type": "llm_response",
                    "usage": {"promptTokens": 200, "completionTokens": 100},
                },
            ],
            epoch=10,
        )
        game["players"][0]["thinkingTimeSecs"] = 10.0
        game["players"][1]["thinkingTimeSecs"] = 20.0
        (games_dir / "game_20260101_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_model_stats(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert "a/model-a::medium" in result["models"]
        assert "a/model-a::low" in result["models"]
        assert result["models"]["a/model-a::medium"]["reasoningEffort"] == "medium"
        assert result["models"]["a/model-a::low"]["reasoningEffort"] == "low"


# --- generate_internals_data ---


def test_generate_internals_data_basic():
    """Per-game per-player data points with correct metrics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260115_120000",
            "20260115_120000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=5.0, placement=1, tool_calls_ok=10, tool_calls_failed=1),
                _pilot("Bob", "b/model-b", cost=2.0, placement=2, tool_calls_ok=8, tool_calls_failed=0),
            ],
            [
                {
                    "ts": "T1",
                    "player": "Alice",
                    "type": "llm_response",
                    "usage": {"promptTokens": 1000, "completionTokens": 200, "cachedTokens": 400},
                },
                {
                    "ts": "T2",
                    "player": "Alice",
                    "type": "llm_error",
                    "errorType": "timeout",
                    "errorMessage": "Timed out",
                },
                {
                    "ts": "T3",
                    "player": "Alice",
                    "type": "llm_error",
                    "errorType": "rate_limit",
                    "errorMessage": "Rate limited",
                },
                {
                    "ts": "T4",
                    "player": "Alice",
                    "type": "context_reset",
                    "reason": "repeated_timeouts",
                },
                {
                    "ts": "T5",
                    "player": "Bob",
                    "type": "llm_response",
                    "usage": {"promptTokens": 500, "completionTokens": 100},
                },
            ],
            epoch=10,
        )
        game["players"][0]["thinkingTimeSecs"] = 60.0
        game["players"][1]["thinkingTimeSecs"] = 30.0
        (games_dir / "game_20260115_120000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_internals_data(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert result["generatedAt"]
        assert result["minLeaderboardEpoch"]
        assert len(result["games"]) == 1

        g = result["games"][0]
        assert g["id"] == "game_20260115_120000"
        assert g["ts"] == "2026-01-15T12:00:00"
        assert g["epoch"] == 10
        assert g["format"] == "commander"  # default for games without deckType
        assert len(g["players"]) == 2

        alice = next(p for p in g["players"] if p["key"] == "a/model-a")
        assert alice["modelName"] == "Model A"
        assert alice["won"] is True
        assert alice["costUsd"] == 5.0
        assert alice["promptTokens"] == 1000
        assert alice["completionTokens"] == 200
        assert alice["cachedTokens"] == 400
        assert alice["toolCallsOk"] == 10
        assert alice["toolCallsFailed"] == 1
        assert alice["thinkingTimeSecs"] == 60.0
        assert alice["responses"] == 1
        assert alice["timeouts"] == 1
        assert alice["otherErrors"] == 1
        assert alice["contextResets"] == 1

        bob = next(p for p in g["players"] if p["key"] == "b/model-b")
        assert bob["won"] is False
        assert bob["responses"] == 1
        assert bob["timeouts"] == 0
        assert bob["otherErrors"] == 0
        assert bob["contextResets"] == 0


def test_generate_internals_data_format_detection():
    """Games with deckType produce correct format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        game = _make_game_with_events(
            "game_20260116_000000",
            "20260116_000000",
            "Alice",
            [
                _pilot("Alice", "a/model-a", cost=1.0, placement=1),
                _pilot("Bob", "b/model-b", cost=1.0, placement=2),
            ],
            [],
            epoch=10,
        )
        game["deckType"] = "Constructed - Standard"
        game["players"][0]["thinkingTimeSecs"] = 10.0
        game["players"][1]["thinkingTimeSecs"] = 10.0
        (games_dir / "game_20260116_000000.json.gz").write_bytes(gzip.compress(json.dumps(game).encode()))

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_internals_data(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert result["games"][0]["format"] == "standard"


def test_generate_internals_data_no_games():
    """Empty games directory produces empty output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        games_dir = root / "games"
        games_dir.mkdir()
        data_dir = root / "data"

        models_json = root / "models.json"
        models_json.write_text(json.dumps({"models": []}))

        output_path = generate_internals_data(games_dir, data_dir, models_json)
        result = json.loads(output_path.read_text())

        assert result["games"] == []

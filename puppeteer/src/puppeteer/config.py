"""Configuration for the puppeteer."""

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

from puppeteer.jumpstart import create_random_jumpstart_deck
from puppeteer.llm_cost import DEFAULT_LLM_PROVIDER, SUPPORTED_LLM_PROVIDERS
from puppeteer.log import get_logger
from puppeteer.matchmaker import get_active_presets, get_round_robin_matchup, pick_round_robin_format

logger = get_logger(__name__)


@dataclass
class DeckEntry:
    """A deck from the registry (data/decks/)."""

    name: str  # Display name (e.g., "Death's Shadow")
    strategy: str  # 1-2 sentence strategy summary (may be empty)
    cards: list[str]  # Card lines in .dck format


@dataclass
class PotatoPlayer:
    """Potato personality: pure Java, auto-responds to everything (dumbest)."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None  # Display name from deck registry
    deck_strategy: str | None = None  # Strategy summary from deck registry


@dataclass
class StallerPlayer:
    """Staller personality: pure Java, intentionally slow auto-responder."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None
    deck_strategy: str | None = None


@dataclass
class SleepwalkerPlayer:
    """Sleepwalker personality: MCP-based, Python client controls via stdio."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None
    deck_strategy: str | None = None


@dataclass
class PilotPlayer:
    """Pilot personality: LLM-powered strategic game player."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None
    deck_strategy: str | None = None
    preset: str | None = None  # Named preset from presets.json
    model: str | None = None  # LLM model (resolved from preset)
    provider: str = DEFAULT_LLM_PROVIDER  # LLM API provider slug
    system_prompt: str | None = None  # System prompt (resolved from preset -> prompts.json)
    max_interactions_per_turn: int | None = None  # Loop detection threshold (default 25 in Java)
    reasoning_effort: str | None = None  # Reasoning effort (resolved from preset)
    personality: str | None = None  # Named personality from personalities.json
    prompt_suffix: str | None = None  # Extra prompt text (set by personality resolution)
    tools: list[str] | None = None  # MCP tool names (resolved from preset -> toolsets.json)
    ignore_providers: list[str] | None = None  # OpenRouter providers to exclude (from models.json)
    provider_order: list[str] | None = None  # OpenRouter providers to prefer, in order (from models.json)
    cache_control: dict | None = None  # Prompt cache_control config (from models.json)


@dataclass
class ReplayPlayer:
    """Replay pilot: scripted MCP tool calls for golden tests."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None
    deck_strategy: str | None = None
    script: str | None = None  # Path to script JSON file, relative to project root


@dataclass
class CpuPlayer:
    """XMage built-in COMPUTER_MAD AI."""

    name: str
    deck: str | None = None  # Path to .dck file, relative to project root
    deck_name: str | None = None
    deck_strategy: str | None = None


# Union type for all player types
Player = PotatoPlayer | StallerPlayer | SleepwalkerPlayer | PilotPlayer | ReplayPlayer | CpuPlayer

# XMage server username constraints (from Mage.Server/config/config.xml)
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 14


def _load_json_file(name: str, config_file: Path | None) -> dict:
    """Load a JSON file by name, searching config dir then puppeteer/.

    Returns empty dict if no file found.
    """
    candidates: list[Path] = []
    if config_file is not None:
        candidates.append(config_file.parent / name)
    candidates.append(Path(f"puppeteer/{name}"))

    for candidate in candidates:
        if candidate.exists():
            with open(candidate) as f:
                return json.load(f)
    return {}


def load_personalities(config_file: Path | None) -> dict[str, dict]:
    """Load personality definitions from personalities.json."""
    return _load_json_file("personalities.json", config_file)


def load_models(config_file: Path | None) -> dict:
    """Load model definitions from models.json."""
    return _load_json_file("models.json", config_file)


def load_presets(config_file: Path | None) -> dict:
    """Load preset definitions from presets.json."""
    return _load_json_file("presets.json", config_file)


def load_prompts(config_file: Path | None) -> dict[str, str]:
    """Load prompt definitions from prompts/ dir and prompts.json.

    Scans for .md files in a ``prompts/`` directory (sibling to config file,
    then ``puppeteer/prompts/``).  Each file's stem becomes a prompt key with
    the file contents as the value.  Then loads ``prompts.json`` (if present)
    and merges on top, so JSON entries override .md files with the same name.
    """
    result: dict[str, str] = {}

    # Scan prompts/ directories for .md files
    prompt_dirs: list[Path] = []
    if config_file is not None:
        prompt_dirs.append(config_file.parent / "prompts")
    prompt_dirs.append(Path("puppeteer/prompts"))

    for prompt_dir in prompt_dirs:
        if prompt_dir.is_dir():
            for md_file in sorted(prompt_dir.glob("*.md")):
                key = md_file.stem
                if key not in result:
                    result[key] = md_file.read_text().strip()

    # JSON overrides .md files
    json_prompts = _load_json_file("prompts.json", config_file)
    result.update(json_prompts)

    return result


def load_toolsets(config_file: Path | None) -> dict[str, list[str]]:
    """Load toolset definitions from toolsets.json."""
    return _load_json_file("toolsets.json", config_file)


def resolve_preset(
    player: PilotPlayer,
    presets_data: dict,
    prompts: dict[str, str],
    toolsets: dict[str, list[str]] | None = None,
) -> None:
    """Apply preset defaults to a player in-place. Player-level fields win."""
    if not player.preset:
        return

    presets = presets_data.get("presets", {})
    pdata = presets.get(player.preset)
    if pdata is None:
        raise ValueError(f"Unknown preset: {player.preset!r}. Available: {sorted(presets.keys())}")

    # Fill in defaults from preset (player-level fields win)
    if player.model is None and "model" in pdata:
        player.model = pdata["model"]
    if player.reasoning_effort is None and "reasoning_effort" in pdata:
        player.reasoning_effort = pdata["reasoning_effort"]
    if player.system_prompt is None and "system_prompt" in pdata:
        prompt_key = pdata["system_prompt"]
        if prompt_key not in prompts:
            raise ValueError(
                f"Preset {player.preset!r} references unknown prompt {prompt_key!r}. "
                f"Available: {sorted(prompts.keys())}"
            )
        player.system_prompt = prompts[prompt_key]
    if player.tools is None and "toolset" in pdata:
        toolset_key = pdata["toolset"]
        if toolsets is None or toolset_key not in toolsets:
            available = sorted(toolsets.keys()) if toolsets else []
            raise ValueError(
                f"Preset {player.preset!r} references unknown toolset {toolset_key!r}. Available: {available}"
            )
        player.tools = list(toolsets[toolset_key])


def _validate_name_parts(personalities: dict[str, dict], presets_data: dict, models_data: dict) -> None:
    """Validate all active preset x personality name_part combos fit XMage name limits.

    Called at startup when random resolution is needed. Fails fast with a clear
    error listing any offending combinations.
    """
    pool = get_active_presets(presets_data)
    if not pool:
        return
    presets = presets_data.get("presets", {})
    models_by_id = {m["id"]: m for m in models_data.get("models", [])}
    errors: list[str] = []
    for preset_key in pool:
        preset = presets.get(preset_key)
        if preset is None:
            errors.append(f"active preset {preset_key!r} not found in presets")
            continue
        model_id = preset.get("model", "")
        model = models_by_id.get(model_id)
        if model is None:
            errors.append(f"Preset {preset_key!r} model {model_id!r} not found in models list")
            continue
        if "name_part" not in model:
            errors.append(f"Model {model_id!r} missing name_part")
            continue
        m_part = model["name_part"]
        for p_key, p_data in personalities.items():
            p_part = p_data.get("name_part", p_key)
            name = f"{m_part} {p_part}"
            if len(name) < MIN_USERNAME_LENGTH or len(name) > MAX_USERNAME_LENGTH:
                errors.append(
                    f"{name!r} ({preset_key}/{model_id} + {p_key}) is {len(name)} chars, "
                    f"must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH}"
                )
    if errors:
        raise ValueError("Invalid name_part combinations:\n  " + "\n  ".join(errors))


def _generate_player_name(
    model_id: str,
    personality_key: str,
    models_data: dict,
    personalities: dict[str, dict],
) -> str:
    """Generate a player name from model name_part + personality name_part."""
    models_by_id = {m["id"]: m for m in models_data.get("models", [])}
    model = models_by_id.get(model_id, {})
    m_part = model.get("name_part", model_id.split("/")[-1][:6])
    p_data = personalities.get(personality_key, {})
    p_part = p_data.get("name_part", personality_key[:7])
    return f"{m_part} {p_part}"


def _resolve_randoms(
    players: list[tuple[PilotPlayer, bool]],
    personalities: dict[str, dict],
    presets_data: dict,
    prompts: dict[str, str],
    models_data: dict,
    toolsets: dict[str, list[str]] | None = None,
    cross_game_used_names: set[str] | None = None,
    round_robin_picks: list[str] | None = None,
) -> None:
    """Resolve 'random'/'round-robin' preset/personality values and apply defaults.

    Each player tuple is (player, had_explicit_name). Modifies players in-place.
    Avoids duplicate personalities and presets across players.

    cross_game_used_names: names already taken by other games in a parallel
    batch.  When a randomly generated name collides, the personality is
    re-rolled to produce a unique name.

    round_robin_picks: ordered preset names for coverage-optimal matchup
    (from matchmaker.get_round_robin_matchup). Required when any player has
    preset="round-robin". Consumed in order via pop(0).
    """
    has_randoms = any(p.preset in ("random", "round-robin") or p.personality == "random" for p, _ in players)
    if has_randoms:
        _validate_name_parts(personalities, presets_data, models_data)

    preset_pool = get_active_presets(presets_data)
    available_personalities = list(personalities.keys())
    available_presets = list(preset_pool)

    used_personalities: set[str] = set()
    used_presets: set[str] = set()

    for player, had_explicit_name in players:
        was_random_personality = player.personality == "random"

        # Resolve random personality
        if player.personality == "random":
            remaining = [k for k in available_personalities if k not in used_personalities]
            if not remaining:
                # All used — reset the pool (allows >15 players)
                used_personalities.clear()
                remaining = available_personalities
            chosen_p = random.choice(remaining)
            used_personalities.add(chosen_p)
            player.personality = chosen_p

        # Resolve random preset
        if player.preset == "random":
            remaining = [m for m in available_presets if m not in used_presets]
            if not remaining:
                used_presets.clear()
                remaining = available_presets
            chosen_preset = random.choice(remaining)
            used_presets.add(chosen_preset)
            player.preset = chosen_preset

        # Resolve round-robin preset (coverage-optimal matchup from matchmaker)
        elif player.preset == "round-robin":
            assert round_robin_picks, (
                "preset='round-robin' requires round_robin_picks (from matchmaker.get_round_robin_matchup)"
            )
            chosen_preset = round_robin_picks.pop(0)
            used_presets.add(chosen_preset)
            player.preset = chosen_preset

        # Apply preset (sets model, reasoning_effort, system_prompt, tools)
        resolve_preset(player, presets_data, prompts, toolsets)

        # Apply model-level settings from models.json
        if player.model:
            models_by_id = {m["id"]: m for m in models_data.get("models", [])}
            model_entry = models_by_id.get(player.model, {})
            if player.ignore_providers is None and "ignore_providers" in model_entry:
                player.ignore_providers = model_entry["ignore_providers"]
            if player.provider_order is None and "provider_order" in model_entry:
                player.provider_order = model_entry["provider_order"]
            if player.cache_control is None and "cache_control" in model_entry:
                player.cache_control = model_entry["cache_control"]

            if player.provider != DEFAULT_LLM_PROVIDER:
                assert player.ignore_providers is None, (
                    f"ignore_providers requires provider={DEFAULT_LLM_PROVIDER!r}, got {player.provider!r}"
                )
                assert player.provider_order is None, (
                    f"provider_order requires provider={DEFAULT_LLM_PROVIDER!r}, got {player.provider!r}"
                )

            # Re-roll expressive personality if model skips them (personality infection prevention)
            if was_random_personality and model_entry.get("skip_expressive_personalities"):
                p_data = personalities.get(player.personality, {})
                if p_data.get("expressive"):
                    non_expressive = [
                        k
                        for k in available_personalities
                        if k not in used_personalities and not personalities.get(k, {}).get("expressive")
                    ]
                    assert non_expressive, (
                        f"No non-expressive personalities available for model {player.model!r} "
                        f"(has skip_expressive_personalities). Add more non-expressive "
                        f"personalities or remove the restriction."
                    )
                    chosen_p = random.choice(non_expressive)
                    used_personalities.add(chosen_p)
                    player.personality = chosen_p

        # Generate name if needed (personality was random and no explicit name)
        if was_random_personality and not had_explicit_name:
            assert player.model is not None, "Model must be set before name generation"
            player.name = _generate_player_name(player.model, player.personality, models_data, personalities)

            # Avoid cross-game name collisions in parallel batches.  Two bridge
            # clients with the same XMage username on the same server will
            # endlessly kick each other's sessions (same-host duplicate
            # detection), so we re-roll personality until the name is unique.
            if cross_game_used_names is not None:
                while player.name in cross_game_used_names:
                    remaining = [k for k in available_personalities if k not in used_personalities]
                    assert remaining, (
                        f"Cannot generate unique player name for model {player.model!r}: "
                        f"all personality combinations collide with names from other games "
                        f"({cross_game_used_names}). Reduce num_games or add more "
                        f"personalities/presets."
                    )
                    chosen_p = random.choice(remaining)
                    used_personalities.add(chosen_p)
                    player.personality = chosen_p
                    player.name = _generate_player_name(player.model, player.personality, models_data, personalities)

            # Mark as having a name so _resolve_personality skips the name requirement
            had_explicit_name = True

        _resolve_personality(player, personalities, models_data, had_explicit_name)


def _resolve_personality(
    player: PilotPlayer,
    personalities: dict[str, dict],
    models_data: dict,
    had_explicit_name: bool,
) -> None:
    """Apply personality defaults to a player in-place (prompt_suffix and name only)."""
    if not player.personality:
        return

    pdata = personalities.get(player.personality)
    if pdata is None:
        raise ValueError(f"Unknown personality: {player.personality!r}. Available: {sorted(personalities.keys())}")

    # Name: player JSON name > generated name (from random resolution)
    if not had_explicit_name:
        # For non-random personalities without a preset, we need a name.
        # Use name_part as a fallback, but it may be too short on its own.
        if player.model:
            # Generate from model + personality
            player.name = _generate_player_name(player.model, player.personality, models_data, personalities)
        else:
            # No model set — use name_part directly
            p_part = pdata.get("name_part", player.personality[:7])
            player.name = p_part

    # Validate name length
    if len(player.name) < MIN_USERNAME_LENGTH or len(player.name) > MAX_USERNAME_LENGTH:
        raise ValueError(
            f"Player name {player.name!r} must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} "
            f"characters (XMage server limit)"
        )

    # Set prompt_suffix from personality
    if player.prompt_suffix is None and "prompt_suffix" in pdata:
        player.prompt_suffix = pdata["prompt_suffix"]


_DECK_TYPE_TO_DIR: dict[str, str] = {
    "Variant Magic - Freeform Commander": "Commander",
    "Variant Magic - Commander": "Commander",
    "Constructed - Legacy": "Legacy",
    "Constructed - Modern": "Modern",
    "Constructed - Standard": "Standard",
}

# Maps XMage deck type to our registry directory in data/decks/
_DECK_TYPE_TO_FORMAT_DIR: dict[str, str] = {
    "Variant Magic - Freeform Commander": "commander",
    "Variant Magic - Commander": "commander",
    "Constructed - Legacy": "legacy",
    "Constructed - Modern": "modern",
    "Constructed - Standard": "standard",
    "Limited": "jumpstart",
}


def load_deck_registry(project_root: Path, format_dir: str) -> list[DeckEntry]:
    """Load all deck entries from data/decks/{format_dir}/.

    Each JSON file in the directory defines one deck with name, strategy, and cards.
    """
    registry_dir = project_root / "data" / "decks" / format_dir
    assert registry_dir.is_dir(), f"Deck registry directory not found: {registry_dir}"
    entries = []
    for json_file in sorted(registry_dir.glob("*.json")):
        data = json.loads(json_file.read_text())
        assert "name" in data, f"Deck file {json_file} missing 'name' field"
        assert "cards" in data or "variants" in data, f"Deck file {json_file} missing 'cards' or 'variants' field"
        cards = data.get("cards", [])
        entries.append(
            DeckEntry(
                name=data["name"],
                strategy=data.get("strategy", ""),
                cards=cards,
            )
        )
    assert entries, f"No deck files found in {registry_dir}"
    return entries


def generate_dck_file(project_root: Path, entry: DeckEntry) -> Path:
    """Write a .dck file from a DeckEntry to tmp/decks/ and return relative path."""
    tmp_dir = project_root / "tmp" / "decks"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # Use deck name as filename, sanitized
    safe_name = entry.name.replace(" ", "-").replace("'", "").replace("/", "-")
    dck_path = tmp_dir / f"{safe_name}.dck"
    dck_path.write_text("\n".join(entry.cards) + "\n")
    return dck_path.relative_to(project_root)


@dataclass
class Config:
    """Puppeteer configuration with sensible defaults."""

    # Hardcoded defaults
    server: str = "localhost"
    start_port: int = 17171
    user: str = "spectator"
    password: str = ""
    server_wait: int = 240
    bridge_delay: int = 5
    log_dir: Path = field(default_factory=lambda: Path.home() / ".mage-bench" / "logs")
    jvm_opens: str = "--add-opens=java.base/java.io=ALL-UNNAMED"
    # Enable XRender pipeline for Java 2D — GPU-accelerated rendering on Linux
    jvm_rendering: str = "-Dsun.java2d.xrender=true"

    @property
    def jvm_bridge_opts(self) -> str:
        """JVM options for bridge (non-GUI) processes."""
        opts = [self.jvm_opens]
        if sys.platform == "darwin":
            opts.append("-Dapple.awt.UIElement=true")
        return " ".join(opts)

    @property
    def run_tag(self) -> str:
        """Derive run tag from config filename for per-target last symlinks."""
        assert self.config_file is not None, "run_tag requires config_file to be set"
        return self.config_file.stem

    def new_game_config(
        self,
        *,
        config_file: Path | None = None,
        user: str | None = None,
        num_games: int | None = None,
        port: int | None = None,
        timestamp: str | None = None,
    ) -> "Config":
        """Create a fresh Config with this run's CLI/runtime options only."""
        return Config(
            server=self.server,
            start_port=self.start_port,
            user=self.user if user is None else user,
            password=self.password,
            server_wait=self.server_wait,
            bridge_delay=self.bridge_delay,
            log_dir=self.log_dir,
            jvm_opens=self.jvm_opens,
            jvm_rendering=self.jvm_rendering,
            config_file=self.config_file if config_file is None else config_file,
            observer=self.observer,
            record=self.record,
            record_output=self.record_output,
            num_games=self.num_games if num_games is None else num_games,
            debug=self.debug,
            skip_compile=self.skip_compile,
            port=self.port if port is None else port,
            timestamp=self.timestamp if timestamp is None else timestamp,
        )

    # CLI options
    config_file: Path | None = None
    batch_config_files: list[Path] = field(default_factory=list)
    observer: bool = False
    record: bool = False
    record_output: Path | None = None
    num_games: int = 1  # Number of parallel games on the same server
    debug: bool = False  # Enable DEBUG-level logging
    skip_compile: bool = False  # Skip compilation (caller already compiled)

    # Match timer settings (XMage enum names, e.g. "MIN__20", "SEC__10")
    match_time_limit: str = ""
    match_buffer_time: str = ""

    # Game format settings (passed to XMage via config JSON)
    game_type: str = ""  # e.g. "Two Player Duel", "Commander Free For All"
    deck_type: str = ""  # e.g. "Constructed - Legacy", "Variant Magic - Freeform Commander"
    deck_type_candidates: list[str] = field(default_factory=list)  # Multi-format rotation
    custom_start_life: int = 0  # 0 = use game type default

    # Post-game behavior
    skip_post_game_prompts: bool = False  # Skip YouTube/export prompts
    tournament_game: bool = False  # Tournament match (bypasses tournament-phase block)

    # Deterministic game options
    skip_init_shuffling: bool = False  # Don't shuffle libraries at game start

    # Runtime state (set during execution)
    port: int = 0
    timestamp: str = ""

    # Player lists by type
    potato_players: list[PotatoPlayer] = field(default_factory=list)
    staller_players: list[StallerPlayer] = field(default_factory=list)
    sleepwalker_players: list[SleepwalkerPlayer] = field(default_factory=list)
    pilot_players: list[PilotPlayer] = field(default_factory=list)
    replay_players: list[ReplayPlayer] = field(default_factory=list)
    cpu_players: list[CpuPlayer] = field(default_factory=list)

    def load_config(
        self,
        cross_game_used_names: set[str] | None = None,
        cross_game_round_robin: list[tuple[str, ...]] | None = None,
        cross_game_format_picks: list[str] | None = None,
    ) -> None:
        """Load player configuration from JSON file.

        cross_game_used_names: names already claimed by earlier games in a
        parallel batch.  Passed to _resolve_randoms so it can re-roll
        personalities to avoid duplicate XMage usernames.

        cross_game_round_robin: matchups already selected by earlier games in a
        parallel batch.  Passed to get_round_robin_matchup as extra_matchups so
        each game in a batch picks a different coverage-optimal pairing.
        The selected matchup is appended to this list for the next game.

        cross_game_format_picks: formats already selected by earlier games in a
        parallel batch.  Passed to pick_round_robin_format so each game in a
        batch spreads across formats.
        """
        if self.config_file is None:
            # Try default locations in order
            candidates = [
                Path(".context/ai-puppeteer-config.json"),  # User override
                Path("configs/dumb.json"),  # Repo default
            ]
            for candidate in candidates:
                if candidate.exists():
                    self.config_file = candidate
                    break
            else:
                return

        if not self.config_file.exists():
            return

        with open(self.config_file) as f:
            data = json.load(f)
            self.match_time_limit = data.get("matchTimeLimit", "")
            self.match_buffer_time = data.get("matchBufferTime", "")
            self.game_type = data.get("gameType", "")
            raw_deck_type = data.get("deckType", "")
            if isinstance(raw_deck_type, list):
                assert len(raw_deck_type) > 0, "deckType list must not be empty"
                self.deck_type_candidates = raw_deck_type
                self.deck_type = raw_deck_type[0]
            else:
                self.deck_type = raw_deck_type
                if raw_deck_type:
                    self.deck_type_candidates = [raw_deck_type]
            self.custom_start_life = data.get("customStartLife", 0)
            self.skip_post_game_prompts = data.get("skipPostGamePrompts", False)
            self.tournament_game = data.get("tournamentGame", False)
            self.skip_init_shuffling = data.get("skipInitShuffling", False)
            personalities = load_personalities(self.config_file)
            models_data = load_models(self.config_file)
            presets_data = load_presets(self.config_file)
            prompts = load_prompts(self.config_file)
            toolsets = load_toolsets(self.config_file)

            # First pass: construct player objects, collecting LLM players for random resolution
            llm_players: list[tuple[PilotPlayer, bool]] = []

            for i, player in enumerate(data.get("players", [])):
                player_type = player.get("type", "")
                has_explicit_name = "name" in player
                name = player.get("name", f"player-{i}")
                deck = player.get("deck")  # Optional deck path

                if player_type == "sleepwalker":
                    self.sleepwalker_players.append(SleepwalkerPlayer(name=name, deck=deck))
                elif player_type == "pilot":
                    assert "base_url" not in player, (
                        "players[].base_url is no longer supported; use players[].provider instead"
                    )
                    provider = player.get("provider", DEFAULT_LLM_PROVIDER)
                    assert provider in SUPPORTED_LLM_PROVIDERS, (
                        f"Unknown player provider {provider!r}. Supported providers: {SUPPORTED_LLM_PROVIDERS}"
                    )
                    p = PilotPlayer(
                        name=name,
                        deck=deck,
                        preset=player.get("preset"),
                        provider=provider,
                        personality=player.get("personality"),
                        tools=player.get("tools"),
                    )
                    llm_players.append((p, has_explicit_name))
                    self.pilot_players.append(p)
                elif player_type == "potato":
                    self.potato_players.append(PotatoPlayer(name=name, deck=deck))
                elif player_type == "staller":
                    self.staller_players.append(StallerPlayer(name=name, deck=deck))
                elif player_type == "replay":
                    self.replay_players.append(ReplayPlayer(name=name, deck=deck, script=player.get("script")))
                elif player_type == "cpu":
                    self.cpu_players.append(CpuPlayer(name=name, deck=deck))
                elif player_type == "skeleton":
                    # Legacy: treat as potato for backwards compatibility
                    self.potato_players.append(PotatoPlayer(name=name, deck=deck))

            # Validate: only pilot players can have deck="choice"
            non_pilot = (
                self.potato_players
                + self.staller_players
                + self.sleepwalker_players
                + self.replay_players
                + self.cpu_players
            )
            non_pilot_choice = [p.name for p in non_pilot if p.deck == "choice"]
            assert not non_pilot_choice, (
                f"deck='choice' requires a pilot player (has LLM), but found on non-pilot player(s): {non_pilot_choice}"
            )

            # Compute round-robin picks if any player uses preset="round-robin"
            num_rr_seats = sum(1 for p, _ in llm_players if p.preset == "round-robin")
            rrp: list[str] | None = None
            if num_rr_seats > 0:
                rrp = get_round_robin_matchup(
                    self.deck_type,
                    num_rr_seats,
                    extra_matchups=cross_game_round_robin,
                )
                if cross_game_round_robin is not None:
                    cross_game_round_robin.append(tuple(rrp))

            # Second pass: resolve random/round-robin presets/personalities and generate names
            _resolve_randoms(
                llm_players,
                personalities,
                presets_data,
                prompts,
                models_data,
                toolsets,
                cross_game_used_names=cross_game_used_names,
                round_robin_picks=rrp,
            )

            # Format rotation: pick the best format for the resolved players
            resolved_presets = [p.preset for p in self.pilot_players if p.preset]
            if len(self.deck_type_candidates) > 1 and resolved_presets:
                chosen_format = pick_round_robin_format(
                    self.deck_type_candidates,
                    resolved_presets,
                    extra_format_picks=cross_game_format_picks,
                )
                self.deck_type = chosen_format
                if cross_game_format_picks is not None:
                    cross_game_format_picks.append(chosen_format)

    def get_players_config_json(self) -> str:
        """Serialize resolved player config to JSON for passing to spectator/GUI client."""
        players = []
        for p in self.pilot_players:
            d = {"type": "pilot", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            if p.model:
                d["model"] = p.model
            players.append(d)
        for p in self.sleepwalker_players:
            d = {"type": "sleepwalker", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            players.append(d)
        for p in self.potato_players:
            d = {"type": "potato", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            players.append(d)
        for p in self.staller_players:
            d = {"type": "staller", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            players.append(d)
        for p in self.replay_players:
            d = {"type": "replay", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            players.append(d)
        for p in self.cpu_players:
            d = {"type": "cpu", "name": p.name}
            if p.deck:
                d["deck"] = p.deck
            players.append(d)
        if not players:
            return ""
        result: dict = {"players": players}
        if self.game_type:
            result["gameType"] = self.game_type
        if self.deck_type:
            result["deckType"] = self.deck_type
        return json.dumps(result, separators=(",", ":"))

    def resolve_random_decks(self, project_root: Path) -> None:
        """Replace any deck="random" with a randomly chosen deck from the registry."""
        # Fail fast if any player still has deck="choice" — caller must resolve those first
        all_typed_players = (
            self.potato_players
            + self.staller_players
            + self.sleepwalker_players
            + self.pilot_players
            + self.replay_players
            + self.cpu_players
        )
        choice_names = [p.name for p in all_typed_players if p.deck == "choice"]
        assert not choice_names, (
            f"Unresolved deck='choice' for {choice_names} — call resolve_choice_decks() before resolve_random_decks()"
        )

        all_players = (
            self.potato_players
            + self.staller_players
            + self.sleepwalker_players
            + self.pilot_players
            + self.replay_players
            + self.cpu_players
        )
        if not any(p.deck == "random" for p in all_players):
            return

        # Jumpstart: combine two random half-decks at runtime
        if self.deck_type == "Limited":
            used_themes: set[str] = set()
            for player in all_players:
                if player.deck == "random":
                    deck_path, deck_name = create_random_jumpstart_deck(project_root, exclude_themes=used_themes)
                    player.deck = str(deck_path)
                    player.deck_name = deck_name
                    # Extract theme names from filename to avoid giving two players identical themes
                    stem = deck_path.stem  # e.g. "Cats+Dogs"
                    for theme in stem.split("+"):
                        used_themes.add(theme.replace("-", " "))
                    logger.info("Random Jumpstart deck for %s: %s", player.name, deck_name)
            return

        format_dir = _DECK_TYPE_TO_FORMAT_DIR.get(self.deck_type)
        assert format_dir, f"Unknown deck type for registry lookup: {self.deck_type!r}"
        registry = load_deck_registry(project_root, format_dir)

        used: set[str] = set()
        for player in all_players:
            if player.deck == "random":
                available = [e for e in registry if e.name not in used]
                if not available:
                    used.clear()
                    available = list(registry)
                chosen = random.choice(available)
                used.add(chosen.name)
                dck_path = generate_dck_file(project_root, chosen)
                player.deck = str(dck_path)
                player.deck_name = chosen.name
                player.deck_strategy = chosen.strategy
                logger.info("Random deck for %s: %s", player.name, chosen.name)

    def resolve_deck_metadata(self, project_root: Path) -> None:
        """Populate deck_name/deck_strategy for players with static deck paths.

        Called after all deck resolution is complete. Looks up static deck paths
        in the registry by matching card content.
        """
        all_players = (
            self.potato_players
            + self.staller_players
            + self.sleepwalker_players
            + self.pilot_players
            + self.replay_players
            + self.cpu_players
        )
        for player in all_players:
            # Skip players that already have metadata (from random/choice resolution)
            # or don't have a deck path
            if player.deck_name or not player.deck:
                continue
            # Try to read the .dck file and match against registry
            dck_file = project_root / player.deck
            if not dck_file.exists():
                continue
            cards = [
                line.strip()
                for line in dck_file.read_text().splitlines()
                if line.strip()
                and not line.strip().startswith("#")
                and not line.strip().startswith("//")
                and not line.strip().startswith("NAME:")
            ]
            # Search all format directories
            for fmt_dir in ("standard", "modern", "legacy", "commander"):
                registry_dir = project_root / "data" / "decks" / fmt_dir
                if not registry_dir.is_dir():
                    continue
                for json_file in registry_dir.glob("*.json"):
                    data = json.loads(json_file.read_text())
                    if data.get("cards") == cards:
                        player.deck_name = data["name"]
                        player.deck_strategy = data.get("strategy", "")
                        break
                if player.deck_name:
                    break

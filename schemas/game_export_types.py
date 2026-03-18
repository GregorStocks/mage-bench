"""Typed helpers for game export JSON.

These types are backed by ``schemas/game-export-v8.schema.json``. Tests keep
the TypedDict field sets aligned with the JSON Schema so Python callers can
load validated exports without falling back to raw ``dict[str, object]`` blobs.
"""

import dataclasses
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Literal, TypeAlias

from typing_extensions import NotRequired, TypeGuard, TypeIs, TypedDict

JsonObject: TypeAlias = dict[str, object]


# -- Nested payload types used by the decision renderer --


class Permanent(TypedDict):
    """A card on the battlefield, in hand, graveyard, exile, or command zone."""

    name: str
    id: NotRequired[str]
    tapped: NotRequired[bool]
    summoning_sick: NotRequired[bool]
    face_down: NotRequired[bool]
    token: NotRequired[bool]
    power: NotRequired[int | str]
    toughness: NotRequired[int | str]
    power_toughness: NotRequired[str]
    pt: NotRequired[str]
    loyalty: NotRequired[int]
    counters: NotRequired[object]
    original_card: NotRequired[str]
    copy: NotRequired[bool]


class StackTarget(TypedDict, total=False):
    """A target of a spell or ability on the stack."""

    name: str
    id: str


class StackItem(TypedDict):
    """A spell or ability on the stack."""

    name: str
    id: NotRequired[str]
    source_card: NotRequired[str]
    ability_text: NotRequired[str]
    targets: NotRequired[list[str | StackTarget]]


class CombatCreature(TypedDict):
    """A creature in a combat group (attacker or blocker) or incoming attacker."""

    name: str
    id: NotRequired[str]
    power: NotRequired[int | str]
    toughness: NotRequired[int | str]
    power_toughness: NotRequired[str]
    pt: NotRequired[str]


class Choice(TypedDict, total=False):
    """A choice available to the player. Shape varies by decision type."""

    index: int
    name: str
    description: str
    id: str
    action: str
    mana_cost: str
    choice_type: str


class MultiAmountItem(TypedDict):
    """An item in a multi-amount decision."""

    description: str
    min: NotRequired[int]
    max: NotRequired[int]


_ACTION_TYPES = {"turn_change", "phase_change", "chat"}
_ANNOTATION_SEVERITIES = {"questionable", "minor", "moderate", "major"}
_LLM_EVENT_TYPES = {
    "game_start",
    "llm_response",
    "tool_call",
    "stall",
    "context_reset",
    "context_trim",
    "llm_error",
    "auto_pilot_mode",
}


@dataclass(frozen=True, kw_only=True)
class Player:
    name: str
    type: str
    toolCallsOk: int
    toolCallsFailed: int
    thinkingTimeSecs: float
    model: str | None = None
    deckName: str | None = None
    deckStrategy: str | None = None
    commander: str | None = None
    reasoningEffort: str | None = None
    totalCostUsd: float | None = None
    placement: int | None = None
    tools: list[str] | None = None
    timedOut: bool | None = None


@dataclass(frozen=True, kw_only=True)
class PilotPlayer:
    """Pilot player with required model field.  Narrowed via is_pilot_player()."""

    name: str
    model: str
    toolCallsOk: int
    toolCallsFailed: int
    thinkingTimeSecs: float
    type: Literal["pilot"] = "pilot"
    deckName: str | None = None
    deckStrategy: str | None = None
    commander: str | None = None
    reasoningEffort: str | None = None
    totalCostUsd: float | None = None
    placement: int | None = None
    tools: list[str] | None = None
    timedOut: bool | None = None


def is_pilot_player(player: Player) -> TypeGuard[PilotPlayer]:
    """Narrow a Player to PilotPlayer.  Crashes if type is pilot but model is missing."""
    if player.type != "pilot":
        return False
    assert isinstance(player.model, str) and player.model, (
        f"pilot player missing model: {player!r}"
    )
    return True


class SnapshotPlayer(TypedDict):
    name: str
    life: int
    library_size: int
    battlefield: list[str | Permanent]
    graveyard: list[str | Permanent]
    hand: list[str | Permanent]
    hand_count: NotRequired[int]
    exile: NotRequired[list[str | Permanent]]
    counters: NotRequired[object]
    commanders: NotRequired[list[str | Permanent]]
    command_zone: NotRequired[list[str | Permanent]]
    is_active: NotRequired[bool]
    has_left: NotRequired[bool]
    mana_pool: NotRequired[JsonObject]


class CombatGroup(TypedDict, total=False):
    attackers: list[CombatCreature]
    blockers: list[CombatCreature]
    blocked: bool
    defending: str


class Snapshot(TypedDict):
    seq: int
    turn: int
    phase: str | None
    step: str | None
    active_player: str | None
    priority_player: str | None
    players: list[SnapshotPlayer]
    stack: list[str | StackItem]
    ts: NotRequired[str]
    combat: NotRequired[list[CombatGroup]]


Action = TypedDict(
    "Action",
    {
        "seq": int,
        "message": NotRequired[str],
        "type": NotRequired[str],
        "turn": NotRequired[int],
        "phase": NotRequired[str | None],
        "step": NotRequired[str | None],
        "active_player": NotRequired[str | None],
        "ts": NotRequired[str],
        "from": NotRequired[str],
    },
)


@dataclass(kw_only=True)
class LlmUsage:
    promptTokens: int | None = None
    completionTokens: int | None = None
    cachedTokens: int | None = None
    reasoningTokens: int | None = None


@dataclass(kw_only=True)
class _LlmEventBase:
    type: str
    player: str
    ts: str | None = None
    seq: int | None = None
    gameSeq: int | None = None


@dataclass(kw_only=True)
class GameStartEvent(_LlmEventBase):
    type: Literal["game_start"]
    model: str | None = None
    availableTools: list[str] | None = None


@dataclass(kw_only=True)
class LlmResponseEvent(_LlmEventBase):
    type: Literal["llm_response"]
    reasoning: str | None = None
    thinking: str | None = None
    toolCalls: object | None = None
    usage: LlmUsage | None = None
    costUsd: float | None = None


@dataclass(kw_only=True)
class ToolCallEvent(_LlmEventBase):
    type: Literal["tool_call"]
    tool: str
    args: JsonObject
    result: str
    latencyMs: int | None = None


@dataclass(kw_only=True)
class StallEvent(_LlmEventBase):
    type: Literal["stall"]
    turnsWithoutProgress: int | None = None
    lastTools: list[str] | None = None


@dataclass(kw_only=True)
class ContextResetEvent(_LlmEventBase):
    type: Literal["context_reset"]
    reason: str | None = None


@dataclass(kw_only=True)
class ContextTrimEvent(_LlmEventBase):
    type: Literal["context_trim"]
    messagesBefore: int | None = None
    messagesAfter: int | None = None


@dataclass(kw_only=True)
class LlmErrorEvent(_LlmEventBase):
    type: Literal["llm_error"]
    errorType: str | None = None
    errorMessage: str | None = None


@dataclass(kw_only=True)
class AutoPilotModeEvent(_LlmEventBase):
    type: Literal["auto_pilot_mode"]
    reason: str | None = None


LlmEvent: TypeAlias = (
    GameStartEvent
    | LlmResponseEvent
    | ToolCallEvent
    | StallEvent
    | ContextResetEvent
    | ContextTrimEvent
    | LlmErrorEvent
    | AutoPilotModeEvent
)


_LLM_EVENT_CLASSES: dict[str, type[LlmEvent]] = {
    "game_start": GameStartEvent,
    "llm_response": LlmResponseEvent,
    "tool_call": ToolCallEvent,
    "stall": StallEvent,
    "context_reset": ContextResetEvent,
    "context_trim": ContextTrimEvent,
    "llm_error": LlmErrorEvent,
    "auto_pilot_mode": AutoPilotModeEvent,
}


def _llm_event_from_dict(d: JsonObject) -> LlmEvent:
    """Convert a validated raw dict into the appropriate LlmEvent dataclass."""
    event_type = d["type"]
    assert isinstance(event_type, str) and event_type in _LLM_EVENT_CLASSES, (
        f"unknown llm event type: {event_type!r}"
    )
    cls = _LLM_EVENT_CLASSES[event_type]
    field_names = {f.name for f in dataclasses.fields(cls)}
    kwargs: dict[str, object] = {}
    extra: dict[str, object] = {}
    for k, v in d.items():
        if k not in field_names:
            extra[k] = v
            continue
        if k == "usage" and isinstance(v, dict):
            usage_fields = {f.name for f in dataclasses.fields(LlmUsage)}
            usage_kwargs = {uk: uv for uk, uv in v.items() if uk in usage_fields}
            usage_extra = {uk: uv for uk, uv in v.items() if uk not in usage_fields}
            usage_instance = LlmUsage(**usage_kwargs)
            object.__setattr__(usage_instance, "_source_keys", frozenset(v.keys()))
            object.__setattr__(usage_instance, "_extra", usage_extra)
            kwargs[k] = usage_instance
        else:
            kwargs[k] = v
    instance = cls(**kwargs)  # type: ignore[arg-type]
    # Track which keys were in the source dict so serialization can distinguish
    # "field was explicitly null" from "field was absent" (both are None on the dataclass).
    # Also preserve any unknown keys so round-trip serialization doesn't drop them.
    object.__setattr__(instance, "_source_keys", frozenset(d.keys()))
    object.__setattr__(instance, "_extra", extra)
    return instance


class GameOver(TypedDict):
    seq: int
    message: str


class Annotation(TypedDict):
    decisionIndex: int
    snapshotIndex: NotRequired[int]
    player: str
    type: Literal["blunder"]
    severity: str
    description: str
    actionTaken: str
    betterLine: str
    llmReasoning: NotRequired[str]


class PilotContext(TypedDict, total=False):
    untappedLands: int
    landDropsUsed: int
    playableCards: list[str]
    combatPhase: str | None
    alreadyAttacking: list[str | CombatCreature]
    incomingAttackers: list[str | CombatCreature]


class Decision(TypedDict):
    index: int
    snapshotIndex: int
    player: str
    turn: int
    phase: str | None
    actionType: str
    responseType: str
    message: str
    choices: list[Choice]
    choiceCount: int
    isForced: bool
    llmEventIndices: list[int]
    subsequentActions: list[str]
    step: NotRequired[str | None]
    pilotContext: NotRequired[PilotContext]
    chosen: NotRequired[object]
    chosenArgs: NotRequired[JsonObject]
    actionResult: NotRequired[JsonObject]
    castRolledBack: NotRequired[bool]
    items: NotRequired[list[MultiAmountItem]]
    totalMin: NotRequired[int]
    totalMax: NotRequired[int]


class GameError(TypedDict):
    ts: str
    player: str
    source: str
    message: str
    decisionIndex: NotRequired[int]


class CardMetadata(TypedDict, total=False):
    mana_cost: str
    type_line: str
    oracle_text: str
    power: str
    toughness: str
    loyalty: str
    defense: str


class _GameExportBase(TypedDict):
    version: Literal[8]
    id: str
    timestamp: str
    gameType: str
    deckType: str
    totalTurns: int
    winner: str | None
    harnessEpoch: int
    youtubeUrl: str
    players: list[Player]
    cardImages: dict[str, str]
    snapshots: list[Snapshot]
    actions: list[Action]
    llmEvents: list[LlmEvent]
    gameOver: GameOver | None
    season: int
    tournament: str | None


class _OptionalGameExportFields(TypedDict, total=False):
    cardData: dict[str, CardMetadata]
    decisions: list[Decision]
    errors: list[GameError]


class _AnnotatedGameExportFields(TypedDict):
    annotations: list[Annotation]
    blunderScriptVersion: int


class GameExport(
    _GameExportBase, _AnnotatedGameExportFields, _OptionalGameExportFields
):
    pass


class BuiltGameExport(_GameExportBase, _OptionalGameExportFields, total=False):
    annotations: list[Annotation]
    blunderScriptVersion: int


def _type_name(value: object) -> str:
    return type(value).__name__


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_object(value: object, source: str) -> JsonObject:
    assert isinstance(value, dict), (
        f"{source}: expected object, got {_type_name(value)}"
    )
    return value


def _require_key(obj: JsonObject, key: str, source: str) -> object:
    assert key in obj, f"{source}: missing {key}"
    return obj[key]


def _require_str(value: object, source: str) -> None:
    assert isinstance(value, str), f"{source}: expected string, got {_type_name(value)}"


def _require_non_empty_str(value: object, source: str) -> None:
    _require_str(value, source)
    assert value, f"{source}: expected non-empty string"


def _require_optional_str(value: object, source: str) -> None:
    assert value is None or isinstance(value, str), (
        f"{source}: expected string or null, got {_type_name(value)}"
    )


def _require_int(value: object, source: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )


def _require_non_negative_int(value: object, source: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )
    assert value >= 0, f"{source}: expected non-negative int, got {value!r}"


def _require_positive_int(value: object, source: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )
    assert value >= 1, f"{source}: expected positive int, got {value!r}"


def _require_number(value: object, source: str) -> None:
    assert _is_number(value), f"{source}: expected number, got {_type_name(value)}"


def _require_bool(value: object, source: str) -> None:
    assert isinstance(value, bool), f"{source}: expected bool, got {_type_name(value)}"


def _require_list(value: object, source: str) -> list[object]:
    assert isinstance(value, list), f"{source}: expected list, got {_type_name(value)}"
    return value


def _require_str_list(value: object, source: str) -> None:
    for index, item in enumerate(_require_list(value, source)):
        _require_str(item, f"{source}[{index}]")


def _require_int_list(value: object, source: str) -> None:
    for index, item in enumerate(_require_list(value, source)):
        _require_non_negative_int(item, f"{source}[{index}]")


def _require_object_list(value: object, source: str) -> None:
    for index, item in enumerate(_require_list(value, source)):
        _require_object(item, f"{source}[{index}]")


def _require_int_or_str(value: object, source: str) -> None:
    assert _is_int(value) or isinstance(value, str), (
        f"{source}: expected int or string, got {_type_name(value)}"
    )


def _is_permanent(value: object, source: str) -> TypeIs[Permanent]:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "name", source), f"{source}.name")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    if "tapped" in obj:
        _require_bool(obj["tapped"], f"{source}.tapped")
    if "summoning_sick" in obj:
        _require_bool(obj["summoning_sick"], f"{source}.summoning_sick")
    if "face_down" in obj:
        _require_bool(obj["face_down"], f"{source}.face_down")
    if "token" in obj:
        _require_bool(obj["token"], f"{source}.token")
    if "power" in obj:
        _require_int_or_str(obj["power"], f"{source}.power")
    if "toughness" in obj:
        _require_int_or_str(obj["toughness"], f"{source}.toughness")
    if "power_toughness" in obj:
        _require_str(obj["power_toughness"], f"{source}.power_toughness")
    if "pt" in obj:
        _require_str(obj["pt"], f"{source}.pt")
    if "loyalty" in obj:
        _require_int(obj["loyalty"], f"{source}.loyalty")
    if "original_card" in obj:
        _require_str(obj["original_card"], f"{source}.original_card")
    if "copy" in obj:
        _require_bool(obj["copy"], f"{source}.copy")
    return True


def _is_stack_target(value: object, source: str) -> TypeIs[StackTarget]:
    obj = _require_object(value, source)
    if "name" in obj:
        _require_str(obj["name"], f"{source}.name")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    return True


def _is_stack_item(value: object, source: str) -> TypeIs[StackItem]:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "name", source), f"{source}.name")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    if "source_card" in obj:
        _require_str(obj["source_card"], f"{source}.source_card")
    if "ability_text" in obj:
        _require_str(obj["ability_text"], f"{source}.ability_text")
    if "targets" in obj:
        _validate_str_or_typed_list(
            obj["targets"], f"{source}.targets", _is_stack_target
        )
    return True


def _is_combat_creature(value: object, source: str) -> TypeIs[CombatCreature]:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "name", source), f"{source}.name")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    if "power" in obj:
        _require_int_or_str(obj["power"], f"{source}.power")
    if "toughness" in obj:
        _require_int_or_str(obj["toughness"], f"{source}.toughness")
    if "power_toughness" in obj:
        _require_str(obj["power_toughness"], f"{source}.power_toughness")
    if "pt" in obj:
        _require_str(obj["pt"], f"{source}.pt")
    return True


def _is_choice(value: object, source: str) -> TypeIs[Choice]:
    obj = _require_object(value, source)
    if "index" in obj:
        _require_int(obj["index"], f"{source}.index")
    if "name" in obj:
        _require_str(obj["name"], f"{source}.name")
    if "description" in obj:
        _require_str(obj["description"], f"{source}.description")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    if "action" in obj:
        _require_str(obj["action"], f"{source}.action")
    if "mana_cost" in obj:
        _require_str(obj["mana_cost"], f"{source}.mana_cost")
    if "choice_type" in obj:
        _require_str(obj["choice_type"], f"{source}.choice_type")
    return True


def _is_multi_amount_item(value: object, source: str) -> TypeIs[MultiAmountItem]:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "description", source), f"{source}.description")
    if "min" in obj:
        _require_int(obj["min"], f"{source}.min")
    if "max" in obj:
        _require_int(obj["max"], f"{source}.max")
    return True


def _validate_str_or_typed_list(
    value: object,
    source: str,
    typed_validator: Callable[[object, str], bool],
) -> None:
    """Validate a list where items can be strings or typed dicts."""
    for index, item in enumerate(_require_list(value, source)):
        if isinstance(item, dict):
            assert typed_validator(item, f"{source}[{index}]")
        else:
            _require_str(item, f"{source}[{index}]")


def _validate_card_list(value: object, source: str) -> None:
    """Validate a list of cards that can be strings or Permanent dicts."""
    _validate_str_or_typed_list(value, source, _is_permanent)


def _validate_player(value: object, source: str) -> Player:
    """Validate a raw dict and construct a Player instance.

    If *value* is already a Player (e.g. re-validation), returns it as-is.
    """
    if isinstance(value, Player):
        return value
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "name", source), f"{source}.name")
    _require_str(_require_key(obj, "type", source), f"{source}.type")
    _require_non_negative_int(
        _require_key(obj, "toolCallsOk", source), f"{source}.toolCallsOk"
    )
    _require_non_negative_int(
        _require_key(obj, "toolCallsFailed", source),
        f"{source}.toolCallsFailed",
    )
    _require_number(
        _require_key(obj, "thinkingTimeSecs", source), f"{source}.thinkingTimeSecs"
    )
    if "model" in obj:
        _require_str(obj["model"], f"{source}.model")
    if "deckName" in obj:
        _require_str(obj["deckName"], f"{source}.deckName")
    if "deckStrategy" in obj:
        _require_str(obj["deckStrategy"], f"{source}.deckStrategy")
    if "commander" in obj:
        _require_str(obj["commander"], f"{source}.commander")
    if "reasoningEffort" in obj:
        _require_str(obj["reasoningEffort"], f"{source}.reasoningEffort")
    if "totalCostUsd" in obj:
        _require_number(obj["totalCostUsd"], f"{source}.totalCostUsd")
    if "placement" in obj:
        _require_positive_int(obj["placement"], f"{source}.placement")
    if "tools" in obj:
        _require_str_list(obj["tools"], f"{source}.tools")
    if "timedOut" in obj:
        _require_bool(obj["timedOut"], f"{source}.timedOut")
    if obj["type"] == "pilot":
        _require_non_empty_str(_require_key(obj, "model", source), f"{source}.model")
    return Player(
        name=obj["name"],  # type: ignore[arg-type]
        type=obj["type"],  # type: ignore[arg-type]
        toolCallsOk=obj["toolCallsOk"],  # type: ignore[arg-type]
        toolCallsFailed=obj["toolCallsFailed"],  # type: ignore[arg-type]
        thinkingTimeSecs=obj["thinkingTimeSecs"],  # type: ignore[arg-type]
        model=obj.get("model"),  # type: ignore[arg-type]
        deckName=obj.get("deckName"),  # type: ignore[arg-type]
        deckStrategy=obj.get("deckStrategy"),  # type: ignore[arg-type]
        commander=obj.get("commander"),  # type: ignore[arg-type]
        reasoningEffort=obj.get("reasoningEffort"),  # type: ignore[arg-type]
        totalCostUsd=obj.get("totalCostUsd"),  # type: ignore[arg-type]
        placement=obj.get("placement"),  # type: ignore[arg-type]
        tools=obj.get("tools"),  # type: ignore[arg-type]
        timedOut=obj.get("timedOut"),  # type: ignore[arg-type]
    )


def _is_snapshot_player(value: object, source: str) -> TypeIs[SnapshotPlayer]:
    obj = _require_object(value, source)
    _require_non_empty_str(_require_key(obj, "name", source), f"{source}.name")
    _require_int(_require_key(obj, "life", source), f"{source}.life")
    _require_non_negative_int(
        _require_key(obj, "library_size", source), f"{source}.library_size"
    )
    _validate_card_list(
        _require_key(obj, "battlefield", source), f"{source}.battlefield"
    )
    _validate_card_list(_require_key(obj, "graveyard", source), f"{source}.graveyard")
    _validate_card_list(_require_key(obj, "hand", source), f"{source}.hand")
    if "hand_count" in obj:
        _require_non_negative_int(obj["hand_count"], f"{source}.hand_count")
    if "exile" in obj:
        _validate_card_list(obj["exile"], f"{source}.exile")
    if "commanders" in obj:
        _validate_card_list(obj["commanders"], f"{source}.commanders")
    if "command_zone" in obj:
        _validate_card_list(obj["command_zone"], f"{source}.command_zone")
    if "is_active" in obj:
        _require_bool(obj["is_active"], f"{source}.is_active")
    if "has_left" in obj:
        _require_bool(obj["has_left"], f"{source}.has_left")
    if "mana_pool" in obj:
        _require_object(obj["mana_pool"], f"{source}.mana_pool")
    return True


def _is_combat_group(value: object, source: str) -> TypeIs[CombatGroup]:
    obj = _require_object(value, source)
    if "attackers" in obj:
        for idx, item in enumerate(
            _require_list(obj["attackers"], f"{source}.attackers")
        ):
            assert _is_combat_creature(item, f"{source}.attackers[{idx}]")
    if "blockers" in obj:
        for idx, item in enumerate(
            _require_list(obj["blockers"], f"{source}.blockers")
        ):
            assert _is_combat_creature(item, f"{source}.blockers[{idx}]")
    if "blocked" in obj:
        _require_bool(obj["blocked"], f"{source}.blocked")
    if "defending" in obj:
        _require_str(obj["defending"], f"{source}.defending")
    return True


def _is_snapshot(value: object, source: str) -> TypeIs[Snapshot]:
    obj = _require_object(value, source)
    _require_int(_require_key(obj, "seq", source), f"{source}.seq")
    _require_int(_require_key(obj, "turn", source), f"{source}.turn")
    _require_optional_str(_require_key(obj, "phase", source), f"{source}.phase")
    _require_optional_str(_require_key(obj, "step", source), f"{source}.step")
    _require_optional_str(
        _require_key(obj, "active_player", source), f"{source}.active_player"
    )
    _require_optional_str(
        _require_key(obj, "priority_player", source), f"{source}.priority_player"
    )
    for index, player in enumerate(
        _require_list(_require_key(obj, "players", source), f"{source}.players")
    ):
        assert _is_snapshot_player(player, f"{source}.players[{index}]")
    _validate_str_or_typed_list(
        _require_key(obj, "stack", source), f"{source}.stack", _is_stack_item
    )
    if "ts" in obj:
        _require_str(obj["ts"], f"{source}.ts")
    if "combat" in obj:
        for index, group in enumerate(_require_list(obj["combat"], f"{source}.combat")):
            assert _is_combat_group(group, f"{source}.combat[{index}]")
    return True


def _is_action(value: object, source: str) -> TypeIs[Action]:
    obj = _require_object(value, source)
    _require_int(_require_key(obj, "seq", source), f"{source}.seq")
    if "message" in obj:
        _require_str(obj["message"], f"{source}.message")
    if "type" in obj:
        _require_str(obj["type"], f"{source}.type")
        assert obj["type"] in _ACTION_TYPES, (
            f"{source}.type: unexpected action type {obj['type']!r}"
        )
    if "turn" in obj:
        _require_int(obj["turn"], f"{source}.turn")
    if "phase" in obj:
        _require_optional_str(obj["phase"], f"{source}.phase")
    if "step" in obj:
        _require_optional_str(obj["step"], f"{source}.step")
    if "active_player" in obj:
        _require_optional_str(obj["active_player"], f"{source}.active_player")
    if "ts" in obj:
        _require_str(obj["ts"], f"{source}.ts")
    if "from" in obj:
        _require_str(obj["from"], f"{source}.from")
    return True


def _is_llm_usage(value: object, source: str) -> bool:
    obj = _require_object(value, source)
    for key in ("promptTokens", "completionTokens", "cachedTokens", "reasoningTokens"):
        if key in obj:
            _require_non_negative_int(obj[key], f"{source}.{key}")
    return True


def _is_llm_event(value: object, source: str) -> bool:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "type", source), f"{source}.type")
    assert obj["type"] in _LLM_EVENT_TYPES, (
        f"{source}.type: unexpected llm event type {obj['type']!r}"
    )
    _require_str(_require_key(obj, "player", source), f"{source}.player")

    # Base fields (shared by all variants)
    if "ts" in obj:
        _require_str(obj["ts"], f"{source}.ts")
    if "seq" in obj:
        _require_int(obj["seq"], f"{source}.seq")
    if "gameSeq" in obj:
        _require_int(obj["gameSeq"], f"{source}.gameSeq")

    # Per-variant required fields
    if obj["type"] == "tool_call":
        _require_str(_require_key(obj, "tool", source), f"{source}.tool")
        _require_object(_require_key(obj, "args", source), f"{source}.args")
        _require_str(_require_key(obj, "result", source), f"{source}.result")

    # Validate all known optional fields when present (regardless of variant,
    # so cross-variant typos like "tool": 123 on an llm_response are caught).
    if "model" in obj:
        _require_str(obj["model"], f"{source}.model")
    if "availableTools" in obj:
        _require_str_list(obj["availableTools"], f"{source}.availableTools")
    if "reasoning" in obj:
        _require_optional_str(obj["reasoning"], f"{source}.reasoning")
    if "thinking" in obj:
        _require_optional_str(obj["thinking"], f"{source}.thinking")
    if "usage" in obj:
        assert _is_llm_usage(obj["usage"], f"{source}.usage")
    if "costUsd" in obj:
        _require_number(obj["costUsd"], f"{source}.costUsd")
    if "tool" in obj:
        _require_str(obj["tool"], f"{source}.tool")
    if "args" in obj:
        _require_object(obj["args"], f"{source}.args")
    if "result" in obj:
        _require_str(obj["result"], f"{source}.result")
    if "latencyMs" in obj:
        _require_int(obj["latencyMs"], f"{source}.latencyMs")
    if "turnsWithoutProgress" in obj:
        _require_int(obj["turnsWithoutProgress"], f"{source}.turnsWithoutProgress")
    if "lastTools" in obj:
        _require_str_list(obj["lastTools"], f"{source}.lastTools")
    if "reason" in obj:
        _require_str(obj["reason"], f"{source}.reason")
    if "errorType" in obj:
        _require_str(obj["errorType"], f"{source}.errorType")
    if "errorMessage" in obj:
        _require_str(obj["errorMessage"], f"{source}.errorMessage")
    if "messagesBefore" in obj:
        _require_int(obj["messagesBefore"], f"{source}.messagesBefore")
    if "messagesAfter" in obj:
        _require_int(obj["messagesAfter"], f"{source}.messagesAfter")

    return True


def _is_game_over(value: object, source: str) -> TypeIs[GameOver]:
    obj = _require_object(value, source)
    _require_int(_require_key(obj, "seq", source), f"{source}.seq")
    _require_str(_require_key(obj, "message", source), f"{source}.message")
    return True


def _is_annotation(value: object, source: str) -> TypeIs[Annotation]:
    obj = _require_object(value, source)
    _require_non_negative_int(
        _require_key(obj, "decisionIndex", source), f"{source}.decisionIndex"
    )
    if "snapshotIndex" in obj:
        _require_non_negative_int(obj["snapshotIndex"], f"{source}.snapshotIndex")
    _require_str(_require_key(obj, "player", source), f"{source}.player")
    _require_str(_require_key(obj, "type", source), f"{source}.type")
    assert obj["type"] == "blunder", (
        f"{source}.type: expected 'blunder', got {obj['type']!r}"
    )
    _require_str(_require_key(obj, "severity", source), f"{source}.severity")
    assert obj["severity"] in _ANNOTATION_SEVERITIES, (
        f"{source}.severity: unexpected annotation severity {obj['severity']!r}"
    )
    _require_str(_require_key(obj, "description", source), f"{source}.description")
    _require_str(_require_key(obj, "actionTaken", source), f"{source}.actionTaken")
    _require_str(_require_key(obj, "betterLine", source), f"{source}.betterLine")
    if "llmReasoning" in obj:
        _require_str(obj["llmReasoning"], f"{source}.llmReasoning")
    return True


def _is_pilot_context(value: object, source: str) -> TypeIs[PilotContext]:
    obj = _require_object(value, source)
    if "untappedLands" in obj:
        _require_non_negative_int(obj["untappedLands"], f"{source}.untappedLands")
    if "landDropsUsed" in obj:
        _require_non_negative_int(obj["landDropsUsed"], f"{source}.landDropsUsed")
    if "playableCards" in obj:
        _require_str_list(obj["playableCards"], f"{source}.playableCards")
    if "combatPhase" in obj:
        _require_optional_str(obj["combatPhase"], f"{source}.combatPhase")
    if "alreadyAttacking" in obj:
        _validate_str_or_typed_list(
            obj["alreadyAttacking"], f"{source}.alreadyAttacking", _is_combat_creature
        )
    if "incomingAttackers" in obj:
        _validate_str_or_typed_list(
            obj["incomingAttackers"], f"{source}.incomingAttackers", _is_combat_creature
        )
    return True


def _is_decision(value: object, source: str) -> TypeIs[Decision]:
    obj = _require_object(value, source)
    _require_non_negative_int(_require_key(obj, "index", source), f"{source}.index")
    _require_non_negative_int(
        _require_key(obj, "snapshotIndex", source), f"{source}.snapshotIndex"
    )
    _require_str(_require_key(obj, "player", source), f"{source}.player")
    _require_non_negative_int(_require_key(obj, "turn", source), f"{source}.turn")
    _require_optional_str(_require_key(obj, "phase", source), f"{source}.phase")
    _require_str(_require_key(obj, "actionType", source), f"{source}.actionType")
    _require_str(_require_key(obj, "responseType", source), f"{source}.responseType")
    _require_str(_require_key(obj, "message", source), f"{source}.message")
    for index, choice in enumerate(
        _require_list(_require_key(obj, "choices", source), f"{source}.choices")
    ):
        assert _is_choice(choice, f"{source}.choices[{index}]")
    _require_non_negative_int(
        _require_key(obj, "choiceCount", source), f"{source}.choiceCount"
    )
    _require_bool(_require_key(obj, "isForced", source), f"{source}.isForced")
    _require_int_list(
        _require_key(obj, "llmEventIndices", source), f"{source}.llmEventIndices"
    )
    _require_str_list(
        _require_key(obj, "subsequentActions", source), f"{source}.subsequentActions"
    )
    if "step" in obj:
        _require_optional_str(obj["step"], f"{source}.step")
    if "pilotContext" in obj:
        assert _is_pilot_context(obj["pilotContext"], f"{source}.pilotContext")
    if "chosenArgs" in obj:
        _require_object(obj["chosenArgs"], f"{source}.chosenArgs")
    if "actionResult" in obj:
        _require_object(obj["actionResult"], f"{source}.actionResult")
    if "castRolledBack" in obj:
        _require_bool(obj["castRolledBack"], f"{source}.castRolledBack")
    if "items" in obj:
        for index, item in enumerate(_require_list(obj["items"], f"{source}.items")):
            assert _is_multi_amount_item(item, f"{source}.items[{index}]")
    if "totalMin" in obj:
        _require_non_negative_int(obj["totalMin"], f"{source}.totalMin")
    if "totalMax" in obj:
        _require_non_negative_int(obj["totalMax"], f"{source}.totalMax")
    return True


def _is_game_error(value: object, source: str) -> TypeIs[GameError]:
    obj = _require_object(value, source)
    _require_str(_require_key(obj, "ts", source), f"{source}.ts")
    _require_str(_require_key(obj, "player", source), f"{source}.player")
    _require_str(_require_key(obj, "source", source), f"{source}.source")
    _require_str(_require_key(obj, "message", source), f"{source}.message")
    if "decisionIndex" in obj:
        _require_non_negative_int(obj["decisionIndex"], f"{source}.decisionIndex")
    return True


def _is_card_metadata(value: object, source: str) -> TypeIs[CardMetadata]:
    obj = _require_object(value, source)
    for key in (
        "mana_cost",
        "type_line",
        "oracle_text",
        "power",
        "toughness",
        "loyalty",
        "defense",
    ):
        if key in obj:
            _require_str(obj[key], f"{source}.{key}")
    return True


def _validate_common_game_export(value: object, source: str) -> JsonObject:
    obj = _require_object(value, source)
    version = _require_key(obj, "version", source)
    _require_int(version, f"{source}.version")
    assert version == 8, f"{source}.version: expected 8, got {version!r}"
    _require_non_empty_str(_require_key(obj, "id", source), f"{source}.id")
    _require_str(_require_key(obj, "timestamp", source), f"{source}.timestamp")
    _require_non_empty_str(_require_key(obj, "gameType", source), f"{source}.gameType")
    _require_non_empty_str(_require_key(obj, "deckType", source), f"{source}.deckType")
    _require_non_negative_int(
        _require_key(obj, "totalTurns", source), f"{source}.totalTurns"
    )
    winner = _require_key(obj, "winner", source)
    _require_optional_str(winner, f"{source}.winner")
    _require_non_negative_int(
        _require_key(obj, "harnessEpoch", source), f"{source}.harnessEpoch"
    )
    _require_str(_require_key(obj, "youtubeUrl", source), f"{source}.youtubeUrl")
    players = _require_list(_require_key(obj, "players", source), f"{source}.players")
    for index in range(len(players)):
        players[index] = _validate_player(players[index], f"{source}.players[{index}]")
    card_images = _require_object(
        _require_key(obj, "cardImages", source), f"{source}.cardImages"
    )
    for name, url in card_images.items():
        _require_str(name, f"{source}.cardImages key")
        _require_str(url, f"{source}.cardImages[{name}]")
    snapshots = _require_list(
        _require_key(obj, "snapshots", source), f"{source}.snapshots"
    )
    for index, snapshot in enumerate(snapshots):
        assert _is_snapshot(snapshot, f"{source}.snapshots[{index}]")
    actions = _require_list(_require_key(obj, "actions", source), f"{source}.actions")
    for index, action in enumerate(actions):
        assert _is_action(action, f"{source}.actions[{index}]")
    llm_events = _require_list(
        _require_key(obj, "llmEvents", source), f"{source}.llmEvents"
    )
    for index in range(len(llm_events)):
        if dataclasses.is_dataclass(llm_events[index]):
            continue  # Already converted (re-validation)
        event_obj = _require_object(llm_events[index], f"{source}.llmEvents[{index}]")
        assert _is_llm_event(event_obj, f"{source}.llmEvents[{index}]")
        llm_events[index] = _llm_event_from_dict(event_obj)
    game_over = _require_key(obj, "gameOver", source)
    assert game_over is None or _is_game_over(game_over, f"{source}.gameOver")
    _require_non_negative_int(_require_key(obj, "season", source), f"{source}.season")
    tournament = _require_key(obj, "tournament", source)
    _require_optional_str(tournament, f"{source}.tournament")
    if "cardData" in obj:
        card_data = _require_object(obj["cardData"], f"{source}.cardData")
        for card_name, metadata in card_data.items():
            _require_str(card_name, f"{source}.cardData key")
            assert _is_card_metadata(metadata, f"{source}.cardData[{card_name}]")
    if "decisions" in obj:
        for index, decision in enumerate(
            _require_list(obj["decisions"], f"{source}.decisions")
        ):
            assert _is_decision(decision, f"{source}.decisions[{index}]")
    if "errors" in obj:
        for index, error in enumerate(_require_list(obj["errors"], f"{source}.errors")):
            assert _is_game_error(error, f"{source}.errors[{index}]")
    if "annotations" in obj:
        for index, annotation in enumerate(
            _require_list(obj["annotations"], f"{source}.annotations")
        ):
            assert _is_annotation(annotation, f"{source}.annotations[{index}]")
    if "blunderScriptVersion" in obj:
        _require_non_negative_int(
            obj["blunderScriptVersion"], f"{source}.blunderScriptVersion"
        )
    return obj


def is_built_game_export(
    value: object, source: str = "game export"
) -> TypeIs[BuiltGameExport]:
    _validate_common_game_export(value, source)
    return True


def is_game_export(value: object, source: str = "game export") -> TypeIs[GameExport]:
    obj = _validate_common_game_export(value, source)
    annotations = _require_key(obj, "annotations", source)
    _require_list(annotations, f"{source}.annotations")
    blunder_version = _require_key(obj, "blunderScriptVersion", source)
    _require_non_negative_int(blunder_version, f"{source}.blunderScriptVersion")
    return True


def require_built_game_export(
    value: object, source: str = "game export"
) -> BuiltGameExport:
    assert is_built_game_export(value, source)
    return value


def require_game_export(value: object, source: str = "game export") -> GameExport:
    assert is_game_export(value, source)
    return value


def load_game_export(path: str | Path) -> GameExport:
    export_path = Path(path)
    raw = (
        gzip.decompress(export_path.read_bytes())
        if export_path.suffix == ".gz"
        else export_path.read_text()
    )
    return require_game_export(json.loads(raw), source=export_path.name)


def load_built_game_export(path: str | Path) -> BuiltGameExport:
    export_path = Path(path)
    raw = (
        gzip.decompress(export_path.read_bytes())
        if export_path.suffix == ".gz"
        else export_path.read_text()
    )
    return require_built_game_export(json.loads(raw), source=export_path.name)


def json_default(obj: object) -> object:
    """Handle dataclass instances in json.dumps.

    If the instance was created via ``_llm_event_from_dict``, only fields
    that were present in the source dict are serialized (preserving the
    distinction between "field was explicitly null" and "field was absent").
    Otherwise, fields with None values are omitted.

    Usage: ``json.dumps(export, default=json_default)``
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        source_keys: frozenset[str] | None = getattr(obj, "_source_keys", None)
        if source_keys is not None:
            result = {
                f.name: getattr(obj, f.name)
                for f in dataclasses.fields(obj)
                if f.name in source_keys
            }
            # Re-emit unknown keys preserved from the source dict.
            extra: dict[str, object] | None = getattr(obj, "_extra", None)
            if extra:
                result.update(extra)
            return result
        return {
            f.name: getattr(obj, f.name)
            for f in dataclasses.fields(obj)
            if getattr(obj, f.name) is not None
        }
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


__all__ = [
    "Action",
    "Annotation",
    "AutoPilotModeEvent",
    "BuiltGameExport",
    "CardMetadata",
    "Choice",
    "CombatCreature",
    "CombatGroup",
    "ContextResetEvent",
    "ContextTrimEvent",
    "Decision",
    "GameError",
    "GameExport",
    "GameOver",
    "GameStartEvent",
    "JsonObject",
    "LlmErrorEvent",
    "LlmEvent",
    "LlmResponseEvent",
    "LlmUsage",
    "MultiAmountItem",
    "Permanent",
    "PilotContext",
    "PilotPlayer",
    "Player",
    "Snapshot",
    "SnapshotPlayer",
    "StackItem",
    "StackTarget",
    "StallEvent",
    "ToolCallEvent",
    "json_default",
    "is_built_game_export",
    "is_game_export",
    "is_pilot_player",
    "load_built_game_export",
    "load_game_export",
    "require_built_game_export",
    "require_game_export",
]

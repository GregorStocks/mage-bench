"""Typed helpers for game export JSON.

These types are backed by ``schemas/game-export-v8.schema.json``. Tests keep
the in-memory Python types aligned with the JSON Schema so callers can load
validated exports with typed nested payloads instead of raw ``dict[str, object]``
blobs.
"""

import copy
import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, ClassVar, Literal, TypeAlias, TypeVar, cast

from typing_extensions import TypeGuard, TypeIs

from scripts.json5_utils import loads_json5

JsonObject: TypeAlias = dict[str, object]
T = TypeVar("T")


# -- Nested payload types used by the decision renderer --


@dataclass(frozen=True, slots=True)
class _DecisionSupportRecord:
    """Shared helpers for decision-support leaves with schema extras."""

    _extras: Mapping[str, object] = field(
        default_factory=dict,
        repr=False,
        compare=True,
        kw_only=True,
    )
    _present_fields: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=True,
        kw_only=True,
    )

    _KNOWN_FIELDS: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "_extras", MappingProxyType(dict(self._extras)))
        present_fields = self._present_fields or frozenset(
            name for name in self._KNOWN_FIELDS if getattr(self, name) is not None
        )
        object.__setattr__(self, "_present_fields", frozenset(present_fields))

    @property
    def extras(self) -> Mapping[str, object]:
        return self._extras

    def has_field(self, key: str) -> bool:
        return key in self._present_fields or key in self._extras

    def get_value(self, key: str) -> object | None:
        if key in self._KNOWN_FIELDS:
            return cast(object, getattr(self, key))
        return self._extras.get(key)

    def to_mapping(self) -> JsonObject:
        obj: JsonObject = {}
        for key in self._KNOWN_FIELDS:
            if key in self._present_fields:
                obj[key] = cast(object, getattr(self, key))
        obj.update(self._extras)
        return obj

    def _deepcopy_mapping(self, memo: dict[int, object]) -> JsonObject:
        return copy.deepcopy(self.to_mapping(), memo)


def _extras_from_mapping(
    obj: Mapping[str, object], known_fields: tuple[str, ...]
) -> Mapping[str, object]:
    return {key: value for key, value in obj.items() if key not in known_fields}


def _present_fields_from_mapping(
    obj: Mapping[str, object], known_fields: tuple[str, ...]
) -> frozenset[str]:
    return frozenset(key for key in known_fields if key in obj)


@dataclass(frozen=True, slots=True)
class Permanent:
    """A card on the battlefield, in hand, graveyard, exile, or command zone."""

    name: str
    id: str | None = None
    tapped: bool | None = None
    summoning_sick: bool | None = None
    face_down: bool | None = None
    token: bool | None = None
    power: int | str | None = None
    toughness: int | str | None = None
    power_toughness: str | None = None
    pt: str | None = None
    loyalty: int | None = None
    counters: object | None = None
    original_card: str | None = None
    copy: bool | None = None
    # The JSON schema allows additional leaf fields; keep them here so loading
    # into dataclasses does not silently drop export data.
    _extras: JsonObject = field(
        default_factory=dict, repr=False, compare=False, kw_only=True
    )


@dataclass(frozen=True, slots=True)
class StackTarget:
    """A target of a spell or ability on the stack."""

    name: str | None = None
    id: str | None = None
    _extras: JsonObject = field(
        default_factory=dict, repr=False, compare=False, kw_only=True
    )


@dataclass(frozen=True, slots=True)
class StackItem:
    """A spell or ability on the stack."""

    name: str
    id: str | None = None
    source_card: str | None = None
    ability_text: str | None = None
    targets: list[str | StackTarget] | None = None
    _extras: JsonObject = field(
        default_factory=dict, repr=False, compare=False, kw_only=True
    )


@dataclass(frozen=True, slots=True)
class CombatCreature:
    """A creature in a combat group (attacker or blocker) or incoming attacker."""

    name: str
    id: str | None = None
    power: int | str | None = None
    toughness: int | str | None = None
    power_toughness: str | None = None
    pt: str | None = None
    _extras: JsonObject = field(
        default_factory=dict, repr=False, compare=False, kw_only=True
    )


def _public_dataclass_fields(dataclass_cls: Any) -> set[str]:
    return {
        member.name
        for member in fields(dataclass_cls)
        if not member.name.startswith("_")
    }


_PERMANENT_FIELDS = _public_dataclass_fields(Permanent)
_STACK_TARGET_FIELDS = _public_dataclass_fields(StackTarget)
_STACK_ITEM_FIELDS = _public_dataclass_fields(StackItem)
_COMBAT_CREATURE_FIELDS = _public_dataclass_fields(CombatCreature)


def export_record_field(record: object, field_name: str) -> object | None:
    """Read a field from a loaded export record (dataclass or dict)."""

    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        if field_name in record.__dataclass_fields__:
            return cast(object | None, getattr(record, field_name))
        extras: JsonObject | None = getattr(record, "_extras", None)
        if extras is not None:
            return extras.get(field_name)
        return None
    if isinstance(record, dict):
        return record.get(field_name)
    return None


@dataclass(frozen=True, slots=True)
class Choice(_DecisionSupportRecord):
    """A choice available to the player. Shape varies by decision type."""

    index: int | None = None
    name: str | None = None
    description: str | None = None
    id: str | None = None
    action: str | None = None
    mana_cost: str | None = None
    choice_type: str | None = None

    _KNOWN_FIELDS: ClassVar[tuple[str, ...]] = (
        "index",
        "name",
        "description",
        "id",
        "action",
        "mana_cost",
        "choice_type",
    )

    @classmethod
    def from_mapping(cls, obj: Mapping[str, object]) -> "Choice":
        return cls(
            index=cast(int | None, obj["index"] if "index" in obj else None),
            name=cast(str | None, obj["name"] if "name" in obj else None),
            description=cast(
                str | None, obj["description"] if "description" in obj else None
            ),
            id=cast(str | None, obj["id"] if "id" in obj else None),
            action=cast(str | None, obj["action"] if "action" in obj else None),
            mana_cost=cast(
                str | None, obj["mana_cost"] if "mana_cost" in obj else None
            ),
            choice_type=cast(
                str | None, obj["choice_type"] if "choice_type" in obj else None
            ),
            _extras=_extras_from_mapping(obj, cls._KNOWN_FIELDS),
            _present_fields=_present_fields_from_mapping(obj, cls._KNOWN_FIELDS),
        )

    @classmethod
    def coerce_list(cls, raw: list[object]) -> "list[Choice]":
        """Convert a list of raw dicts/Choice instances to typed Choice list."""
        return [
            c if isinstance(c, Choice) else cls.from_mapping(c)
            for c in raw
            if isinstance(c, (dict, Choice))
        ]

    def __deepcopy__(self, memo: dict[int, object]) -> "Choice":
        duplicate = Choice.from_mapping(self._deepcopy_mapping(memo))
        memo[id(self)] = duplicate
        return duplicate


@dataclass(frozen=True, slots=True)
class MultiAmountItem(_DecisionSupportRecord):
    """An item in a multi-amount decision."""

    description: str
    min: int | None = None
    max: int | None = None

    _KNOWN_FIELDS: ClassVar[tuple[str, ...]] = ("description", "min", "max")

    @classmethod
    def from_mapping(cls, obj: Mapping[str, object]) -> "MultiAmountItem":
        return cls(
            description=cast(str, obj["description"]),
            min=cast(int | None, obj["min"] if "min" in obj else None),
            max=cast(int | None, obj["max"] if "max" in obj else None),
            _extras=_extras_from_mapping(obj, cls._KNOWN_FIELDS),
            _present_fields=_present_fields_from_mapping(obj, cls._KNOWN_FIELDS),
        )

    @classmethod
    def coerce_list(cls, raw: list[object]) -> "list[MultiAmountItem]":
        """Convert a list of raw dicts/MultiAmountItem instances to typed list."""
        return [
            item if isinstance(item, MultiAmountItem) else cls.from_mapping(item)
            for item in raw
            if isinstance(item, (dict, MultiAmountItem))
        ]

    def __deepcopy__(self, memo: dict[int, object]) -> "MultiAmountItem":
        duplicate = MultiAmountItem.from_mapping(self._deepcopy_mapping(memo))
        memo[id(self)] = duplicate
        return duplicate


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


@dataclass(frozen=True, kw_only=True)
class SnapshotPlayer:
    """A player's state at a point in the game."""

    name: str
    life: int
    library_size: int
    battlefield: list[str | Permanent]
    graveyard: list[str | Permanent]
    hand: list[str | Permanent]
    hand_count: int | None = None
    exile: list[str | Permanent] | None = None
    counters: object | None = None
    commanders: list[str | Permanent] | None = None
    command_zone: list[str | Permanent] | None = None
    is_active: bool | None = None
    has_left: bool | None = None
    mana_pool: JsonObject | None = None


@dataclass(frozen=True, kw_only=True)
class CombatGroup:
    """A group of attackers and blockers in combat."""

    attackers: list[CombatCreature] | None = None
    blockers: list[CombatCreature] | None = None
    blocked: bool | None = None
    defending: str | None = None
    _extras: JsonObject = field(default_factory=dict, repr=False, compare=False)


_COMBAT_GROUP_FIELDS = _public_dataclass_fields(CombatGroup)


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """A snapshot of the game state at a point in time."""

    seq: int
    turn: int
    phase: str | None
    step: str | None
    active_player: str | None
    priority_player: str | None
    players: list[SnapshotPlayer]
    stack: list[str | StackItem]
    ts: str | None = None
    combat: list[CombatGroup] | None = None


@dataclass
class Action:
    seq: int
    message: str | None = None
    type: str | None = None
    turn: int | None = None
    phase: str | None = None
    step: str | None = None
    active_player: str | None = None
    ts: str | None = None
    from_: str | None = None


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


@dataclass
class GameOver:
    seq: int
    message: str


@dataclass
class Annotation:
    decisionIndex: int
    player: str
    type: Literal["blunder"]
    severity: str
    description: str
    actionTaken: str
    betterLine: str
    snapshotIndex: int | None = None
    llmReasoning: str | None = None


@dataclass(frozen=True, slots=True)
class PilotContext(_DecisionSupportRecord):
    untappedLands: int | None = None
    landDropsUsed: int | None = None
    playableCards: list[str] | None = None
    combatPhase: str | None = None
    alreadyAttacking: list[str | CombatCreature] | None = None
    incomingAttackers: list[str | CombatCreature] | None = None

    _KNOWN_FIELDS: ClassVar[tuple[str, ...]] = (
        "untappedLands",
        "landDropsUsed",
        "playableCards",
        "combatPhase",
        "alreadyAttacking",
        "incomingAttackers",
    )

    @classmethod
    def from_mapping(cls, obj: Mapping[str, object]) -> "PilotContext":
        return cls(
            untappedLands=cast(
                int | None, obj["untappedLands"] if "untappedLands" in obj else None
            ),
            landDropsUsed=cast(
                int | None, obj["landDropsUsed"] if "landDropsUsed" in obj else None
            ),
            playableCards=cast(
                list[str] | None,
                obj["playableCards"] if "playableCards" in obj else None,
            ),
            combatPhase=cast(
                str | None, obj["combatPhase"] if "combatPhase" in obj else None
            ),
            alreadyAttacking=cast(
                list[str | CombatCreature] | None,
                obj["alreadyAttacking"] if "alreadyAttacking" in obj else None,
            ),
            incomingAttackers=cast(
                list[str | CombatCreature] | None,
                obj["incomingAttackers"] if "incomingAttackers" in obj else None,
            ),
            _extras=_extras_from_mapping(obj, cls._KNOWN_FIELDS),
            _present_fields=_present_fields_from_mapping(obj, cls._KNOWN_FIELDS),
        )

    def __deepcopy__(self, memo: dict[int, object]) -> "PilotContext":
        duplicate = PilotContext.from_mapping(self._deepcopy_mapping(memo))
        memo[id(self)] = duplicate
        return duplicate


DecisionSupportRecord: TypeAlias = Choice | MultiAmountItem | PilotContext


def decision_support_get(record: DecisionSupportRecord, key: str) -> object | None:
    return record.get_value(key)


def decision_support_has(record: DecisionSupportRecord, key: str) -> bool:
    return record.has_field(key)


def game_export_to_jsonable(value: object) -> object:
    """Convert export leaves to plain JSON-compatible dict/list structures."""
    if isinstance(value, (Choice, MultiAmountItem, PilotContext)):
        return game_export_to_jsonable(value.to_mapping())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return game_export_to_jsonable(json_default(value))
    if isinstance(value, dict):
        return {key: game_export_to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [game_export_to_jsonable(item) for item in value]
    return value


@dataclass(slots=True)
class Decision:
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
    step: str | None = None
    pilotContext: PilotContext | None = None
    chosen: object = None
    chosenArgs: JsonObject | None = None
    actionResult: JsonObject | None = None
    castRolledBack: bool = False
    items: list[MultiAmountItem] | None = None
    totalMin: int | None = None
    totalMax: int | None = None
    actionSeq: int | None = None

    @classmethod
    def from_dict(cls, value: JsonObject) -> "Decision":
        raw_choices = value["choices"]
        assert isinstance(raw_choices, list), (
            f"Decision.choices must be a list, got {raw_choices!r}"
        )
        choices: list[Choice] = []
        for index, choice in enumerate(raw_choices):
            assert isinstance(choice, (dict, Choice)), (
                f"Decision.choices[{index}] must be an object, got {choice!r}"
            )
            choices.append(
                choice if isinstance(choice, Choice) else Choice.from_mapping(choice)
            )
        chosen_args = value.get("chosenArgs")
        assert chosen_args is None or isinstance(chosen_args, dict), (
            f"Decision.chosenArgs must be an object when present, got {chosen_args!r}"
        )
        action_result = value.get("actionResult")
        assert action_result is None or isinstance(action_result, dict), (
            f"Decision.actionResult must be an object when present, got {action_result!r}"
        )
        raw_pilot_context = value.get("pilotContext")
        assert raw_pilot_context is None or isinstance(
            raw_pilot_context, (dict, PilotContext)
        ), (
            f"Decision.pilotContext must be an object when present, got {raw_pilot_context!r}"
        )
        pilot_context = (
            raw_pilot_context
            if raw_pilot_context is None or isinstance(raw_pilot_context, PilotContext)
            else PilotContext.from_mapping(raw_pilot_context)
        )
        raw_items = value.get("items")
        assert raw_items is None or isinstance(raw_items, list), (
            f"Decision.items must be a list when present, got {raw_items!r}"
        )
        items: list[MultiAmountItem] | None = None
        if raw_items is not None:
            items = []
            for index, item in enumerate(raw_items):
                assert isinstance(item, (dict, MultiAmountItem)), (
                    f"Decision.items[{index}] must be an object, got {item!r}"
                )
                items.append(
                    item
                    if isinstance(item, MultiAmountItem)
                    else MultiAmountItem.from_mapping(item)
                )
        total_min = value.get("totalMin")
        assert total_min is None or _is_int(total_min), (
            f"Decision.totalMin must be an int when present, got {total_min!r}"
        )
        total_max = value.get("totalMax")
        assert total_max is None or _is_int(total_max), (
            f"Decision.totalMax must be an int when present, got {total_max!r}"
        )
        action_seq = value.get("actionSeq")
        assert action_seq is None or _is_int(action_seq), (
            f"Decision.actionSeq must be an int when present, got {action_seq!r}"
        )
        cast_rolled_back = value.get("castRolledBack", False)
        assert isinstance(cast_rolled_back, bool), (
            f"Decision.castRolledBack must be a bool when present, got {cast_rolled_back!r}"
        )
        return cls(
            index=cast(int, value["index"]),
            snapshotIndex=cast(int, value["snapshotIndex"]),
            player=cast(str, value["player"]),
            turn=cast(int, value["turn"]),
            phase=cast(str | None, value["phase"]),
            actionType=cast(str, value["actionType"]),
            responseType=cast(str, value["responseType"]),
            message=cast(str, value["message"]),
            choices=choices,
            choiceCount=cast(int, value["choiceCount"]),
            isForced=cast(bool, value["isForced"]),
            llmEventIndices=cast(list[int], value["llmEventIndices"]),
            subsequentActions=cast(list[str], value["subsequentActions"]),
            step=cast(str | None, value.get("step")),
            pilotContext=pilot_context,
            chosen=value.get("chosen"),
            chosenArgs=cast(JsonObject | None, chosen_args),
            actionResult=cast(JsonObject | None, action_result),
            castRolledBack=cast_rolled_back,
            items=items,
            totalMin=cast(int | None, total_min),
            totalMax=cast(int | None, total_max),
            actionSeq=cast(int | None, action_seq),
        )

    def to_dict(self) -> JsonObject:
        result: JsonObject = {
            "index": self.index,
            "snapshotIndex": self.snapshotIndex,
            "player": self.player,
            "turn": self.turn,
            "phase": self.phase,
            "step": self.step,
            "actionType": self.actionType,
            "responseType": self.responseType,
            "message": self.message,
            "choices": self.choices,
            "choiceCount": self.choiceCount,
            "isForced": self.isForced,
            "chosen": self.chosen,
            "llmEventIndices": self.llmEventIndices,
            "subsequentActions": self.subsequentActions,
        }
        if self.pilotContext is not None:
            result["pilotContext"] = self.pilotContext
        if self.chosenArgs is not None:
            result["chosenArgs"] = self.chosenArgs
        if self.actionResult is not None:
            result["actionResult"] = self.actionResult
        if self.castRolledBack:
            result["castRolledBack"] = True
        if self.items is not None:
            result["items"] = self.items
        if self.totalMin is not None:
            result["totalMin"] = self.totalMin
        if self.totalMax is not None:
            result["totalMax"] = self.totalMax
        if self.actionSeq is not None:
            result["actionSeq"] = self.actionSeq
        return result

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass
class GameError:
    ts: str
    player: str
    source: str
    message: str
    decisionIndex: int | None = None


@dataclass
class CardMetadata:
    mana_cost: str | None = None
    type_line: str | None = None
    oracle_text: str | None = None
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    defense: str | None = None


def _game_export_to_dict(obj: "BuiltGameExport | GameExport") -> JsonObject:
    """Shared serializer for game export dataclasses.

    Required fields (no default) are always emitted, even when None.
    Optional fields (default=None) are omitted when None.
    """
    result: JsonObject = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if value is None and f.default is None:
            continue
        result[f.name] = value
    return result


def _game_export_from_dict(
    cls: type["BuiltGameExport | GameExport"], obj: JsonObject
) -> "BuiltGameExport | GameExport":
    """Shared constructor for game export dataclasses from validated dicts."""
    kwargs: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        if (
            f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING
        ):
            if f.name in obj:
                kwargs[f.name] = obj[f.name]
        else:
            kwargs[f.name] = obj[f.name]
    return cls(**kwargs)  # type: ignore[arg-type]


@dataclass(slots=True)
class BuiltGameExport:
    """Game export with optional annotations (pre-annotation stage)."""

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
    cardData: dict[str, CardMetadata] | None = None
    decisions: list[Decision] | None = None
    errors: list[GameError] | None = None
    annotations: list[Annotation] | None = None
    blunderScriptVersion: int | None = None

    def to_dict(self) -> JsonObject:
        return _game_export_to_dict(self)

    @classmethod
    def from_dict(cls, obj: JsonObject) -> "BuiltGameExport":
        return cast("BuiltGameExport", _game_export_from_dict(cls, obj))


@dataclass(slots=True)
class GameExport:
    """Game export with required annotations (post-annotation stage)."""

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
    annotations: list[Annotation]
    blunderScriptVersion: int
    cardData: dict[str, CardMetadata] | None = None
    decisions: list[Decision] | None = None
    errors: list[GameError] | None = None

    def to_dict(self) -> JsonObject:
        return _game_export_to_dict(self)

    @classmethod
    def from_dict(cls, obj: JsonObject) -> "GameExport":
        return cast("GameExport", _game_export_from_dict(cls, obj))


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


def _require_str(value: object, source: str) -> str:
    assert isinstance(value, str), f"{source}: expected string, got {_type_name(value)}"
    return value


def _require_non_empty_str(value: object, source: str) -> str:
    result = _require_str(value, source)
    assert result, f"{source}: expected non-empty string"
    return result


def _require_optional_str(value: object, source: str) -> str | None:
    assert value is None or isinstance(value, str), (
        f"{source}: expected string or null, got {_type_name(value)}"
    )
    return value


def _require_int(value: object, source: str) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )
    return value


def _require_non_negative_int(value: object, source: str) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )
    assert value >= 0, f"{source}: expected non-negative int, got {value!r}"
    return value


def _require_positive_int(value: object, source: str) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"{source}: expected int, got {_type_name(value)}"
    )
    assert value >= 1, f"{source}: expected positive int, got {value!r}"
    return value


def _require_number(value: object, source: str) -> int | float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), (
        f"{source}: expected number, got {_type_name(value)}"
    )
    return value


def _require_bool(value: object, source: str) -> bool:
    assert isinstance(value, bool), f"{source}: expected bool, got {_type_name(value)}"
    return value


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


def _is_permanent(value: object, source: str) -> bool:
    if isinstance(value, Permanent):
        _require_str(value.name, f"{source}.name")
        if value.id is not None:
            _require_str(value.id, f"{source}.id")
        if value.tapped is not None:
            _require_bool(value.tapped, f"{source}.tapped")
        if value.summoning_sick is not None:
            _require_bool(value.summoning_sick, f"{source}.summoning_sick")
        if value.face_down is not None:
            _require_bool(value.face_down, f"{source}.face_down")
        if value.token is not None:
            _require_bool(value.token, f"{source}.token")
        if value.power is not None:
            _require_int_or_str(value.power, f"{source}.power")
        if value.toughness is not None:
            _require_int_or_str(value.toughness, f"{source}.toughness")
        if value.power_toughness is not None:
            _require_str(value.power_toughness, f"{source}.power_toughness")
        if value.pt is not None:
            _require_str(value.pt, f"{source}.pt")
        if value.loyalty is not None:
            _require_int(value.loyalty, f"{source}.loyalty")
        if value.original_card is not None:
            _require_str(value.original_card, f"{source}.original_card")
        if value.copy is not None:
            _require_bool(value.copy, f"{source}.copy")
        return True
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


def _is_stack_target(value: object, source: str) -> bool:
    if isinstance(value, StackTarget):
        if value.name is not None:
            _require_str(value.name, f"{source}.name")
        if value.id is not None:
            _require_str(value.id, f"{source}.id")
        return True
    obj = _require_object(value, source)
    if "name" in obj:
        _require_str(obj["name"], f"{source}.name")
    if "id" in obj:
        _require_str(obj["id"], f"{source}.id")
    return True


def _is_stack_item(value: object, source: str) -> bool:
    if isinstance(value, StackItem):
        _require_str(value.name, f"{source}.name")
        if value.id is not None:
            _require_str(value.id, f"{source}.id")
        if value.source_card is not None:
            _require_str(value.source_card, f"{source}.source_card")
        if value.ability_text is not None:
            _require_str(value.ability_text, f"{source}.ability_text")
        if value.targets is not None:
            for index, target in enumerate(value.targets):
                if isinstance(target, str):
                    _require_str(target, f"{source}.targets[{index}]")
                else:
                    assert _is_stack_target(target, f"{source}.targets[{index}]")
        return True
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


def _is_combat_creature(value: object, source: str) -> bool:
    if isinstance(value, CombatCreature):
        _require_str(value.name, f"{source}.name")
        if value.id is not None:
            _require_str(value.id, f"{source}.id")
        if value.power is not None:
            _require_int_or_str(value.power, f"{source}.power")
        if value.toughness is not None:
            _require_int_or_str(value.toughness, f"{source}.toughness")
        if value.power_toughness is not None:
            _require_str(value.power_toughness, f"{source}.power_toughness")
        if value.pt is not None:
            _require_str(value.pt, f"{source}.pt")
        return True
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


def _coerce_choice(value: object, source: str) -> Choice:
    if isinstance(value, Choice):
        obj = value.to_mapping()
    else:
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
    if isinstance(value, Choice):
        return value
    return Choice.from_mapping(obj)


def _coerce_multi_amount_item(value: object, source: str) -> MultiAmountItem:
    if isinstance(value, MultiAmountItem):
        obj = value.to_mapping()
    else:
        obj = _require_object(value, source)
    _require_str(_require_key(obj, "description", source), f"{source}.description")
    if "min" in obj:
        _require_int(obj["min"], f"{source}.min")
    if "max" in obj:
        _require_int(obj["max"], f"{source}.max")
    if isinstance(value, MultiAmountItem):
        return value
    return MultiAmountItem.from_mapping(obj)


def _validate_str_or_typed_list(
    value: object,
    source: str,
    typed_validator: Callable[[object, str], bool],
) -> None:
    """Validate a list where items can be strings or typed records."""
    for index, item in enumerate(_require_list(value, source)):
        if isinstance(item, str):
            _require_str(item, f"{source}[{index}]")
        else:
            assert typed_validator(item, f"{source}[{index}]")


def _coerce_str_or_typed_list(
    value: object,
    source: str,
    typed_loader: Callable[[object, str], T],
) -> list[str | T]:
    result: list[str | T] = []
    for index, item in enumerate(_require_list(value, source)):
        if isinstance(item, str):
            result.append(item)
        else:
            result.append(typed_loader(item, f"{source}[{index}]"))
    return result


def _validate_card_list(value: object, source: str) -> None:
    """Validate a list of cards that can be strings or Permanent dicts."""
    _validate_str_or_typed_list(value, source, _is_permanent)


def _coerce_card_list(value: object, source: str) -> list[str | Permanent]:
    return _coerce_str_or_typed_list(value, source, _coerce_permanent)


def _coerce_extra_fields(
    obj: JsonObject,
    source: str,
    known_fields: set[str],
) -> JsonObject:
    # Preserve schema-allowed additional properties separately so the typed
    # fields stay explicit while round-tripping remains lossless. Schema-valid
    # payloads may themselves contain an "_extras" key, so preserve it as just
    # another unknown JSON field instead of reserving the name.
    extras: JsonObject = {}
    for key, value in obj.items():
        if key not in known_fields:
            extras[key] = value
    return extras


def _coerce_permanent(value: object, source: str) -> Permanent:
    assert _is_permanent(value, source)
    if isinstance(value, Permanent):
        return value
    obj = _require_object(value, source)
    return Permanent(
        name=cast(str, obj["name"]),
        id=cast(str | None, obj.get("id")),
        tapped=cast(bool | None, obj.get("tapped")),
        summoning_sick=cast(bool | None, obj.get("summoning_sick")),
        face_down=cast(bool | None, obj.get("face_down")),
        token=cast(bool | None, obj.get("token")),
        power=cast(int | str | None, obj.get("power")),
        toughness=cast(int | str | None, obj.get("toughness")),
        power_toughness=cast(str | None, obj.get("power_toughness")),
        pt=cast(str | None, obj.get("pt")),
        loyalty=cast(int | None, obj.get("loyalty")),
        counters=obj.get("counters"),
        original_card=cast(str | None, obj.get("original_card")),
        copy=cast(bool | None, obj.get("copy")),
        _extras=_coerce_extra_fields(obj, source, _PERMANENT_FIELDS),
    )


def _coerce_stack_target(value: object, source: str) -> StackTarget:
    assert _is_stack_target(value, source)
    if isinstance(value, StackTarget):
        return value
    obj = _require_object(value, source)
    return StackTarget(
        name=cast(str | None, obj.get("name")),
        id=cast(str | None, obj.get("id")),
        _extras=_coerce_extra_fields(obj, source, _STACK_TARGET_FIELDS),
    )


def _coerce_stack_item(value: object, source: str) -> StackItem:
    assert _is_stack_item(value, source)
    if isinstance(value, StackItem):
        return value
    obj = _require_object(value, source)
    targets = obj.get("targets")
    return StackItem(
        name=cast(str, obj["name"]),
        id=cast(str | None, obj.get("id")),
        source_card=cast(str | None, obj.get("source_card")),
        ability_text=cast(str | None, obj.get("ability_text")),
        targets=(
            _coerce_str_or_typed_list(
                targets, f"{source}.targets", _coerce_stack_target
            )
            if targets is not None
            else None
        ),
        _extras=_coerce_extra_fields(obj, source, _STACK_ITEM_FIELDS),
    )


def _coerce_combat_creature(value: object, source: str) -> CombatCreature:
    assert _is_combat_creature(value, source)
    if isinstance(value, CombatCreature):
        return value
    obj = _require_object(value, source)
    return CombatCreature(
        name=cast(str, obj["name"]),
        id=cast(str | None, obj.get("id")),
        power=cast(int | str | None, obj.get("power")),
        toughness=cast(int | str | None, obj.get("toughness")),
        power_toughness=cast(str | None, obj.get("power_toughness")),
        pt=cast(str | None, obj.get("pt")),
        _extras=_coerce_extra_fields(obj, source, _COMBAT_CREATURE_FIELDS),
    )


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


def _is_snapshot_player(value: object, source: str) -> bool:
    if isinstance(value, SnapshotPlayer):
        _require_non_empty_str(value.name, f"{source}.name")
        _require_int(value.life, f"{source}.life")
        _require_non_negative_int(value.library_size, f"{source}.library_size")
        _validate_card_list(value.battlefield, f"{source}.battlefield")
        _validate_card_list(value.graveyard, f"{source}.graveyard")
        _validate_card_list(value.hand, f"{source}.hand")
        if value.hand_count is not None:
            _require_non_negative_int(value.hand_count, f"{source}.hand_count")
        if value.exile is not None:
            _validate_card_list(value.exile, f"{source}.exile")
        if value.commanders is not None:
            _validate_card_list(value.commanders, f"{source}.commanders")
        if value.command_zone is not None:
            _validate_card_list(value.command_zone, f"{source}.command_zone")
        if value.is_active is not None:
            _require_bool(value.is_active, f"{source}.is_active")
        if value.has_left is not None:
            _require_bool(value.has_left, f"{source}.has_left")
        if value.mana_pool is not None:
            _require_object(value.mana_pool, f"{source}.mana_pool")
        return True
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


def _is_combat_group(value: object, source: str) -> bool:
    if isinstance(value, CombatGroup):
        if value.attackers is not None:
            for idx, creature in enumerate(value.attackers):
                assert _is_combat_creature(creature, f"{source}.attackers[{idx}]")
        if value.blockers is not None:
            for idx, creature in enumerate(value.blockers):
                assert _is_combat_creature(creature, f"{source}.blockers[{idx}]")
        if value.blocked is not None:
            _require_bool(value.blocked, f"{source}.blocked")
        if value.defending is not None:
            _require_str(value.defending, f"{source}.defending")
        return True
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


def _is_snapshot(value: object, source: str) -> bool:
    if isinstance(value, Snapshot):
        _require_int(value.seq, f"{source}.seq")
        _require_int(value.turn, f"{source}.turn")
        _require_optional_str(value.phase, f"{source}.phase")
        _require_optional_str(value.step, f"{source}.step")
        _require_optional_str(value.active_player, f"{source}.active_player")
        _require_optional_str(value.priority_player, f"{source}.priority_player")
        for idx, sp in enumerate(value.players):
            assert _is_snapshot_player(sp, f"{source}.players[{idx}]")
        _validate_str_or_typed_list(value.stack, f"{source}.stack", _is_stack_item)
        if value.ts is not None:
            _require_str(value.ts, f"{source}.ts")
        if value.combat is not None:
            for idx, cg in enumerate(value.combat):
                assert _is_combat_group(cg, f"{source}.combat[{idx}]")
        return True
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


def _parse_action(value: object, source: str) -> Action:
    if isinstance(value, Action):
        return value
    obj = _require_object(value, source)
    seq = _require_int(_require_key(obj, "seq", source), f"{source}.seq")
    message: str | None = None
    if "message" in obj:
        message = _require_str(obj["message"], f"{source}.message")
    type_: str | None = None
    if "type" in obj:
        type_ = _require_str(obj["type"], f"{source}.type")
        assert type_ in _ACTION_TYPES, (
            f"{source}.type: unexpected action type {type_!r}"
        )
    turn: int | None = None
    if "turn" in obj:
        turn = _require_int(obj["turn"], f"{source}.turn")
    phase: str | None = None
    if "phase" in obj:
        phase = _require_optional_str(obj["phase"], f"{source}.phase")
    step: str | None = None
    if "step" in obj:
        step = _require_optional_str(obj["step"], f"{source}.step")
    active_player: str | None = None
    if "active_player" in obj:
        active_player = _require_optional_str(
            obj["active_player"], f"{source}.active_player"
        )
    ts: str | None = None
    if "ts" in obj:
        ts = _require_str(obj["ts"], f"{source}.ts")
    from_: str | None = None
    if "from" in obj:
        from_ = _require_str(obj["from"], f"{source}.from")
    return Action(
        seq=seq,
        message=message,
        type=type_,
        turn=turn,
        phase=phase,
        step=step,
        active_player=active_player,
        ts=ts,
        from_=from_,
    )


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


def _parse_game_over(value: object, source: str) -> GameOver:
    if isinstance(value, GameOver):
        return value
    obj = _require_object(value, source)
    seq = _require_int(_require_key(obj, "seq", source), f"{source}.seq")
    message = _require_str(_require_key(obj, "message", source), f"{source}.message")
    return GameOver(seq=seq, message=message)


def _parse_annotation(value: object, source: str) -> Annotation:
    if isinstance(value, Annotation):
        return value
    obj = _require_object(value, source)
    decision_index = _require_non_negative_int(
        _require_key(obj, "decisionIndex", source), f"{source}.decisionIndex"
    )
    snapshot_index: int | None = None
    if "snapshotIndex" in obj:
        snapshot_index = _require_non_negative_int(
            obj["snapshotIndex"], f"{source}.snapshotIndex"
        )
    player = _require_str(_require_key(obj, "player", source), f"{source}.player")
    type_ = _require_str(_require_key(obj, "type", source), f"{source}.type")
    assert type_ == "blunder", f"{source}.type: expected 'blunder', got {type_!r}"
    severity = _require_str(_require_key(obj, "severity", source), f"{source}.severity")
    assert severity in _ANNOTATION_SEVERITIES, (
        f"{source}.severity: unexpected annotation severity {severity!r}"
    )
    description = _require_str(
        _require_key(obj, "description", source), f"{source}.description"
    )
    action_taken = _require_str(
        _require_key(obj, "actionTaken", source), f"{source}.actionTaken"
    )
    better_line = _require_str(
        _require_key(obj, "betterLine", source), f"{source}.betterLine"
    )
    llm_reasoning: str | None = None
    if "llmReasoning" in obj:
        llm_reasoning = _require_str(obj["llmReasoning"], f"{source}.llmReasoning")
    return Annotation(
        decisionIndex=decision_index,
        player=player,
        type="blunder",
        severity=severity,
        description=description,
        actionTaken=action_taken,
        betterLine=better_line,
        snapshotIndex=snapshot_index,
        llmReasoning=llm_reasoning,
    )


def _coerce_pilot_context(value: object, source: str) -> PilotContext:
    if isinstance(value, PilotContext):
        obj = dict(value.to_mapping())
    else:
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
        obj["alreadyAttacking"] = _coerce_str_or_typed_list(
            obj["alreadyAttacking"],
            f"{source}.alreadyAttacking",
            _coerce_combat_creature,
        )
    if "incomingAttackers" in obj:
        obj["incomingAttackers"] = _coerce_str_or_typed_list(
            obj["incomingAttackers"],
            f"{source}.incomingAttackers",
            _coerce_combat_creature,
        )
    return PilotContext.from_mapping(obj)


def _is_decision(value: object, source: str) -> TypeIs[Decision]:
    if isinstance(value, Decision):
        obj = value.to_dict()
    else:
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
    choices = _require_list(_require_key(obj, "choices", source), f"{source}.choices")
    for index, choice in enumerate(choices):
        choices[index] = _coerce_choice(choice, f"{source}.choices[{index}]")
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
        obj["pilotContext"] = _coerce_pilot_context(
            obj["pilotContext"], f"{source}.pilotContext"
        )
    if "chosenArgs" in obj:
        _require_object(obj["chosenArgs"], f"{source}.chosenArgs")
    if "actionResult" in obj:
        _require_object(obj["actionResult"], f"{source}.actionResult")
    if "castRolledBack" in obj:
        _require_bool(obj["castRolledBack"], f"{source}.castRolledBack")
    if "items" in obj:
        items = _require_list(obj["items"], f"{source}.items")
        for index, item in enumerate(items):
            items[index] = _coerce_multi_amount_item(item, f"{source}.items[{index}]")
    if "totalMin" in obj:
        _require_non_negative_int(obj["totalMin"], f"{source}.totalMin")
    if "totalMax" in obj:
        _require_non_negative_int(obj["totalMax"], f"{source}.totalMax")
    if "actionSeq" in obj:
        _require_non_negative_int(obj["actionSeq"], f"{source}.actionSeq")
    return True


def _parse_game_error(value: object, source: str) -> GameError:
    if isinstance(value, GameError):
        return value
    obj = _require_object(value, source)
    ts = _require_str(_require_key(obj, "ts", source), f"{source}.ts")
    player = _require_str(_require_key(obj, "player", source), f"{source}.player")
    source_ = _require_str(_require_key(obj, "source", source), f"{source}.source")
    message = _require_str(_require_key(obj, "message", source), f"{source}.message")
    decision_index: int | None = None
    if "decisionIndex" in obj:
        decision_index = _require_non_negative_int(
            obj["decisionIndex"], f"{source}.decisionIndex"
        )
    return GameError(
        ts=ts,
        player=player,
        source=source_,
        message=message,
        decisionIndex=decision_index,
    )


def _parse_card_metadata(value: object, source: str) -> CardMetadata:
    if isinstance(value, CardMetadata):
        return value
    obj = _require_object(value, source)
    kwargs: dict[str, str] = {}
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
            kwargs[key] = _require_str(obj[key], f"{source}.{key}")
    return CardMetadata(**kwargs)


def _coerce_snapshot_player(value: object, source: str) -> SnapshotPlayer:
    assert _is_snapshot_player(value, source)
    if isinstance(value, SnapshotPlayer):
        return value
    obj = _require_object(value, source)
    return SnapshotPlayer(
        name=cast(str, obj["name"]),
        life=cast(int, obj["life"]),
        library_size=cast(int, obj["library_size"]),
        battlefield=_coerce_card_list(obj["battlefield"], f"{source}.battlefield"),
        graveyard=_coerce_card_list(obj["graveyard"], f"{source}.graveyard"),
        hand=_coerce_card_list(obj["hand"], f"{source}.hand"),
        hand_count=cast(int | None, obj.get("hand_count")),
        exile=_coerce_card_list(obj["exile"], f"{source}.exile")
        if "exile" in obj
        else None,
        counters=obj.get("counters"),
        commanders=_coerce_card_list(obj["commanders"], f"{source}.commanders")
        if "commanders" in obj
        else None,
        command_zone=_coerce_card_list(obj["command_zone"], f"{source}.command_zone")
        if "command_zone" in obj
        else None,
        is_active=cast(bool | None, obj.get("is_active")),
        has_left=cast(bool | None, obj.get("has_left")),
        mana_pool=cast(JsonObject | None, obj.get("mana_pool")),
    )


def _coerce_combat_group(value: object, source: str) -> CombatGroup:
    assert _is_combat_group(value, source)
    if isinstance(value, CombatGroup):
        return value
    obj = _require_object(value, source)
    return CombatGroup(
        attackers=[
            _coerce_combat_creature(item, f"{source}.attackers[{index}]")
            for index, item in enumerate(
                _require_list(obj["attackers"], f"{source}.attackers")
            )
        ]
        if "attackers" in obj
        else None,
        blockers=[
            _coerce_combat_creature(item, f"{source}.blockers[{index}]")
            for index, item in enumerate(
                _require_list(obj["blockers"], f"{source}.blockers")
            )
        ]
        if "blockers" in obj
        else None,
        blocked=cast(bool | None, obj.get("blocked")),
        defending=cast(str | None, obj.get("defending")),
        _extras=_coerce_extra_fields(obj, source, _COMBAT_GROUP_FIELDS),
    )


def _coerce_snapshot(value: object, source: str) -> Snapshot:
    assert _is_snapshot(value, source)
    if isinstance(value, Snapshot):
        return value
    obj = _require_object(value, source)
    return Snapshot(
        seq=cast(int, obj["seq"]),
        turn=cast(int, obj["turn"]),
        phase=cast(str | None, obj["phase"]),
        step=cast(str | None, obj["step"]),
        active_player=cast(str | None, obj["active_player"]),
        priority_player=cast(str | None, obj["priority_player"]),
        players=[
            _coerce_snapshot_player(player, f"{source}.players[{index}]")
            for index, player in enumerate(
                _require_list(obj["players"], f"{source}.players")
            )
        ],
        stack=_coerce_str_or_typed_list(
            obj["stack"], f"{source}.stack", _coerce_stack_item
        ),
        ts=cast(str | None, obj.get("ts")),
        combat=[
            _coerce_combat_group(group, f"{source}.combat[{index}]")
            for index, group in enumerate(
                _require_list(obj["combat"], f"{source}.combat")
            )
        ]
        if "combat" in obj
        else None,
    )


def _coerce_decision(value: object, source: str) -> Decision:
    assert _is_decision(value, source)
    if isinstance(value, Decision):
        if value.pilotContext is None:
            return value
        return dataclasses.replace(
            value,
            pilotContext=_coerce_pilot_context(
                value.pilotContext, f"{source}.pilotContext"
            ),
        )
    obj = _require_object(value, source)
    decision = dict(obj)
    if "pilotContext" in obj:
        decision["pilotContext"] = _coerce_pilot_context(
            obj["pilotContext"], f"{source}.pilotContext"
        )
    return cast(Decision, decision)


def _coerce_common_game_export(obj: JsonObject, source: str) -> JsonObject:
    coerced = dict(obj)
    coerced["snapshots"] = [
        _coerce_snapshot(snapshot, f"{source}.snapshots[{index}]")
        for index, snapshot in enumerate(
            _require_list(obj["snapshots"], f"{source}.snapshots")
        )
    ]
    if "decisions" in obj:
        coerced["decisions"] = [
            _coerce_decision(decision, f"{source}.decisions[{index}]")
            for index, decision in enumerate(
                _require_list(obj["decisions"], f"{source}.decisions")
            )
        ]
    return coerced


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
        actions[index] = _parse_action(action, f"{source}.actions[{index}]")
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
    if game_over is not None:
        obj["gameOver"] = _parse_game_over(game_over, f"{source}.gameOver")
    _require_non_negative_int(_require_key(obj, "season", source), f"{source}.season")
    tournament = _require_key(obj, "tournament", source)
    _require_optional_str(tournament, f"{source}.tournament")
    if "cardData" in obj:
        card_data = _require_object(obj["cardData"], f"{source}.cardData")
        for card_name in card_data:
            _require_str(card_name, f"{source}.cardData key")
            card_data[card_name] = _parse_card_metadata(
                card_data[card_name], f"{source}.cardData[{card_name}]"
            )
    if "decisions" in obj:
        decisions = _require_list(obj["decisions"], f"{source}.decisions")
        for index, decision in enumerate(decisions):
            assert _is_decision(decision, f"{source}.decisions[{index}]")
            if not isinstance(decision, Decision):
                decisions[index] = Decision.from_dict(
                    _require_object(decision, f"{source}.decisions[{index}]")
                )
    if "errors" in obj:
        errors = _require_list(obj["errors"], f"{source}.errors")
        for index, error in enumerate(errors):
            errors[index] = _parse_game_error(error, f"{source}.errors[{index}]")
    if "annotations" in obj:
        annotations = _require_list(obj["annotations"], f"{source}.annotations")
        for index, annotation in enumerate(annotations):
            annotations[index] = _parse_annotation(
                annotation, f"{source}.annotations[{index}]"
            )
    if "blunderScriptVersion" in obj:
        _require_non_negative_int(
            obj["blunderScriptVersion"], f"{source}.blunderScriptVersion"
        )
    return obj


def is_built_game_export(value: object, source: str = "game export") -> bool:
    if isinstance(value, (BuiltGameExport, GameExport)):
        return True
    _validate_common_game_export(value, source)
    return True


def is_game_export(value: object, source: str = "game export") -> bool:
    if isinstance(value, GameExport):
        return True
    obj = _validate_common_game_export(value, source)
    annotations = _require_key(obj, "annotations", source)
    _require_list(annotations, f"{source}.annotations")
    blunder_version = _require_key(obj, "blunderScriptVersion", source)
    _require_non_negative_int(blunder_version, f"{source}.blunderScriptVersion")
    return True


def require_built_game_export(
    value: object, source: str = "game export"
) -> BuiltGameExport:
    if isinstance(value, (BuiltGameExport, GameExport)):
        return (
            value
            if isinstance(value, BuiltGameExport)
            else BuiltGameExport.from_dict(value.to_dict())
        )
    assert is_built_game_export(value, source)
    coerced = _coerce_common_game_export(_require_object(value, source), source)
    return BuiltGameExport.from_dict(coerced)


def require_game_export(value: object, source: str = "game export") -> GameExport:
    if isinstance(value, GameExport):
        return value
    assert is_game_export(value, source)
    coerced = _coerce_common_game_export(_require_object(value, source), source)
    return GameExport.from_dict(coerced)


def require_snapshot(value: object, source: str = "snapshot") -> Snapshot:
    """Validate and coerce a snapshot payload to the typed Snapshot dataclass."""
    return _coerce_snapshot(value, source)


def parse_game_export(raw: str, *, source: str = "game export") -> GameExport:
    """Validate a serialized game export and return the typed dataclass."""
    return require_game_export(loads_json5(raw), source=source)


def parse_built_game_export(
    raw: str, *, source: str = "built game export"
) -> BuiltGameExport:
    """Validate a serialized built export that may omit annotations."""
    return require_built_game_export(loads_json5(raw), source=source)


def json_default(obj: object) -> object:
    """Handle dataclass instances in json.dumps.

    If the instance was created via ``_llm_event_from_dict``, only fields
    that were present in the source dict are serialized (preserving the
    distinction between "field was explicitly null" and "field was absent").
    Otherwise, fields with None values are omitted.

    Usage: ``json.dumps(export, default=json_default)``
    """
    if isinstance(obj, (GameExport, BuiltGameExport)):
        return obj.to_dict()
    if isinstance(obj, Decision):
        return obj.to_dict()
    if isinstance(obj, (Choice, MultiAmountItem, PilotContext)):
        return obj.to_mapping()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        field_names = {f.name for f in dataclasses.fields(obj)}
        if "_extras" in field_names:
            result: dict[str, object] = {}
            extras: Mapping[str, object] = {}
            for f in dataclasses.fields(obj):
                value = getattr(obj, f.name)
                if f.name == "_extras":
                    assert isinstance(value, Mapping), (
                        f"dataclass _extras must be a mapping, got {value!r}"
                    )
                    extras = value
                    continue
                if value is not None:
                    result[f.name] = value
            for key, value in extras.items():
                assert key not in result, f"duplicate dataclass export key {key!r}"
                result[str(key)] = value
            return result
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
        result = {}
        for f in dataclasses.fields(obj):
            v = getattr(obj, f.name)
            # Include required fields even when None (preserves null in JSON),
            # omit optional fields (those with defaults) when None.
            if v is not None or (
                f.default is dataclasses.MISSING
                and f.default_factory is dataclasses.MISSING
            ):
                result[f.name] = v
        if "from_" in result:
            result["from"] = result.pop("from_")
        return result
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
    "DecisionSupportRecord",
    "export_record_field",
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
    "decision_support_get",
    "decision_support_has",
    "game_export_to_jsonable",
    "json_default",
    "is_built_game_export",
    "is_game_export",
    "is_pilot_player",
    "parse_built_game_export",
    "parse_game_export",
    "require_built_game_export",
    "require_game_export",
    "require_snapshot",
]

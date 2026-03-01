package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetActionChoicesTool {
    @Tool(
        name = "get_action_choices",
        description = "Get available choices for the current pending action. Call before choose_action. "
            + "With until: blocks like pass_priority until a decision is needed, "
            + "then returns choices in one call. "
            + "Without until: returns immediately (action_pending=false if nothing to do). "
            + "Includes context (phase/turn), full board state, stack (when non-empty), and land_drops_used (during your main phase). "
            + "response_type: select (cards to play, attackers, blockers), boolean (yes/no), "
            + "index (target/ability), amount, pile, or multi_amount. "
            + "During combat: combat_phase indicates declare_attackers or declare_blockers; "
            + "combat shows all combat groups (attackers, blockers, blocked status) during any combat step.",
        output = {
            @Tool.Field(name = "action_pending", type = "boolean", description = "Whether an action is pending (false if nothing to do)"),
            @Tool.Field(name = "action_type", type = "string", description = "XMage callback method name"),
            @Tool.Field(name = "message", type = "string", description = "Human-readable prompt from XMage"),
            @Tool.Field(name = "response_type", type = "string", description = "How to respond: \"select\", \"boolean\", \"index\", \"amount\", \"pile\", or \"multi_amount\""),
            @Tool.Field(name = "respond_with", type = "string", description = "Exact choose_action parameter(s) to use for this action type"),
            @Tool.Field(name = "context", type = "string", description = "Turn/phase context (e.g. \"T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN\")"),
            @Tool.Field(name = "board", type = "array[object]",
                description = "Full board state — same format as get_game_state players array: life, library_size, hand (yours only with rules), "
                    + "battlefield (name, tapped, P/T, rules, counters), graveyard (name, rules), exile (name, rules), mana_pool, counters, commanders. "
                    + "Omitted when board_unchanged=true (board_cursor matched)."),
            @Tool.Field(name = "board_cursor", type = "integer",
                description = "Cursor for the current board state. Pass back in the next call to skip the board if unchanged."),
            @Tool.Field(name = "board_unchanged", type = "boolean",
                description = "True when the provided board_cursor matches — board field is omitted."),
            @Tool.Field(name = "choices", type = "array[object]", description = "Structured choices with index, name, and type-specific fields (action/mana_cost/power/toughness for cards; choice_type for combat/mana; target_type/controller/tapped for targets)"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards during mulligan: name, mana_cost, is_land, power/toughness, rules"),
            @Tool.Field(name = "combat_phase", type = "string", description = "\"declare_attackers\" or \"declare_blockers\""),
            @Tool.Field(name = "combat", type = "array[object]", description = "Combat groups during any combat step: attackers (name/id/power/toughness), blockers, blocked boolean, defending player"),
            @Tool.Field(name = "stack", type = "array[object]", description = "Spells/abilities currently on the stack: name, owner, targets (only present when stack is non-empty)"),
            @Tool.Field(name = "untapped_lands", type = "integer", description = "Number of untapped lands"),
            @Tool.Field(name = "game_seq", type = "integer", description = "Game sequence number for determinism tracking"),
            @Tool.Field(name = "land_drops_used", type = "integer", description = "Number of lands played this turn (during your main phase)"),
            @Tool.Field(name = "already_attacking", type = "array[object]",
                description = "Creatures already declared as attackers (during declare_attackers when some are pre-declared)"),
            @Tool.Field(name = "incoming_attackers", type = "array[object]",
                description = "Attacking creatures and their current blockers (during declare_blockers)"),
            @Tool.Field(name = "required", type = "boolean", description = "Whether targeting is required (for GAME_TARGET)"),
            @Tool.Field(name = "can_cancel", type = "boolean", description = "Whether targeting can be cancelled (for GAME_TARGET)"),
            @Tool.Field(name = "note", type = "string", description = "Informational note (e.g. filtered choice list size)"),
            @Tool.Field(name = "pile1", type = "array[object]", description = "First pile contents (for GAME_CHOOSE_PILE)"),
            @Tool.Field(name = "pile2", type = "array[object]", description = "Second pile contents (for GAME_CHOOSE_PILE)"),
            @Tool.Field(name = "min", type = "integer", description = "Minimum allowed value (for GAME_GET_AMOUNT)"),
            @Tool.Field(name = "max", type = "integer", description = "Maximum allowed value (for GAME_GET_AMOUNT)"),
            @Tool.Field(name = "total_min", type = "integer", description = "Total minimum across all items (for GAME_GET_MULTI_AMOUNT)"),
            @Tool.Field(name = "total_max", type = "integer", description = "Total maximum across all items (for GAME_GET_MULTI_AMOUNT)"),
            @Tool.Field(name = "items", type = "array[object]", description = "Per-item details for multi_amount: description, min, max, default"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died"),
            @Tool.Field(name = "game_over", type = "boolean", description = "Whether the game ended"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "Chat messages received since last check"),
            @Tool.Field(name = "stop_reason", type = "string", description = "Why pass_priority returned (only when until is set)")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(
                description = "Skip ahead to a target, then return choices. "
                    + "Same values as pass_priority's until parameter. Omit to return immediately.",
                allowed_values = {
                    "upkeep", "draw", "precombat_main", "begin_combat",
                    "declare_attackers", "declare_blockers",
                    "end_combat", "postcombat_main",
                    "end_of_turn", "my_turn", "stack_resolved"
                }
            ) String until,
            @Param(
                description = "Board cursor from a previous pass_priority or get_action_choices result. "
                    + "When provided and the board hasn't changed, the board field is omitted to save tokens."
            ) Long board_cursor) {
        if (until != null) {
            return handler.waitAndGetChoices(until, board_cursor);
        } else {
            return handler.getActionChoices(board_cursor);
        }
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Select (play cards)", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "message", "Select card to play or pass priority",
                "response_type", "select",
                "context", "T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
                "board", List.of(
                    json("name", "Player1", "life", 20, "is_you", true, "is_active", true,
                        "library_size", 49, "hand_size", 5,
                        "hand", List.of(
                            json("name", "Lightning Bolt", "mana_cost", "{R}", "rules", List.of("Lightning Bolt deals 3 damage to any target."), "playable", true),
                            json("name", "Mountain", "is_land", true, "rules", List.of("{T}: Add {R}."), "playable", true)),
                        "battlefield", List.of(
                            json("name", "Mountain", "tapped", false, "rules", List.of("{T}: Add {R}."), "id", "p1"),
                            json("name", "Goblin Guide", "tapped", false, "power", 2, "toughness", 2, "rules", List.of("Haste", "Whenever Goblin Guide attacks, defending player reveals the top card of their library. If it's a land card, that player puts it into their hand."), "id", "p2"))),
                    json("name", "Player2", "life", 20, "is_you", false, "is_active", false,
                        "library_size", 52, "hand_size", 7,
                        "battlefield", List.of(
                            json("name", "Island", "tapped", false, "rules", List.of("{T}: Add {U}."), "id", "p3")))),
                "board_cursor", 5,
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Board unchanged (cursor matched)", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "message", "Play instants and activated abilities",
                "response_type", "select",
                "context", "T4 PRECOMBAT_MAIN (Opponent)",
                "board_unchanged", true,
                "board_cursor", 5,
                "stack", List.of(json("name", "Sheoldred's Edict", "owner", "Opponent")),
                "choices", List.of(
                    json("index", 0, "name", "Counterspell", "action", "cast", "mana_cost", "{U}{U}")),
                "untapped_lands", 3)),
            example("Boolean (mulligan)", json(
                "action_pending", true,
                "action_type", "GAME_ASK",
                "message", "Mulligan hand?",
                "response_type", "boolean",
                "context", "T0 PREGAME",
                "board_cursor", 1,
                "your_hand", List.of(
                    json("name", "Mountain", "is_land", true),
                    json("name", "Lightning Bolt", "mana_cost", "{R}", "rules", List.of("Lightning Bolt deals 3 damage to any target."))))));
    }

}

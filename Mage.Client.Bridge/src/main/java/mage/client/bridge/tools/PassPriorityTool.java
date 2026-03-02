package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class PassPriorityTool {
    @Tool(
        name = "pass_priority",
        description = "Pass priority. Blocks until you have a pending action "
            + "(playable cards, combat, or non-priority action like mulligan/targeting). "
            + "With until: skip to a target step/phase; always stops for combat and non-priority actions. "
            + "When action_pending=true, includes choices so you can call choose_action immediately.",
        output = {
            @Tool.Field(name = "action_pending", type = "boolean", description = "Whether a decision is needed"),
            @Tool.Field(name = "action_type", type = "string", description = "XMage callback method name"),
            @Tool.Field(name = "has_playable_cards", type = "boolean", description = "Whether you have playable cards"),
            @Tool.Field(name = "combat_phase", type = "string", description = "declare_attackers or declare_blockers"),
            @Tool.Field(name = "current_step", type = "string", description = "Current step (for reached_step/step_not_reached)"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "New chat messages"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died"),
            @Tool.Field(name = "stop_reason", type = "string",
                description = "Why returned: playable_cards, combat, non_priority_action, "
                    + "game_over, reached_step, step_not_reached"),
            @Tool.Field(name = "response_type", type = "string",
                description = "select, boolean, index, amount, pile, or multi_amount",
                conditional = "action_pending"),
            @Tool.Field(name = "message", type = "string", description = "Prompt from XMage",
                conditional = "action_pending"),
            @Tool.Field(name = "context", type = "string",
                description = "Turn/phase context, e.g. T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
                conditional = "action_pending"),
            @Tool.Field(name = "board", type = "array[object]",
                description = "Board state (players array). Omitted when board_unchanged=true.",
                conditional = "action_pending"),
            @Tool.Field(name = "board_cursor", type = "integer",
                description = "Pass back to skip unchanged board.",
                conditional = "action_pending"),
            @Tool.Field(name = "board_unchanged", type = "boolean",
                description = "Board omitted (cursor matched).",
                conditional = "action_pending"),
            @Tool.Field(name = "choices", type = "array[object]",
                description = "Available choices with index and name",
                conditional = "action_pending"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards",
                conditional = "action_pending"),
            @Tool.Field(name = "untapped_lands", type = "integer", description = "Untapped land count",
                conditional = "action_pending"),
            @Tool.Field(name = "game_seq", type = "integer", description = "Sequence number"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "warning", type = "string", description = "Warning message"),
            @Tool.Field(name = "game_over", type = "boolean", description = "Whether the game ended"),
            @Tool.Field(name = "respond_with", type = "string",
                description = "choose_action parameter(s) to use",
                conditional = "action_pending"),
            @Tool.Field(name = "stack", type = "array[object]",
                description = "Stack (when non-empty)",
                conditional = "action_pending"),
            @Tool.Field(name = "combat", type = "array[object]",
                description = "Combat groups",
                conditional = "action_pending"),
            @Tool.Field(name = "land_drops_used", type = "integer",
                description = "Lands played this turn",
                conditional = "action_pending"),
            @Tool.Field(name = "already_attacking", type = "array[object]",
                description = "Pre-declared attackers",
                conditional = "action_pending"),
            @Tool.Field(name = "incoming_attackers", type = "array[object]",
                description = "Attackers (during declare_blockers)",
                conditional = "action_pending"),
            @Tool.Field(name = "required", type = "boolean",
                description = "Targeting is required",
                conditional = "action_pending"),
            @Tool.Field(name = "can_cancel", type = "boolean",
                description = "Targeting can be cancelled",
                conditional = "action_pending"),
            @Tool.Field(name = "note", type = "string",
                description = "Informational note",
                conditional = "action_pending"),
            @Tool.Field(name = "pile1", type = "array[object]",
                description = "Pile 1 contents",
                conditional = "action_pending"),
            @Tool.Field(name = "pile2", type = "array[object]",
                description = "Pile 2 contents",
                conditional = "action_pending"),
            @Tool.Field(name = "min", type = "integer",
                description = "Min allowed value",
                conditional = "action_pending"),
            @Tool.Field(name = "max", type = "integer",
                description = "Max allowed value",
                conditional = "action_pending"),
            @Tool.Field(name = "total_min", type = "integer",
                description = "Total min (multi_amount)",
                conditional = "action_pending"),
            @Tool.Field(name = "total_max", type = "integer",
                description = "Total max (multi_amount)",
                conditional = "action_pending"),
            @Tool.Field(name = "items", type = "array[object]",
                description = "Per-item details for multi_amount",
                conditional = "action_pending")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(
                description = "Skip to a target step/phase. Omit to block until next action.",
                allowed_values = {
                    "upkeep", "draw", "precombat_main", "begin_combat",
                    "declare_attackers", "declare_blockers",
                    "end_combat", "postcombat_main",
                    "end_of_turn", "my_turn", "stack_resolved"
                }
            ) String until,
            @Param(
                description = "Board cursor from previous result. Omits board when unchanged."
            ) Long board_cursor) {
        return handler.passPriority(until, board_cursor);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Playable cards found", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "has_playable_cards", true,
                "stop_reason", "playable_cards",
                "response_type", "select",
                "context", "T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
                "board", List.of(json("name", "You", "life", 20, "is_you", true), json("name", "Opp", "life", 18, "is_you", false)),
                "board_cursor", 3,
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Board unchanged (cursor matched)", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "has_playable_cards", true,
                "stop_reason", "playable_cards",
                "response_type", "select",
                "context", "T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
                "board_unchanged", true,
                "board_cursor", 3,
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Non-priority action (mulligan)", json(
                "action_pending", true,
                "action_type", "GAME_ASK",
                "stop_reason", "non_priority_action",
                "response_type", "boolean",
                "message", "Mulligan hand?",
                "context", "T0 PREGAME",
                "board", List.of(json("name", "You", "life", 20, "is_you", true), json("name", "Opp", "life", 20, "is_you", false)),
                "board_cursor", 1,
                "your_hand", List.of(
                    json("name", "Mountain", "is_land", true),
                    json("name", "Lightning Bolt", "mana_cost", "{R}")))));
    }
}

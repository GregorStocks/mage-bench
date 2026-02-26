package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class PassPriorityTool {
    @Tool(
        name = "pass_priority",
        description = "Pass priority. Blocks until you have a pending action (playable cards, "
            + "combat, non-priority action like mulligan/targeting). "
            + "With until: skips ahead to a target step or phase. "
            + "Step values (current turn, client-side): upkeep, draw, precombat_main, "
            + "begin_combat, declare_attackers, declare_blockers, end_combat, postcombat_main. "
            + "Cross-turn values (server-side): end_of_turn, my_turn, stack_resolved. "
            + "Always stops for combat and non-priority actions. "
            + "Auto-handles mechanical callbacks (mana payment failures, "
            + "optional targets with no legal targets). "
            + "Returns stop_reason indicating why the call returned. "
            + "When action_pending=true, includes full action choices "
            + "(response_type, choices, context, etc.) so you can call choose_action immediately.",
        output = {
            @Tool.Field(name = "action_pending", type = "boolean", description = "Whether a decision-requiring action was found"),
            @Tool.Field(name = "action_type", type = "string", description = "XMage callback method name"),
            @Tool.Field(name = "has_playable_cards", type = "boolean", description = "Whether you have playable cards in hand"),
            @Tool.Field(name = "combat_phase", type = "string", description = "\"declare_attackers\" or \"declare_blockers\""),
            @Tool.Field(name = "current_step", type = "string", description = "Current game step (only for reached_step/step_not_reached)"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "Chat messages received since last check"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died during priority passing"),
            @Tool.Field(name = "stop_reason", type = "string",
                description = "Why the call returned: playable_cards, combat, non_priority_action, "
                    + "game_over, reached_step (target step reached), step_not_reached (turn ended without reaching step)"),
            @Tool.Field(name = "response_type", type = "string",
                description = "How to respond: \"select\", \"boolean\", \"index\", \"amount\", \"pile\", or \"multi_amount\"",
                conditional = "action_pending"),
            @Tool.Field(name = "message", type = "string", description = "Human-readable prompt from XMage",
                conditional = "action_pending"),
            @Tool.Field(name = "context", type = "string",
                description = "Turn/phase context (e.g. \"T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN\")",
                conditional = "action_pending"),
            @Tool.Field(name = "board", type = "array[object]",
                description = "Full board state — same format as get_game_state players array. "
                    + "Omitted when board_unchanged=true (board_cursor matched).",
                conditional = "action_pending"),
            @Tool.Field(name = "board_cursor", type = "integer",
                description = "Cursor for the current board state. Pass back in the next call to skip the board if unchanged.",
                conditional = "action_pending"),
            @Tool.Field(name = "board_unchanged", type = "boolean",
                description = "True when the provided board_cursor matches — board field is omitted.",
                conditional = "action_pending"),
            @Tool.Field(name = "choices", type = "array[object]",
                description = "Structured choices with index, name, and type-specific fields",
                conditional = "action_pending"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards with name, mana_cost",
                conditional = "action_pending"),
            @Tool.Field(name = "untapped_lands", type = "integer", description = "Number of untapped lands",
                conditional = "action_pending")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(
                description = "Skip ahead to a target. "
                    + "Step values yield within the current turn (client-side): "
                    + "upkeep, draw, precombat_main, begin_combat, declare_attackers, "
                    + "declare_blockers, end_combat, postcombat_main. "
                    + "Cross-turn values use server-side yield: "
                    + "end_of_turn (skip rest of turn), my_turn (skip to your next turn), "
                    + "stack_resolved (wait for stack to resolve). "
                    + "Omit to block until next actionable priority.",
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

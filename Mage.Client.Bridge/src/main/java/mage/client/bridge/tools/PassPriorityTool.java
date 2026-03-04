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
            + "When action_pending=true, includes choices so you can call choose_action immediately."
    )
    public static ActionResult execute(
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

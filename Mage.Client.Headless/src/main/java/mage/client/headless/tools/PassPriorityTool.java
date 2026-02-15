package mage.client.headless.tools;

import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

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
            @Tool.Field(name = "actions_passed", type = "integer", description = "Number of priority passes performed"),
            @Tool.Field(name = "has_playable_cards", type = "boolean", description = "Whether you have playable cards in hand"),
            @Tool.Field(name = "combat_phase", type = "string", description = "\"declare_attackers\" or \"declare_blockers\""),
            @Tool.Field(name = "current_step", type = "string", description = "Current game step (only for reached_step/step_not_reached)"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "Chat messages received since last check"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died during priority passing"),
            @Tool.Field(name = "stop_reason", type = "string",
                description = "Why the call returned: playable_cards, combat, non_priority_action, "
                    + "game_over, reached_step (target step reached), step_not_reached (turn ended without reaching step), "
                    + "pending_action_from_choose_action (a previous choose_action left a pending action — call get_action_choices/choose_action instead)"),
            @Tool.Field(name = "response_type", type = "string",
                description = "How to respond: \"select\", \"boolean\", \"index\", \"amount\", \"pile\", or \"multi_amount\"",
                conditional = "action_pending"),
            @Tool.Field(name = "message", type = "string", description = "Human-readable prompt from XMage",
                conditional = "action_pending"),
            @Tool.Field(name = "context", type = "string",
                description = "Turn/phase context (e.g. \"T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN\")",
                conditional = "action_pending"),
            @Tool.Field(name = "players", type = "string", description = "Life total summary (e.g. \"You(20), Opp(18)\")",
                conditional = "action_pending"),
            @Tool.Field(name = "choices", type = "array[object]",
                description = "Structured choices with index, name, and type-specific fields",
                conditional = "action_pending"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards with name, mana_cost",
                conditional = "action_pending"),
            @Tool.Field(name = "mana_pool", type = "object", description = "Current mana pool {R, G, U, W, B, C}",
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
            ) String until) {
        return handler.passPriority(until);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Playable cards found", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "actions_passed", 3,
                "has_playable_cards", true,
                "stop_reason", "playable_cards",
                "response_type", "select",
                "context", "T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
                "players", "You(20), Opp(18)",
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Combat phase", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "actions_passed", 5,
                "has_playable_cards", false,
                "combat_phase", "declare_attackers",
                "stop_reason", "combat",
                "response_type", "select",
                "context", "T4 COMBAT (Player1) YOUR_COMBAT",
                "players", "You(18), Opp(15)")),
            example("Non-priority action (mulligan)", json(
                "action_pending", true,
                "action_type", "GAME_ASK",
                "actions_passed", 0,
                "stop_reason", "non_priority_action",
                "response_type", "boolean",
                "message", "Mulligan hand?",
                "context", "T0 PREGAME",
                "players", "You(20), Opp(20)",
                "your_hand", List.of(
                    json("name", "Mountain", "is_land", true),
                    json("name", "Lightning Bolt", "mana_cost", "{R}")))));
    }
}

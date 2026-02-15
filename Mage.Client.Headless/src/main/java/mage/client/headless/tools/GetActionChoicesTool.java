package mage.client.headless.tools;

import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

public class GetActionChoicesTool {
    @Tool(
        name = "get_action_choices",
        description = "Get available choices for the current pending action. Call before choose_action. "
            + "With until: blocks like pass_priority until a decision is needed, "
            + "then returns choices in one call. "
            + "Without until: returns immediately (action_pending=false if nothing to do). "
            + "Includes context (phase/turn), players (life totals), and land_drops_used (during your main phase). "
            + "response_type: select (cards to play, attackers, blockers), boolean (yes/no), "
            + "index (target/ability), amount, pile, or multi_amount. "
            + "During combat: combat_phase indicates declare_attackers or declare_blockers.",
        output = {
            @Tool.Field(name = "action_pending", type = "boolean", description = "Whether an action is pending (false if nothing to do)"),
            @Tool.Field(name = "action_type", type = "string", description = "XMage callback method name"),
            @Tool.Field(name = "message", type = "string", description = "Human-readable prompt from XMage"),
            @Tool.Field(name = "response_type", type = "string", description = "How to respond: \"select\", \"boolean\", \"index\", \"amount\", \"pile\", or \"multi_amount\""),
            @Tool.Field(name = "respond_with", type = "string", description = "Exact choose_action parameter(s) to use for this action type"),
            @Tool.Field(name = "context", type = "string", description = "Turn/phase context (e.g. \"T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN\")"),
            @Tool.Field(name = "players", type = "string", description = "Life total summary (e.g. \"You(20), Opp(18)\")"),
            @Tool.Field(name = "choices", type = "array[object]", description = "Structured choices with index, name, and type-specific fields (action/mana_cost/power/toughness for cards; choice_type for combat/mana; target_type/controller/tapped for targets)"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards with name, mana_cost"),
            @Tool.Field(name = "combat_phase", type = "string", description = "\"declare_attackers\" or \"declare_blockers\""),
            @Tool.Field(name = "mana_pool", type = "object", description = "Current mana pool {R, G, U, W, B, C}"),
            @Tool.Field(name = "untapped_lands", type = "integer", description = "Number of untapped lands"),
            @Tool.Field(name = "min_amount", type = "integer", description = "Minimum allowed value"),
            @Tool.Field(name = "max_amount", type = "integer", description = "Maximum allowed value"),
            @Tool.Field(name = "actions_passed", type = "integer", description = "Number of priority passes performed before the decision"),
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
            ) String until) {
        if (until != null) {
            return handler.waitAndGetChoices(until);
        } else {
            return handler.getActionChoices();
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
                "players", "You(20), Opp(18)",
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Boolean (mulligan)", json(
                "action_pending", true,
                "action_type", "GAME_ASK",
                "message", "Mulligan hand?",
                "response_type", "boolean",
                "context", "T0 PREGAME",
                "players", "You(20), Opp(20)",
                "your_hand", List.of(
                    json("name", "Mountain", "is_land", true),
                    json("name", "Lightning Bolt", "mana_cost", "{R}")),
                "hand_size", 7,
                "land_count", 3)));
    }
}

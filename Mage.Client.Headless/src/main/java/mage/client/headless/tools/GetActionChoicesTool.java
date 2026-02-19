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
            @Tool.Field(name = "board", type = "array[object]", description = "Full board state — same format as get_game_state players array: life, library_size, hand (yours only with rules), battlefield (name, tapped, P/T, rules, counters), graveyard (name, rules), exile (name, rules), mana_pool, counters, commanders"),
            @Tool.Field(name = "choices", type = "array[object]", description = "Structured choices with index, name, and type-specific fields (action/mana_cost/power/toughness for cards; choice_type for combat/mana; target_type/controller/tapped for targets)"),
            @Tool.Field(name = "your_hand", type = "array[object]", description = "Hand cards during mulligan: name, mana_cost, is_land, power/toughness, rules"),
            @Tool.Field(name = "combat_phase", type = "string", description = "\"declare_attackers\" or \"declare_blockers\""),
            @Tool.Field(name = "combat", type = "array[object]", description = "Combat groups during any combat step: attackers (name/id/power/toughness), blockers, blocked boolean, defending player"),
            @Tool.Field(name = "stack", type = "array[object]", description = "Spells/abilities currently on the stack: name, owner, targets (only present when stack is non-empty)"),
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
                "choices", List.of(
                    json("index", 0, "name", "Lightning Bolt", "action", "cast", "mana_cost", "{R}"),
                    json("index", 1, "name", "Mountain", "action", "land")),
                "untapped_lands", 2)),
            example("Select (respond to opponent's spell)", json(
                "action_pending", true,
                "action_type", "GAME_SELECT",
                "message", "Play instants and activated abilities",
                "response_type", "select",
                "context", "T4 PRECOMBAT_MAIN (Opponent)",
                "board", List.of(
                    json("name", "You", "life", 18, "is_you", true),
                    json("name", "Opponent", "life", 20, "is_you", false)),
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
                "your_hand", List.of(
                    json("name", "Mountain", "is_land", true),
                    json("name", "Lightning Bolt", "mana_cost", "{R}", "rules", List.of("Lightning Bolt deals 3 damage to any target."))))));
    }

}

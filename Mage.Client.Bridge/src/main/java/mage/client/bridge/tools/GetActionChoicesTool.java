package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetActionChoicesTool {
    @Tool(
        name = "get_action_choices",
        description = "Get choices for the current pending action. "
            + "With until: blocks like pass_priority until a decision is needed. "
            + "Without until: returns immediately (action_pending=false if nothing to do)."
    )
    public static ActionResult execute(
            BridgeCallbackHandler handler,
            @Param(
                description = "Skip to a target step/phase, then return choices. Omit to return immediately.",
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

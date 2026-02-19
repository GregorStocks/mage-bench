package mage.client.headless.tools;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

public class GetGameStateTool {
    @Tool(
        name = "get_game_state",
        description = "Get full game state: turn, phase, players, stack, combat. Each player has life, mana_pool, "
            + "hand (yours only), battlefield (name, tapped, P/T, counters, rules, token/copy/face_down flags, "
            + "modified + original_card when changed from printed card), graveyard (name, rules), exile (name, rules), commanders.",
        output = {
            @Tool.Field(name = "available", type = "boolean", description = "Whether game state is available"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "cursor", type = "integer", description = "Cursor for the latest known game state"),
            @Tool.Field(name = "unchanged", type = "boolean", description = "True when the provided cursor already matches the latest state"),
            @Tool.Field(name = "turn", type = "integer", description = "Current turn number"),
            @Tool.Field(name = "phase", type = "string", description = "Current phase (e.g. PRECOMBAT_MAIN, COMBAT)"),
            @Tool.Field(name = "step", type = "string", description = "Current step within the phase"),
            @Tool.Field(name = "active_player", type = "string", description = "Name of the player whose turn it is"),
            @Tool.Field(name = "priority_player", type = "string", description = "Name of the player who currently has priority"),
            @Tool.Field(name = "players", type = "array[object]", description = "Player objects: name, life, library_size, hand_size, is_active, is_you, hand (yours only), battlefield, graveyard, exile, mana_pool, counters, commanders"),
            @Tool.Field(name = "stack", type = "array[object]", description = "Stack objects: name, rules, targets (resolved target names)"),
            @Tool.Field(name = "combat", type = "array[object]", description = "Combat groups: attackers, blockers, blocked, defending")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "State cursor from previous get_game_state call. If unchanged, returns a compact payload.") Long cursor) {
        return handler.getGameState(cursor);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Mid-game state", json(
                "available", true,
                "turn", 4,
                "phase", "PRECOMBAT_MAIN",
                "step", "PRECOMBAT_MAIN",
                "active_player", "Player1",
                "priority_player", "Player1",
                "players", List.of(
                    json("name", "Player1",
                        "life", 18,
                        "library_size", 49,
                        "hand_size", 5,
                        "is_active", true,
                        "is_you", true,
                        "hand", List.of(
                            json("name", "Lightning Bolt", "mana_cost", "{R}", "playable", true),
                            json("name", "Mountain", "is_land", true, "playable", true)),
                        "battlefield", List.of(
                            json("name", "Mountain", "tapped", false),
                            json("name", "Goblin Guide", "tapped", false, "power", 2, "toughness", 2)),
                        "mana_pool", json("R", 0)),
                    json("name", "Player2",
                        "life", 20,
                        "library_size", 52,
                        "hand_size", 7,
                        "is_active", false,
                        "is_you", false,
                        "battlefield", List.of(
                            json("name", "Island", "tapped", false)))),
                "stack", Collections.emptyList())));
    }
}

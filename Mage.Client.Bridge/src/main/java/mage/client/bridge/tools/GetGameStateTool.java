package mage.client.bridge.tools;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetGameStateTool {
    @Tool(
        name = "get_game_state",
        description = "Get full game state: turn, phase, players (life, hand, battlefield, graveyard, exile), stack, combat.",
        output = {
            @Tool.Field(name = "available", type = "boolean", description = "Whether state is available"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "cursor", type = "integer", description = "State cursor"),
            @Tool.Field(name = "unchanged", type = "boolean", description = "Cursor matched (no changes)"),
            @Tool.Field(name = "turn", type = "integer", description = "Turn number"),
            @Tool.Field(name = "phase", type = "string", description = "Current phase"),
            @Tool.Field(name = "step", type = "string", description = "Current step"),
            @Tool.Field(name = "active_player", type = "string", description = "Whose turn it is"),
            @Tool.Field(name = "priority_player", type = "string", description = "Who has priority"),
            @Tool.Field(name = "players", type = "array[object]", description = "Player objects with life, hand, battlefield, graveyard, exile, mana_pool"),
            @Tool.Field(name = "stack", type = "array[object]", description = "Stack: name, rules, targets"),
            @Tool.Field(name = "combat", type = "array[object]", description = "Combat groups"),
            @Tool.Field(name = "game_seq", type = "integer", description = "Sequence number")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Cursor from previous call. Returns compact payload if unchanged.") Long cursor) {
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

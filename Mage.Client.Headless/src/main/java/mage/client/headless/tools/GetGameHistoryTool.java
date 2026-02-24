package mage.client.headless.tools;

import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

public class GetGameHistoryTool {
    @Tool(
        name = "get_game_history",
        description = "Structured per-turn game history with noise filtered out. Returns actions grouped by "
            + "player turns — spells cast, lands played, attacks declared, etc. Automatic/redundant messages "
            + "(draw step, zone moves, skip attack) are excluded. Use this instead of get_game_log when you "
            + "want a clean summary of what happened.",
        output = {
            @Tool.Field(name = "turns", type = "array[object]",
                description = "Turn objects: player, turn_number, life_totals (map of player name to life), actions (list of action strings)"),
            @Tool.Field(name = "truncated", type = "boolean",
                description = "Whether older history was trimmed from the buffer")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Only include turns starting from this player turn number. Defaults to all turns.") Integer since_turn,
            @Param(description = "Player name for since_turn filter. Defaults to you (the calling player). Only used with since_turn.") String since_player) {
        return handler.getGameHistory(since_player, since_turn);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Two turns of history", json(
                "turns", List.of(
                    json("player", "Alice", "turn_number", 1,
                        "life_totals", json("Alice", 20, "Bob", 20),
                        "actions", List.of("Alice plays Mountain", "Alice casts Goblin Guide")),
                    json("player", "Bob", "turn_number", 1,
                        "life_totals", json("Alice", 20, "Bob", 18),
                        "actions", List.of("Bob plays Island", "Bob casts Ponder"))),
                "truncated", false)),
            example("Since turn filter", json(
                "turns", List.of(
                    json("player", "Alice", "turn_number", 3,
                        "life_totals", json("Alice", 14, "Bob", 12),
                        "actions", List.of("Alice casts Lightning Bolt targeting Bob"))),
                "truncated", false)));
    }
}

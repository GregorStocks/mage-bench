package mage.client.headless.tools;

import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

public class GetGameHistoryTool {
    @Tool(
        name = "get_game_history",
        description = "Get structured game history showing player actions (casts, attacks, blocks, life changes, etc.) "
            + "grouped by turn and phase. Unlike get_game_log which returns raw chat text, this returns "
            + "structured action descriptions. Use cursor for incremental updates between decisions.",
        output = {
            @Tool.Field(name = "history", type = "string", description = "Formatted game history text grouped by turn/phase"),
            @Tool.Field(name = "cursor", type = "integer", description = "Cursor to pass to the next get_game_history call for incremental updates"),
            @Tool.Field(name = "event_count", type = "integer", description = "Number of events included in this response")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Only include events from this turn number onward") Integer since_turn,
            @Param(description = "Cursor from a previous get_game_history call. Returns only new events since this cursor. Mutually exclusive with since_turn.") Integer cursor) {

        if (since_turn != null && cursor != null) {
            throw new RuntimeException("since_turn and cursor are mutually exclusive — provide one or neither");
        }

        return handler.getGameHistory(since_turn, cursor);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Full history", json(
                "history", "Turn 1 (Alice):\n  Precombat Main:\n    - Alice played Mountain\n    - Alice cast Goblin Guide\n\nTurn 1 (Bob):\n  Precombat Main:\n    - Bob played Island\n",
                "cursor", 8,
                "event_count", 4)),
            example("Incremental update", json(
                "history", "Turn 3 (Alice):\n  Precombat Main:\n    - Alice cast Lightning Bolt targeting Bob's Grizzly Bears\n  Declare Attackers:\n    - Alice attacked with Goblin Guide\n",
                "cursor", 24,
                "event_count", 2)),
            example("No new events", json(
                "history", "No game events recorded yet.",
                "cursor", 24,
                "event_count", 0)));
    }
}

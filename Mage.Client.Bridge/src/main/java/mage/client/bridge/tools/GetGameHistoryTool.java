package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetGameHistoryTool {

    public static class Result {
        @ResultField(description = "History text grouped by turn/phase")
        public String history;

        @ResultField(description = "Cursor for next call")
        public Integer cursor;

        @ResultField(description = "Events in this response")
        public Integer event_count;
    }

    @Tool(
        name = "get_game_history",
        description = "Get structured game history (casts, attacks, life changes) grouped by turn/phase. "
            + "Use cursor for incremental updates."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Events from this turn onward") Integer since_turn,
            @Param(description = "Cursor for incremental updates. Mutually exclusive with since_turn.") Integer cursor) {

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

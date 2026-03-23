package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetGameLogTool {

    public static class Result {
        @ResultField(description = "Game log text")
        public String log;

        @ResultField(description = "Full log length in chars")
        public Integer total_length;

        @ResultField(description = "Older content was omitted")
        public Boolean truncated;

        @ResultField(description = "Game log cursor for next call")
        public Integer cursor;

        @ResultField(description = "Cursor was too old and was reset",
            conditional = "when cursor parameter was used")
        public Boolean cursor_reset;

        @ResultField(description = "Turn number the log starts from",
            conditional = "when since_turn parameter was used")
        public Integer since_turn;

        @ResultField(description = "Player whose turn the log starts from",
            conditional = "when since_turn parameter was used")
        public String since_player;
    }

    @Tool(
        name = "get_game_log",
        description = "Get game log text. Use since_turn for turn-based recap, "
            + "cursor for incremental updates, or max_chars for recent text."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Max chars to return (0 or omit for all)") Integer max_chars,
            @Param(description = "Cursor from previous call for incremental updates. Mutually exclusive with since_turn.") Integer cursor,
            @Param(description = "Get log from this turn number onward. Mutually exclusive with cursor.") Integer since_turn,
            @Param(description = "Player for since_turn filter (defaults to you)") String since_player) {

        if (since_turn != null && cursor != null) {
            throw new RuntimeException("since_turn and cursor are mutually exclusive — provide one or neither");
        }

        if (since_turn != null) {
            return handler.getGameLogSinceTurn(since_player, since_turn);
        }

        int mc = max_chars != null ? max_chars : 0;
        return handler.getGameLogChunk(mc, cursor);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Truncated log", json(
                "log", "Alice turn 3:\nAlice cast Lightning Bolt targeting Bob",
                "total_length", 523,
                "truncated", true,
                "cursor", 42)),
            example("Cursor delta", json(
                "log", "Bob cast Swords to Plowshares targeting Goblin Guide",
                "total_length", 530,
                "truncated", false,
                "cursor", 45)),
            example("Since turn recap", json(
                "log", "Bob turn 2:\nBob cast Sol Ring\nAlice turn 3:\nAlice played Forest",
                "total_length", 540,
                "truncated", false,
                "cursor", 50,
                "since_turn", 2,
                "since_player", "Bob")));
    }
}

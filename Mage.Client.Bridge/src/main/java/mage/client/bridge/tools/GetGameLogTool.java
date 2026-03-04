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

        @ResultField(description = "Cursor for next call")
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
                "log", "Alice turn 3 (20 - 15)\nAlice casts Lightning Bolt...",
                "total_length", 5234,
                "truncated", true,
                "cursor", 5234)),
            example("Cursor delta", json(
                "log", "Bob casts Swords to Plowshares targeting Goblin Guide.",
                "total_length", 5301,
                "truncated", false,
                "cursor", 5301)),
            example("Since turn recap", json(
                "log", "Bob turn 2 (20 - 18)\nBob casts Sol Ring\nAlice turn 3 (20 - 18)\nAlice plays Forest",
                "total_length", 5400,
                "truncated", false,
                "cursor", 5400,
                "since_turn", 2,
                "since_player", "Bob")));
    }
}

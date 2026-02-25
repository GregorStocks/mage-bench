package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetGameLogTool {
    @Tool(
        name = "get_game_log",
        description = "Get game log text. Three modes: (1) since_turn=N to get events since a player's Nth turn "
            + "(best for recapping opponents' turns in multiplayer), (2) cursor from a previous call "
            + "for incremental updates, (3) max_chars for the most recent N characters.",
        output = {
            @Tool.Field(name = "log", type = "string", description = "Game log text (may be truncated if max_chars was set)"),
            @Tool.Field(name = "total_length", type = "integer", description = "Total length of the full game log in characters"),
            @Tool.Field(name = "truncated", type = "boolean", description = "Whether older content was omitted"),
            @Tool.Field(name = "cursor", type = "integer", description = "Cursor to pass to the next get_game_log call"),
            @Tool.Field(name = "cursor_reset", type = "boolean",
                description = "Whether requested cursor was too old and had to be reset to oldest retained log offset",
                conditional = "when cursor parameter was used"),
            @Tool.Field(name = "since_turn", type = "integer",
                description = "The per-player turn number the log starts from",
                conditional = "when since_turn parameter was used"),
            @Tool.Field(name = "since_player", type = "string",
                description = "The player whose turn the log starts from",
                conditional = "when since_turn parameter was used")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Max characters to return (0 or omit for all)") Integer max_chars,
            @Param(description = "Cursor offset from previous get_game_log call. Returns new log text since this offset. Mutually exclusive with since_turn.") Integer cursor,
            @Param(description = "Return log entries starting from this player turn number. Turn markers use per-player numbering (e.g. 'Alice turn 3'). Mutually exclusive with cursor.") Integer since_turn,
            @Param(description = "Player name for since_turn filter. Defaults to you (the calling player). Only used with since_turn.") String since_player) {

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

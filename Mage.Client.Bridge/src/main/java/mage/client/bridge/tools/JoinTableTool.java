package mage.client.bridge.tools;

import mage.client.bridge.BridgeCallbackHandler;

/**
 * MCP tool for joining the next available game table in keepAlive (multi-game) mode.
 * Resets all game state, loads the specified deck, joins the table, and waits
 * for the game to start before returning.
 */
public class JoinTableTool {

    public static class Result {
        @ResultField(description = "Whether the table was joined and game started")
        public Boolean joined;
    }

    @Tool(
        name = "join_table",
        description = "Reset state and join the next available game table with a new deck. "
            + "Used in multi-game sessions to transition between games without restarting the JVM. "
            + "Blocks until the game starts."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Absolute path to the deck file (.dck)", required = true) String deck_path,
            @Param(description = "UUID of the specific table to join (from spectator). "
                + "When provided, joins only this table instead of polling for any available table.") String table_id) {
        try {
            java.util.UUID targetTableId = table_id != null ? java.util.UUID.fromString(table_id) : null;
            handler.joinNextTable(deck_path, targetTableId);
            var result = new Result();
            result.joined = true;
            return result;
        } catch (Exception e) {
            throw new RuntimeException("Failed to join table: " + e.getMessage(), e);
        }
    }
}

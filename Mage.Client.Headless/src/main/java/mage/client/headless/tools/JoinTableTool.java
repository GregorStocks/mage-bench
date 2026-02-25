package mage.client.headless.tools;

import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

/**
 * MCP tool for joining the next available game table in keepAlive (multi-game) mode.
 * Resets all game state, loads the specified deck, joins the table, and waits
 * for the game to start before returning.
 */
public class JoinTableTool {
    @Tool(
        name = "join_table",
        description = "Reset state and join the next available game table with a new deck. "
            + "Used in multi-game sessions to transition between games without restarting the JVM. "
            + "Blocks until the game starts.",
        output = {
            @Tool.Field(name = "joined", type = "boolean", description = "Whether the table was joined and game started")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Absolute path to the deck file (.dck)", required = true) String deck_path) {
        try {
            handler.joinNextTable(deck_path);
            return Map.of("joined", true);
        } catch (Exception e) {
            throw new RuntimeException("Failed to join table: " + e.getMessage(), e);
        }
    }
}

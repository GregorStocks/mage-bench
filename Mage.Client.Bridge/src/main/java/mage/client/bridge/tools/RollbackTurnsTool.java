package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

/** Undo: ask the server to roll the game back to the start of a turn. */
public class RollbackTurnsTool {

    public static class Result {
        @ResultField(description = "Whether the rollback request was sent (the server applies it once every other player grants it)")
        public Boolean success;
        @ResultField(description = "Why the request was not sent, when success is false")
        public String error;
    }

    @Tool(
        name = "rollback_turns",
        description = "Roll the game back to the start of a turn: 0 = the current turn, 1 = the previous turn (undo). "
            + "Only works while you hold priority; other players must allow it (this bridge grants such requests automatically)."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "How many turns back: 0 = start of the current turn, 1 = previous turn, …") Integer turns) {
        int n = turns == null ? 0 : Math.max(0, turns);
        String error = handler.requestRollback(n);
        var result = new Result();
        result.success = error == null;
        result.error = error;
        return result;
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Success", json("success", true)),
            example("No game", json("success", false, "error", "no active game")));
    }
}

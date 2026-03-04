package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ConcedeTool {

    public static class Result {
        @ResultField(description = "Whether the concession was sent")
        public Boolean success;
    }

    @Tool(
        name = "concede",
        description = "Concede the current game."
    )
    public static Result execute(BridgeCallbackHandler handler) {
        boolean success = handler.concede();
        var result = new Result();
        result.success = success;
        return result;
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Success", json(
                "success", true)));
    }
}

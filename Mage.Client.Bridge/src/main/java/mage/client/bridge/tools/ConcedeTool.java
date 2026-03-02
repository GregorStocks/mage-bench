package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ConcedeTool {
    @Tool(
        name = "concede",
        description = "Concede the current game.",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether the concession was sent")
        }
    )
    public static Map<String, Object> execute(BridgeCallbackHandler handler) {
        boolean success = handler.concede();
        return Map.of("success", success);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Success", json(
                "success", true)));
    }
}

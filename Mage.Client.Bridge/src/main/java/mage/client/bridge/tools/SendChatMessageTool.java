package mage.client.bridge.tools;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class SendChatMessageTool {
    @Tool(
        name = "send_chat_message",
        description = "Send a chat message to the game",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether the message was sent"),
            @Tool.Field(name = "error", type = "string", description = "Error message when success is false")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Message to send", required = true) String message) {
        if (message == null) {
            throw new RuntimeException("Missing required 'message' parameter");
        }
        String error = handler.sendChatMessage(message);
        Map<String, Object> result = new HashMap<>();
        if (error != null) {
            result.put("success", false);
            result.put("error", error);
        } else {
            result.put("success", true);
        }
        return result;
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Success", json(
                "success", true)),
            example("Error", json(
                "success", false,
                "error", "no active game")));
    }
}

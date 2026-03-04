package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class SendChatMessageTool {

    public static class Result {
        @ResultField(description = "Whether the message was sent")
        public Boolean success;

        @ResultField(description = "Error message when success is false")
        public String error;
    }

    @Tool(
        name = "send_chat_message",
        description = "Send a chat message to the game"
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Message to send", required = true) String message) {
        if (message == null) {
            throw new RuntimeException("Missing required 'message' parameter");
        }
        String error = handler.sendChatMessage(message);
        var result = new Result();
        if (error != null) {
            result.success = false;
            result.error = error;
        } else {
            result.success = true;
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

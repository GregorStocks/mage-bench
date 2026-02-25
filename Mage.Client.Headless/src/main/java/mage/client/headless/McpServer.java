package mage.client.headless;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.apache.log4j.Logger;

import mage.client.headless.tools.*;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * MCP (Model Context Protocol) server using stdio transport.
 * Implements JSON-RPC 2.0 over newline-delimited stdin/stdout.
 *
 */
public class McpServer {

    private static final Logger logger = Logger.getLogger(McpServer.class);
    private static final String PROTOCOL_VERSION = "2024-11-05";
    private static final String SERVER_NAME = "xmage-bridge";
    private static final String SERVER_VERSION = "1.0.0";

    private static final Class<?>[] TOOL_CLASSES = {
        GetGameLogTool.class,
        GetGameHistoryTool.class,
        SendChatMessageTool.class,
        PassPriorityTool.class,
        GetGameStateTool.class,
        GetOracleTextTool.class,
        GetActionChoicesTool.class,
        ChooseActionTool.class,
        ConcedeTool.class,
    };

    /** Additional tools only available in keepAlive (multi-game) mode. */
    private static final Class<?>[] KEEP_ALIVE_TOOL_CLASSES = {
        JoinTableTool.class,
    };

    private final BridgeMageClient client;
    private final Gson gson;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final PrintWriter stdout;
    private boolean initialized = false;
    private final McpToolRegistry registry;

    public McpServer(BridgeMageClient client) {
        this(client, false);
    }

    public McpServer(BridgeMageClient client, boolean keepAlive) {
        this.client = client;
        this.gson = new GsonBuilder().create();
        this.stdout = new PrintWriter(System.out, true);
        if (keepAlive) {
            // Merge base tools + keepAlive tools
            Class<?>[] allTools = new Class<?>[TOOL_CLASSES.length + KEEP_ALIVE_TOOL_CLASSES.length];
            System.arraycopy(TOOL_CLASSES, 0, allTools, 0, TOOL_CLASSES.length);
            System.arraycopy(KEEP_ALIVE_TOOL_CLASSES, 0, allTools, TOOL_CLASSES.length, KEEP_ALIVE_TOOL_CLASSES.length);
            this.registry = new McpToolRegistry(allTools);
        } else {
            this.registry = new McpToolRegistry(TOOL_CLASSES);
        }
    }

    /**
     * Start the MCP server. Blocks until shutdown.
     */
    public void start() {
        running.set(true);
        logger.info("MCP server starting on stdio");

        try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in))) {
            String line;
            while (running.get() && (line = reader.readLine()) != null) {
                if (line.trim().isEmpty()) {
                    continue;
                }
                try {
                    handleMessage(line);
                } catch (Exception e) {
                    logger.error("Error handling MCP message", e);
                }
            }
        } catch (IOException e) {
            logger.error("Error reading from stdin", e);
        }

        logger.info("MCP server stopped");
    }

    public void stop() {
        running.set(false);
    }

    private void handleMessage(String json) {
        JsonObject message = JsonParser.parseString(json).getAsJsonObject();

        String method = message.has("method") ? message.get("method").getAsString() : null;
        JsonElement id = message.has("id") ? message.get("id") : null;
        JsonObject params = message.has("params") ? message.getAsJsonObject("params") : null;

        // Handle notifications (no id)
        if (id == null) {
            handleNotification(method, params);
            return;
        }

        // Handle requests (have id)
        try {
            Object result = handleRequest(method, params);
            sendResponse(id, result, null);
        } catch (Exception e) {
            client.getCallbackHandler().logError("MCP request failed (" + method + "): " + e.getMessage());
            sendError(id, -32603, e.getMessage());
        }
    }

    private void handleNotification(String method, JsonObject params) {
        if ("notifications/initialized".equals(method)) {
            logger.info("MCP client sent initialized notification");
            // Client is ready, nothing to do
        } else {
            logger.debug("Unhandled notification: " + method);
        }
    }

    private Object handleRequest(String method, JsonObject params) {
        return switch (method) {
            case "initialize" -> handleInitialize(params);
            case "tools/list" -> handleToolsList(params);
            case "tools/call" -> handleToolsCall(params);
            default -> throw new RuntimeException("Unknown method: " + method);
        };
    }

    private Map<String, Object> handleInitialize(JsonObject params) {
        initialized = true;
        logger.info("MCP initialized with protocol version " + PROTOCOL_VERSION);
        return Map.of(
                "protocolVersion", PROTOCOL_VERSION,
                "capabilities", Map.of("tools", Map.of()),
                "serverInfo", Map.of("name", SERVER_NAME, "version", SERVER_VERSION)
        );
    }

    private Map<String, Object> handleToolsList(JsonObject params) {
        return Map.of("tools", registry.getDefinitions());
    }

    private Map<String, Object> handleToolsCall(JsonObject params) {
        String toolName = params.has("name") && !params.get("name").isJsonNull()
                ? params.get("name").getAsString() : null;
        if (toolName == null) {
            throw new RuntimeException("Missing required 'name' parameter");
        }
        JsonObject arguments = params.has("arguments") ? params.getAsJsonObject("arguments") : new JsonObject();

        Map<String, Object> toolResult = registry.call(toolName, arguments, client.getCallbackHandler());

        // Format as MCP tool result
        return Map.of(
                "content", List.of(Map.of("type", "text", "text", gson.toJson(toolResult))),
                "structuredContent", toolResult,
                "isError", false
        );
    }

    private void sendResponse(JsonElement id, Object result, Object error) {
        Map<String, Object> response = new HashMap<>();
        response.put("jsonrpc", "2.0");
        response.put("id", id);

        if (error != null) {
            response.put("error", error);
        } else {
            response.put("result", result);
        }

        String json = gson.toJson(response);
        synchronized (stdout) {
            stdout.println(json);
            stdout.flush();
        }
    }

    private void sendError(JsonElement id, int code, String message) {
        sendResponse(id, null, Map.of("code", code, "message", message));
    }

    /**
     * Print MCP tool definitions as JSON to stdout.
     * Used by `make mcp-tools` to generate mcp-tools.json.
     */
    public static void main(String[] args) {
        McpToolRegistry reg = new McpToolRegistry(TOOL_CLASSES);
        Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
        System.out.println(gson.toJson(reg.getDefinitions()));
    }
}

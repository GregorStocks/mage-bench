package mage.client.bridge;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.apache.log4j.Logger;

import mage.client.bridge.tools.*;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * MCP (Model Context Protocol) server using HTTP transport.
 * Implements JSON-RPC 2.0 over HTTP POST on /mcp.
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
    private final McpToolRegistry registry;
    private HttpServer httpServer;

    public McpServer(BridgeMageClient client) {
        this(client, false);
    }

    public McpServer(BridgeMageClient client, boolean keepAlive) {
        this.client = client;
        this.gson = new GsonBuilder().create();
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
     * Start the MCP HTTP server on the given port. Blocks until shutdown.
     */
    public void start(int port) {
        running.set(true);

        try {
            httpServer = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
            // Use a virtual-thread executor so blocking tool calls (e.g.
            // pass_priority spinning for callbacks) don't prevent concurrent
            // requests (e.g. concede) from being handled.  The default
            // single-thread executor caused golden test timeouts: a stuck
            // pass_priority blocked the only handler thread, making the
            // subsequent concede unreachable until the HTTP socket timed out.
            httpServer.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
            httpServer.createContext("/mcp", this::handleHttpRequest);
            httpServer.start();
            logger.info("MCP HTTP server started on port " + port);

            // Block until stopped
            while (running.get()) {
                try {
                    Thread.sleep(1000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        } catch (IOException e) {
            logger.error("Failed to start MCP HTTP server on port " + port, e);
        } finally {
            if (httpServer != null) {
                httpServer.stop(0);
            }
            logger.info("MCP HTTP server stopped");
        }
    }

    public void stop() {
        running.set(false);
        if (httpServer != null) {
            // Give in-flight HTTP handlers time to complete before closing
            // connections.  passPriority polls every 200ms for game_over, so
            // 5s is more than enough for a clean response.
            httpServer.stop(5);
        }
    }

    private void handleHttpRequest(HttpExchange exchange) throws IOException {
        // Only accept POST
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }

        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        try {
            JsonObject message = JsonParser.parseString(body).getAsJsonObject();
            String method = message.has("method") ? message.get("method").getAsString() : null;
            JsonElement id = message.has("id") ? message.get("id") : null;
            JsonObject params = message.has("params") ? message.getAsJsonObject("params") : null;

            // Handle notifications (no id) — return 202 Accepted
            if (id == null) {
                handleNotification(method);
                exchange.sendResponseHeaders(202, -1);
                exchange.close();
                return;
            }

            // Handle requests (have id) — return JSON-RPC response
            Map<String, Object> response = new HashMap<>();
            response.put("jsonrpc", "2.0");
            response.put("id", id);

            try {
                Object result = handleRequest(method, params);
                response.put("result", result);
            } catch (Exception e) {
                logger.warn("[" + client.getUsername() + "] MCP request failed (" + method + "): " + e.getMessage());
                response.put("error", Map.of("code", -32603, "message", e.getMessage()));
            }

            byte[] responseBytes = gson.toJson(response).getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, responseBytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(responseBytes);
            }
        } catch (Exception e) {
            logger.error("Error handling MCP HTTP request", e);
            byte[] errorBytes = gson.toJson(Map.of(
                    "jsonrpc", "2.0",
                    "error", Map.of("code", -32700, "message", "Parse error: " + e.getMessage())
            )).getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, errorBytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(errorBytes);
            }
        }
    }

    private void handleNotification(String method) {
        if ("notifications/initialized".equals(method)) {
            logger.info("MCP client sent initialized notification");
            // Client is ready, nothing to do
        } else {
            logger.debug("Unhandled notification: " + method);
        }
    }

    private Object handleRequest(String method, JsonObject params) {
        return switch (method) {
            case "initialize" -> handleInitialize();
            case "tools/list" -> handleToolsList();
            case "tools/call" -> handleToolsCall(params);
            default -> throw new RuntimeException("Unknown method: " + method);
        };
    }

    private Map<String, Object> handleInitialize() {
        logger.info("MCP initialized with protocol version " + PROTOCOL_VERSION);
        return Map.of(
                "protocolVersion", PROTOCOL_VERSION,
                "capabilities", Map.of("tools", Map.of()),
                "serverInfo", Map.of("name", SERVER_NAME, "version", SERVER_VERSION)
        );
    }

    private Map<String, Object> handleToolsList() {
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

    /**
     * Print MCP tool definitions as JSON to stdout.
     * Used by `make regen-mcp-tools` to generate mcp-tools.json.
     */
    public static void main(String[] args) {
        McpToolRegistry reg = new McpToolRegistry(TOOL_CLASSES);
        Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
        System.out.println(gson.toJson(reg.getDefinitions()));
    }
}

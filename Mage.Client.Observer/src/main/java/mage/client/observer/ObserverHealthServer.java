package mage.client.observer;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.apache.log4j.Logger;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Lightweight HTTP health server for observer readiness detection.
 *
 * Provides two long-polling endpoints so the Python test harness can detect
 * readiness without polling log files:
 *
 * <ul>
 *   <li>{@code GET /health?timeout=N} — blocks until lobby is initialized</li>
 *   <li>{@code POST /wait-for-ready} — blocks until a game table is created for
 *       the requested gameDir</li>
 * </ul>
 */
public class ObserverHealthServer {

    private static final Logger LOGGER = Logger.getLogger(ObserverHealthServer.class);

    private final HttpServer httpServer;
    private final Gson gson = new Gson();
    private final CountDownLatch lobbyReady = new CountDownLatch(1);
    private final ConcurrentHashMap<String, CompletableFuture<String>> gameReadyFutures = new ConcurrentHashMap<>();

    public ObserverHealthServer(int port) throws IOException {
        httpServer = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        httpServer.createContext("/health", this::handleHealth);
        httpServer.createContext("/wait-for-ready", this::handleWaitForReady);
    }

    public void start() {
        httpServer.start();
        LOGGER.info("Observer health server started on port " + httpServer.getAddress().getPort());
    }

    public void stop() {
        httpServer.stop(2);
    }

    /** Signal that the lobby is initialized and ready for commands. */
    public void signalLobbyReady() {
        lobbyReady.countDown();
    }

    /** Signal that a game table has been created for the given gameDir. */
    public void signalGameReady(String gameDir, String tableId) {
        CompletableFuture<String> future = gameReadyFutures.computeIfAbsent(gameDir, k -> new CompletableFuture<>());
        future.complete(tableId);
    }

    private void handleHealth(HttpExchange exchange) throws IOException {
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }

        int timeout = parseQueryInt(exchange, "timeout", 120);

        try {
            if (lobbyReady.await(timeout, TimeUnit.SECONDS)) {
                sendJson(exchange, 200, "{\"status\":\"ready\"}");
            } else {
                sendJson(exchange, 408, "{\"status\":\"timeout\"}");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            sendJson(exchange, 500, "{\"status\":\"interrupted\"}");
        }
    }

    private void handleWaitForReady(HttpExchange exchange) throws IOException {
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }

        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        JsonObject req = gson.fromJson(body, JsonObject.class);
        String gameDir = req.get("gameDir").getAsString();
        int timeout = req.has("timeout") ? req.get("timeout").getAsInt() : 240;

        CompletableFuture<String> future = gameReadyFutures.computeIfAbsent(gameDir, k -> new CompletableFuture<>());

        try {
            String tableId = future.get(timeout, TimeUnit.SECONDS);
            sendJson(exchange, 200, "{\"ready\":true,\"tableId\":\"" + tableId + "\"}");
        } catch (TimeoutException e) {
            sendJson(exchange, 408, "{\"ready\":false,\"error\":\"timeout\"}");
        } catch (Exception e) {
            sendJson(exchange, 500, "{\"ready\":false,\"error\":\"" + e.getMessage() + "\"}");
        } finally {
            gameReadyFutures.remove(gameDir);
        }
    }

    private void sendJson(HttpExchange exchange, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static int parseQueryInt(HttpExchange exchange, String key, int defaultValue) {
        String query = exchange.getRequestURI().getQuery();
        if (query == null) return defaultValue;
        for (String param : query.split("&")) {
            String[] kv = param.split("=", 2);
            if (kv.length == 2 && kv[0].equals(key)) {
                try {
                    return Integer.parseInt(kv[1]);
                } catch (NumberFormatException e) {
                    return defaultValue;
                }
            }
        }
        return defaultValue;
    }
}

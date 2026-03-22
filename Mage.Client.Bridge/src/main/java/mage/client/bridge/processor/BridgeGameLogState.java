package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;
import mage.game.BridgeLogEntry;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class BridgeGameLogState {
    private final List<String> unseenChat = new ArrayList<>();
    private final List<BridgeChatLogEntry> chatLog = new ArrayList<>();
    private String lastChatMessage = null;
    private long lastChatTimeMs = 0;
    private int bridgeEventCursor = 0;
    private final List<BridgeLogEntry> cachedBridgeEvents = new ArrayList<>();

    public void reset() {
        unseenChat.clear();
        chatLog.clear();
        lastChatMessage = null;
        lastChatTimeMs = 0;
        bridgeEventCursor = 0;
        cachedBridgeEvents.clear();
    }

    public void recordTalkMessage(String username, String user, String msg) {
        if (user == null || msg == null || msg.isEmpty()) {
            return;
        }
        chatLog.add(new BridgeChatLogEntry(bridgeEventCursor, msg, "[Chat] " + user + ": " + msg));
        if (!user.equals(username)) {
            unseenChat.add(user + ": " + msg);
        }
    }

    public void addSystemMessage(String message) {
        unseenChat.add(message);
    }

    public void attachUnseenChat(Map<String, Object> result, boolean playerDead, boolean gameOver) {
        if (playerDead) {
            result.put("player_dead", true);
        }
        if (gameOver) {
            result.put("game_over", true);
        }
        if (!unseenChat.isEmpty()) {
            result.put("recent_chat", new ArrayList<>(unseenChat));
            unseenChat.clear();
        }
    }

    public void attachUnseenChat(ActionResult result, boolean playerDead, boolean gameOver) {
        if (playerDead) {
            result.player_dead = true;
        }
        if (gameOver) {
            result.game_over = true;
        }
        if (!unseenChat.isEmpty()) {
            result.recent_chat = new ArrayList<>(unseenChat);
            unseenChat.clear();
        }
    }

    public boolean shouldSuppressOutgoingChat(String message, long nowMs, long dedupWindowMs) {
        if (message.equals(lastChatMessage) && (nowMs - lastChatTimeMs) < dedupWindowMs) {
            return true;
        }
        lastChatMessage = message;
        lastChatTimeMs = nowMs;
        return false;
    }

    public List<BridgeChatLogEntry> snapshotChatLog() {
        return new ArrayList<>(chatLog);
    }

    public List<BridgeLogEntry> snapshotBridgeEvents() {
        return new ArrayList<>(cachedBridgeEvents);
    }

    public int nextBridgeEventCursor() {
        return cachedBridgeEvents.isEmpty() ? 0 : cachedBridgeEvents.get(cachedBridgeEvents.size() - 1).index() + 1;
    }

    public List<BridgeLogEntry> cachedBridgeEventsSince(int sinceCursor) {
        return cachedBridgeEvents.stream()
            .filter(e -> e.index() >= sinceCursor)
            .toList();
    }

    public List<BridgeLogEntry> pullBridgeEvents(
            Session session,
            UUID gameId,
            UUID playerId,
            Logger logger,
            String username) {
        try {
            List<BridgeLogEntry> events = session.getBridgeEvents(gameId, playerId, bridgeEventCursor);
            mergeFetchedBridgeEvents(events);
            return events != null ? events : List.of();
        } catch (Exception e) {
            logger.error("[" + username + "] Failed to pull bridge events", e);
            return List.of();
        }
    }

    public void mergeFetchedBridgeEvents(List<BridgeLogEntry> events) {
        if (events == null || events.isEmpty()) {
            return;
        }
        bridgeEventCursor = events.get(events.size() - 1).index() + 1;
        int cacheHighWater = cachedBridgeEvents.isEmpty() ? -1
            : cachedBridgeEvents.get(cachedBridgeEvents.size() - 1).index();
        for (BridgeLogEntry entry : events) {
            if (entry.index() > cacheHighWater) {
                cachedBridgeEvents.add(entry);
            }
        }
    }
}

package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;
import mage.game.BridgeLogEntry;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class BridgeGameLogState {
    private record PendingOutgoingChat(String message, long sentAtMs) {
    }

    private final List<String> unseenChat = new ArrayList<>();
    private final List<BridgePublishedLogEntry> publishedLog = new ArrayList<>();
    private final Set<Integer> publishedBridgeEventIndexes = new HashSet<>();
    private final ArrayDeque<PendingOutgoingChat> pendingOutgoingChatEchoes = new ArrayDeque<>();
    private String lastChatMessage = null;
    private long lastChatTimeMs = 0;
    private int nextPublishedCursor = 0;
    private int lastPublishedBridgeEventIndex = -1;

    public void reset() {
        unseenChat.clear();
        publishedLog.clear();
        publishedBridgeEventIndexes.clear();
        pendingOutgoingChatEchoes.clear();
        lastChatMessage = null;
        lastChatTimeMs = 0;
        lastPublishedBridgeEventIndex = -1;
    }

    public void recordOutgoingChatMessage(
            String username,
            String message,
            long nowMs,
            long echoDedupWindowMs) {
        if (message == null || message.isEmpty()) {
            return;
        }
        prunePendingOutgoingChatEchoes(nowMs, echoDedupWindowMs);
        pendingOutgoingChatEchoes.addLast(new PendingOutgoingChat(message, nowMs));
        appendRenderedEntry("[Chat] " + username + ": " + message);
    }

    public void recordTalkMessage(String username, String user, String msg, long nowMs, long echoDedupWindowMs) {
        if (user == null || msg == null || msg.isEmpty()) {
            return;
        }
        prunePendingOutgoingChatEchoes(nowMs, echoDedupWindowMs);
        if (user.equals(username) && consumePendingOutgoingEcho(msg)) {
            return;
        }
        appendRenderedEntry("[Chat] " + user + ": " + msg);
        if (!user.equals(username)) {
            unseenChat.add(user + ": " + msg);
        }
    }

    public void addSystemMessage(String message) {
        appendRenderedEntry(message);
        unseenChat.add(message);
    }

    public void attachUnseenChat(Map<String, Object> result, boolean playerDead, boolean gameOver) {
        if (playerDead) {
            result.put("player_dead", true);
        }
        if (gameOver) {
            result.put("game_over", true);
        }
        List<String> recentChat = drainUnseenChat();
        if (recentChat != null) {
            result.put("recent_chat", recentChat);
        }
    }

    public void attachUnseenChat(ActionResult result, boolean playerDead, boolean gameOver) {
        if (playerDead) {
            result.player_dead = true;
        }
        if (gameOver) {
            result.game_over = true;
        }
        List<String> recentChat = drainUnseenChat();
        if (recentChat != null) {
            result.recent_chat = recentChat;
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

    public BridgePublishedGameLog publishedGameLog() {
        return new BridgePublishedGameLog(List.copyOf(publishedLog), nextPublishedCursor);
    }

    public void recordFetchedBridgeEvents(List<BridgeLogEntry> events) {
        if (events == null || events.isEmpty()) {
            return;
        }
        for (BridgeLogEntry event : events) {
            if (event == null) {
                continue;
            }
            if (publishedBridgeEventIndexes.contains(event.index())) {
                continue;
            }
            if (event.index() <= lastPublishedBridgeEventIndex) {
                throw new IllegalStateException(
                    "Bridge event fetch returned out-of-order unseen index " + event.index()
                        + " after " + lastPublishedBridgeEventIndex
                );
            }
            publishedBridgeEventIndexes.add(event.index());
            lastPublishedBridgeEventIndex = event.index();
            publishedLog.add(new BridgePublishedLogEntry(nextPublishedCursor++, event, null));
        }
    }

    private void appendRenderedEntry(String rendered) {
        publishedLog.add(new BridgePublishedLogEntry(nextPublishedCursor++, null, rendered));
    }

    private void prunePendingOutgoingChatEchoes(long nowMs, long echoDedupWindowMs) {
        while (!pendingOutgoingChatEchoes.isEmpty()) {
            PendingOutgoingChat oldest = pendingOutgoingChatEchoes.peekFirst();
            if ((nowMs - oldest.sentAtMs()) < echoDedupWindowMs) {
                return;
            }
            pendingOutgoingChatEchoes.removeFirst();
        }
    }

    private boolean consumePendingOutgoingEcho(String message) {
        for (var iterator = pendingOutgoingChatEchoes.iterator(); iterator.hasNext(); ) {
            PendingOutgoingChat pending = iterator.next();
            if (pending.message().equals(message)) {
                iterator.remove();
                return true;
            }
        }
        return false;
    }

    private List<String> drainUnseenChat() {
        if (unseenChat.isEmpty()) {
            return null;
        }
        List<String> recentChat = new ArrayList<>(unseenChat);
        unseenChat.clear();
        return recentChat;
    }
}

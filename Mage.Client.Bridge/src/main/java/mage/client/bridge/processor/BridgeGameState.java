package mage.client.bridge.processor;

import mage.client.bridge.RoundTracker;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

public final class BridgeGameState {
    private final Map<UUID, UUID> activeGames = new ConcurrentHashMap<>();
    private final Map<UUID, UUID> gameChatIds = new ConcurrentHashMap<>();
    private volatile boolean keepAliveAfterGame = false;
    private volatile boolean gameEverStarted = false;
    private volatile UUID currentGameId = null;
    private volatile UUID currentPlayerId = null;
    private volatile boolean superseded = false;
    private volatile boolean playerDead = false;
    private volatile GameView lastGameView = null;
    private final RoundTracker roundTracker = new RoundTracker();
    private volatile long lastCallbackReceivedAt = 0;
    private volatile long lastActionableCallbackAt = 0;

    public boolean keepAliveAfterGame() {
        return keepAliveAfterGame;
    }

    public void setKeepAliveAfterGame(boolean keepAliveAfterGame) {
        this.keepAliveAfterGame = keepAliveAfterGame;
    }

    public boolean gameEverStarted() {
        return gameEverStarted;
    }

    public boolean gameOverObserved() {
        return activeGames.isEmpty() && gameEverStarted;
    }

    public UUID currentGameId() {
        return currentGameId;
    }

    public UUID currentPlayerId() {
        return currentPlayerId;
    }

    public boolean superseded() {
        return superseded;
    }

    public void markSuperseded() {
        superseded = true;
    }

    public boolean playerDead() {
        return playerDead;
    }

    public void markPlayerDead() {
        playerDead = true;
    }

    public void clearPlayerDead() {
        playerDead = false;
    }

    public synchronized void updateLastGameView(
            GameView gameView,
            String source,
            Logger logger,
            String username) {
        if (gameView == null) {
            return;
        }
        GameView old = lastGameView;
        if (old != null && gameView.getGameSeq() < old.getGameSeq()) {
            String effectiveSource = source != null ? source : "unknown";
            logger.warn("[" + username + "] lastGameView REJECTED backward update game_seq "
                + old.getGameSeq() + " -> " + gameView.getGameSeq() + " (source=" + effectiveSource
                + ", thread=" + Thread.currentThread().getName() + ")");
            return;
        }
        lastGameView = gameView;
        roundTracker.update(gameView);
        int oldSeq = old != null ? old.getGameSeq() : -1;
        int newSeq = gameView.getGameSeq();
        if (oldSeq != newSeq) {
            String effectiveSource = source != null ? source : "unknown";
            String step = gameView.getStep() != null ? gameView.getStep().toString() : "null";
            logger.debug("[" + username + "] lastGameView game_seq " + oldSeq
                + " -> " + newSeq + " (source=" + effectiveSource + ", step=" + step
                + ", thread=" + Thread.currentThread().getName() + ")");
        }
    }

    public GameView lastGameView() {
        return lastGameView;
    }

    public int updateRound(GameView gameView) {
        return roundTracker.update(gameView);
    }

    public int currentRound() {
        return roundTracker.getGameRound();
    }

    public void recordCallbackArrival(boolean actionable) {
        long now = System.currentTimeMillis();
        lastCallbackReceivedAt = now;
        if (actionable) {
            lastActionableCallbackAt = now;
        }
    }

    public long lastCallbackReceivedAt() {
        return lastCallbackReceivedAt;
    }

    public long lastActionableCallbackAt() {
        return lastActionableCallbackAt;
    }

    public void activateGame(UUID gameId, UUID playerId) {
        activeGames.put(gameId, playerId);
        currentGameId = gameId;
        currentPlayerId = playerId;
        gameEverStarted = true;
    }

    public boolean containsActiveGame(UUID gameId) {
        return activeGames.containsKey(gameId);
    }

    public int activeGamesSize() {
        return activeGames.size();
    }

    public boolean removeActiveGame(UUID gameId) {
        return activeGames.remove(gameId) != null;
    }

    public UUID playerIdForGame(UUID gameId) {
        if (gameId == null) {
            return null;
        }
        UUID playerId = activeGames.get(gameId);
        if (playerId != null) {
            return playerId;
        }
        return gameId.equals(currentGameId) ? currentPlayerId : null;
    }

    public void rememberGameChatId(UUID gameId, UUID chatId) {
        gameChatIds.put(gameId, chatId);
    }

    public UUID chatIdForGame(UUID gameId) {
        return gameChatIds.get(gameId);
    }

    public UUID forgetGameChatId(UUID gameId) {
        return gameChatIds.remove(gameId);
    }

    public void resetProcessorState() {
        activeGames.clear();
        gameChatIds.clear();
        currentGameId = null;
        currentPlayerId = null;
        gameEverStarted = false;
        lastGameView = null;
        lastActionableCallbackAt = 0;
        roundTracker.reset();
    }
}

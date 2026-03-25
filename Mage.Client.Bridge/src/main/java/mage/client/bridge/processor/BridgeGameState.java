package mage.client.bridge.processor;

import mage.client.bridge.RoundTracker;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.UUID;

public final class BridgeGameState {
    private boolean keepAliveAfterGame = false;
    private boolean gameEverStarted = false;
    private boolean activeGame = false;
    private UUID currentGameId = null;
    private UUID currentPlayerId = null;
    private UUID currentChatId = null;
    private boolean superseded = false;
    private boolean playerDead = false;
    private long generation = 0;
    private GameView lastGameView = null;
    private final RoundTracker roundTracker = new RoundTracker();
    private long lastCallbackReceivedAt = 0;
    private long lastActionableCallbackAt = 0;

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
        return !activeGame && gameEverStarted;
    }

    public boolean hasActiveGame() {
        return activeGame;
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

    public long generation() {
        return generation;
    }

    public void markPlayerDead() {
        playerDead = true;
    }

    public void clearPlayerDead() {
        playerDead = false;
    }

    public boolean updateLastGameView(
            GameView gameView,
            String source,
            Logger logger,
            String username) {
        if (gameView == null) {
            return false;
        }
        GameView old = lastGameView;
        if (old != null && gameView.getGameSeq() < old.getGameSeq()) {
            String effectiveSource = source != null ? source : "unknown";
            logger.warn("[" + username + "] lastGameView REJECTED backward update game_seq "
                + old.getGameSeq() + " -> " + gameView.getGameSeq() + " (source=" + effectiveSource
                + ", thread=" + Thread.currentThread().getName() + ")");
            return false;
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
        return true;
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
        if (activeGame && currentGameId != null && !currentGameId.equals(gameId)) {
            throw new IllegalStateException(
                "Bridge processor already owns active game " + currentGameId + ", cannot activate " + gameId
            );
        }
        generation++;
        activeGame = true;
        currentGameId = gameId;
        currentPlayerId = playerId;
        currentChatId = null;
        gameEverStarted = true;
    }

    public boolean isCurrentActiveGame(UUID gameId) {
        return activeGame && gameId != null && gameId.equals(currentGameId);
    }

    public boolean clearActiveGame(UUID gameId) {
        if (!isCurrentActiveGame(gameId)) {
            return false;
        }
        activeGame = false;
        return true;
    }

    public UUID playerIdForGame(UUID gameId) {
        if (gameId == null) {
            return null;
        }
        return gameId.equals(currentGameId) ? currentPlayerId : null;
    }

    public void setCurrentChatId(UUID gameId, UUID chatId) {
        if (!activeGame) {
            throw new IllegalStateException("Cannot attach chat ID without an active game");
        }
        if (currentGameId == null || !currentGameId.equals(gameId)) {
            throw new IllegalStateException("Cannot attach chat ID to non-current game " + gameId);
        }
        currentChatId = chatId;
    }

    public UUID currentChatId() {
        return currentChatId;
    }

    public UUID clearCurrentChatId(UUID gameId) {
        if (currentGameId == null || !currentGameId.equals(gameId)) {
            return null;
        }
        UUID chatId = currentChatId;
        currentChatId = null;
        return chatId;
    }

    public void resetProcessorState() {
        generation++;
        activeGame = false;
        currentGameId = null;
        currentPlayerId = null;
        currentChatId = null;
        gameEverStarted = false;
        lastGameView = null;
        lastActionableCallbackAt = 0;
        roundTracker.reset();
    }
}

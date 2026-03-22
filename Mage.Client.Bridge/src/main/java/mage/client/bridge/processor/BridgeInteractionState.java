package mage.client.bridge.processor;

import mage.view.GameView;

import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

public final class BridgeInteractionState {
    private final Set<UUID> failedManaCasts = ConcurrentHashMap.newKeySet();
    private volatile UUID poolManaPayingForId = null;
    private volatile int poolManaAttempts = 0;
    private volatile CopyOnWriteArrayList<BridgeManaPlanEntry> manaPlan = null;
    private volatile Integer manaPlanAbilityIndex = null;
    private volatile boolean manaPlanAutoTapFallback = true;
    private volatile int lastTurnNumber = -1;
    private volatile int interactionsThisTurn = 0;
    private volatile int maxInteractionsPerTurn = 25;

    public void setMaxInteractionsPerTurn(int maxInteractionsPerTurn) {
        this.maxInteractionsPerTurn = maxInteractionsPerTurn;
    }

    public int maxInteractionsPerTurn() {
        return maxInteractionsPerTurn;
    }

    public int interactionsThisTurn() {
        return interactionsThisTurn;
    }

    public int incrementInteractionsThisTurn() {
        interactionsThisTurn++;
        return interactionsThisTurn;
    }

    public int lastTurnNumber() {
        return lastTurnNumber;
    }

    public void advanceTurn(GameView gameView) {
        if (gameView == null) {
            return;
        }
        int turn = gameView.getTurn();
        if (turn == lastTurnNumber) {
            return;
        }
        lastTurnNumber = turn;
        failedManaCasts.clear();
        interactionsThisTurn = 0;
        resetPoolManaTracking();
        clearManaPlan();
    }

    public boolean failedManaCast(UUID objectId) {
        return failedManaCasts.contains(objectId);
    }

    public void markFailedManaCast(UUID objectId) {
        if (objectId != null) {
            failedManaCasts.add(objectId);
        }
    }

    public CopyOnWriteArrayList<BridgeManaPlanEntry> manaPlan() {
        return manaPlan;
    }

    public void setManaPlan(CopyOnWriteArrayList<BridgeManaPlanEntry> manaPlan, boolean autoTapFallback) {
        this.manaPlan = manaPlan;
        this.manaPlanAutoTapFallback = autoTapFallback;
        this.manaPlanAbilityIndex = null;
    }

    public void clearManaPlan() {
        manaPlan = null;
        manaPlanAbilityIndex = null;
        manaPlanAutoTapFallback = true;
    }

    public Integer manaPlanAbilityIndex() {
        return manaPlanAbilityIndex;
    }

    public void setManaPlanAbilityIndex(Integer manaPlanAbilityIndex) {
        this.manaPlanAbilityIndex = manaPlanAbilityIndex;
    }

    public Integer consumeManaPlanAbilityIndex() {
        Integer abilityIndex = manaPlanAbilityIndex;
        manaPlanAbilityIndex = null;
        return abilityIndex;
    }

    public boolean manaPlanAutoTapFallback() {
        return manaPlanAutoTapFallback;
    }

    public void resetPoolManaTracking() {
        poolManaPayingForId = null;
        poolManaAttempts = 0;
    }

    public int recordPoolManaAttempt(UUID payingForId) {
        if (payingForId != null && payingForId.equals(poolManaPayingForId)) {
            poolManaAttempts++;
        } else {
            poolManaPayingForId = payingForId;
            poolManaAttempts = 1;
        }
        return poolManaAttempts;
    }

    public void resetRuntimeState() {
        failedManaCasts.clear();
        resetPoolManaTracking();
        clearManaPlan();
        lastTurnNumber = -1;
        interactionsThisTurn = 0;
    }
}

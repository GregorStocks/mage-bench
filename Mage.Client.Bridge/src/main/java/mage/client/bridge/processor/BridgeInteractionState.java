package mage.client.bridge.processor;

import mage.view.GameView;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

public final class BridgeInteractionState {
    private final Set<UUID> failedManaCasts = new HashSet<>();
    private UUID poolManaPayingForId = null;
    private int poolManaAttempts = 0;
    private List<BridgeManaPlanEntry> manaPlan = null;
    private Integer manaPlanAbilityIndex = null;
    private boolean manaPlanAutoTapFallback = true;
    private int lastTurnNumber = -1;
    private int interactionsThisTurn = 0;
    private int maxInteractionsPerTurn = 25;

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

    public List<BridgeManaPlanEntry> manaPlan() {
        return manaPlan;
    }

    public void setManaPlan(List<BridgeManaPlanEntry> manaPlan, boolean autoTapFallback) {
        this.manaPlan = manaPlan == null ? null : new ArrayList<>(manaPlan);
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

package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;

import java.util.List;
import java.util.Objects;

public final class BridgeDecisionState {
    private volatile PendingAction pendingAction = null;
    private List<Object> lastChoices = null;
    private String lastChoicesActionType = null;
    private String lastChoicesResponseType = null;
    private int lastChoicesCount = -1;
    private long lastChoicesGeneratedAtMs = 0;
    private BridgeChooseActionFlow pendingChooseActionFlow = null;
    private BridgePassPriorityFlow pendingPassPriorityFlow = null;
    private Runnable pendingActionChangedListener = () -> {};

    public PendingAction pendingAction() {
        return pendingAction;
    }

    public PendingAction replacePendingAction(PendingAction nextAction) {
        PendingAction previousAction = pendingAction;
        pendingAction = nextAction;
        pendingActionChangedListener.run();
        return previousAction;
    }

    public PendingAction replacePendingActionWithoutNotify(PendingAction nextAction) {
        PendingAction previousAction = pendingAction;
        pendingAction = nextAction;
        return previousAction;
    }

    public void restorePendingAction(PendingAction action) {
        pendingAction = action;
        pendingActionChangedListener.run();
    }

    public boolean clearPendingActionIfCurrent(PendingAction action) {
        if (pendingAction == action) {
            pendingAction = null;
            pendingActionChangedListener.run();
            return true;
        }
        return false;
    }

    public void restorePendingActionIfEmpty(PendingAction action) {
        if (pendingAction == null) {
            pendingAction = action;
            pendingActionChangedListener.run();
        }
    }

    public boolean hasPendingAction() {
        return pendingAction != null;
    }

    public List<Object> lastChoices() {
        return lastChoices;
    }

    public void setLastChoices(List<Object> choices) {
        lastChoices = choices;
    }

    public void clearLastChoices() {
        lastChoices = null;
    }

    public void recordChoiceSnapshot(String actionType, String responseType, int choiceCount) {
        lastChoicesActionType = actionType;
        lastChoicesResponseType = responseType;
        lastChoicesCount = choiceCount;
        lastChoicesGeneratedAtMs = System.currentTimeMillis();
    }

    public void clearChoiceSnapshot() {
        lastChoicesActionType = null;
        lastChoicesResponseType = null;
        lastChoicesCount = -1;
        lastChoicesGeneratedAtMs = 0;
    }

    public String lastChoicesActionType() {
        return lastChoicesActionType;
    }

    public String lastChoicesResponseType() {
        return lastChoicesResponseType;
    }

    public int lastChoicesCount() {
        return lastChoicesCount;
    }

    public long lastChoicesGeneratedAtMs() {
        return lastChoicesGeneratedAtMs;
    }

    public BridgeChooseActionFlow pendingChooseActionFlow() {
        return pendingChooseActionFlow;
    }

    public void setPendingChooseActionFlow(BridgeChooseActionFlow flow) {
        pendingChooseActionFlow = flow;
    }

    public void clearPendingChooseActionFlowIfCurrent(BridgeChooseActionFlow flow) {
        if (pendingChooseActionFlow == flow) {
            pendingChooseActionFlow = null;
        }
    }

    public BridgePassPriorityFlow pendingPassPriorityFlow() {
        return pendingPassPriorityFlow;
    }

    public void setPendingPassPriorityFlow(BridgePassPriorityFlow flow) {
        pendingPassPriorityFlow = flow;
    }

    public void clearPendingPassPriorityFlowIfCurrent(BridgePassPriorityFlow flow) {
        if (pendingPassPriorityFlow == flow) {
            pendingPassPriorityFlow = null;
        }
    }

    public void setPendingActionChangedListener(Runnable listener) {
        pendingActionChangedListener = Objects.requireNonNull(listener);
    }

    public void notifyPendingActionChanged() {
        pendingActionChangedListener.run();
    }

    public void reset() {
        pendingAction = null;
        pendingActionChangedListener.run();
        lastChoices = null;
        clearChoiceSnapshot();
        pendingChooseActionFlow = null;
        pendingPassPriorityFlow = null;
    }
}

package mage.client.bridge;

import mage.client.bridge.processor.BridgeChooseActionFlowContext;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeChooseActionStartResult;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.Set;
import java.util.UUID;

final class BridgeChooseActionFlowContextImpl implements BridgeChooseActionFlowContext {
    private final BridgeCallbackHandler handler;
    private final BridgeProcessorState processorState;

    BridgeChooseActionFlowContextImpl(
            BridgeCallbackHandler handler,
            BridgeProcessorState processorState) {
        this.handler = handler;
        this.processorState = processorState;
    }

    @Override
    public PendingAction currentPendingAction() {
        return processorState.decisionState().pendingAction();
    }

    @Override
    public PendingAction currentDecisionAction() {
        return handler.currentDecisionAction();
    }

    @Override
    public boolean requestCannotContinue() {
        return processorState.gameState().superseded()
            || processorState.gameState().playerDead()
            || processorState.gameState().gameOverObserved()
            || !handler.clientRunning();
    }

    @Override
    public ChooseActionTool.Result noPendingActionResult() {
        return handler.noPendingChooseActionResult();
    }

    @Override
    public BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action) {
        return handler.applyChooseActionNow(input, action);
    }

    @Override
    public String detectCombatSelect(PendingAction action) {
        return handler.detectCombatSelect(action);
    }

    @Override
    public UUID resolveShortId(String shortId) {
        return handler.resolveShortId(shortId);
    }

    @Override
    public Set<UUID> validTargets(PendingAction action) {
        return handler.validTargets(action);
    }

    @Override
    public boolean clearPendingActionIfCurrent(PendingAction action) {
        return handler.clearPendingActionIfCurrent(action);
    }

    @Override
    public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
        handler.sendBooleanOrDie(gameId, data, sendContext);
    }

    @Override
    public void sendUuidOrDie(UUID gameId, UUID data, String sendContext) {
        handler.sendUuidOrDie(gameId, data, sendContext);
    }

    @Override
    public void sendStringOrDie(UUID gameId, String data, String sendContext) {
        handler.sendStringOrDie(gameId, data, sendContext);
    }

    @Override
    public void clearLastChoices() {
        processorState.decisionState().clearLastChoices();
    }

    @Override
    public ChooseActionTool.Result buildChooseActionError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action) {
        return handler.buildError(result, errorCode, message, retryable, action);
    }

    @Override
    public void finishChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction,
            PendingAction nextAction) {
        handler.finishChooseActionWithNextDecision(result, previousAction, nextAction);
    }

    @Override
    public void finishChooseActionWithoutNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction) {
        handler.finishChooseActionWithoutNextDecision(result, previousAction);
    }

    @Override
    public void finishBatchChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction nextAction) {
        handler.finishBatchChooseActionWithNextDecision(result, nextAction);
    }

    @Override
    public void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result) {
        handler.finishBatchChooseActionWithoutNextDecision(result);
    }

    @Override
    public ChooseActionTool.Result cancelledChooseActionResult(
            PendingAction previousAction,
            ChooseActionTool.Result partialResult) {
        return handler.cancelledChooseActionResult(previousAction, partialResult);
    }
}

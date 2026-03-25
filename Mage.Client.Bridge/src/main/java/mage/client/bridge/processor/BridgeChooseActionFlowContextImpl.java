package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.Set;
import java.util.UUID;

public final class BridgeChooseActionFlowContextImpl implements BridgeChooseActionFlowContext {
    private final BridgeDecisionFlowService decisionFlowService;
    private final BridgeProcessorState processorState;

    public BridgeChooseActionFlowContextImpl(
            BridgeDecisionFlowService decisionFlowService,
            BridgeProcessorState processorState) {
        this.decisionFlowService = decisionFlowService;
        this.processorState = processorState;
    }

    @Override
    public PendingAction currentPendingAction() {
        return processorState.decisionState().pendingAction();
    }

    @Override
    public PendingAction currentDecisionAction() {
        return decisionFlowService.currentDecisionAction();
    }

    @Override
    public boolean requestCannotContinue() {
        return decisionFlowService.requestCannotContinue();
    }

    @Override
    public ChooseActionTool.Result noPendingActionResult() {
        return decisionFlowService.noPendingChooseActionResult();
    }

    @Override
    public BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action) {
        return decisionFlowService.applyChooseAction(input, action);
    }

    @Override
    public String detectCombatSelect(PendingAction action) {
        return decisionFlowService.detectCombatSelect(action);
    }

    @Override
    public UUID resolveShortId(String shortId) {
        return decisionFlowService.resolveShortId(shortId);
    }

    @Override
    public Set<UUID> validTargets(PendingAction action) {
        return decisionFlowService.validTargets(action);
    }

    @Override
    public boolean clearPendingActionIfCurrent(PendingAction action) {
        return decisionFlowService.clearPendingActionIfCurrent(action);
    }

    @Override
    public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
        decisionFlowService.sendBooleanOrDie(gameId, data, sendContext);
    }

    @Override
    public void sendUuidOrDie(UUID gameId, UUID data, String sendContext) {
        decisionFlowService.sendUuidOrDie(gameId, data, sendContext);
    }

    @Override
    public void sendStringOrDie(UUID gameId, String data, String sendContext) {
        decisionFlowService.sendStringOrDie(gameId, data, sendContext);
    }

    @Override
    public ChooseActionTool.Result buildChooseActionError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action) {
        return decisionFlowService.buildChooseActionError(result, errorCode, message, retryable, action);
    }

    @Override
    public void finishChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction,
            PendingAction nextAction) {
        decisionFlowService.finishChooseActionWithNextDecision(result, previousAction, nextAction);
    }

    @Override
    public void finishChooseActionWithoutNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction) {
        decisionFlowService.finishChooseActionWithoutNextDecision(result, previousAction);
    }

    @Override
    public void finishBatchChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction nextAction) {
        decisionFlowService.finishBatchChooseActionWithNextDecision(result, nextAction);
    }

    @Override
    public void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result) {
        decisionFlowService.finishBatchChooseActionWithoutNextDecision(result);
    }

    @Override
    public ChooseActionTool.Result cancelledChooseActionResult(
            PendingAction previousAction,
            ChooseActionTool.Result partialResult) {
        return decisionFlowService.cancelledChooseActionResult(previousAction, partialResult);
    }
}

package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.Set;
import java.util.UUID;

public interface BridgeChooseActionFlowContext {
    PendingAction currentPendingAction();

    PendingAction currentDecisionAction();

    boolean requestCannotContinue();

    ChooseActionTool.Result noPendingActionResult();

    BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action);

    String detectCombatSelect(PendingAction action);

    UUID resolveShortId(String shortId);

    Set<UUID> validTargets(PendingAction action);

    boolean clearPendingActionIfCurrent(PendingAction action);

    void sendBooleanOrDie(UUID gameId, boolean data, String sendContext);

    void sendUuidOrDie(UUID gameId, UUID data, String sendContext);

    void sendStringOrDie(UUID gameId, String data, String sendContext);

    ChooseActionTool.Result buildChooseActionError(
        ChooseActionTool.Result result,
        String errorCode,
        String message,
        boolean retryable,
        PendingAction action
    );

    void finishChooseActionWithNextDecision(
        ChooseActionTool.Result result,
        PendingAction previousAction,
        PendingAction nextAction
    );

    void finishChooseActionWithoutNextDecision(
        ChooseActionTool.Result result,
        PendingAction previousAction
    );

    void finishBatchChooseActionWithNextDecision(
        ChooseActionTool.Result result,
        PendingAction nextAction
    );

    void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result);

    ChooseActionTool.Result cancelledChooseActionResult(
        PendingAction previousAction,
        ChooseActionTool.Result partialResult
    );
}

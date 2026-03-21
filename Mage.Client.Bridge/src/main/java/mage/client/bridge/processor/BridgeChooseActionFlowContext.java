package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ChooseActionTool;

public interface BridgeChooseActionFlowContext {
    PendingAction currentDecisionAction();

    boolean requestCannotContinue();

    ChooseActionTool.Result noPendingActionResult();

    BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action);

    void finishChooseActionWithNextDecision(
        ChooseActionTool.Result result,
        PendingAction previousAction,
        PendingAction nextAction
    );

    void finishChooseActionWithoutNextDecision(
        ChooseActionTool.Result result,
        PendingAction previousAction
    );

    ChooseActionTool.Result interruptedChooseActionResult(
        PendingAction previousAction,
        ChooseActionTool.Result partialResult
    );
}

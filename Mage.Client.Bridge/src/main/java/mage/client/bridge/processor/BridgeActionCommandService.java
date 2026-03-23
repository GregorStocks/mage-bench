package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.Map;
import java.util.function.Consumer;
import java.util.function.Supplier;

public final class BridgeActionCommandService {
    private final String username;
    private final Logger logger;
    private final BridgeProcessorState processorState;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final BridgeChooseActionFlowManager chooseActionFlowManager;
    private final BridgePassPriorityFlowManager passPriorityFlowManager;
    private final BridgeConcedeFlowManager concedeFlowManager;
    private final Supplier<Session> sessionSupplier;
    private final long chatDedupWindowMs;
    private final Supplier<Map<String, Object>> executeDefaultActionImpl;
    private final Consumer<ActionResult> actionResultChatAttacher;
    private final Consumer<ChooseActionTool.Result> chooseActionResultChatAttacher;

    public BridgeActionCommandService(
            String username,
            Logger logger,
            BridgeProcessorState processorState,
            BridgeGameLogRefresher gameLogRefresher,
            BridgeChooseActionFlowManager chooseActionFlowManager,
            BridgePassPriorityFlowManager passPriorityFlowManager,
            BridgeConcedeFlowManager concedeFlowManager,
            Supplier<Session> sessionSupplier,
            long chatDedupWindowMs,
            Supplier<Map<String, Object>> executeDefaultActionImpl,
            Consumer<ActionResult> actionResultChatAttacher,
            Consumer<ChooseActionTool.Result> chooseActionResultChatAttacher) {
        this.username = username;
        this.logger = logger;
        this.processorState = processorState;
        this.gameLogRefresher = gameLogRefresher;
        this.chooseActionFlowManager = chooseActionFlowManager;
        this.passPriorityFlowManager = passPriorityFlowManager;
        this.concedeFlowManager = concedeFlowManager;
        this.sessionSupplier = sessionSupplier;
        this.chatDedupWindowMs = chatDedupWindowMs;
        this.executeDefaultActionImpl = executeDefaultActionImpl;
        this.actionResultChatAttacher = actionResultChatAttacher;
        this.chooseActionResultChatAttacher = chooseActionResultChatAttacher;
    }

    public Map<String, Object> executeDefaultAction() {
        return executeDefaultActionImpl.get();
    }

    public BridgeChooseActionFlow startChooseActionFlow(BridgeChooseActionInput input) {
        if (processorState.decisionState().pendingChooseActionFlow() != null) {
            return null;
        }
        processorState.interactionState().incrementInteractionsThisTurn();
        return chooseActionFlowManager.startPendingFlow(input);
    }

    public ChooseActionTool.Result chooseActionAlreadyPendingResult() {
        var result = new ChooseActionTool.Result();
        result.success = false;
        result.error = "choose_action already pending";
        result.error_code = "choose_action_already_pending";
        result.retryable = true;
        chooseActionResultChatAttacher.accept(result);
        return result;
    }

    public ChooseActionTool.Result cancelChooseActionFlow(BridgeChooseActionFlow flow) {
        return chooseActionFlowManager.cancelFlow(flow);
    }

    public BridgePassPriorityFlow startPassPriorityFlow(String until, Long boardCursorParam) {
        if (processorState.decisionState().pendingPassPriorityFlow() != null) {
            return null;
        }
        processorState.interactionState().incrementInteractionsThisTurn();
        return passPriorityFlowManager.startPendingFlow(until, boardCursorParam);
    }

    public ActionResult passPriorityAlreadyPendingResult() {
        var result = new ActionResult();
        result.error = "pass_priority already pending";
        actionResultChatAttacher.accept(result);
        return result;
    }

    public ActionResult cancelPassPriorityFlow(BridgePassPriorityFlow flow) {
        return passPriorityFlowManager.cancelFlow(flow);
    }

    public String sendChatMessage(String message) {
        var gameId = processorState.gameState().currentGameId();
        if (gameId == null) {
            logger.warn("[" + username + "] Cannot send chat: no active game");
            return "no active game";
        }
        var chatId = processorState.gameState().currentChatId();
        if (chatId == null) {
            logger.warn("[" + username + "] Cannot send chat: no chat ID for game " + gameId);
            return "no chat session for this game";
        }
        long now = System.currentTimeMillis();
        if (processorState.gameLogState().shouldSuppressOutgoingChat(message, now, chatDedupWindowMs)) {
            logger.info("[" + username + "] Suppressing duplicate chat message");
            return null;
        }
        if (!sessionSupplier.get().sendChatMessage(chatId, message)) {
            return "server rejected the message";
        }
        long publishAfterSyncEpoch = gameLogRefresher.captureSyncBarrierEpoch();
        processorState.gameLogState().recordOutgoingChatMessage(
            username,
            message,
            now,
            chatDedupWindowMs,
            publishAfterSyncEpoch
        );
        return null;
    }

    public BridgeConcedeFlow startConcedeFlow() {
        return concedeFlowManager.startPendingFlow();
    }
}

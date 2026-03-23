package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeChooseActionFlow;
import mage.client.bridge.processor.BridgeChooseActionFlowManager;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeConcedeFlow;
import mage.client.bridge.processor.BridgeConcedeFlowManager;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowManager;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.function.Consumer;
import java.util.function.Supplier;

public final class BridgeMcpActionApi {
    private final String username;
    private final Logger logger;
    private final BridgeProcessor processor;
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

    public BridgeMcpActionApi(
            String username,
            Logger logger,
            BridgeProcessor processor,
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
        this.processor = processor;
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
        return processor.submit(BridgeCommand.of(executeDefaultActionImpl));
    }

    public ChooseActionTool.Result chooseAction(
            Integer index,
            String id,
            Boolean answer,
            Integer amount,
            int[] amounts,
            Integer pile,
            String text,
            String[] manaPlanArray,
            Boolean autoTap,
            String[] attackers,
            String[] blockersArray) {
        BridgeChooseActionInput input = new BridgeChooseActionInput(
            index,
            id,
            answer,
            amount,
            amounts,
            pile,
            text,
            manaPlanArray,
            autoTap,
            attackers,
            blockersArray
        );
        BridgeChooseActionFlow flow = submitProcessorCommandPreservingInterrupt(() -> {
            if (processorState.decisionState().pendingChooseActionFlow() != null) {
                return null;
            }
            processorState.interactionState().incrementInteractionsThisTurn();
            return chooseActionFlowManager.startPendingFlow(input);
        });
        if (flow == null) {
            return submitProcessorCommandPreservingInterrupt(() -> {
                var result = new ChooseActionTool.Result();
                result.success = false;
                result.error = "choose_action already pending";
                result.error_code = "choose_action_already_pending";
                result.retryable = true;
                chooseActionResultChatAttacher.accept(result);
                return result;
            });
        }

        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            return cancelChooseActionFlowAfterCallerInterrupt(flow);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("chooseAction request failed", cause);
        }
    }

    public String sendChatMessage(String message) {
        return processor.submit(BridgeCommand.of(() -> sendChatMessageImpl(message)));
    }

    public boolean concede() {
        BridgeConcedeFlow flow = processor.submit(BridgeCommand.of(concedeFlowManager::startPendingFlow));
        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for concede result", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("concede request failed", cause);
        }
    }

    public ActionResult passPriority(String until, Long boardCursorParam) {
        BridgePassPriorityFlow flow = submitProcessorCommandPreservingInterrupt(() -> {
            if (processorState.decisionState().pendingPassPriorityFlow() != null) {
                return null;
            }
            processorState.interactionState().incrementInteractionsThisTurn();
            return passPriorityFlowManager.startPendingFlow(until, boardCursorParam);
        });

        if (flow == null) {
            return submitProcessorCommandPreservingInterrupt(() -> {
                var result = new ActionResult();
                result.error = "pass_priority already pending";
                actionResultChatAttacher.accept(result);
                return result;
            });
        }

        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            return cancelPassPriorityFlowAfterCallerInterrupt(flow);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("passPriority request failed", cause);
        }
    }

    public ActionResult waitAndGetChoices(String until, Long boardCursorParam) {
        return passPriority(until, boardCursorParam);
    }

    private ChooseActionTool.Result cancelChooseActionFlowAfterCallerInterrupt(BridgeChooseActionFlow flow) {
        try {
            return submitProcessorCommandPreservingInterrupt(() -> chooseActionFlowManager.cancelFlow(flow));
        } catch (IllegalStateException e) {
            return chooseActionFlowManager.cancelFlow(flow);
        } finally {
            Thread.currentThread().interrupt();
        }
    }

    private ActionResult cancelPassPriorityFlowAfterCallerInterrupt(BridgePassPriorityFlow flow) {
        try {
            return submitProcessorCommandPreservingInterrupt(() -> passPriorityFlowManager.cancelFlow(flow));
        } catch (IllegalStateException e) {
            return passPriorityFlowManager.cancelFlow(flow);
        } finally {
            Thread.currentThread().interrupt();
        }
    }

    private <T> T submitProcessorCommandPreservingInterrupt(Supplier<T> supplier) {
        return processor.submitPreservingInterrupt(BridgeCommand.of(supplier));
    }

    private String sendChatMessageImpl(String message) {
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
}

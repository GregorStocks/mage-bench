package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.listener.BridgeCallbackIngressErrorHandler;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import mage.view.AbilityPickerView;
import mage.view.ChatMessage;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.PlayerView;
import mage.view.TableClientMessage;
import mage.view.UserRequestMessage;
import org.apache.log4j.Logger;

import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.Predicate;
import java.util.function.Supplier;

public final class BridgeCallbackProcessorService
        implements BridgeCallbackDispatcherContext, BridgeCallbackIngressErrorHandler {
    private final String username;
    private final Logger logger;
    private final BridgeProcessorState processorState;
    private final Supplier<BridgeProcessor> processorSupplier;
    private final Supplier<BridgeStartGameFlowManager> startGameFlowManagerSupplier;
    private final Predicate<ClientCallbackMethod> actionableCallback;
    private final Supplier<Session> sessionSupplier;
    private final BooleanSupplier shouldLogBridgeEvents;
    private final BridgeCallbackBridgeEventLogger bridgeEventLogger;
    private final Consumer<String> errorLogger;
    private final Runnable advancePendingFlows;
    private final Runnable stopClient;
    private final Runnable clearShortIds;
    private final BiConsumer<GameView, String> projectPublishedGameState;
    private final long chatDedupWindowMs;

    public BridgeCallbackProcessorService(
            String username,
            Logger logger,
            BridgeProcessorState processorState,
            Supplier<BridgeProcessor> processorSupplier,
            Supplier<BridgeStartGameFlowManager> startGameFlowManagerSupplier,
            Predicate<ClientCallbackMethod> actionableCallback,
            Supplier<Session> sessionSupplier,
            BooleanSupplier shouldLogBridgeEvents,
            BridgeCallbackBridgeEventLogger bridgeEventLogger,
            Consumer<String> errorLogger,
            Runnable advancePendingFlows,
            Runnable stopClient,
            Runnable clearShortIds,
            BiConsumer<GameView, String> projectPublishedGameState,
            long chatDedupWindowMs) {
        this.username = username;
        this.logger = logger;
        this.processorState = processorState;
        this.processorSupplier = processorSupplier;
        this.startGameFlowManagerSupplier = startGameFlowManagerSupplier;
        this.actionableCallback = actionableCallback;
        this.sessionSupplier = sessionSupplier;
        this.shouldLogBridgeEvents = shouldLogBridgeEvents;
        this.bridgeEventLogger = bridgeEventLogger;
        this.errorLogger = errorLogger;
        this.advancePendingFlows = advancePendingFlows;
        this.stopClient = stopClient;
        this.clearShortIds = clearShortIds;
        this.projectPublishedGameState = projectPublishedGameState;
        this.chatDedupWindowMs = chatDedupWindowMs;
    }

    @Override
    public String nonCurrentGameCallbackIgnoreReason(UUID callbackGameId, ClientCallbackMethod method) {
        if (callbackGameId == null) {
            return null;
        }

        boolean gameScoped = actionableCallback.test(method)
                || method == ClientCallbackMethod.GAME_INIT
                || method == ClientCallbackMethod.GAME_OVER
                || method == ClientCallbackMethod.GAME_UPDATE
                || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM;
        if (!gameScoped) {
            return null;
        }

        UUID gameId = processorState.gameState().currentGameId();
        if (gameId == null) {
            return "no_current_game_id";
        }
        if (!gameId.equals(callbackGameId)) {
            return "non_current_game";
        }
        if (!processorState.gameState().isCurrentActiveGame(callbackGameId)) {
            return "inactive_game";
        }
        return null;
    }

    @Override
    public void logCallbackReceived(UUID callbackGameId, ClientCallbackMethod method, String ignoreReason) {
        String summary = summarizeCallbackContext(callbackGameId, ignoreReason);
        logger.debug("[" + username + "] Callback received: " + method + " (" + summary + ")");
        bridgeEventLogger.logBridgeEvent("CALLBACK_RECEIVED", callbackGameId, method.name() + " | " + summary);
    }

    @Override
    public boolean shouldIgnoreNonCurrentGameCallback(
            UUID callbackGameId,
            ClientCallbackMethod method,
            String ignoreReason) {
        if (ignoreReason == null) {
            return false;
        }

        String warnMessage;
        if ("no_current_game_id".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for game " + callbackGameId + " (no currentGameId)";
        } else if ("non_current_game".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for non-current game " + callbackGameId
                + " (currentGameId=" + processorState.gameState().currentGameId() + ")";
        } else if ("inactive_game".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for inactive game " + callbackGameId
                + " (not the current active game)";
        } else {
            warnMessage = "Ignoring " + method + " for game " + callbackGameId
                + " (reason=" + ignoreReason + ")";
        }
        logger.warn("[" + username + "] " + warnMessage);
        bridgeEventLogger.logBridgeEvent(
            "CALLBACK_IGNORED",
            callbackGameId,
            method.name() + " | " + summarizeCallbackContext(callbackGameId, ignoreReason)
        );
        return true;
    }

    @Override
    public void recordCallbackArrival(ClientCallbackMethod method) {
        processorState.gameState().recordCallbackArrival(actionableCallback.test(method));
    }

    @Override
    public BridgeActionableCallbackOutcome createActionableOutcome(ClientCallbackMethod method) {
        return new BridgeRecordedActionableCallbackOutcome(
            method,
            logger,
            username,
            summary -> bridgeEventLogger.logBridgeEvent(
                "CALLBACK_OUTCOME",
                processorState.gameState().currentGameId(),
                summary
            )
        );
    }

    @Override
    public boolean shouldLogBridgeEvents() {
        return shouldLogBridgeEvents.getAsBoolean();
    }

    @Override
    public String buildBridgeStateSummary() {
        GameView gameView = processorState.gameState().lastGameView();
        if (gameView == null) {
            return null;
        }
        var summary = new StringBuilder();
        summary.append("T").append(processorState.gameState().currentRound());
        if (gameView.getPhase() != null) {
            summary.append(" ").append(gameView.getPhase());
        }
        summary.append(" | ");
        UUID gameId = processorState.gameState().currentGameId();
        UUID myPlayerId = processorState.gameState().playerIdForGame(gameId);
        for (PlayerView player : gameView.getPlayers()) {
            boolean isMe = player.getPlayerId().equals(myPlayerId);
            summary.append(player.getName());
            if (isMe) {
                summary.append("(me)");
            }
            summary.append(":").append(player.getLife()).append("hp");
            summary.append(",").append(player.getHandCount()).append("h");
            summary.append(",").append(player.getBattlefield() != null ? player.getBattlefield().size() : 0).append("bf");
            summary.append(" | ");
        }
        if (gameView.getMyHand() != null && !gameView.getMyHand().isEmpty()) {
            summary.append("Hand:[");
            boolean first = true;
            for (var card : gameView.getMyHand().values()) {
                if (!first) {
                    summary.append(",");
                }
                summary.append(card.getDisplayName());
                first = false;
            }
            summary.append("]");
        }
        return summary.toString();
    }

    @Override
    public void logBridgeEvent(ClientCallbackMethod method, UUID gameId, String summary) {
        bridgeEventLogger.logBridgeEvent(method.name(), gameId, summary);
    }

    @Override
    public void storePendingAction(UUID gameId, ClientCallbackMethod method, Object data) {
        String message = extractMessage(data);
        int gameSeq = 0;
        GameView gameView = extractGameView(data);
        if (gameView != null) {
            boolean updated = processorState.gameState().updateLastGameView(
                gameView,
                "storePendingAction:" + method.name(),
                logger,
                username
            );
            if (updated) {
                projectPublishedGameState.accept(gameView, "storePendingAction:" + method.name());
                processorState.interactionState().advanceTurn(gameView);
            }
            gameSeq = gameView.getGameSeq();
        }
        PendingAction newAction = new PendingAction(gameId, method, data, message, gameSeq);
        PendingAction replacedAction = processorState.decisionState().replacePendingAction(newAction);
        if (replacedAction != null) {
            String summary = "old=" + summarizePendingAction(replacedAction)
                + ",new=" + summarizePendingAction(newAction);
            logger.warn("[" + username + "] Pending action replaced: " + summary);
            bridgeEventLogger.logBridgeEvent("PENDING_ACTION_REPLACED", gameId, summary);
        }
        logger.debug("[" + username + "] Stored pending action: " + method + " - " + message);
        advancePendingFlows.run();
    }

    @Override
    public void handleStartGame(UUID gameId, Object data) {
        TableClientMessage message = (TableClientMessage) data;
        UUID startTableId = message.getCurrentTableId();
        String ignoreReason = startGameFlowManager().ignoreReasonForStartGame(
            startTableId,
            processorState.gameState().keepAliveAfterGame()
        );
        if (ignoreReason != null) {
            logger.warn("[" + username + "] Ignoring START_GAME for table "
                    + startTableId + " because " + ignoreReason
                    + " (gameId=" + gameId + ")");
            return;
        }
        UUID playerId = message.getPlayerId();
        processorState.gameState().activateGame(gameId, playerId);
        clearShortIds.run();

        Session session = sessionSupplier.get();
        if (!session.joinGame(gameId)) {
            logger.error("[" + username + "] Failed to join game: " + gameId);
        }

        session.getGameChatId(gameId).ifPresent(chatId -> {
            processorState.gameState().setCurrentChatId(gameId, chatId);
            session.joinChat(chatId);
            logger.info("[" + username + "] Joined game chat: " + chatId);
        });

        logger.info("[" + username + "] Game started: gameId=" + gameId + ", playerId=" + playerId);
        startGameFlowManager().completePendingFlow();
    }

    @Override
    public void handleGameInit(Object data) {
        GameView gameView = (GameView) data;
        updateLastGameView(gameView, "GAME_INIT");
        logger.info("[" + username + "] Game initialized: " + gameView.getPlayers().size() + " players");
    }

    @Override
    public void logGameState(Object data) {
        GameView gameView = extractGameView(data);
        if (gameView != null) {
            updateLastGameView(gameView, "GAME_UPDATE");
            processorState.interactionState().advanceTurn(gameView);
            logger.debug("[" + username + "] Game update: turn " + gameView.getTurn()
                    + ", phase " + gameView.getPhase() + ", active player " + gameView.getActivePlayerName());
            return;
        }
        if (data instanceof GameClientMessage message) {
            logger.debug("[" + username + "] Game inform: " + message.getMessage());
        }
    }

    @Override
    public void handleGameOver(UUID gameId, Object data) {
        GameClientMessage message = (GameClientMessage) data;
        GameView gameView = message.getGameView();
        if (gameView != null) {
            updateLastGameView(gameView, "handleGameOver");
        }
        cleanupGame(gameId);
        logger.info("[" + username + "] Game over: " + message.getMessage());

        if (processorState.gameState().keepAliveAfterGame()) {
            logger.info("[" + username + "] Game ended (keepAlive mode, staying connected)");
        } else {
            logger.info("[" + username + "] Game ended, stopping client");
            stopClient.run();
        }
        advancePendingFlows.run();
    }

    @Override
    public void handleEndGameInfo(UUID gameId) {
        boolean wasActive = cleanupGame(gameId);
        if (!wasActive) {
            logger.info("[" + username + "] End game info received for game " + gameId);
            return;
        }
        logger.warn("[" + username + "] END_GAME_INFO cleaning up game " + gameId
            + " (GAME_OVER was likely dropped)");
        if (!processorState.gameState().keepAliveAfterGame()) {
            logger.info("[" + username + "] END_GAME_INFO stopping client (missed GAME_OVER)");
            stopClient.run();
        }
        advancePendingFlows.run();
    }

    @Override
    public void handleChatMessage(Object data) {
        if (data instanceof ChatMessage chatMessage) {
            if (chatMessage.getMessageType() == ChatMessage.MessageType.GAME) {
                String message = chatMessage.getMessage();
                if (!processorState.gameState().playerDead()
                        && message != null
                        && message.contains("has lost the game")
                        && message.contains(username)) {
                    processorState.gameState().markPlayerDead();
                    logger.info("[" + username + "] Player death detected from game log");
                }
            } else if (chatMessage.getMessageType() == ChatMessage.MessageType.TALK) {
                String user = chatMessage.getUsername();
                String message = chatMessage.getMessage();
                if (user != null && message != null && !message.isEmpty()) {
                    processorState.gameLogState().recordTalkMessage(
                        username,
                        user,
                        message,
                        System.currentTimeMillis(),
                        chatDedupWindowMs
                    );
                }
            }
            logger.debug("[" + username + "] Chat: " + chatMessage.getMessage());
            return;
        }
        logEvent(ClientCallbackMethod.CHATMESSAGE, data);
    }

    @Override
    public void logEvent(ClientCallbackMethod method, Object data) {
        logger.debug("[" + username + "] Event: " + method + " - " + data);
    }

    @Override
    public void handleUserRequestDialog(Object data) {
        UserRequestMessage request = (UserRequestMessage) data;
        if (request.getButton1Action() == mage.constants.PlayerAction.ADD_PERMISSION_TO_SEE_HAND_CARDS) {
            logger.info("[" + username + "] Auto-granting hand permission to " + request.getRelatedUserName());
            sessionSupplier.get().sendPlayerAction(
                mage.constants.PlayerAction.ADD_PERMISSION_TO_SEE_HAND_CARDS,
                request.getGameId(),
                request.getRelatedUserId()
            );
            return;
        }
        logger.debug("[" + username + "] Ignoring user request dialog: " + request.getTitle());
    }

    @Override
    public void logUnhandledCallback(ClientCallbackMethod method) {
        logger.debug("[" + username + "] Unhandled callback: " + method);
    }

    @Override
    public void handleProcessorCallbackException(ClientCallbackMethod method, Exception e, boolean actionable) {
        handleCallbackIngressException(method, e, actionable);
    }

    @Override
    public void handleCallbackIngressException(ClientCallbackMethod method, Exception exception, boolean actionable) {
        errorLogger.accept("Error handling callback " + method + ": " + exception.getMessage());
        logger.debug("[" + username + "] Callback error stack trace", exception);
        if (!actionable) {
            return;
        }
        logger.error("[" + username + "] CRITICAL: Actionable callback " + method
                + " dropped due to exception — declaring player dead to prevent hang");
        processorState.gameState().markPlayerDead();
        try {
            processorSupplier.get().submit(BridgeCommand.of(() -> {
                advancePendingFlows.run();
                return null;
            }));
        } catch (IllegalStateException ignored) {
            // Processor is already gone; there is nothing left to wake.
        }
    }

    private BridgeStartGameFlowManager startGameFlowManager() {
        return startGameFlowManagerSupplier.get();
    }

    private void updateLastGameView(GameView gameView, String source) {
        boolean updated = processorState.gameState().updateLastGameView(gameView, source, logger, username);
        if (updated) {
            projectPublishedGameState.accept(gameView, source);
        }
    }

    private boolean cleanupGame(UUID gameId) {
        boolean wasActive = processorState.gameState().clearActiveGame(gameId);
        UUID chatId = processorState.gameState().clearCurrentChatId(gameId);
        if (chatId != null) {
            sessionSupplier.get().leaveChat(chatId);
        }
        return wasActive;
    }

    private String summarizeCallbackContext(UUID callbackGameId, String ignoreReason) {
        PendingAction action = processorState.decisionState().pendingAction();
        boolean callbackActive = callbackGameId != null && processorState.gameState().isCurrentActiveGame(callbackGameId);
        var summary = new StringBuilder();
        summary.append("callbackGameId=").append(callbackGameId);
        summary.append(",currentGameId=").append(processorState.gameState().currentGameId());
        summary.append(",callbackActive=").append(callbackActive);
        summary.append(",pendingAction=").append(summarizePendingAction(action));
        if (ignoreReason != null) {
            summary.append(",ignoreReason=").append(ignoreReason);
        }
        return summary.toString();
    }

    private static String summarizePendingAction(PendingAction action) {
        if (action == null) {
            return "none";
        }
        return "method=" + action.method().name()
            + ",gameId=" + action.gameId()
            + ",gameSeq=" + action.gameSeq()
            + ",message=" + abbreviateForLog(action.message(), 120);
    }

    private static String abbreviateForLog(String value, int maxChars) {
        if (value == null) {
            return "null";
        }
        String normalized = value.replace('\n', ' ').replace('\r', ' ');
        if (normalized.length() <= maxChars) {
            return normalized;
        }
        return normalized.substring(0, Math.max(0, maxChars - 3)) + "...";
    }

    private static GameView extractGameView(Object data) {
        if (data instanceof GameView gameView) {
            return gameView;
        }
        if (data instanceof GameClientMessage message) {
            return message.getGameView();
        }
        if (data instanceof AbilityPickerView picker) {
            return picker.getGameView();
        }
        return null;
    }

    private static String extractMessage(Object data) {
        if (data instanceof GameClientMessage message) {
            if (message.getMessage() != null) {
                return message.getMessage();
            }
            if (message.getChoice() != null && message.getChoice().getMessage() != null) {
                return message.getChoice().getMessage();
            }
        } else if (data instanceof AbilityPickerView picker) {
            return picker.getMessage();
        }
        return "";
    }
}

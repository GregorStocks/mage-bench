package mage.client.bridge;

import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.view.AbilityPickerView;
import mage.view.ChatMessage;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.TableClientMessage;
import org.apache.log4j.Logger;

import java.io.Serializable;
import java.util.EnumSet;
import java.util.Map;
import java.util.UUID;

final class BridgeCallbackRuntime {

    // Track actionable callbacks (GAME_SELECT, GAME_ASK, etc.) separately from passive
    // ones (CHATMESSAGE, GAME_UPDATE). Used by zombie detection and progress logging.
    private static final EnumSet<ClientCallbackMethod> ACTIONABLE_CALLBACKS = EnumSet.of(
        ClientCallbackMethod.GAME_SELECT, ClientCallbackMethod.GAME_ASK,
        ClientCallbackMethod.GAME_TARGET, ClientCallbackMethod.GAME_CHOOSE_ABILITY,
        ClientCallbackMethod.GAME_CHOOSE_CHOICE, ClientCallbackMethod.GAME_CHOOSE_PILE,
        ClientCallbackMethod.GAME_PLAY_MANA, ClientCallbackMethod.GAME_PLAY_XMANA,
        ClientCallbackMethod.GAME_GET_AMOUNT, ClientCallbackMethod.GAME_GET_MULTI_AMOUNT
    );

    private static final Logger logger = Logger.getLogger(BridgeCallbackHandler.class);

    private final BridgeCallbackHandler handler;

    BridgeCallbackRuntime(BridgeCallbackHandler handler) {
        this.handler = handler;
    }

    void handleCallback(ClientCallback callback) {
        try {
            callback.decompressData();
            UUID objectId = callback.getObjectId();
            ClientCallbackMethod method = callback.getMethod();
            String ignoreReason = nonCurrentGameCallbackIgnoreReason(objectId, method);
            handler.logCallbackReceived(objectId, method, ignoreReason);
            if (shouldIgnoreNonCurrentGameCallback(objectId, method, ignoreReason)) {
                return;
            }
            handler.lastCallbackReceivedAt = System.currentTimeMillis();
            if (ACTIONABLE_CALLBACKS.contains(method)) {
                handler.lastActionableCallbackAt = System.currentTimeMillis();
            }
            ActionableCallbackOutcome actionableOutcome = ACTIONABLE_CALLBACKS.contains(method)
                    ? new ActionableCallbackOutcome(method)
                    : null;

            // Bridge JSONL dump: log every callback
            if (handler.bridgeLogPath != null) {
                String summary = null;
                if (method == ClientCallbackMethod.GAME_UPDATE || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM) {
                    summary = handler.buildBridgeStateSummary();
                } else if (method == ClientCallbackMethod.CHATMESSAGE) {
                    Object chatData = callback.getData();
                    if (chatData instanceof ChatMessage chatMsg) {
                        summary = chatMsg.getMessageType() + ": " + chatMsg.getMessage();
                    }
                } else if (method == ClientCallbackMethod.GAME_OVER) {
                    summary = "Game over";
                }
                handler.logBridgeEvent(method, objectId, summary);
            }

            switch (method) {
                case START_GAME:
                    handleStartGame(objectId, callback);
                    break;

                case GAME_INIT: // Initialization: sets first lastGameView; not a recurring passive update
                    handleGameInit(callback);
                    break;

                case GAME_UPDATE: // Passive: debug logging only, no state mutation
                case GAME_UPDATE_AND_INFORM:
                    logGameState(callback);
                    break;

                case GAME_ASK:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_ASK");
                    break;

                case GAME_SELECT:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_SELECT");
                    break;

                case GAME_TARGET:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_TARGET");
                    break;

                case GAME_CHOOSE_ABILITY:
                    // Always defer to the synchronous decision boundary.
                    // Mana-plan consumption and empty-choices auto-handling happen in
                    // maybeAutoHandleNonDecisionAction, not on the callback thread.
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_ABILITY");
                    break;

                case GAME_CHOOSE_CHOICE:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_CHOICE");
                    break;

                case GAME_CHOOSE_PILE:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_PILE");
                    break;

                case GAME_PLAY_MANA:
                case GAME_PLAY_XMANA:
                    // XMage is blocked on this exact callback and only accepts the
                    // corresponding sendPlayer* response. We cannot "wait until
                    // later when we have priority" without first recording the
                    // authoritative callback payload and waking the synchronous tool
                    // thread that will answer it.
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction(method.name());
                    break;

                case GAME_GET_AMOUNT:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_GET_AMOUNT");
                    break;

                case GAME_GET_MULTI_AMOUNT:
                    storePendingAction(objectId, method, callback);
                    actionableOutcome.storedPendingAction("GAME_GET_MULTI_AMOUNT");
                    break;

                case GAME_OVER:
                    handler.handleGameOver(objectId, callback);
                    break;

                case END_GAME_INFO:
                    handler.handleEndGameInfo(objectId);
                    break;

                case CHATMESSAGE:
                    handleChatMessage(callback);
                    break;

                case SERVER_MESSAGE: // Passive: log-only, no state mutation
                case GAME_ERROR:
                case GAME_INFORM_PERSONAL:
                case JOINED_TABLE:
                    handler.logEvent(callback);
                    break;

                case USER_REQUEST_DIALOG:
                    handler.handleUserRequestDialog(callback);
                    break;

                default:
                    logger.debug("[" + handler.client.getUsername() + "] Unhandled callback: " + method);
            }
            if (actionableOutcome != null) {
                actionableOutcome.verifyRecorded();
            }
        } catch (Exception e) {
            handler.logError("Error handling callback " + callback.getMethod() + ": " + e.getMessage());
            logger.debug("[" + handler.client.getUsername() + "] Callback error stack trace", e);
            // If this was an actionable callback (one that requires a player response),
            // the server's game thread is now stuck in waitForResponse() forever because
            // no response was sent. Signal playerDead so passPriority/chooseAction exit
            // immediately instead of hanging until the Python HTTP timeout (120s).
            if (ACTIONABLE_CALLBACKS.contains(callback.getMethod())) {
                logger.error("[" + handler.client.getUsername() + "] CRITICAL: Actionable callback " + callback.getMethod()
                        + " dropped due to exception - declaring player dead to prevent hang");
                handler.playerDead = true;
                synchronized (handler.actionLock) {
                    handler.actionLock.notifyAll();
                }
            }
        }
    }

    private void storePendingAction(UUID gameId, ClientCallbackMethod method, ClientCallback callback) {
        Object data = callback.getData();
        String message = extractMessage(data);
        // Capture GameView and game_seq from the decision callback itself,
        // not from lastGameView (which can be updated by later gameUpdate
        // callbacks racing on the callback thread).
        int gameSeq = 0;
        GameView gv = extractGameView(data);
        if (gv != null) {
            handler.updateLastGameView(gv, "storePendingAction:" + method.name());
            gameSeq = gv.getGameSeq();
        }
        PendingAction replacedAction;
        PendingAction newAction = new PendingAction(gameId, method, data, message, gameSeq);
        synchronized (handler.actionLock) {
            replacedAction = handler.pendingAction;
            handler.pendingAction = newAction;
            handler.actionLock.notifyAll();
        }
        if (replacedAction != null) {
            String summary = "old=" + handler.summarizePendingAction(replacedAction)
                + ",new=" + handler.summarizePendingAction(newAction);
            logger.warn("[" + handler.client.getUsername() + "] Pending action replaced: " + summary);
            handler.logBridgeEvent("PENDING_ACTION_REPLACED", gameId, summary);
        }
        logger.debug("[" + handler.client.getUsername() + "] Stored pending action: " + method + " - " + message);
    }

    /**
     * Ignore late callbacks from stale games in keepAlive mode.
     *
     * Without this guard, callbacks from an older game can overwrite pendingAction
     * for the current game and strand pass_priority/choose_action waiting on the
     * wrong game flow.
     */
    private String nonCurrentGameCallbackIgnoreReason(UUID callbackGameId, ClientCallbackMethod method) {
        if (callbackGameId == null) {
            return null;
        }

        // START_GAME is intentionally excluded: it's the callback that
        // *establishes* currentGameId, so filtering it would be circular.
        boolean gameScoped = ACTIONABLE_CALLBACKS.contains(method)
                || method == ClientCallbackMethod.GAME_INIT
                || method == ClientCallbackMethod.GAME_OVER
                || method == ClientCallbackMethod.GAME_UPDATE
                || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM;
        if (!gameScoped) {
            return null;
        }

        UUID gameId = handler.currentGameId;
        if (gameId == null) {
            return "no_current_game_id";
        }
        if (!gameId.equals(callbackGameId)) {
            return "non_current_game";
        }
        if (!handler.activeGames.containsKey(callbackGameId)) {
            return "inactive_game";
        }
        return null;
    }

    private boolean shouldIgnoreNonCurrentGameCallback(
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
                + " (currentGameId=" + handler.currentGameId + ")";
        } else if ("inactive_game".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for inactive game " + callbackGameId
                + " (not in activeGames)";
        } else {
            warnMessage = "Ignoring " + method + " for game " + callbackGameId
                + " (reason=" + ignoreReason + ")";
        }
        logger.warn("[" + handler.client.getUsername() + "] " + warnMessage);
        handler.logBridgeEvent(
            "CALLBACK_IGNORED",
            callbackGameId,
            method.name() + " | " + handler.summarizeCallbackContext(callbackGameId, ignoreReason));
        return true;
    }

    // Passive callback: CHATMESSAGE
    // Remaining effects after passive-state audit (see issue: minimize-bridge-passive-callback-state):
    //  REQUIRED  - playerDead detection: early bail-out prevents bridge hangs after elimination
    //  REQUIRED  - unseenChat buffering: surfaces player-to-player chat + system messages via attachUnseenChat()
    //  REQUIRED  - chatLog capture: TALK messages interleaved with bridge events by renderGameLogFlat()
    //  DONE      - gameLog accumulation: migrated to server-side bridge events (epoch 55)
    private void handleChatMessage(ClientCallback callback) {
        Object data = callback.getData();
        if (data instanceof ChatMessage chatMsg) {
            if (chatMsg.getMessageType() == ChatMessage.MessageType.GAME) {
                String msg = chatMsg.getMessage();
                // Detect when our player has lost the game
                if (!handler.playerDead && msg != null && msg.contains("has lost the game")
                        && msg.contains(handler.client.getUsername())) {
                    handler.playerDead = true;
                    logger.info("[" + handler.client.getUsername() + "] Player death detected from game log");
                }
            } else if (chatMsg.getMessageType() == ChatMessage.MessageType.TALK) {
                String user = chatMsg.getUsername();
                String msg = chatMsg.getMessage();
                if (user != null && msg != null && !msg.isEmpty()) {
                    // Capture chat for game log rendering (interleaved with bridge events).
                    // bridgeEventCursor is the best-known event position; it advances when
                    // pullBridgeEvents() runs. Chat arriving before the first pull gets
                    // cursor=0, placing it before game events - chronologically correct since
                    // the chat predates the first event pull.
                    synchronized (handler.chatLog) {
                        handler.chatLog.add(new BridgeChatLogEntry(
                            handler.bridgeEventCursor,
                            msg,
                            "[Chat] " + user + ": " + msg
                        ));
                    }
                    // Buffer chat from other players so pass_priority can surface it
                    if (!user.equals(handler.client.getUsername())) {
                        synchronized (handler.unseenChat) {
                            handler.unseenChat.add(user + ": " + msg);
                        }
                    }
                }
            }
            logger.debug("[" + handler.client.getUsername() + "] Chat: " + chatMsg.getMessage());
        } else {
            handler.logEvent(callback);
        }
    }

    private void handleStartGame(UUID gameId, ClientCallback callback) {
        TableClientMessage message = (TableClientMessage) callback.getData();
        UUID startTableId = message.getCurrentTableId();
        if (handler.keepAliveAfterGame && !handler.startGameArmed) {
            logger.warn("[" + handler.client.getUsername() + "] Ignoring START_GAME for table "
                    + startTableId + " because join_table has not armed a next game"
                    + " (gameId=" + gameId + ")");
            return;
        }
        UUID expectedTableId = handler.expectedStartTableId;
        if (expectedTableId != null && !expectedTableId.equals(startTableId)) {
            logger.warn("[" + handler.client.getUsername() + "] Ignoring START_GAME for table "
                    + startTableId + " while waiting for table " + expectedTableId
                    + " (gameId=" + gameId + ")");
            return;
        }
        handler.expectedStartTableId = null;
        handler.startGameArmed = false;
        UUID playerId = message.getPlayerId();
        handler.activeGames.put(gameId, playerId);
        handler.currentGameId = gameId;
        handler.currentPlayerId = playerId;
        handler.gameEverStarted = true;
        handler.shortIds.clear();

        // Join the game session (creates GameSessionPlayer on server)
        if (!handler.session.joinGame(gameId)) {
            logger.error("[" + handler.client.getUsername() + "] Failed to join game: " + gameId);
        }

        // Get chat ID for this game and join to receive incoming messages
        handler.session.getGameChatId(gameId).ifPresent(chatId -> {
            handler.gameChatIds.put(gameId, chatId);
            handler.session.joinChat(chatId);
            logger.info("[" + handler.client.getUsername() + "] Joined game chat: " + chatId);
        });

        logger.info("[" + handler.client.getUsername() + "] Game started: gameId=" + gameId + ", playerId=" + playerId);
        handler.gameStartLatch.countDown();
    }

    private void handleGameInit(ClientCallback callback) {
        GameView gameView = (GameView) callback.getData();
        handler.updateLastGameView(gameView, "GAME_INIT");
        logger.info("[" + handler.client.getUsername() + "] Game initialized: " + gameView.getPlayers().size() + " players");
    }

    // Passive callback: GAME_UPDATE / GAME_UPDATE_AND_INFORM
    // No state mutation - actionable callbacks provide fresh GameViews at decision time via
    // storePendingAction(). Short ID registration for non-CardView objects (players, lookedAt
    // cards) happens in getStableShortId() which checks the GameView's lookedAt zone directly.
    private void logGameState(ClientCallback callback) {
        Object data = callback.getData();
        if (data instanceof GameView gameView) {
            logger.debug("[" + handler.client.getUsername() + "] Game update: turn " + gameView.getTurn()
                    + ", phase " + gameView.getPhase() + ", active player " + gameView.getActivePlayerName());
        } else if (data instanceof GameClientMessage message) {
            logger.debug("[" + handler.client.getUsername() + "] Game inform: " + message.getMessage());
        }
    }

    private static GameView extractGameView(Object data) {
        if (data instanceof GameClientMessage gcm) {
            return gcm.getGameView();
        }
        if (data instanceof AbilityPickerView apv) {
            return apv.getGameView();
        }
        return null;
    }

    private static String extractMessage(Object data) {
        if (data instanceof GameClientMessage msg) {
            if (msg.getMessage() != null) {
                return msg.getMessage();
            }
            if (msg.getChoice() != null && msg.getChoice().getMessage() != null) {
                return msg.getChoice().getMessage();
            }
        } else if (data instanceof AbilityPickerView picker) {
            return picker.getMessage();
        }
        return "";
    }

    private final class ActionableCallbackOutcome {
        private final ClientCallbackMethod method;
        private String outcome = null;

        private ActionableCallbackOutcome(ClientCallbackMethod method) {
            this.method = method;
        }

        void storedPendingAction(String detail) {
            record("stored_pending_action:" + detail);
        }

        void verifyRecorded() {
            if (outcome == null) {
                throw new IllegalStateException(
                        "Actionable callback " + method
                        + " returned without storing a pending action or sending a response");
            }
        }

        private void record(String nextOutcome) {
            if (outcome != null) {
                throw new IllegalStateException(
                        "Actionable callback " + method
                        + " recorded multiple outcomes: " + outcome + " then " + nextOutcome);
            }
            outcome = nextOutcome;
            logger.debug("[" + handler.client.getUsername() + "] Callback outcome " + method + ": " + nextOutcome);
            handler.logBridgeEvent("CALLBACK_OUTCOME", method.name() + ": " + nextOutcome);
        }
    }
}

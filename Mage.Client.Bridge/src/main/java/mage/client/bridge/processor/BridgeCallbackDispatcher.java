package mage.client.bridge.processor;

import mage.interfaces.callback.ClientCallbackMethod;
import mage.view.ChatMessage;

import java.util.EnumSet;
import java.util.UUID;

public final class BridgeCallbackDispatcher {
    private static final EnumSet<ClientCallbackMethod> ACTIONABLE_CALLBACKS = EnumSet.of(
        ClientCallbackMethod.GAME_SELECT, ClientCallbackMethod.GAME_ASK,
        ClientCallbackMethod.GAME_TARGET, ClientCallbackMethod.GAME_CHOOSE_ABILITY,
        ClientCallbackMethod.GAME_CHOOSE_CHOICE, ClientCallbackMethod.GAME_CHOOSE_PILE,
        ClientCallbackMethod.GAME_PLAY_MANA, ClientCallbackMethod.GAME_PLAY_XMANA,
        ClientCallbackMethod.GAME_GET_AMOUNT, ClientCallbackMethod.GAME_GET_MULTI_AMOUNT);

    private final BridgeCallbackDispatcherContext context;

    public BridgeCallbackDispatcher(BridgeCallbackDispatcherContext context) {
        this.context = context;
    }

    public void process(BridgeCallbackEvent event) {
        UUID objectId = event.objectId();
        ClientCallbackMethod method = event.method();
        Object data = event.data();
        boolean actionable = ACTIONABLE_CALLBACKS.contains(method);
        try {
            String ignoreReason = context.nonCurrentGameCallbackIgnoreReason(objectId, method);
            context.logCallbackReceived(objectId, method, ignoreReason);
            if (context.shouldIgnoreNonCurrentGameCallback(objectId, method, ignoreReason)) {
                return;
            }
            context.recordCallbackArrival(method);
            BridgeActionableCallbackOutcome actionableOutcome =
                actionable ? context.createActionableOutcome(method) : null;

            if (context.shouldLogBridgeEvents()) {
                String summary = null;
                if (method == ClientCallbackMethod.GAME_UPDATE
                        || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM) {
                    summary = context.buildBridgeStateSummary();
                } else if (method == ClientCallbackMethod.CHATMESSAGE) {
                    if (data instanceof ChatMessage chatMessage) {
                        summary = chatMessage.getMessageType() + ": " + chatMessage.getMessage();
                    }
                } else if (method == ClientCallbackMethod.GAME_OVER) {
                    summary = "Game over";
                }
                context.logBridgeEvent(method, objectId, summary);
            }

            switch (method) {
                case START_GAME:
                    context.handleStartGame(objectId, data);
                    break;

                case GAME_INIT:
                    context.handleGameInit(data);
                    break;

                case GAME_UPDATE:
                case GAME_UPDATE_AND_INFORM:
                    context.logGameState(data);
                    break;

                case GAME_ASK:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_ASK");
                    break;

                case GAME_SELECT:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_SELECT");
                    break;

                case GAME_TARGET:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_TARGET");
                    break;

                case GAME_CHOOSE_ABILITY:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_ABILITY");
                    break;

                case GAME_CHOOSE_CHOICE:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_CHOICE");
                    break;

                case GAME_CHOOSE_PILE:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_CHOOSE_PILE");
                    break;

                case GAME_PLAY_MANA:
                case GAME_PLAY_XMANA:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction(method.name());
                    break;

                case GAME_GET_AMOUNT:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_GET_AMOUNT");
                    break;

                case GAME_GET_MULTI_AMOUNT:
                    context.storePendingAction(objectId, method, data);
                    actionableOutcome.storedPendingAction("GAME_GET_MULTI_AMOUNT");
                    break;

                case GAME_OVER:
                    context.handleGameOver(objectId, data);
                    break;

                case END_GAME_INFO:
                    context.handleEndGameInfo(objectId);
                    break;

                case CHATMESSAGE:
                    context.handleChatMessage(data);
                    break;

                case SERVER_MESSAGE:
                case GAME_ERROR:
                case GAME_INFORM_PERSONAL:
                case JOINED_TABLE:
                    context.logEvent(method, data);
                    break;

                case USER_REQUEST_DIALOG:
                    context.handleUserRequestDialog(data);
                    break;

                default:
                    context.logUnhandledCallback(method);
            }
            if (actionableOutcome != null) {
                actionableOutcome.verifyRecorded();
            }
        } catch (Exception e) {
            context.handleProcessorCallbackException(method, e, actionable);
        }
    }
}

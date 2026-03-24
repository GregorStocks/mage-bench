package mage.client.bridge.processor;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.choices.Choice;
import mage.client.bridge.BridgePromptFormatting;
import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;
import mage.constants.ManaType;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.remote.Session;
import mage.util.MultiAmountMessage;
import mage.view.AbilityPickerView;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import org.apache.log4j.Logger;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.function.Supplier;
import java.util.regex.Pattern;

public final class BridgeDecisionFlowService {
    private enum DecisionBoundaryStatus {
        READY,
        AUTO_HANDLED,
        CHANGED
    }

    private enum NonDecisionActionStatus {
        NOT_HANDLED,
        AUTO_HANDLED,
        CHANGED
    }

    private record DecisionBoundaryTransition(DecisionBoundaryStatus status, PendingAction action) {
    }

    private static final Pattern REGEX_WHITE = Pattern.compile("\\x7b.{0,2}W.{0,2}\\x7d");
    private static final Pattern REGEX_BLUE = Pattern.compile("\\x7b.{0,2}U.{0,2}\\x7d");
    private static final Pattern REGEX_BLACK = Pattern.compile("\\x7b.{0,2}B.{0,2}\\x7d");
    private static final Pattern REGEX_RED = Pattern.compile("\\x7b.{0,2}R.{0,2}\\x7d");
    private static final Pattern REGEX_GREEN = Pattern.compile("\\x7b.{0,2}G.{0,2}\\x7d");
    private static final Pattern REGEX_COLORLESS = Pattern.compile("\\x7b.{0,2}C.{0,2}\\x7d");
    private static final int MAX_POOL_MANA_ATTEMPTS = 10;

    private final String username;
    private final Logger logger;
    private final BridgeProcessorState processorState;
    private final BridgePublishedQueryBuilder publishedQueryBuilder;
    private final BridgeProcessorServices processorServices;
    private final Supplier<Session> sessionSupplier;
    private final Supplier<Boolean> clientRunningSupplier;
    private final Consumer<String> errorLogger;
    private final BridgeEventLogger eventLogger;

    public BridgeDecisionFlowService(
            String username,
            Logger logger,
            BridgeProcessorState processorState,
            BridgePublishedQueryBuilder publishedQueryBuilder,
            BridgeProcessorServices processorServices,
            Supplier<Session> sessionSupplier,
            Supplier<Boolean> clientRunningSupplier,
            Consumer<String> errorLogger,
            BridgeEventLogger eventLogger) {
        this.username = username;
        this.logger = logger;
        this.processorState = processorState;
        this.publishedQueryBuilder = publishedQueryBuilder;
        this.processorServices = processorServices;
        this.sessionSupplier = sessionSupplier;
        this.clientRunningSupplier = clientRunningSupplier;
        this.errorLogger = errorLogger;
        this.eventLogger = eventLogger;
    }

    public Map<String, Object> executeDefaultAction() {
        var result = new HashMap<String, Object>();
        PendingAction action = processorState.decisionState().pendingAction();
        if (action == null) {
            result.put("success", false);
            result.put("error", "No pending action");
            attachUnseenChat(result);
            return result;
        }

        processorState.decisionState().clearPendingActionIfCurrent(action);

        UUID gameId = action.gameId();
        ClientCallbackMethod method = action.method();
        Object data = action.data();

        result.put("success", true);
        result.put("action_type", method.name());

        switch (method) {
            case GAME_ASK, GAME_SELECT -> {
                sendBooleanOrDie(gameId, false, "defaultAction:" + method.name());
                result.put("action_taken", "passed_priority");
            }
            case GAME_PLAY_MANA, GAME_PLAY_XMANA -> {
                sendBooleanOrDie(gameId, false, "defaultAction:" + method.name());
                result.put("action_taken", "cancelled_mana");
            }
            case GAME_TARGET -> {
                GameClientMessage targetMsg = (GameClientMessage) data;
                boolean required = targetMsg.isFlag();
                Set<UUID> targets = findValidTargets(targetMsg);
                if (required && targets != null && !targets.isEmpty()) {
                    UUID firstTarget = selectDeterministicTarget(targets, null);
                    sendUuidOrDie(gameId, firstTarget, "defaultAction:GAME_TARGET");
                    result.put("action_taken", "selected_first_target");
                } else {
                    sendBooleanOrDie(gameId, false, "defaultAction:GAME_TARGET_cancel");
                    result.put("action_taken", "cancelled");
                }
            }
            case GAME_CHOOSE_ABILITY -> {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> abilityChoices = picker.getChoices();
                if (abilityChoices != null && !abilityChoices.isEmpty()) {
                    UUID firstChoice = abilityChoices.keySet().iterator().next();
                    sendUuidOrDie(gameId, firstChoice, "defaultAction:GAME_CHOOSE_ABILITY");
                    result.put("action_taken", "selected_first_ability");
                } else {
                    sendUuidOrDie(gameId, null, "defaultAction:GAME_CHOOSE_ABILITY_null");
                    result.put("action_taken", "no_abilities");
                }
            }
            case GAME_CHOOSE_CHOICE -> {
                GameClientMessage choiceMsg = (GameClientMessage) data;
                Choice choice = choiceMsg.getChoice();
                if (choice != null) {
                    if (choice.isKeyChoice()) {
                        Map<String, String> keyChoices = choice.getKeyChoices();
                        if (keyChoices != null && !keyChoices.isEmpty()) {
                            String firstKey = keyChoices.keySet().iterator().next();
                            sendStringOrDie(gameId, firstKey, "defaultAction:GAME_CHOOSE_CHOICE_key");
                            result.put("action_taken", "selected_first_key_choice");
                        } else {
                            sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                            result.put("action_taken", "no_choices");
                        }
                    } else {
                        Set<String> choices = choice.getChoices();
                        if (choices != null && !choices.isEmpty()) {
                            String firstChoice = choices.iterator().next();
                            sendStringOrDie(gameId, firstChoice, "defaultAction:GAME_CHOOSE_CHOICE");
                            result.put("action_taken", "selected_first_choice");
                        } else {
                            sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                            result.put("action_taken", "no_choices");
                        }
                    }
                } else {
                    sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                    result.put("action_taken", "null_choice");
                }
            }
            case GAME_CHOOSE_PILE -> {
                sendBooleanOrDie(gameId, true, "defaultAction:GAME_CHOOSE_PILE");
                result.put("action_taken", "selected_pile_1");
            }
            case GAME_GET_AMOUNT -> {
                GameClientMessage amountMsg = (GameClientMessage) data;
                int min = amountMsg.getMin();
                sendIntegerOrDie(gameId, min, "defaultAction:GAME_GET_AMOUNT");
                result.put("action_taken", "selected_min_amount");
                result.put("amount", min);
            }
            case GAME_GET_MULTI_AMOUNT -> {
                GameClientMessage multiMsg = (GameClientMessage) data;
                var sb = new StringBuilder();
                if (multiMsg.getMessages() != null) {
                    for (int i = 0; i < multiMsg.getMessages().size(); i++) {
                        if (i > 0) {
                            sb.append(" ");
                        }
                        sb.append(multiMsg.getMessages().get(i).defaultValue);
                    }
                }
                sendStringOrDie(gameId, sb.toString(), "defaultAction:GAME_GET_MULTI_AMOUNT");
                result.put("action_taken", "selected_default_multi_amount");
            }
            default -> {
                result.put("success", false);
                result.put("error", "Unknown action type: " + method);
            }
        }

        attachUnseenChat(result);
        return result;
    }

    public PendingAction currentDecisionAction() {
        while (true) {
            PendingAction action = processorState.decisionState().pendingAction();
            if (action == null) {
                return null;
            }
            DecisionBoundaryTransition transition =
                transitionToDecisionBoundary(action, "currentDecisionAction");
            if (transition.status() == DecisionBoundaryStatus.READY) {
                return transition.action();
            }
        }
    }

    public PendingAction resolvePassPriorityAction(PendingAction action) {
        DecisionBoundaryTransition transition =
            transitionToDecisionBoundary(action, "passPriority");
        return transition.status() == DecisionBoundaryStatus.READY ? transition.action() : null;
    }

    public GameView preparePassPriorityActionView(PendingAction action) {
        if (action.data() instanceof GameClientMessage gcm) {
            GameView gv = gcm.getGameView();
            if (gv != null) {
                updateLastGameView(gv, "passPriority:" + action.method().name());
                processorState.interactionState().advanceTurn(gv);
                return gv;
            }
        }
        return processorState.gameState().lastGameView();
    }

    public boolean requestCannotContinue() {
        return processorState.gameState().superseded()
            || processorState.gameState().playerDead()
            || processorState.gameState().gameOverObserved()
            || !clientRunning();
    }

    public ChooseActionTool.Result noPendingChooseActionResult() {
        var result = new ChooseActionTool.Result();
        return buildChooseActionError(result, "no_pending_action", "No pending action (game over or shutting down)", false, null);
    }

    public BridgeChooseActionStartResult applyChooseAction(
            BridgeChooseActionInput input,
            PendingAction action) {
        var result = new ChooseActionTool.Result();
        result.game_seq = action.gameSeq();
        Integer resolvedIndex = input.index();
        String[] effectiveManaPlan = input.manaPlan();
        String id = input.id();
        Boolean answer = input.answer();
        Integer amount = input.amount();
        int[] amounts = input.amounts();
        Integer pile = input.pile();
        String text = input.text();
        Boolean autoTap = input.autoTap();

        if (processorState.interactionState().interactionsThisTurn() > processorState.interactionState().maxInteractionsPerTurn()) {
            logger.warn("[" + username + "] Loop detected (" + processorState.interactionState().interactionsThisTurn()
                + " interactions this turn), auto-handling " + action.method().name());
            try {
                executeDefaultAction();
            } catch (BridgeResponseDeliveryException e) {
                result.success = false;
                result.error = e.getMessage();
                result.error_code = "response_delivery_failed";
                result.retryable = false;
                attachUnseenChat(result);
                return chooseActionDone(result);
            }
            result.success = true;
            result.action_taken = "auto_passed_loop_detected";
            result.warning = "Too many interactions this turn (" + processorState.interactionState().interactionsThisTurn() + "). Auto-passing until next turn.";
            return chooseActionDone(result);
        }

        ClientCallbackMethod method = action.method();

        if (id != null && method != ClientCallbackMethod.GAME_CHOOSE_CHOICE) {
            if (resolvedIndex != null) {
                logger.warn("[" + username + "] choose_action: both id=" + id + " and index=" + resolvedIndex + " provided, preferring id");
                result.warning = "Both id and index provided; used id=" + id + ", ignored index=" + resolvedIndex;
                resolvedIndex = null;
            }
            List<Object> choices = processorState.decisionState().lastChoices();
            if (choices == null) {
                buildActionChoices(action, null);
                choices = processorState.decisionState().lastChoices();
            }
            if ("all".equals(id)) {
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if ("special".equals(choices.get(i))) {
                            resolvedIndex = i;
                            break;
                        }
                    }
                }
                if (resolvedIndex == null) {
                    return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                        "\"all\" is not available in current choices", true, action, true));
                }
            } else {
                UUID resolvedUuid = processorServices.shortIds().tryResolve(id);
                if (resolvedUuid == null) {
                    return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                        "Unknown short ID: " + id + ". Call get_action_choices to see current options.",
                        true, action, true));
                }
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if (resolvedUuid.equals(choices.get(i))) {
                            resolvedIndex = i;
                            break;
                        }
                    }
                }
                if (resolvedIndex == null) {
                    return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                        "Object " + id + " not found in current choices", true, action, true));
                }
            }
        }

        if (effectiveManaPlan != null && effectiveManaPlan.length == 0) {
            effectiveManaPlan = null;
        }

        if (resolvedIndex != null && processorState.decisionState().lastChoices() == null) {
            logger.info("[" + username + "] choose_action: auto-populating choices (get_action_choices was not called)");
            buildActionChoices(action, null);
        }

        processorState.decisionState().clearPendingActionIfCurrent(action);

        UUID gameId = action.gameId();
        Object data = action.data();
        result.success = true;

        try {
            switch (method) {
                case GAME_ASK -> {
                    if (answer == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "GAME_ASK requires choice=\"yes\" or choice=\"no\". This is a yes/no question.", true, action));
                    }
                    if (resolvedIndex != null) {
                        logger.warn("[" + username + "] choose_action: ignoring index=" + resolvedIndex + " for GAME_ASK (boolean-only)");
                    }
                    sendBooleanOrDie(gameId, answer, "chooseAction:GAME_ASK");
                    result.action_taken = answer ? "yes" : "no";
                }
                case GAME_SELECT -> {
                    boolean usedIndex = false;
                    if (resolvedIndex != null) {
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                            if (answer != null) {
                                logger.warn("[" + username + "] choose_action: index " + resolvedIndex
                                    + " out of range, falling through to answer=" + answer + " for GAME_SELECT");
                            } else {
                                return chooseActionDone(buildChooseActionError(result, "index_out_of_range",
                                    "Index " + resolvedIndex + " is out of range"
                                        + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                        + ". Call get_action_choices to see current options.", true, action, true));
                            }
                        } else {
                            Object chosen = choices.get(resolvedIndex);
                            if (chosen instanceof UUID chosenUuid) {
                                if (effectiveManaPlan != null) {
                                    CopyOnWriteArrayList<BridgeManaPlanEntry> parsedPlan;
                                    try {
                                        parsedPlan = parseManaPlan(effectiveManaPlan);
                                    } catch (IllegalArgumentException e) {
                                        return chooseActionDone(buildChooseActionError(result, "invalid_mana_plan",
                                            "Invalid mana_plan: " + e.getMessage()
                                                + ". Expected: [\"p1\",\"p2:0\",\"RED\"]", true, action));
                                    }
                                    for (BridgeManaPlanEntry entry : parsedPlan) {
                                        if ("tap".equals(entry.type()) && processorServices.shortIds().tryResolve(entry.value()) == null) {
                                            return chooseActionDone(buildChooseActionError(result, "invalid_mana_plan",
                                                "Mana plan references unknown permanent '" + entry.value()
                                                    + "'. Check the board state for correct permanent IDs.", true, action));
                                        }
                                    }
                                    processorState.interactionState().setManaPlan(parsedPlan, !(autoTap != null && !autoTap));
                                    result.mana_plan_set = true;
                                    result.mana_plan_size = parsedPlan.size();
                                } else if (autoTap != null && autoTap) {
                                    processorState.interactionState().clearManaPlan();
                                }
                                sendUuidOrDie(gameId, chosenUuid, "chooseAction:GAME_SELECT_index");
                                result.action_taken = "selected_" + resolvedIndex;
                                usedIndex = true;
                            } else if (chosen instanceof String chosenStr) {
                                sendStringOrDie(gameId, chosenStr, "chooseAction:GAME_SELECT_special");
                                result.action_taken = "special_" + chosenStr;
                                usedIndex = true;
                            } else {
                                return chooseActionDone(buildChooseActionError(result, "internal_error",
                                    "Unexpected choice type at index " + resolvedIndex, false, action));
                            }
                        }
                    }
                    if (!usedIndex) {
                        if (answer != null) {
                            sendBooleanOrDie(gameId, answer, "chooseAction:GAME_SELECT_answer");
                            result.action_taken = answer ? "confirmed" : "passed_priority";
                        } else {
                            return chooseActionDone(buildChooseActionError(result, "missing_param",
                                "GAME_SELECT requires choice=pN to play a card, or choice=\"no\" to pass priority. Call get_action_choices first to see available cards.",
                                true, action, true));
                        }
                    }
                }
                case GAME_PLAY_MANA, GAME_PLAY_XMANA -> {
                    boolean usedManaIndex = false;
                    if (resolvedIndex != null) {
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                            if (answer != null && !answer) {
                                logger.warn("[" + username + "] choose_action: index " + resolvedIndex
                                    + " out of range, falling through to cancel for GAME_PLAY_MANA");
                            } else {
                                return chooseActionDone(buildChooseActionError(result, "index_out_of_range",
                                    "Index " + resolvedIndex + " is out of range"
                                        + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                        + ". Call get_action_choices to see current options.", true, action, true));
                            }
                        } else {
                            Object manaChoice = choices.get(resolvedIndex);
                            if (manaChoice instanceof UUID manaUuid) {
                                sendUuidOrDie(gameId, manaUuid, "chooseAction:GAME_PLAY_MANA");
                                result.action_taken = "tapped_mana_" + resolvedIndex;
                                usedManaIndex = true;
                            } else if (manaChoice instanceof ManaType manaType) {
                                UUID manaPlayerId = getManaPoolPlayerId(gameId, processorState.gameState().lastGameView());
                                if (manaPlayerId == null) {
                                    return chooseActionDone(buildChooseActionError(result, "internal_error",
                                        "Could not resolve player ID for mana pool selection", false, action));
                                }
                                sendManaTypeOrDie(gameId, manaPlayerId, manaType, "chooseAction:GAME_PLAY_MANA_pool");
                                result.action_taken = "used_pool_" + manaType;
                                usedManaIndex = true;
                            } else {
                                return chooseActionDone(buildChooseActionError(result, "internal_error",
                                    "Unsupported mana choice type at index " + resolvedIndex, false, action));
                            }
                        }
                    }
                    if (!usedManaIndex) {
                        boolean cancel = false;
                        if (answer != null && !answer) {
                            cancel = true;
                        } else if (answer != null && answer) {
                            List<Object> choices = processorState.decisionState().lastChoices();
                            if (choices == null || choices.isEmpty()) {
                                logger.warn("[" + username + "] choose_action: answer=true for GAME_PLAY_MANA with no mana sources, auto-cancelling");
                                cancel = true;
                            }
                        }
                        if (cancel) {
                            UUID payingForId = extractPayingForId(action.message());
                            if (payingForId != null) {
                                processorState.interactionState().markFailedManaCast(payingForId);
                            }
                            processorState.interactionState().clearManaPlan();
                            sendBooleanOrDie(gameId, false, "chooseAction:GAME_PLAY_MANA_cancel");
                            result.action_taken = "cancelled_spell";
                        } else {
                            return chooseActionDone(buildChooseActionError(result, "missing_param",
                                "GAME_PLAY_MANA requires choice=pN to choose a mana source, or choice=\"no\" to cancel the spell. Call get_action_choices first to see available mana sources.",
                                true, action, true));
                        }
                    }
                }
                case GAME_TARGET -> {
                    GameClientMessage targetMsg = (GameClientMessage) data;
                    boolean required = targetMsg.isFlag();

                    if (resolvedIndex != null) {
                        if (answer != null) {
                            logger.warn("[" + username + "] choose_action: ignoring answer=" + answer + " because index was also provided for GAME_TARGET");
                        }
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices != null && resolvedIndex >= 0 && resolvedIndex < choices.size()) {
                            UUID targetUUID = (UUID) choices.get(resolvedIndex);
                            sendUuidOrDie(gameId, targetUUID, "chooseAction:GAME_TARGET_index");
                            result.action_taken = "selected_target_" + resolvedIndex;
                            break;
                        }
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                        if (!required) {
                            List<Object> targetChoices = processorState.decisionState().lastChoices();
                            return chooseActionDone(buildChooseActionError(result, "index_out_of_range",
                                "Index " + resolvedIndex + " is out of range"
                                    + (targetChoices != null ? " (valid: 0-" + (targetChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current targets.", true, action, true));
                        }
                        logger.warn("[" + username + "] choose_action: index " + resolvedIndex
                            + " out of range for required GAME_TARGET (choices="
                            + (choices == null ? "null" : choices.size()) + "), auto-selecting");
                    } else if (answer != null && !answer) {
                        if (!required) {
                            sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_cancel");
                            result.action_taken = "cancelled";
                            break;
                        }
                        logger.warn("[" + username + "] choose_action: answer=false invalid for required GAME_TARGET, auto-selecting");
                    } else if (!required) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "GAME_TARGET requires choice=pN to select a target, or choice=\"no\" to cancel targeting. Call get_action_choices first to see available targets.",
                            true, action, true));
                    }

                    Set<UUID> autoTargets = findValidTargets(targetMsg);
                    if (autoTargets != null && !autoTargets.isEmpty()) {
                        UUID firstTarget = selectDeterministicTarget(autoTargets, processorState.decisionState().lastChoices());
                        logger.warn("[" + username + "] choose_action: auto-selecting first target for required GAME_TARGET");
                        sendUuidOrDie(gameId, firstTarget, "chooseAction:GAME_TARGET_auto_select");
                        result.action_taken = "auto_selected_required_target";
                        result.warning = "Required target auto-selected. Use get_action_choices first, then index=N.";
                    } else {
                        logger.error("[" + username + "] Required GAME_TARGET has no valid targets — cancelling to avoid infinite loop");
                        sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_no_valid");
                        result.action_taken = "cancelled_no_valid_targets";
                    }
                }
                case GAME_CHOOSE_ABILITY -> {
                    if (resolvedIndex == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "GAME_CHOOSE_ABILITY requires index=N. Call get_action_choices first to see the available abilities, then choose_action with the index of the one you want.",
                            true, action, true));
                    }
                    List<Object> abilityChoices = processorState.decisionState().lastChoices();
                    if (abilityChoices == null || resolvedIndex < 0 || resolvedIndex >= abilityChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, abilityChoices);
                        return chooseActionDone(buildChooseActionError(result, "index_out_of_range",
                            "Index " + resolvedIndex + " is out of range"
                                + (abilityChoices != null ? " (valid: 0-" + (abilityChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                + ". Call get_action_choices to see current options.", true, action, true));
                    }
                    UUID abilityUUID = (UUID) abilityChoices.get(resolvedIndex);
                    sendUuidOrDie(gameId, abilityUUID, "chooseAction:GAME_CHOOSE_ABILITY");
                    result.action_taken = "selected_ability_" + resolvedIndex;
                }
                case GAME_CHOOSE_CHOICE -> {
                    if (text != null && !text.isEmpty()) {
                        GameClientMessage choiceMsg = (GameClientMessage) data;
                        Choice choiceObj = choiceMsg.getChoice();
                        if (choiceObj == null) {
                            return chooseActionDone(buildChooseActionError(result, "internal_error", "No choice available", false, action));
                        }
                        if (choiceObj.isKeyChoice()) {
                            Map<String, String> keyChoices = choiceObj.getKeyChoices();
                            String matchedKey = null;
                            if (keyChoices != null) {
                                for (Map.Entry<String, String> entry : keyChoices.entrySet()) {
                                    if (entry.getValue().equalsIgnoreCase(text) || entry.getKey().equalsIgnoreCase(text)) {
                                        matchedKey = entry.getKey();
                                        break;
                                    }
                                }
                            }
                            if (matchedKey == null) {
                                return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true));
                            }
                            sendStringOrDie(gameId, matchedKey, "chooseAction:GAME_CHOOSE_CHOICE_key");
                        } else {
                            Set<String> choices = choiceObj.getChoices();
                            String matched = null;
                            if (choices != null) {
                                for (String choice : choices) {
                                    if (choice.equalsIgnoreCase(text)) {
                                        matched = choice;
                                        break;
                                    }
                                }
                            }
                            if (matched == null) {
                                return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true));
                            }
                            sendStringOrDie(gameId, matched, "chooseAction:GAME_CHOOSE_CHOICE");
                        }
                        result.action_taken = "selected_choice_text_" + text;
                        break;
                    }
                    if (id != null && !id.isEmpty()) {
                        return chooseActionDone(buildChooseActionError(result, "invalid_choice",
                            "GAME_CHOOSE_CHOICE does not accept choice=\"" + id + "\" by name. Use text=\"" + id + "\" or choice=N with the current options.",
                            true, action, true));
                    }
                    if (resolvedIndex == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "Integer 'index' or string 'text' required for GAME_CHOOSE_CHOICE", true, action, true));
                    }
                    List<Object> choiceChoices = processorState.decisionState().lastChoices();
                    if (choiceChoices == null || resolvedIndex < 0 || resolvedIndex >= choiceChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choiceChoices);
                        return chooseActionDone(buildChooseActionError(result, "index_out_of_range",
                            "Index " + resolvedIndex + " is out of range"
                                + (choiceChoices != null ? " (valid: 0-" + (choiceChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                + ". Call get_action_choices to see current options.", true, action, true));
                    }
                    String choiceStr = (String) choiceChoices.get(resolvedIndex);
                    sendStringOrDie(gameId, choiceStr, "chooseAction:GAME_CHOOSE_CHOICE_index");
                    result.action_taken = "selected_choice_" + resolvedIndex;
                }
                case GAME_CHOOSE_PILE -> {
                    if (pile == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "Integer 'pile' (1 or 2) required for GAME_CHOOSE_PILE", true, action));
                    }
                    boolean pileChoice = pile == 1;
                    sendBooleanOrDie(gameId, pileChoice, "chooseAction:GAME_CHOOSE_PILE");
                    result.action_taken = "selected_pile_" + pile;
                }
                case GAME_GET_AMOUNT -> {
                    if (amount == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "Integer 'amount' required for GAME_GET_AMOUNT", true, action));
                    }
                    GameClientMessage msg = (GameClientMessage) data;
                    int clamped = Math.max(msg.getMin(), Math.min(msg.getMax(), amount));
                    sendIntegerOrDie(gameId, clamped, "chooseAction:GAME_GET_AMOUNT");
                    result.action_taken = "amount_" + clamped;
                }
                case GAME_GET_MULTI_AMOUNT -> {
                    if (amounts == null) {
                        return chooseActionDone(buildChooseActionError(result, "missing_param",
                            "Array 'amounts' required for GAME_GET_MULTI_AMOUNT", true, action));
                    }
                    GameClientMessage msg = (GameClientMessage) data;
                    String validationError = validateMultiAmountInput(msg, amounts);
                    if (validationError != null) {
                        return chooseActionDone(buildChooseActionError(result, "invalid_multi_amount", validationError, true, action));
                    }
                    var sb = new StringBuilder();
                    for (int i = 0; i < amounts.length; i++) {
                        if (i > 0) {
                            sb.append(" ");
                        }
                        sb.append(amounts[i]);
                    }
                    sendStringOrDie(gameId, sb.toString(), "chooseAction:GAME_GET_MULTI_AMOUNT");
                    result.action_taken = "multi_amount";
                }
                default -> {
                    return chooseActionDone(buildChooseActionError(result, "unknown_action_type", "Unknown action type: " + method, false, null));
                }
            }
        } catch (BridgeResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            attachUnseenChat(result);
            return chooseActionDone(result);
        } finally {
            processorState.decisionState().clearLastChoices();
            if (Boolean.FALSE.equals(result.success)) {
                logger.warn("[" + username + "] choose_action failed: " + result.error);
            }
        }

        return chooseActionAwaitNextDecision(result);
    }

    public ChooseActionTool.Result chooseActionDeliveryErrorResult(String message) {
        var result = new ChooseActionTool.Result();
        result.success = false;
        result.error = message;
        result.error_code = "response_delivery_failed";
        result.retryable = false;
        attachUnseenChat(result);
        return result;
    }

    public boolean clearPendingActionIfCurrent(PendingAction action) {
        return processorState.decisionState().clearPendingActionIfCurrent(action);
    }

    public String detectCombatSelect(PendingAction action) {
        if (action == null || action.method() != ClientCallbackMethod.GAME_SELECT) {
            return null;
        }
        Object data = action.data();
        if (data instanceof GameClientMessage gameClientMessage) {
            Map<String, Serializable> options = gameClientMessage.getOptions();
            if (options != null) {
                if (options.containsKey("possibleAttackers")) {
                    return "attackers";
                }
                if (options.containsKey("possibleBlockers")) {
                    return "blockers";
                }
            }
        }
        return null;
    }

    public UUID resolveShortId(String shortId) {
        return processorServices.shortIds().resolve(shortId);
    }

    public Set<UUID> validTargets(PendingAction action) {
        if (!(action.data() instanceof GameClientMessage targetMsg)) {
            return null;
        }
        return findValidTargets(targetMsg);
    }

    public ChooseActionTool.Result buildChooseActionError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action) {
        return buildChooseActionError(result, errorCode, message, retryable, action, false);
    }

    public void finishChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction,
            PendingAction nextAction) {
        result.game_seq = nextAction.gameSeq();
        mergeActionChoices(result, null, nextAction);
        String summary = "after=" + summarizePendingAction(previousAction)
            + ",woke_to=" + summarizePendingAction(nextAction)
            + ",gameOver=" + processorState.gameState().gameOverObserved();
        logger.info("[" + username + "] chooseAction wakeup: " + summary);
        eventLogger.log("CHOOSE_ACTION_WAKEUP", nextAction.gameId(), summary);
    }

    public void finishChooseActionWithoutNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction) {
        String summary = "after=" + summarizePendingAction(previousAction)
            + ",woke_to=game_over"
            + ",playerDead=" + processorState.gameState().playerDead()
            + ",activeGame=" + processorState.gameState().hasActiveGame()
            + ",clientRunning=" + clientRunning();
        logger.info("[" + username + "] chooseAction wakeup: " + summary);
        eventLogger.log("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
        attachUnseenChat(result);
    }

    public void finishBatchChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction nextAction) {
        result.game_seq = nextAction.gameSeq();
        mergeActionChoices(result, null, nextAction);
    }

    public void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result) {
        attachUnseenChat(result);
    }

    public ChooseActionTool.Result cancelledChooseActionResult(
            PendingAction previousAction,
            ChooseActionTool.Result partialResult) {
        ChooseActionTool.Result result = partialResult != null ? partialResult : new ChooseActionTool.Result();
        result.success = false;
        result.error = "Cancelled while waiting for choose_action";
        result.error_code = "cancelled";
        result.retryable = false;
        attachUnseenChat(result);
        if (previousAction != null) {
            String summary = "after=" + summarizePendingAction(previousAction) + ",woke_to=cancelled";
            logger.info("[" + username + "] chooseAction wakeup: " + summary);
            eventLogger.log("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
        }
        return result;
    }

    public ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam) {
        return pendingActionResult(action, stopReason, boardCursorParam, null);
    }

    public ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam,
            Consumer<ActionResult> customizer) {
        var result = new ActionResult();
        result.action_pending = true;
        result.action_type = action.method().name();
        result.game_seq = action.gameSeq();
        result.stop_reason = stopReason;
        if (customizer != null) {
            customizer.accept(result);
        }
        attachUnseenChat(result);
        mergeActionChoices(result, boardCursorParam, action);
        return result;
    }

    public ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
        return pendingActionResult(action, "stack_resolved", boardCursorParam);
    }

    public ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam) {
        return pendingActionResult(action, stopReason, boardCursorParam, result -> {
            if (gameView != null && gameView.getStep() != null) {
                result.current_step = gameView.getStep().toString();
            }
        });
    }

    public UUID lowestStackObjectId(GameView gameView) {
        if (gameView == null || gameView.getStack() == null || gameView.getStack().isEmpty()) {
            return null;
        }
        UUID lowest = null;
        for (UUID stackObjectId : gameView.getStack().keySet()) {
            lowest = stackObjectId;
        }
        return lowest;
    }

    public boolean stackContains(GameView gameView, UUID stackObjectId) {
        return gameView != null
            && gameView.getStack() != null
            && stackObjectId != null
            && gameView.getStack().containsKey(stackObjectId);
    }

    public void finalizePassPriorityResult(
            BridgePassPriorityFlow flow,
            String until,
            int actionsPassed,
            PendingAction action,
            GameView view,
            ActionResult result,
            boolean actionPending) {
        processorState.decisionState().clearPendingPassPriorityFlowIfCurrent(flow);
        attachUnseenChat(result);
        logPassPriorityReturn(until, actionsPassed, action, view, result, actionPending);
    }

    public void attachUnseenChat(Map<String, Object> result) {
        processorState.gameLogState().attachUnseenChat(
            result,
            processorState.gameState().playerDead(),
            processorState.gameState().gameOverObserved()
        );
    }

    public void attachUnseenChat(ActionResult result) {
        processorState.gameLogState().attachUnseenChat(
            result,
            processorState.gameState().playerDead(),
            processorState.gameState().gameOverObserved()
        );
    }

    public String username() {
        return username;
    }

    public int interactionsThisTurn() {
        return processorState.interactionState().interactionsThisTurn();
    }

    public int maxInteractionsPerTurn() {
        return processorState.interactionState().maxInteractionsPerTurn();
    }

    public int lastTurnNumber() {
        return processorState.interactionState().lastTurnNumber();
    }

    public UUID currentGameId() {
        return processorState.gameState().currentGameId();
    }

    public GameView lastGameView() {
        return processorState.gameState().lastGameView();
    }

    public boolean hasActiveGame() {
        return processorState.gameState().hasActiveGame();
    }

    public boolean superseded() {
        return processorState.gameState().superseded();
    }

    public boolean playerDead() {
        return processorState.gameState().playerDead();
    }

    public boolean gameEverStarted() {
        return processorState.gameState().gameEverStarted();
    }

    public boolean clientRunning() {
        return clientRunningSupplier.get();
    }

    public long lastActionableCallbackAt() {
        return processorState.gameState().lastActionableCallbackAt();
    }

    public long lastCallbackReceivedAt() {
        return processorState.gameState().lastCallbackReceivedAt();
    }

    public void declareZombieGame(long absoluteIdleMs) {
        logger.error("[" + username + "] Zombie game detected: no actionable callback for "
            + absoluteIdleMs + "ms, declaring game dead");
        errorLogger.accept("Zombie game detected: no actionable callback for " + absoluteIdleMs + "ms");
        processorState.gameState().markPlayerDead();
    }

    public boolean failedManaCast(UUID objectId) {
        return processorState.interactionState().failedManaCast(objectId);
    }

    public static String validateMultiAmountInput(GameClientMessage msg, int[] amounts) {
        List<MultiAmountMessage> items = msg.getMessages();
        if (items == null) {
            throw new IllegalStateException("GAME_GET_MULTI_AMOUNT is missing item metadata");
        }

        int expectedCount = items.size();
        String expectedEntries = expectedCount + " " + (expectedCount == 1 ? "entry" : "entries");
        String expectedShape = "Expected " + expectedCount + " amount"
            + (expectedCount == 1 ? "" : "s")
            + " and total " + formatAmountRange(msg.getMin(), msg.getMax()) + ".";

        if (amounts.length != expectedCount) {
            return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: expected " + expectedEntries
                + ", got " + amounts.length + ". " + expectedShape;
        }

        long total = 0;
        for (int i = 0; i < expectedCount; i++) {
            MultiAmountMessage item = items.get(i);
            int value = amounts[i];
            total += value;
            if (value < item.min || value > item.max) {
                return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: amounts[" + i + "]=" + value
                    + " is outside item range " + formatAmountRange(item.min, item.max)
                    + ". " + expectedShape;
            }
        }

        if (total < msg.getMin() || total > msg.getMax()) {
            return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: total " + total
                + " is outside allowed range " + formatAmountRange(msg.getMin(), msg.getMax())
                + ". " + expectedShape;
        }

        return null;
    }

    private ChooseActionTool.Result buildChooseActionError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action,
            boolean attachChoices) {
        result.success = false;
        result.error = message;
        result.error_code = errorCode;
        result.retryable = retryable;
        processorState.decisionState().restorePendingAction(action);
        if (attachChoices) {
            attachChoicesToError(result, action);
        }
        attachUnseenChat(result);
        return result;
    }

    private void attachChoicesToError(ChooseActionTool.Result errorResult, PendingAction action) {
        ActionResult choicesResult = buildActionChoices(action, null);
        if (choicesResult.choices != null) {
            errorResult.choices = choicesResult.choices;
        }
    }

    private ActionResult buildActionChoices(PendingAction action, Long boardCursorParam) {
        return publishedQueryBuilder.buildActionChoices(action, boardCursorParam);
    }

    private void mergeActionChoices(ActionResult result, Long boardCursorParam, PendingAction action) {
        ActionResult choices = buildActionChoices(action, boardCursorParam);
        if (!Boolean.TRUE.equals(choices.action_pending)) {
            result.warning = "Action changed before choices were fetched";
            return;
        }
        result.mergeFrom(choices);
    }

    private DecisionBoundaryTransition transitionToDecisionBoundary(PendingAction action, String source) {
        if (action == null) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        NonDecisionActionStatus nonDecisionStatus = maybeAutoHandleNonDecisionAction(action, source);
        if (nonDecisionStatus == NonDecisionActionStatus.AUTO_HANDLED) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.AUTO_HANDLED, null);
        }
        if (nonDecisionStatus == NonDecisionActionStatus.CHANGED) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        if (processorState.decisionState().pendingAction() != action) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        return new DecisionBoundaryTransition(DecisionBoundaryStatus.READY, action);
    }

    private NonDecisionActionStatus maybeAutoHandleNonDecisionAction(PendingAction action, String source) {
        if (action.method() == ClientCallbackMethod.GAME_PLAY_MANA
                || action.method() == ClientCallbackMethod.GAME_PLAY_XMANA) {
            return maybeAutoHandlePendingManaAction(action, source);
        }
        if (action.method() != ClientCallbackMethod.GAME_TARGET
                && action.method() != ClientCallbackMethod.GAME_CHOOSE_ABILITY) {
            return NonDecisionActionStatus.NOT_HANDLED;
        }
        if (action.method() == ClientCallbackMethod.GAME_TARGET) {
            return maybeAutoHandleGameTarget(action, source);
        }
        return maybeAutoHandleGameChooseAbility(action, source);
    }

    private NonDecisionActionStatus maybeAutoHandleGameTarget(PendingAction action, String source) {
        GameClientMessage targetMsg = (GameClientMessage) action.data();
        Set<UUID> targets = findValidTargets(targetMsg);
        boolean required = targetMsg.isFlag();

        if (!required && (targets == null || targets.isEmpty())) {
            if (clearPendingActionIfCurrent(action)) {
                logger.info("[" + username + "] " + source
                    + ": auto-cancelling optional GAME_TARGET with no valid targets");
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendBooleanOrDie(action.gameId(), false, "auto-cancel optional GAME_TARGET");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        UUID onlyTarget = selectSingleRequiredTarget(targetMsg);
        if (onlyTarget == null) {
            return NonDecisionActionStatus.NOT_HANDLED;
        }

        if (clearPendingActionIfCurrent(action)) {
            logger.info("[" + username + "] " + source
                + ": auto-selecting single required GAME_TARGET " + onlyTarget.toString().substring(0, 8));
            GameView gameView = targetMsg.getGameView();
            updateLastGameView(gameView, source + ":single_required_target");
            processorState.decisionState().clearLastChoices();
            processorState.decisionState().clearChoiceSnapshot();
            sendUuidOrDie(action.gameId(), onlyTarget, "auto-select single required GAME_TARGET");
            return NonDecisionActionStatus.AUTO_HANDLED;
        }
        return processorState.decisionState().pendingAction() != action
            ? NonDecisionActionStatus.CHANGED
            : NonDecisionActionStatus.NOT_HANDLED;
    }

    private NonDecisionActionStatus maybeAutoHandlePendingManaAction(PendingAction action, String source) {
        if (!clearPendingActionIfCurrent(action)) {
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        try {
            processorState.decisionState().clearLastChoices();
            processorState.decisionState().clearChoiceSnapshot();
            boolean handled = handleGamePlayManaAuto(action.gameId(), (GameClientMessage) action.data());
            if (handled) {
                logger.info("[" + username + "] " + source
                    + ": auto-resolved pending " + action.method().name() + " at decision boundary");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
        } catch (BridgeResponseDeliveryException e) {
            throw e;
        } catch (Exception e) {
            errorLogger.accept("Pending mana auto-handler exception: " + e.getMessage());
            logger.debug("[" + username + "] Pending mana auto-handler stack trace", e);
        }

        processorState.decisionState().restorePendingActionIfEmpty(action);
        return processorState.decisionState().pendingAction() != action
            ? NonDecisionActionStatus.CHANGED
            : NonDecisionActionStatus.NOT_HANDLED;
    }

    private NonDecisionActionStatus maybeAutoHandleGameChooseAbility(PendingAction action, String source) {
        AbilityPickerView picker = (AbilityPickerView) action.data();
        Map<UUID, String> choices = picker.getChoices();

        if (choices == null || choices.isEmpty()) {
            if (clearPendingActionIfCurrent(action)) {
                logger.warn("[" + username + "] " + source
                    + ": auto-selecting ability: no choices, sending null");
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), null, "auto GAME_CHOOSE_ABILITY null_choice");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        if (processorState.interactionState().manaPlan() != null) {
            if (clearPendingActionIfCurrent(action)) {
                Integer abilityIdx = processorState.interactionState().consumeManaPlanAbilityIndex();
                UUID selected;
                if (abilityIdx != null) {
                    List<UUID> abilityIds = new ArrayList<>(choices.keySet());
                    if (abilityIdx >= 0 && abilityIdx < abilityIds.size()) {
                        selected = abilityIds.get(abilityIdx);
                        logger.info("[" + username + "] " + source
                            + ": mana plan selecting ability " + abilityIdx + ": \""
                            + picker.getMessage() + "\" -> " + choices.get(selected));
                    } else {
                        logger.warn("[" + username + "] " + source
                            + ": mana plan ability index " + abilityIdx
                            + " out of range (0-" + (abilityIds.size() - 1) + ") for \""
                            + picker.getMessage() + "\", cancelling spell");
                        processorState.interactionState().clearManaPlan();
                        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — mana plan ability index was incorrect.");
                        eventLogger.log("SPELL_CANCELLED", processorState.gameState().currentGameId(), "mana plan ability index out of range");
                        processorState.decisionState().clearLastChoices();
                        processorState.decisionState().clearChoiceSnapshot();
                        sendUuidOrDie(action.gameId(), null, "auto GAME_CHOOSE_ABILITY bad_mana_plan");
                        return NonDecisionActionStatus.AUTO_HANDLED;
                    }
                } else {
                    selected = choices.keySet().iterator().next();
                    if (choices.size() == 1) {
                        logger.info("[" + username + "] " + source
                            + ": mana plan auto-selecting sole ability: \""
                            + picker.getMessage() + "\" -> " + choices.get(selected));
                    } else {
                        logger.info("[" + username + "] " + source
                            + ": mana plan no ability index, picking first of "
                            + choices.size() + ": \"" + picker.getMessage()
                            + "\" -> " + choices.get(selected));
                    }
                }
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), selected, "auto GAME_CHOOSE_ABILITY mana_plan");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        return NonDecisionActionStatus.NOT_HANDLED;
    }

    private void logChoiceOutOfRangeDiagnostic(ClientCallbackMethod method, Integer index, List<Object> choices) {
        long generatedAtMs = processorState.decisionState().lastChoicesGeneratedAtMs();
        long ageMs = generatedAtMs == 0 ? -1 : System.currentTimeMillis() - generatedAtMs;
        PendingAction nowPending = processorState.decisionState().pendingAction();
        String nowPendingType = nowPending == null ? "none" : nowPending.method().name();
        logger.warn("[" + username + "] choose_action out-of-range diagnostic: "
            + "method=" + method.name()
            + ", index=" + index
            + ", choices_size=" + (choices == null ? -1 : choices.size())
            + ", pending_now=" + nowPendingType
            + ", last_choices_action=" + (processorState.decisionState().lastChoicesActionType() == null ? "none" : processorState.decisionState().lastChoicesActionType())
            + ", last_choices_response="
            + (processorState.decisionState().lastChoicesResponseType() == null ? "none" : processorState.decisionState().lastChoicesResponseType())
            + ", last_choices_count=" + processorState.decisionState().lastChoicesCount()
            + ", last_choices_age_ms=" + ageMs);
    }

    private void logPassPriorityReturn(
            String until,
            int actionsPassed,
            PendingAction action,
            GameView gameView,
            ActionResult result,
            boolean returnedChoices) {
        String actionMethod = action != null ? action.method().name() : "none";
        String actionGameId = action != null ? String.valueOf(action.gameId()) : "null";
        int callbackGameSeq = action != null ? action.gameSeq() : -1;
        String step = gameViewStep(gameView);
        String summary = "until=" + until
            + ",stop_reason=" + result.stop_reason
            + ",actionsPassed=" + actionsPassed
            + ",callbackMethod=" + actionMethod
            + ",callbackGameId=" + actionGameId
            + ",callbackGameSeq=" + callbackGameSeq
            + ",step=" + step
            + ",autoPassedBeforeReturn=" + (actionsPassed > 0)
            + ",returnedChoices=" + returnedChoices
            + ",pendingAction=" + summarizePendingAction(processorState.decisionState().pendingAction());
        logger.info("[" + username + "] passPriority RETURN: " + summary);
        eventLogger.log(
            "PASS_PRIORITY_RETURN",
            action != null ? action.gameId() : processorState.gameState().currentGameId(),
            summary
        );
    }

    private String summarizePendingAction(PendingAction action) {
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

    private String gameViewStep(GameView gameView) {
        if (gameView == null || gameView.getStep() == null) {
            return "null";
        }
        return gameView.getStep().toString();
    }

    private static String formatAmountRange(int min, int max) {
        if (min == max) {
            return Integer.toString(min);
        }
        return min + "-" + max;
    }

    private static BridgeChooseActionStartResult chooseActionDone(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, false);
    }

    private static BridgeChooseActionStartResult chooseActionAwaitNextDecision(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, true);
    }

    private void updateLastGameView(GameView gameView, String source) {
        processorState.gameState().updateLastGameView(gameView, source, logger, username);
    }

    private void sendIntegerOrDie(UUID gameId, int data, String context) {
        boolean ok = sessionSupplier.get().sendPlayerInteger(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerInteger(" + data + ")", context, gameId);
        }
    }

    public void sendBooleanOrDie(UUID gameId, boolean data, String context) {
        boolean ok = sessionSupplier.get().sendPlayerBoolean(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerBoolean(" + data + ")", context, gameId);
        }
    }

    public void sendUuidOrDie(UUID gameId, UUID data, String context) {
        boolean ok = sessionSupplier.get().sendPlayerUUID(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerUUID(" + data + ")", context, gameId);
        }
    }

    public void sendStringOrDie(UUID gameId, String data, String context) {
        boolean ok = sessionSupplier.get().sendPlayerString(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerString(" + data + ")", context, gameId);
        }
    }

    private void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType data, String context) {
        boolean ok = sessionSupplier.get().sendPlayerManaType(gameId, playerId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerManaType(" + data + ")", context, gameId);
        }
    }

    private void declareResponseFailed(String call, String context, UUID gameId) {
        String msg = call + " failed — server did not receive response"
            + " (context=" + context + ", gameId=" + gameId + ")";
        logger.error("[" + username + "] CRITICAL: " + msg);
        errorLogger.accept(msg);
        processorState.gameState().markPlayerDead();
        throw new BridgeResponseDeliveryException(msg);
    }

    private Set<UUID> findValidTargets(GameClientMessage message) {
        Set<UUID> targets = message.getTargets();
        if (targets != null && !targets.isEmpty()) {
            return targets;
        }

        Map<String, Serializable> options = message.getOptions();
        if (options != null) {
            Object possibleTargets = options.get("possibleTargets");
            if (possibleTargets instanceof Set<?> possibleSet) {
                @SuppressWarnings("unchecked")
                Set<UUID> possible = (Set<UUID>) possibleSet;
                if (!possible.isEmpty()) {
                    return possible;
                }
            }
        }

        CardsView cardsView = message.getCardsView1();
        if (cardsView != null && !cardsView.isEmpty()) {
            return cardsView.keySet();
        }
        return null;
    }

    private UUID selectSingleRequiredTarget(GameClientMessage message) {
        if (message == null || !message.isFlag()) {
            return null;
        }
        Set<UUID> targets = findValidTargets(message);
        if (targets == null || targets.size() != 1) {
            return null;
        }
        return selectDeterministicTarget(targets, null);
    }

    private UUID selectDeterministicTarget(Set<UUID> targets, List<Object> choices) {
        if (targets == null || targets.isEmpty()) {
            return null;
        }
        if (choices != null && !choices.isEmpty()) {
            for (Object choice : choices) {
                if (choice instanceof UUID candidate && targets.contains(candidate)) {
                    return candidate;
                }
            }
        }
        UUID selected = null;
        int selectedSeq = Integer.MAX_VALUE;
        for (UUID candidate : targets) {
            int seq = processorServices.viewLocator().getStableShortIdSequence(candidate);
            if (selected == null || seq < selectedSeq) {
                selected = candidate;
                selectedSeq = seq;
            }
        }
        return selected;
    }

    private UUID extractPayingForId(String message) {
        if (message == null) {
            return null;
        }
        int idx = message.indexOf("object_id='");
        if (idx < 0) {
            return null;
        }
        int start = idx + "object_id='".length();
        int end = message.indexOf("'", start);
        if (end <= start) {
            return null;
        }
        try {
            return UUID.fromString(message.substring(start, end));
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private ManaPoolView getMyManaPoolView(GameView gameView) {
        if (gameView == null) {
            return null;
        }
        PlayerView myPlayer = gameView.getMyPlayer();
        if (myPlayer == null) {
            return null;
        }
        return myPlayer.getManaPool();
    }

    private int getManaPoolCount(ManaPoolView manaPool, ManaType manaType) {
        if (manaPool == null) {
            return 0;
        }
        return switch (manaType) {
            case WHITE -> manaPool.getWhite();
            case BLUE -> manaPool.getBlue();
            case BLACK -> manaPool.getBlack();
            case RED -> manaPool.getRed();
            case GREEN -> manaPool.getGreen();
            case COLORLESS -> manaPool.getColorless();
            case GENERIC -> 0;
        };
    }

    private void addPreferredPoolManaChoice(List<ManaType> orderedChoices, ManaPoolView manaPool, ManaType manaType) {
        if (getManaPoolCount(manaPool, manaType) > 0 && !orderedChoices.contains(manaType)) {
            orderedChoices.add(manaType);
        }
    }

    private boolean hasExplicitManaSymbol(String promptText) {
        if (promptText == null) {
            return false;
        }
        return REGEX_WHITE.matcher(promptText).find()
            || REGEX_BLUE.matcher(promptText).find()
            || REGEX_BLACK.matcher(promptText).find()
            || REGEX_RED.matcher(promptText).find()
            || REGEX_GREEN.matcher(promptText).find()
            || REGEX_COLORLESS.matcher(promptText).find();
    }

    private boolean addExplicitPoolChoices(List<ManaType> orderedChoices, ManaPoolView manaPool, String promptText) {
        if (promptText == null) {
            return false;
        }
        boolean hasExplicitSymbols = false;
        if (REGEX_WHITE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        }
        if (REGEX_BLUE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        }
        if (REGEX_BLACK.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        }
        if (REGEX_RED.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        }
        if (REGEX_GREEN.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        }
        if (REGEX_COLORLESS.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);
        }
        return hasExplicitSymbols;
    }

    private List<ManaType> getPoolManaChoices(GameView gameView, String promptText) {
        ManaPoolView manaPool = getMyManaPoolView(gameView);
        if (manaPool == null) {
            return new ArrayList<>();
        }
        var orderedChoices = new ArrayList<ManaType>();
        boolean hasExplicitSymbols = addExplicitPoolChoices(orderedChoices, manaPool, promptText);
        if (hasExplicitSymbols) {
            return orderedChoices;
        }
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);
        return orderedChoices;
    }

    private CopyOnWriteArrayList<BridgeManaPlanEntry> parseManaPlan(String[] arr) {
        var plan = new CopyOnWriteArrayList<BridgeManaPlanEntry>();
        for (String entry : arr) {
            if (isPoolColor(entry)) {
                plan.add(new BridgeManaPlanEntry("pool", entry));
            } else {
                int colonIdx = entry.indexOf(':');
                if (colonIdx >= 0) {
                    String shortId = entry.substring(0, colonIdx);
                    int abilityIndex = Integer.parseInt(entry.substring(colonIdx + 1));
                    plan.add(new BridgeManaPlanEntry("tap", shortId, abilityIndex));
                } else {
                    plan.add(new BridgeManaPlanEntry("tap", entry));
                }
            }
        }
        return plan;
    }

    private static boolean isPoolColor(String value) {
        try {
            ManaType.valueOf(value);
            return true;
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    private boolean cancelSpellFromBadManaPlan(UUID gameId, UUID payingForId) {
        processorState.interactionState().markFailedManaCast(payingForId);
        processorState.interactionState().clearManaPlan();
        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — mana plan was incorrect or incomplete.");
        eventLogger.log("SPELL_CANCELLED", processorState.gameState().currentGameId(), "mana plan was incorrect or incomplete");
        sendBooleanOrDie(gameId, false, "cancelSpellFromBadManaPlan");
        return true;
    }

    private UUID getManaPoolPlayerId(UUID gameId, GameView gameView) {
        if (gameView != null) {
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getPlayerId() != null) {
                return myPlayer.getPlayerId();
            }
        }
        return processorState.gameState().playerIdForGame(gameId);
    }

    private boolean handleGamePlayManaAuto(UUID gameId, GameClientMessage message) {
        GameView gameView = message.getGameView();
        updateLastGameView(gameView, "GAME_PLAY_MANA_AUTO");

        String messageText = message.getMessage();
        UUID payingForId = extractPayingForId(messageText);

        CopyOnWriteArrayList<BridgeManaPlanEntry> plan = processorState.interactionState().manaPlan();
        if (plan != null && !plan.isEmpty()) {
            BridgeManaPlanEntry entry = plan.remove(0);

            if ("tap".equals(entry.type())) {
                processorState.interactionState().setManaPlanAbilityIndex(entry.abilityIndex());
                UUID targetId = processorServices.shortIds().tryResolve(entry.value());
                if (targetId == null) {
                    logger.warn("[" + username + "] Mana plan: unknown short ID '" + entry.value() + "', cancelling spell");
                    return cancelSpellFromBadManaPlan(gameId, payingForId);
                }
                PlayableObjectsList playableForPlan = gameView != null ? gameView.getCanPlayObjects() : null;
                if (playableForPlan != null) {
                    PlayableObjectStats stats = playableForPlan.getObjects().get(targetId);
                    if (stats != null && !targetId.equals(payingForId) && !processorState.interactionState().failedManaCast(targetId)) {
                        logger.info("[" + username + "] Mana plan: \"" + messageText + "\" -> tapping " + entry.value());
                        processorState.interactionState().resetPoolManaTracking();
                        sendUuidOrDie(gameId, targetId, "manaAuto:plan_tap");
                        return true;
                    }
                }
                logger.warn("[" + username + "] Mana plan: tap target " + entry.value() + " not available, cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            if ("pool".equals(entry.type())) {
                ManaType manaType = ManaType.valueOf(entry.value());
                UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
                if (manaPlayerId != null) {
                    logger.info("[" + username + "] Mana plan: \"" + messageText + "\" -> using pool " + manaType);
                    sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:plan_pool");
                    return true;
                }
                logger.warn("[" + username + "] Mana plan: pool entry failed (no player ID), cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            logger.warn("[" + username + "] Mana plan: unknown entry type '" + entry.type() + "', cancelling spell");
            return cancelSpellFromBadManaPlan(gameId, payingForId);
        }

        if (plan != null) {
            if (processorState.interactionState().manaPlanAutoTapFallback()) {
                logger.info("[" + username + "] Mana plan: exhausted, falling through to auto-tap for remaining pips");
                processorState.interactionState().clearManaPlan();
            } else {
                logger.warn("[" + username + "] Mana plan: exhausted with pips remaining, cancelling spell (auto_tap=false)");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }
        }

        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        if (playable != null && !playable.isEmpty()) {
            var battlefieldOrder = new HashMap<UUID, Integer>();
            if (gameView != null) {
                int order = 0;
                for (PlayerView player : gameView.getPlayers()) {
                    for (UUID permanentId : player.getBattlefield().keySet()) {
                        battlefieldOrder.put(permanentId, order++);
                    }
                }
            }
            var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
            sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>>comparingInt(entry -> {
                Integer idx = battlefieldOrder.get(entry.getKey());
                return idx != null ? idx : Integer.MAX_VALUE;
            }).thenComparing(entry -> {
                CardView cardView = processorServices.viewLocator().findCardViewById(entry.getKey(), gameView);
                return cardView != null ? processorServices.cardFormatter().safeDisplayName(cardView) : "";
            }).thenComparingInt(entry -> processorServices.viewLocator().getStableShortIdSequence(
                entry.getKey(),
                processorServices.viewLocator().findCardViewById(entry.getKey(), gameView)
            )));

            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                UUID objectId = entry.getKey();
                if (objectId.equals(payingForId)) {
                    continue;
                }
                if (processorState.interactionState().failedManaCast(objectId)) {
                    continue;
                }
                PlayableObjectStats stats = entry.getValue();
                boolean hasTapManaAbility = false;
                for (String name : stats.getAllManaAbilityNames()) {
                    if (name.contains("{T}")) {
                        int colonPos = name.indexOf(':');
                        if (colonPos > 0) {
                            String costPart = name.substring(0, colonPos);
                            if (costPart.matches(".*\\{[0-9WUBRGC]\\}.*")) {
                                continue;
                            }
                        }
                        hasTapManaAbility = true;
                        break;
                    }
                }
                if (hasTapManaAbility) {
                    logger.info("[" + username + "] Mana: \"" + messageText + "\" -> tapping " + objectId.toString().substring(0, 8));
                    processorState.interactionState().resetPoolManaTracking();
                    sendUuidOrDie(gameId, objectId, "manaAuto:tap");
                    return true;
                }
            }
        }

        List<ManaType> poolChoices = getPoolManaChoices(gameView, messageText);
        if (!poolChoices.isEmpty()) {
            UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
            boolean canAutoSelectPoolType = poolChoices.size() == 1 || hasExplicitManaSymbol(messageText);
            if (manaPlayerId != null) {
                int poolManaAttempts = processorState.interactionState().recordPoolManaAttempt(payingForId);
                if (poolManaAttempts > MAX_POOL_MANA_ATTEMPTS) {
                    logger.warn("[" + username + "] Mana: \"" + messageText + "\" -> pool payment not progressing after "
                        + poolManaAttempts + " attempts, cancelling spell");
                    processorState.interactionState().resetPoolManaTracking();
                    processorState.interactionState().clearManaPlan();
                    processorState.interactionState().markFailedManaCast(payingForId);
                    processorState.gameLogState().addSystemMessage("[System] Spell cancelled — not enough mana to complete payment.");
                    eventLogger.log("SPELL_CANCELLED", processorState.gameState().currentGameId(), "not enough mana to complete payment");
                    sendBooleanOrDie(gameId, false, "manaAuto:pool_loop_cancel");
                    return true;
                }

                if (!canAutoSelectPoolType) {
                    logger.info("[" + username + "] Mana: \"" + messageText + "\" -> pool has multiple options, waiting for manual choice");
                    return false;
                }
                ManaType manaType = poolChoices.getFirst();
                logger.info("[" + username + "] Mana: \"" + messageText + "\" -> using pool " + manaType);
                sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:pool");
                return true;
            }
            logger.warn("[" + username + "] Mana: couldn't resolve player ID for mana pool payment");
        }

        logger.info("[" + username + "] Mana: \"" + messageText + "\" -> no mana source available, cancelling spell");
        processorState.interactionState().markFailedManaCast(payingForId);
        processorState.interactionState().clearManaPlan();
        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — not enough mana to complete payment.");
        eventLogger.log("SPELL_CANCELLED", processorState.gameState().currentGameId(), "not enough mana to complete payment");
        sendBooleanOrDie(gameId, false, "manaAuto:no_source_cancel");
        return true;
    }
}

package mage.client.bridge;

import mage.choices.Choice;
import mage.constants.ManaType;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.view.GameClientMessage;
import mage.view.GameView;

import mage.client.bridge.tools.ChooseActionTool;

import org.apache.log4j.Logger;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;

final class BridgeChooseActionExecutor {

    interface Context {
        List<Object> lastChoices();
        ChooseActionTool.Result buildError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action,
            boolean attachChoices
        );
        void logChoiceOutOfRangeDiagnostic(ClientCallbackMethod method, Integer index, List<Object> choices);
        CopyOnWriteArrayList<ManaPlanEntry> parseManaPlan(String[] arr);
        UUID tryResolveShortId(String id);
        void setManaPlan(CopyOnWriteArrayList<ManaPlanEntry> plan);
        void setManaPlanAbilityIndex(Integer abilityIndex);
        void setManaPlanAutoTapFallback(boolean autoTapFallback);
        void addFailedManaCast(UUID objectId);
        UUID extractPayingForId(String message);
        UUID getManaPoolPlayerId(UUID gameId, GameView gameView);
        GameView lastGameView();
        Set<UUID> findValidTargets(GameClientMessage message);
        UUID selectDeterministicTarget(Set<UUID> targets, List<Object> choices);
        void sendBooleanOrDie(UUID gameId, boolean data, String context);
        void sendUuidOrDie(UUID gameId, UUID data, String context);
        void sendStringOrDie(UUID gameId, String data, String context);
        void sendIntegerOrDie(UUID gameId, int data, String context);
        void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType manaType, String context);
    }

    private final BridgeMageClient client;
    private final Logger logger;
    private final Context context;

    BridgeChooseActionExecutor(BridgeMageClient client, Logger logger, Context context) {
        this.client = client;
        this.logger = logger;
        this.context = context;
    }

    ChooseActionTool.Result execute(
            PendingAction action,
            ChooseActionTool.Result result,
            Integer resolvedIndex,
            String id,
            Boolean answer,
            Integer amount,
            int[] amounts,
            Integer pile,
            String text,
            String[] manaPlanArray,
            Boolean autoTap
    ) {
        UUID gameId = action.gameId();
        Object data = action.data();
        ClientCallbackMethod method = action.method();

        switch (method) {
            case GAME_ASK:
                if (answer == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "GAME_ASK requires choice=\"yes\" or choice=\"no\". This is a yes/no question.",
                        true,
                        action,
                        false
                    );
                }
                if (resolvedIndex != null) {
                    logger.warn("[" + client.getUsername() + "] choose_action: ignoring index="
                        + resolvedIndex + " for GAME_ASK (boolean-only)");
                }
                context.sendBooleanOrDie(gameId, answer, "chooseAction:GAME_ASK");
                result.action_taken = answer ? "yes" : "no";
                return result;

            case GAME_SELECT: {
                boolean usedIndex = false;
                if (resolvedIndex != null) {
                    List<Object> choices = context.lastChoices();
                    if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                        context.logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                        if (answer != null) {
                            logger.warn("[" + client.getUsername() + "] choose_action: index "
                                + resolvedIndex + " out of range, falling through to answer="
                                + answer + " for GAME_SELECT");
                        } else {
                            return context.buildError(
                                result,
                                "index_out_of_range",
                                "Index " + resolvedIndex + " is out of range"
                                    + (choices != null
                                        ? " (valid: 0-" + (choices.size() - 1) + ")"
                                        : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.",
                                true,
                                action,
                                true
                            );
                        }
                    } else {
                        Object chosen = choices.get(resolvedIndex);
                        if (chosen instanceof UUID chosenUuid) {
                            if (manaPlanArray != null) {
                                CopyOnWriteArrayList<ManaPlanEntry> parsedPlan;
                                try {
                                    parsedPlan = context.parseManaPlan(manaPlanArray);
                                } catch (IllegalArgumentException e) {
                                    return context.buildError(
                                        result,
                                        "invalid_mana_plan",
                                        "Invalid mana_plan: " + e.getMessage()
                                            + ". Expected: [\"p1\",\"p2:0\",\"RED\"]",
                                        true,
                                        action,
                                        false
                                    );
                                }
                                for (ManaPlanEntry entry : parsedPlan) {
                                    if ("tap".equals(entry.type()) && context.tryResolveShortId(entry.value()) == null) {
                                        return context.buildError(
                                            result,
                                            "invalid_mana_plan",
                                            "Mana plan references unknown permanent '" + entry.value()
                                                + "'. Check the board state for correct permanent IDs.",
                                            true,
                                            action,
                                            false
                                        );
                                    }
                                }
                                context.setManaPlan(parsedPlan);
                                context.setManaPlanAutoTapFallback(!(autoTap != null && !autoTap));
                                result.mana_plan_set = true;
                                result.mana_plan_size = parsedPlan.size();
                            } else if (autoTap != null && autoTap) {
                                context.setManaPlan(null);
                                context.setManaPlanAbilityIndex(null);
                                context.setManaPlanAutoTapFallback(true);
                            }
                            context.sendUuidOrDie(gameId, chosenUuid, "chooseAction:GAME_SELECT_index");
                            result.action_taken = "selected_" + resolvedIndex;
                            usedIndex = true;
                        } else if (chosen instanceof String chosenStr) {
                            context.sendStringOrDie(gameId, chosenStr, "chooseAction:GAME_SELECT_special");
                            result.action_taken = "special_" + chosenStr;
                            usedIndex = true;
                        } else {
                            return context.buildError(
                                result,
                                "internal_error",
                                "Unexpected choice type at index " + resolvedIndex,
                                false,
                                action,
                                false
                            );
                        }
                    }
                }
                if (!usedIndex) {
                    if (answer != null) {
                        context.sendBooleanOrDie(gameId, answer, "chooseAction:GAME_SELECT_answer");
                        result.action_taken = answer ? "confirmed" : "passed_priority";
                    } else {
                        return context.buildError(
                            result,
                            "missing_param",
                            "GAME_SELECT requires choice=pN to play a card, or choice=\"no\" to pass priority. "
                                + "Call get_action_choices first to see available cards.",
                            true,
                            action,
                            true
                        );
                    }
                }
                return result;
            }

            case GAME_PLAY_MANA:
            case GAME_PLAY_XMANA: {
                boolean usedManaIndex = false;
                if (resolvedIndex != null) {
                    List<Object> choices = context.lastChoices();
                    if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                        context.logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                        if (answer != null && !answer) {
                            logger.warn("[" + client.getUsername() + "] choose_action: index "
                                + resolvedIndex + " out of range, falling through to cancel for GAME_PLAY_MANA");
                        } else {
                            return context.buildError(
                                result,
                                "index_out_of_range",
                                "Index " + resolvedIndex + " is out of range"
                                    + (choices != null
                                        ? " (valid: 0-" + (choices.size() - 1) + ")"
                                        : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.",
                                true,
                                action,
                                true
                            );
                        }
                    } else {
                        Object manaChoice = choices.get(resolvedIndex);
                        if (manaChoice instanceof UUID manaUuid) {
                            context.sendUuidOrDie(gameId, manaUuid, "chooseAction:GAME_PLAY_MANA");
                            result.action_taken = "tapped_mana_" + resolvedIndex;
                            usedManaIndex = true;
                        } else if (manaChoice instanceof ManaType manaType) {
                            UUID manaPlayerId = context.getManaPoolPlayerId(gameId, context.lastGameView());
                            if (manaPlayerId == null) {
                                return context.buildError(
                                    result,
                                    "internal_error",
                                    "Could not resolve player ID for mana pool selection",
                                    false,
                                    action,
                                    false
                                );
                            }
                            context.sendManaTypeOrDie(
                                gameId,
                                manaPlayerId,
                                manaType,
                                "chooseAction:GAME_PLAY_MANA_pool"
                            );
                            result.action_taken = "used_pool_" + manaType;
                            usedManaIndex = true;
                        } else {
                            return context.buildError(
                                result,
                                "internal_error",
                                "Unsupported mana choice type at index " + resolvedIndex,
                                false,
                                action,
                                false
                            );
                        }
                    }
                }
                if (!usedManaIndex) {
                    boolean cancel = false;
                    if (answer != null && !answer) {
                        cancel = true;
                    } else if (answer != null && answer) {
                        List<Object> choices = context.lastChoices();
                        if (choices == null || choices.isEmpty()) {
                            logger.warn("[" + client.getUsername()
                                + "] choose_action: answer=true for GAME_PLAY_MANA with no mana sources, auto-cancelling");
                            cancel = true;
                        }
                    }
                    if (cancel) {
                        UUID payingForId = context.extractPayingForId(action.message());
                        if (payingForId != null) {
                            context.addFailedManaCast(payingForId);
                        }
                        context.setManaPlan(null);
                        context.setManaPlanAbilityIndex(null);
                        context.sendBooleanOrDie(gameId, false, "chooseAction:GAME_PLAY_MANA_cancel");
                        result.action_taken = "cancelled_spell";
                    } else {
                        return context.buildError(
                            result,
                            "missing_param",
                            "GAME_PLAY_MANA requires choice=pN to choose a mana source, or choice=\"no\" to cancel the spell. "
                                + "Call get_action_choices first to see available mana sources.",
                            true,
                            action,
                            true
                        );
                    }
                }
                return result;
            }

            case GAME_TARGET: {
                GameClientMessage targetMsg = (GameClientMessage) data;
                boolean required = targetMsg.isFlag();

                if (resolvedIndex != null) {
                    if (answer != null) {
                        logger.warn("[" + client.getUsername()
                            + "] choose_action: ignoring answer=" + answer
                            + " because index was also provided for GAME_TARGET");
                    }
                    List<Object> choices = context.lastChoices();
                    if (choices != null && resolvedIndex >= 0 && resolvedIndex < choices.size()) {
                        UUID targetUuid = (UUID) choices.get(resolvedIndex);
                        context.sendUuidOrDie(gameId, targetUuid, "chooseAction:GAME_TARGET_index");
                        result.action_taken = "selected_target_" + resolvedIndex;
                        return result;
                    }
                    context.logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                    if (!required) {
                        List<Object> targetChoices = context.lastChoices();
                        return context.buildError(
                            result,
                            "index_out_of_range",
                            "Index " + resolvedIndex + " is out of range"
                                + (targetChoices != null
                                    ? " (valid: 0-" + (targetChoices.size() - 1) + ")"
                                    : " (no choices loaded — call get_action_choices first)")
                                + ". Call get_action_choices to see current targets.",
                            true,
                            action,
                            true
                        );
                    }
                    logger.warn("[" + client.getUsername() + "] choose_action: index "
                        + resolvedIndex + " out of range for required GAME_TARGET (choices="
                        + (choices == null ? "null" : choices.size()) + "), auto-selecting");
                } else if (answer != null && !answer) {
                    if (!required) {
                        context.sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_cancel");
                        result.action_taken = "cancelled";
                        return result;
                    }
                    logger.warn("[" + client.getUsername()
                        + "] choose_action: answer=false invalid for required GAME_TARGET, auto-selecting");
                } else if (!required) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "GAME_TARGET requires choice=pN to select a target, or choice=\"no\" to cancel targeting. "
                            + "Call get_action_choices first to see available targets.",
                        true,
                        action,
                        true
                    );
                }

                Set<UUID> autoTargets = context.findValidTargets(targetMsg);
                if (autoTargets != null && !autoTargets.isEmpty()) {
                    UUID firstTarget = context.selectDeterministicTarget(autoTargets, context.lastChoices());
                    logger.warn("[" + client.getUsername()
                        + "] choose_action: auto-selecting first target for required GAME_TARGET");
                    context.sendUuidOrDie(gameId, firstTarget, "chooseAction:GAME_TARGET_auto_select");
                    result.action_taken = "auto_selected_required_target";
                    result.warning = "Required target auto-selected. Use get_action_choices first, then index=N.";
                } else {
                    logger.error("[" + client.getUsername()
                        + "] Required GAME_TARGET has no valid targets — cancelling to avoid infinite loop");
                    context.sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_no_valid");
                    result.action_taken = "cancelled_no_valid_targets";
                }
                return result;
            }

            case GAME_CHOOSE_ABILITY: {
                if (resolvedIndex == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "GAME_CHOOSE_ABILITY requires index=N. Call get_action_choices first to see "
                            + "the available abilities, then choose_action with the index of the one you want.",
                        true,
                        action,
                        true
                    );
                }
                List<Object> abilityChoices = context.lastChoices();
                if (abilityChoices == null || resolvedIndex < 0 || resolvedIndex >= abilityChoices.size()) {
                    context.logChoiceOutOfRangeDiagnostic(method, resolvedIndex, abilityChoices);
                    return context.buildError(
                        result,
                        "index_out_of_range",
                        "Index " + resolvedIndex + " is out of range"
                            + (abilityChoices != null
                                ? " (valid: 0-" + (abilityChoices.size() - 1) + ")"
                                : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.",
                        true,
                        action,
                        true
                    );
                }
                UUID abilityUuid = (UUID) abilityChoices.get(resolvedIndex);
                context.sendUuidOrDie(gameId, abilityUuid, "chooseAction:GAME_CHOOSE_ABILITY");
                result.action_taken = "selected_ability_" + resolvedIndex;
                return result;
            }

            case GAME_CHOOSE_CHOICE: {
                if (text != null && !text.isEmpty()) {
                    GameClientMessage choiceMsg = (GameClientMessage) data;
                    Choice choiceObj = choiceMsg.getChoice();
                    if (choiceObj == null) {
                        return context.buildError(result, "internal_error", "No choice available", false, action, false);
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
                            return context.buildError(
                                result,
                                "invalid_choice",
                                "'" + text + "' is not a valid choice",
                                true,
                                action,
                                true
                            );
                        }
                        context.sendStringOrDie(gameId, matchedKey, "chooseAction:GAME_CHOOSE_CHOICE_key");
                    } else {
                        Set<String> choices = choiceObj.getChoices();
                        String matched = null;
                        if (choices != null) {
                            for (String choiceValue : choices) {
                                if (choiceValue.equalsIgnoreCase(text)) {
                                    matched = choiceValue;
                                    break;
                                }
                            }
                        }
                        if (matched == null) {
                            return context.buildError(
                                result,
                                "invalid_choice",
                                "'" + text + "' is not a valid choice",
                                true,
                                action,
                                true
                            );
                        }
                        context.sendStringOrDie(gameId, matched, "chooseAction:GAME_CHOOSE_CHOICE");
                    }
                    result.action_taken = "selected_choice_text_" + text;
                    return result;
                }
                if (id != null && !id.isEmpty()) {
                    return context.buildError(
                        result,
                        "invalid_choice",
                        "GAME_CHOOSE_CHOICE does not accept choice=\"" + id + "\" by name. "
                            + "Use text=\"" + id + "\" or choice=N with the current options.",
                        true,
                        action,
                        true
                    );
                }
                if (resolvedIndex == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "Integer 'index' or string 'text' required for GAME_CHOOSE_CHOICE",
                        true,
                        action,
                        true
                    );
                }
                List<Object> choiceChoices = context.lastChoices();
                if (choiceChoices == null || resolvedIndex < 0 || resolvedIndex >= choiceChoices.size()) {
                    context.logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choiceChoices);
                    return context.buildError(
                        result,
                        "index_out_of_range",
                        "Index " + resolvedIndex + " is out of range"
                            + (choiceChoices != null
                                ? " (valid: 0-" + (choiceChoices.size() - 1) + ")"
                                : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.",
                        true,
                        action,
                        true
                    );
                }
                String choiceStr = (String) choiceChoices.get(resolvedIndex);
                context.sendStringOrDie(gameId, choiceStr, "chooseAction:GAME_CHOOSE_CHOICE_index");
                result.action_taken = "selected_choice_" + resolvedIndex;
                return result;
            }

            case GAME_CHOOSE_PILE:
                if (pile == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "Integer 'pile' (1 or 2) required for GAME_CHOOSE_PILE",
                        true,
                        action,
                        false
                    );
                }
                context.sendBooleanOrDie(gameId, pile == 1, "chooseAction:GAME_CHOOSE_PILE");
                result.action_taken = "selected_pile_" + pile;
                return result;

            case GAME_GET_AMOUNT: {
                if (amount == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "Integer 'amount' required for GAME_GET_AMOUNT",
                        true,
                        action,
                        false
                    );
                }
                GameClientMessage msg = (GameClientMessage) data;
                int clamped = Math.max(msg.getMin(), Math.min(msg.getMax(), amount));
                context.sendIntegerOrDie(gameId, clamped, "chooseAction:GAME_GET_AMOUNT");
                result.action_taken = "amount_" + clamped;
                return result;
            }

            case GAME_GET_MULTI_AMOUNT: {
                if (amounts == null) {
                    return context.buildError(
                        result,
                        "missing_param",
                        "Array 'amounts' required for GAME_GET_MULTI_AMOUNT",
                        true,
                        action,
                        false
                    );
                }
                GameClientMessage msg = (GameClientMessage) data;
                String validationError;
                try {
                    validationError = BridgeCallbackHandler.validateMultiAmountInput(msg, amounts);
                } catch (IllegalStateException e) {
                    return context.buildError(result, "internal_error", e.getMessage(), false, action, false);
                }
                if (validationError != null) {
                    return context.buildError(
                        result,
                        "invalid_multi_amount",
                        validationError,
                        true,
                        action,
                        false
                    );
                }
                var sb = new StringBuilder();
                for (int i = 0; i < amounts.length; i++) {
                    if (i > 0) {
                        sb.append(" ");
                    }
                    sb.append(amounts[i]);
                }
                context.sendStringOrDie(gameId, sb.toString(), "chooseAction:GAME_GET_MULTI_AMOUNT");
                result.action_taken = "multi_amount";
                return result;
            }

            default:
                return context.buildError(
                    result,
                    "unknown_action_type",
                    "Unknown action type: " + method,
                    false,
                    null,
                    false
                );
        }
    }
}

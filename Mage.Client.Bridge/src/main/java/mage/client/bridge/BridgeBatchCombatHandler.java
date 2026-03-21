package mage.client.bridge;

import mage.interfaces.callback.ClientCallbackMethod;
import mage.util.ShortIdRegistry;
import mage.view.GameClientMessage;

import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class BridgeBatchCombatHandler {

    interface Context {
        void clearPendingAction();
        PendingAction waitForNextCallback();
        PendingAction awaitDecisionAction();
        void mergeActionChoices(ActionResult result, Long boardCursorParam, PendingAction action);
        void attachUnseenChat(ActionResult result);
        ChooseActionTool.Result buildError(
            ChooseActionTool.Result result,
            String errorCode,
            String message,
            boolean retryable,
            PendingAction action,
            boolean attachChoices
        );
        Set<UUID> findValidTargets(GameClientMessage message);
        void sendBooleanOrDie(UUID gameId, boolean data, String context);
        void sendStringOrDie(UUID gameId, String data, String context);
        void sendUuidOrDie(UUID gameId, UUID data, String context);
    }

    private final ShortIdRegistry shortIds;
    private final Context context;

    BridgeBatchCombatHandler(ShortIdRegistry shortIds, Context context) {
        this.shortIds = shortIds;
        this.context = context;
    }

    @SuppressWarnings("unchecked")
    ChooseActionTool.Result handleBatchAttackers(
            String[] attackerIds,
            PendingAction action,
            ChooseActionTool.Result result
    ) {
        try {
            return handleBatchAttackersBody(attackerIds, action, result);
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            context.attachUnseenChat(result);
            return result;
        }
    }

    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchAttackersBody(
            String[] attackerIds,
            PendingAction action,
            ChooseActionTool.Result result
    ) {
        UUID gameId = action.gameId();
        var declared = new ArrayList<Map<String, Object>>();
        var failed = new ArrayList<Map<String, Object>>();

        if (attackerIds.length == 1 && "all".equals(attackerIds[0])) {
            context.clearPendingAction();
            context.sendStringOrDie(gameId, "special", "batchAttack:all");
            PendingAction next = context.waitForNextCallback();
            if (next != null && next.method() == ClientCallbackMethod.GAME_SELECT) {
                context.clearPendingAction();
                context.sendBooleanOrDie(gameId, true, "batchAttack:confirm_all");
            }
            result.success = true;
            result.action_taken = "batch_attack";
            declared.add(Map.of("id", "all"));
            result.declared = new ArrayList<>(declared);
            waitForNextActionAfterBatch(result);
            return result;
        }

        GameClientMessage gcm = (GameClientMessage) action.data();
        Map<String, Serializable> options = gcm.getOptions();
        List<UUID> possibleAttackerUuids = (List<UUID>) options.get("possibleAttackers");

        for (String shortId : attackerIds) {
            UUID attackerUuid;
            try {
                attackerUuid = shortIds.resolve(shortId);
            } catch (IllegalArgumentException e) {
                failed.add(Map.of("id", shortId, "reason", "unknown short ID"));
                continue;
            }

            if (possibleAttackerUuids == null || !possibleAttackerUuids.contains(attackerUuid)) {
                failed.add(Map.of("id", shortId, "reason", "not a valid attacker"));
                continue;
            }

            context.clearPendingAction();
            context.sendUuidOrDie(gameId, attackerUuid, "batchAttack:declare_attacker");
            declared.add(Map.of("id", shortId));

            PendingAction next = context.waitForNextCallback();
            if (next == null) {
                result.interrupted = true;
                break;
            }
            if (next.method() != ClientCallbackMethod.GAME_SELECT) {
                result.interrupted = true;
                break;
            }
            if (next.data() instanceof GameClientMessage nextGcm) {
                Map<String, Serializable> nextOptions = nextGcm.getOptions();
                if (nextOptions != null && nextOptions.containsKey("possibleAttackers")) {
                    possibleAttackerUuids = (List<UUID>) nextOptions.get("possibleAttackers");
                }
            }
        }

        if (!Boolean.TRUE.equals(result.interrupted)) {
            context.clearPendingAction();
            context.sendBooleanOrDie(gameId, true, "batchAttack:confirm");
        }

        result.success = !Boolean.TRUE.equals(result.interrupted) && failed.isEmpty();
        result.action_taken = "batch_attack";
        result.declared = new ArrayList<>(declared);
        if (!failed.isEmpty()) {
            result.failed = new ArrayList<>(failed);
            result.error = batchFailedMessage(failed);
            result.error_code = "batch_failed";
            result.retryable = true;
        }
        waitForNextActionAfterBatch(result);
        return result;
    }

    @SuppressWarnings("unchecked")
    ChooseActionTool.Result handleBatchBlockers(
            String[] blockersArray,
            PendingAction action,
            ChooseActionTool.Result result
    ) {
        try {
            return handleBatchBlockersBody(blockersArray, action, result);
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            context.attachUnseenChat(result);
            return result;
        }
    }

    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchBlockersBody(
            String[] blockersArray,
            PendingAction action,
            ChooseActionTool.Result result
    ) {
        UUID gameId = action.gameId();
        var declared = new ArrayList<Map<String, Object>>();
        var failed = new ArrayList<Map<String, Object>>();

        List<Map<String, String>> assignments;
        try {
            assignments = parseBlockerAssignments(blockersArray);
        } catch (IllegalArgumentException e) {
            return context.buildError(
                result,
                "invalid_blockers",
                "Invalid blockers: " + e.getMessage() + ". Expected: [\"blocker:attacker\",...]",
                false,
                action,
                false
            );
        }

        GameClientMessage gcm = (GameClientMessage) action.data();
        Map<String, Serializable> options = gcm.getOptions();
        List<UUID> possibleBlockerUuids = (List<UUID>) options.get("possibleBlockers");

        for (Map<String, String> assignment : assignments) {
            String blockerShortId = assignment.get("id");
            String attackerShortId = assignment.get("blocks");

            UUID blockerUuid;
            try {
                blockerUuid = shortIds.resolve(blockerShortId);
            } catch (IllegalArgumentException e) {
                failed.add(Map.of("id", blockerShortId, "reason", "unknown short ID"));
                continue;
            }

            if (possibleBlockerUuids == null || !possibleBlockerUuids.contains(blockerUuid)) {
                failed.add(Map.of("id", blockerShortId, "reason", "not a valid blocker"));
                continue;
            }

            context.clearPendingAction();
            context.sendUuidOrDie(gameId, blockerUuid, "batchBlock:declare_blocker");

            PendingAction next = context.waitForNextCallback();
            if (next == null) {
                result.interrupted = true;
                break;
            }

            if (next.method() == ClientCallbackMethod.GAME_TARGET) {
                UUID attackerUuid;
                try {
                    attackerUuid = shortIds.resolve(attackerShortId);
                } catch (IllegalArgumentException e) {
                    failed.add(Map.of("id", blockerShortId, "reason", "unknown attacker ID: " + attackerShortId));
                    context.clearPendingAction();
                    context.sendBooleanOrDie(gameId, false, "batchBlock:cancel_unknown_attacker");
                    next = context.waitForNextCallback();
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.interrupted = true;
                        break;
                    }
                    continue;
                }

                Set<UUID> validTargets = context.findValidTargets((GameClientMessage) next.data());
                if (validTargets == null || !validTargets.contains(attackerUuid)) {
                    failed.add(Map.of(
                        "id", blockerShortId,
                        "reason", "attacker " + attackerShortId + " is not a valid block target"
                    ));
                    context.clearPendingAction();
                    context.sendBooleanOrDie(gameId, false, "batchBlock:cancel_invalid_target");
                    next = context.waitForNextCallback();
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.interrupted = true;
                        break;
                    }
                    continue;
                }

                context.clearPendingAction();
                context.sendUuidOrDie(gameId, attackerUuid, "batchBlock:select_attacker");
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                next = context.waitForNextCallback();
                if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                    result.interrupted = true;
                    break;
                }

                if (next.data() instanceof GameClientMessage nextGcm) {
                    Map<String, Serializable> nextOptions = nextGcm.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else if (next.method() == ClientCallbackMethod.GAME_SELECT) {
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                if (next.data() instanceof GameClientMessage nextGcm) {
                    Map<String, Serializable> nextOptions = nextGcm.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else {
                result.interrupted = true;
                break;
            }
        }

        if (!Boolean.TRUE.equals(result.interrupted)) {
            context.clearPendingAction();
            context.sendBooleanOrDie(gameId, true, "batchBlock:confirm");
        }

        result.success = !Boolean.TRUE.equals(result.interrupted) && failed.isEmpty();
        result.action_taken = "batch_block";
        result.declared = new ArrayList<>(declared);
        if (!failed.isEmpty()) {
            result.failed = new ArrayList<>(failed);
            result.error = batchFailedMessage(failed);
            result.error_code = "batch_failed";
            result.retryable = true;
        }
        waitForNextActionAfterBatch(result);
        return result;
    }

    private List<Map<String, String>> parseBlockerAssignments(String[] arr) {
        var assignments = new ArrayList<Map<String, String>>();
        for (int i = 0; i < arr.length; i++) {
            String entry = arr[i];
            int colonIdx = entry.indexOf(':');
            if (colonIdx < 0) {
                throw new IllegalArgumentException(
                    "blockers entry " + i + " must be \"blocker:attacker\", got: " + entry
                );
            }
            String blockerId = entry.substring(0, colonIdx);
            String attackerId = entry.substring(colonIdx + 1);
            if (blockerId.isEmpty() || attackerId.isEmpty()) {
                throw new IllegalArgumentException(
                    "blockers entry " + i + " has empty id in: " + entry
                );
            }
            assignments.add(Map.of("id", blockerId, "blocks", attackerId));
        }
        return assignments;
    }

    private void waitForNextActionAfterBatch(ChooseActionTool.Result result) {
        PendingAction next = context.awaitDecisionAction();
        if (next != null) {
            result.game_seq = next.gameSeq();
            context.mergeActionChoices(result, null, next);
        } else {
            context.attachUnseenChat(result);
        }
    }

    private String batchFailedMessage(List<Map<String, Object>> failed) {
        var sb = new StringBuilder();
        for (var entry : failed) {
            if (sb.length() > 0) {
                sb.append("; ");
            }
            sb.append(entry.get("id")).append(": ").append(entry.get("reason"));
        }
        return sb.toString();
    }
}

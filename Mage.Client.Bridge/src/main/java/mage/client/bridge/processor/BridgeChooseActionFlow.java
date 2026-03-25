package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ChooseActionTool;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.view.GameClientMessage;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public final class BridgeChooseActionFlow {
    private enum Phase {
        WAITING_FOR_ACTION,
        WAITING_FOR_BATCH_CALLBACK,
        WAITING_FOR_NEXT_DECISION
    }

    private enum BatchMode {
        NONE,
        ATTACKERS,
        BLOCKERS
    }

    private enum BatchPhase {
        NONE,
        ATTACKERS_WAITING_FOR_NEXT_SELECT,
        ATTACKERS_WAITING_FOR_SPECIAL_CONFIRM,
        BLOCKERS_WAITING_FOR_BLOCKER_SELECT,
        BLOCKERS_WAITING_FOR_TARGET_OR_SELECT
    }

    private final BridgeChooseActionFlowContext context;
    private final BridgeChooseActionInput input;
    private final CompletableFuture<ChooseActionTool.Result> result = new CompletableFuture<>();

    private Phase phase = Phase.WAITING_FOR_ACTION;
    private BatchMode batchMode = BatchMode.NONE;
    private BatchPhase batchPhase = BatchPhase.NONE;
    private PendingAction previousAction = null;
    private ChooseActionTool.Result partialResult = null;
    private List<UUID> possibleCombatants = Collections.emptyList();
    private List<BridgeChooseActionBlockerAssignment> blockerAssignments = Collections.emptyList();
    private final List<Map<String, Object>> batchDeclared = new ArrayList<>();
    private final List<Map<String, Object>> batchFailed = new ArrayList<>();
    private int batchIndex = 0;
    private boolean batchResultFinalized = false;

    public BridgeChooseActionFlow(BridgeChooseActionFlowContext context, BridgeChooseActionInput input) {
        this.context = context;
        this.input = input;
    }

    public void start() {
        advance();
    }

    public void advance() {
        if (result.isDone()) {
            return;
        }

        while (!result.isDone()) {
            switch (phase) {
                case WAITING_FOR_ACTION -> {
                    advanceWaitingForAction();
                }
                case WAITING_FOR_BATCH_CALLBACK -> {
                    advanceWaitingForBatchCallback();
                }
                case WAITING_FOR_NEXT_DECISION -> {
                    advanceWaitingForNextDecision();
                }
            }
            if (!result.isDone() && shouldPauseForFutureCallback()) {
                return;
            }
        }
    }

    public boolean isDone() {
        return result.isDone();
    }

    public ChooseActionTool.Result cancel() {
        if (result.isDone()) {
            return result.join();
        }
        ChooseActionTool.Result cancelled = context.cancelledChooseActionResult(previousAction, partialResult);
        result.complete(cancelled);
        return cancelled;
    }

    public ChooseActionTool.Result awaitResult() throws InterruptedException, ExecutionException {
        return result.get();
    }

    public void finish(ChooseActionTool.Result finalResult) {
        result.complete(finalResult);
    }

    public void fail(Throwable t) {
        result.completeExceptionally(t);
    }

    private void advanceWaitingForAction() {
        PendingAction action = context.currentDecisionAction();
        if (action == null) {
            if (context.requestCannotContinue()) {
                result.complete(context.noPendingActionResult());
            }
            return;
        }

        previousAction = action;
        if (tryStartBatchCombat(action)) {
            return;
        }

        BridgeChooseActionStartResult startResult = context.applyChooseAction(input, action);
        partialResult = startResult.result();
        if (!startResult.waitForNextDecision()) {
            result.complete(partialResult);
            return;
        }

        phase = Phase.WAITING_FOR_NEXT_DECISION;
    }

    private boolean tryStartBatchCombat(PendingAction action) {
        String combatType = context.detectCombatSelect(action);
        if (input.attackers() != null && input.attackers().length > 0 && "attackers".equals(combatType)) {
            startBatchAttackers(action);
            return true;
        }
        if (input.blockers() != null && input.blockers().length > 0 && "blockers".equals(combatType)) {
            startBatchBlockers(action);
            return true;
        }
        return false;
    }

    private void startBatchAttackers(PendingAction action) {
        initializeBatchResult(action, "batch_attack");
        batchMode = BatchMode.ATTACKERS;
        batchPhase = BatchPhase.NONE;
        possibleCombatants = extractUuidOptionList(action, "possibleAttackers");

        if (input.attackers().length == 1 && "all".equals(input.attackers()[0])) {
            if (possibleCombatants.size() == 1) {
                batchDeclared.add(Map.of("id", "all"));
                batchIndex = input.attackers().length;
                context.clearPendingActionIfCurrent(action);
                context.sendUuidOrDie(action.gameId(), possibleCombatants.getFirst(), "batchAttack:all_single");
                batchPhase = BatchPhase.ATTACKERS_WAITING_FOR_NEXT_SELECT;
                phase = Phase.WAITING_FOR_BATCH_CALLBACK;
                return;
            }
            batchDeclared.add(Map.of("id", "all"));
            context.clearPendingActionIfCurrent(action);
            context.sendStringOrDie(action.gameId(), "special", "batchAttack:all");
            batchPhase = BatchPhase.ATTACKERS_WAITING_FOR_SPECIAL_CONFIRM;
            phase = Phase.WAITING_FOR_BATCH_CALLBACK;
            return;
        }

        advanceBatchAttackers(action);
    }

    private void startBatchBlockers(PendingAction action) {
        List<BridgeChooseActionBlockerAssignment> assignments;
        try {
            assignments = parseBlockerAssignments(input.blockers());
        } catch (IllegalArgumentException e) {
            result.complete(context.buildChooseActionError(
                new ChooseActionTool.Result(),
                "invalid_blockers",
                "Invalid blockers: " + e.getMessage() + ". Expected: [\"blocker:attacker\",...]",
                false,
                action
            ));
            return;
        }

        initializeBatchResult(action, "batch_block");
        batchMode = BatchMode.BLOCKERS;
        batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT;
        blockerAssignments = assignments;
        possibleCombatants = extractUuidOptionList(action, "possibleBlockers");

        advanceBatchBlockers(action);
    }

    private void advanceWaitingForBatchCallback() {
        PendingAction action = context.currentPendingAction();
        if (action == null) {
            if (context.requestCannotContinue()) {
                finalizeBatchResult(true);
                context.finishBatchChooseActionWithoutNextDecision(partialResult);
                result.complete(partialResult);
            }
            return;
        }

        if (batchMode == BatchMode.ATTACKERS) {
            advanceBatchAttackers(action);
            return;
        }
        if (batchMode == BatchMode.BLOCKERS) {
            advanceBatchBlockers(action);
        }
    }

    private void advanceWaitingForNextDecision() {
        PendingAction nextAction = context.currentDecisionAction();
        if (nextAction != null) {
            if (batchMode == BatchMode.NONE) {
                context.finishChooseActionWithNextDecision(partialResult, previousAction, nextAction);
            } else {
                context.finishBatchChooseActionWithNextDecision(partialResult, nextAction);
            }
            result.complete(partialResult);
            return;
        }

        if (context.requestCannotContinue()) {
            if (batchMode == BatchMode.NONE) {
                context.finishChooseActionWithoutNextDecision(partialResult, previousAction);
            } else {
                context.finishBatchChooseActionWithoutNextDecision(partialResult);
            }
            result.complete(partialResult);
        }
    }

    private void advanceBatchAttackers(PendingAction action) {
        if (batchPhase == BatchPhase.ATTACKERS_WAITING_FOR_SPECIAL_CONFIRM) {
            if (!isAttackersCombatSelect(action)) {
                transitionBatchToNextDecision(false);
                return;
            }
            context.clearPendingActionIfCurrent(action);
            context.sendBooleanOrDie(action.gameId(), true, "batchAttack:confirm_all");
            transitionBatchToNextDecision(false);
            return;
        }

        if (batchPhase == BatchPhase.ATTACKERS_WAITING_FOR_NEXT_SELECT && !isAttackersCombatSelect(action)) {
            transitionBatchToNextDecision(false);
            return;
        }

        if (action.method() != ClientCallbackMethod.GAME_SELECT) {
            transitionBatchToNextDecision(true);
            return;
        }

        possibleCombatants = extractUuidOptionList(action, "possibleAttackers");
        while (batchIndex < input.attackers().length) {
            String shortId = input.attackers()[batchIndex];
            UUID attackerUuid = resolveCombatant(shortId, "not a valid attacker");
            if (attackerUuid == null) {
                batchIndex++;
                continue;
            }

            context.clearPendingActionIfCurrent(action);
            context.sendUuidOrDie(action.gameId(), attackerUuid, "batchAttack:declare_attacker");
            batchDeclared.add(Map.of("id", shortId));
            batchIndex++;
            batchPhase = BatchPhase.ATTACKERS_WAITING_FOR_NEXT_SELECT;
            phase = Phase.WAITING_FOR_BATCH_CALLBACK;
            return;
        }

        context.clearPendingActionIfCurrent(action);
        context.sendBooleanOrDie(action.gameId(), true, "batchAttack:confirm");
        transitionBatchToNextDecision(false);
    }

    private boolean isAttackersCombatSelect(PendingAction action) {
        if (action.method() != ClientCallbackMethod.GAME_SELECT) {
            return false;
        }
        if ("attackers".equals(context.detectCombatSelect(action))) {
            return true;
        }
        String message = action.message();
        return message != null && message.toLowerCase(Locale.ROOT).contains("attack");
    }

    private void advanceBatchBlockers(PendingAction action) {
        while (true) {
            if (batchPhase == BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT) {
                if (action.method() != ClientCallbackMethod.GAME_SELECT) {
                    transitionBatchToNextDecision(true);
                    return;
                }

                possibleCombatants = extractUuidOptionList(action, "possibleBlockers");
                while (batchIndex < blockerAssignments.size()) {
                    BridgeChooseActionBlockerAssignment assignment = blockerAssignments.get(batchIndex);
                    UUID blockerUuid = resolveCombatant(assignment.id(), "not a valid blocker");
                    if (blockerUuid == null) {
                        batchIndex++;
                        continue;
                    }

                    context.clearPendingActionIfCurrent(action);
                    context.sendUuidOrDie(action.gameId(), blockerUuid, "batchBlock:declare_blocker");
                    batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_TARGET_OR_SELECT;
                    phase = Phase.WAITING_FOR_BATCH_CALLBACK;
                    return;
                }

                context.clearPendingActionIfCurrent(action);
                context.sendBooleanOrDie(action.gameId(), true, "batchBlock:confirm");
                transitionBatchToNextDecision(false);
                return;
            }

            if (batchPhase != BatchPhase.BLOCKERS_WAITING_FOR_TARGET_OR_SELECT) {
                throw new IllegalStateException("Unexpected blocker batch phase: " + batchPhase);
            }

            BridgeChooseActionBlockerAssignment assignment = blockerAssignments.get(batchIndex);
            if (action.method() == ClientCallbackMethod.GAME_TARGET) {
                if (!handleBatchBlockerTarget(action, assignment)) {
                    phase = Phase.WAITING_FOR_BATCH_CALLBACK;
                    return;
                }
                continue;
            }

            if (action.method() == ClientCallbackMethod.GAME_SELECT) {
                batchDeclared.add(Map.of("id", assignment.id(), "blocks", assignment.blocks()));
                batchIndex++;
                batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT;
                phase = Phase.WAITING_FOR_BATCH_CALLBACK;
                continue;
            }

            transitionBatchToNextDecision(true);
            return;
        }
    }

    private boolean handleBatchBlockerTarget(PendingAction action, BridgeChooseActionBlockerAssignment assignment) {
        UUID attackerUuid;
        try {
            attackerUuid = context.resolveShortId(assignment.blocks());
        } catch (IllegalArgumentException e) {
            batchFailed.add(Map.of("id", assignment.id(), "reason", "unknown attacker ID: " + assignment.blocks()));
            context.clearPendingActionIfCurrent(action);
            context.sendBooleanOrDie(action.gameId(), false, "batchBlock:cancel_unknown_attacker");
            batchIndex++;
            batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT;
            return false;
        }

        Set<UUID> validTargets = context.validTargets(action);
        if (validTargets == null || !validTargets.contains(attackerUuid)) {
            batchFailed.add(Map.of("id", assignment.id(), "reason",
                "attacker " + assignment.blocks() + " is not a valid block target"));
            context.clearPendingActionIfCurrent(action);
            context.sendBooleanOrDie(action.gameId(), false, "batchBlock:cancel_invalid_target");
            batchIndex++;
            batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT;
            return false;
        }

        context.clearPendingActionIfCurrent(action);
        context.sendUuidOrDie(action.gameId(), attackerUuid, "batchBlock:select_attacker");
        batchDeclared.add(Map.of("id", assignment.id(), "blocks", assignment.blocks()));
        batchIndex++;
        batchPhase = BatchPhase.BLOCKERS_WAITING_FOR_BLOCKER_SELECT;
        return false;
    }

    private void transitionBatchToNextDecision(boolean interrupted) {
        finalizeBatchResult(interrupted);
        phase = Phase.WAITING_FOR_NEXT_DECISION;
        batchPhase = BatchPhase.NONE;
    }

    private void initializeBatchResult(PendingAction action, String actionTaken) {
        partialResult = new ChooseActionTool.Result();
        partialResult.game_seq = action.gameSeq();
        partialResult.action_taken = actionTaken;
        batchDeclared.clear();
        batchFailed.clear();
        batchIndex = 0;
        batchResultFinalized = false;
    }

    private void finalizeBatchResult(boolean interrupted) {
        if (batchResultFinalized) {
            return;
        }
        partialResult.success = !interrupted && batchFailed.isEmpty();
        if (interrupted) {
            partialResult.interrupted = true;
        }
        partialResult.declared = new ArrayList<>(batchDeclared);
        if (!batchFailed.isEmpty()) {
            partialResult.failed = new ArrayList<>(batchFailed);
            partialResult.error = batchFailedMessage();
            partialResult.error_code = "batch_failed";
            partialResult.retryable = true;
        }
        batchResultFinalized = true;
    }

    private UUID resolveCombatant(String shortId, String invalidReason) {
        UUID combatantUuid;
        try {
            combatantUuid = context.resolveShortId(shortId);
        } catch (IllegalArgumentException e) {
            batchFailed.add(Map.of("id", shortId, "reason", "unknown short ID"));
            return null;
        }

        if (possibleCombatants == null || !possibleCombatants.contains(combatantUuid)) {
            batchFailed.add(Map.of("id", shortId, "reason", invalidReason));
            return null;
        }

        return combatantUuid;
    }

    @SuppressWarnings("unchecked")
    private static List<UUID> extractUuidOptionList(PendingAction action, String key) {
        if (!(action.data() instanceof GameClientMessage message)) {
            return Collections.emptyList();
        }

        Map<String, Serializable> options = message.getOptions();
        if (options == null) {
            return Collections.emptyList();
        }

        Object raw = options.get(key);
        if (!(raw instanceof List<?> rawList)) {
            return Collections.emptyList();
        }
        return (List<UUID>) rawList;
    }

    private static List<BridgeChooseActionBlockerAssignment> parseBlockerAssignments(String[] arr) {
        var assignments = new ArrayList<BridgeChooseActionBlockerAssignment>();
        for (int i = 0; i < arr.length; i++) {
            String entry = arr[i];
            int colonIdx = entry.indexOf(':');
            if (colonIdx < 0) {
                throw new IllegalArgumentException("blockers entry " + i + " must be \"blocker:attacker\", got: " + entry);
            }
            String blockerId = entry.substring(0, colonIdx);
            String attackerId = entry.substring(colonIdx + 1);
            if (blockerId.isEmpty() || attackerId.isEmpty()) {
                throw new IllegalArgumentException("blockers entry " + i + " has empty id in: " + entry);
            }
            assignments.add(new BridgeChooseActionBlockerAssignment(blockerId, attackerId));
        }
        return assignments;
    }

    private String batchFailedMessage() {
        var sb = new StringBuilder();
        for (Map<String, Object> entry : batchFailed) {
            if (sb.length() > 0) {
                sb.append("; ");
            }
            sb.append(entry.get("id")).append(": ").append(entry.get("reason"));
        }
        return sb.toString();
    }

    private boolean shouldPauseForFutureCallback() {
        return switch (phase) {
            case WAITING_FOR_ACTION, WAITING_FOR_NEXT_DECISION -> context.currentDecisionAction() == null;
            case WAITING_FOR_BATCH_CALLBACK -> context.currentPendingAction() == null;
        };
    }
}

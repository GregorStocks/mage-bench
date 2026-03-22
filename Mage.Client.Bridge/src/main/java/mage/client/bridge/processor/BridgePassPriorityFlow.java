package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.constants.PhaseStep;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.view.GameClientMessage;
import mage.view.GameView;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public final class BridgePassPriorityFlow {
    private final BridgePassPriorityFlowContext context;
    private final String until;
    private final Long boardCursorParam;
    private final CompletableFuture<ActionResult> result = new CompletableFuture<>();

    private int actionsPassed = 0;
    private int lastSeenGameSeq = 0;
    private PhaseStep targetStep = null;
    private boolean yieldUntilMyTurn = false;
    private boolean yieldUntilEndOfTurn = false;
    private boolean yieldUntilStackResolved = false;
    private UUID yieldUntilStackResolvedObjectId = null;
    private int yieldStartTurn = -1;
    private long startTimeMs = 0;
    private long lastProgressLogAtMs = 0;
    private int waitLoops = 0;

    public BridgePassPriorityFlow(BridgePassPriorityFlowContext context, String until, Long boardCursorParam) {
        this.context = context;
        this.until = until;
        this.boardCursorParam = boardCursorParam;
    }

    public void start() {
        long now = System.currentTimeMillis();
        startTimeMs = now;
        lastProgressLogAtMs = now;
        boolean yieldActive = initialize();
        if (result.isDone()) {
            return;
        }
        logEnter(yieldActive);
        advance();
    }

    public void advance() {
        if (result.isDone()) {
            return;
        }
        while (true) {
            PendingAction action = context.currentPendingAction();
            if (action == null) {
                maybeFinishGameOver();
                return;
            }

            lastSeenGameSeq = action.gameSeq();
            PendingAction readyAction = context.resolvePassPriorityAction(action);
            if (readyAction == null) {
                continue;
            }

            action = readyAction;
            ClientCallbackMethod method = action.method();
            GameView actionView = context.preparePassPriorityActionView(action);

            if (targetStep != null && context.lastTurnNumber() != yieldStartTurn) {
                finish(context.stepYieldResult(action, actionView, "step_not_reached", boardCursorParam), action, actionView, true);
                return;
            }

            if (context.interactionsThisTurn() > context.maxInteractionsPerTurn()) {
                logLoopDetected(method);
                context.executeDefaultAction();
                actionsPassed++;
                continue;
            }

            if (method != ClientCallbackMethod.GAME_SELECT) {
                finish(context.pendingActionResult(action, "non_priority_action", boardCursorParam), action, extractGameView(action), true);
                return;
            }

            String combatType = context.detectCombatSelect(action);
            if (combatType != null) {
                finish(
                    context.pendingActionResult(
                        action,
                        "combat",
                        boardCursorParam,
                        built -> built.combat_phase = combatType
                    ),
                    action,
                    extractGameView(action),
                    true
                );
                return;
            }

            if (yieldUntilMyTurn) {
                GameView gv = actionView;
                if (gv != null && context.username().equals(gv.getActivePlayerName())) {
                    yieldUntilMyTurn = false;
                } else {
                    context.clearPendingActionIfCurrent(action);
                    context.sendBooleanOrDie(action.gameId(), false, "passPriority:yield_my_turn");
                    actionsPassed++;
                    continue;
                }
            }

            if (yieldUntilEndOfTurn) {
                GameView gv = actionView;
                PhaseStep step = gv != null ? gv.getStep() : null;
                int turnNum = gv != null ? gv.getTurn() : yieldStartTurn;
                if (step == PhaseStep.END_TURN || step == PhaseStep.CLEANUP || turnNum > yieldStartTurn) {
                    String reason = (turnNum > yieldStartTurn) ? "turn_advanced" : "end_of_turn";
                    finish(context.pendingActionResult(action, reason, boardCursorParam), action, actionView, true);
                    return;
                }
                context.clearPendingActionIfCurrent(action);
                context.sendBooleanOrDie(action.gameId(), false, "passPriority:yield_end_of_turn");
                actionsPassed++;
                continue;
            }

            if (yieldUntilStackResolved) {
                GameView gv = actionView;
                if (!context.stackContains(gv, yieldUntilStackResolvedObjectId)) {
                    finish(context.stackResolvedResult(action, boardCursorParam), action, gv, true);
                    return;
                }
            }

            if (targetStep != null) {
                GameView gv = actionView;
                if (gv != null && gv.getStep() != null
                    && (gv.getStep() == targetStep || gv.getStep().isAfter(targetStep))) {
                    finish(context.stepYieldResult(action, gv, "reached_step", boardCursorParam), action, gv, true);
                    return;
                }
                context.clearPendingActionIfCurrent(action);
                context.sendBooleanOrDie(action.gameId(), false, "passPriority:step_yield");
                actionsPassed++;
                continue;
            }

            GameView viewForPlayableCheck = extractGameView(action);
            boolean hasPlayableCards = hasPlayableCards(viewForPlayableCheck);
            logPlayableCheck(action, viewForPlayableCheck, hasPlayableCards);

            if (hasPlayableCards && actionsPassed > 0) {
                finish(
                    context.pendingActionResult(
                        action,
                        "playable_cards",
                        boardCursorParam,
                        built -> built.has_playable_cards = true
                    ),
                    action,
                    viewForPlayableCheck,
                    true
                );
                return;
            }

            context.clearPendingActionIfCurrent(action);
            context.sendBooleanOrDie(action.gameId(), false, "passPriority:auto_pass");
            actionsPassed++;
        }
    }

    public void tick() {
        if (result.isDone()) {
            return;
        }
        waitLoops++;
        long now = System.currentTimeMillis();
        if (now - lastProgressLogAtMs >= 30_000) {
            lastProgressLogAtMs = now;
            loggerStillWaiting(now);
        }
        advance();
    }

    public boolean isDone() {
        return result.isDone();
    }

    public ActionResult cancel() {
        if (result.isDone()) {
            return result.join();
        }
        var cancelled = new ActionResult();
        cancelled.action_pending = false;
        cancelled.stop_reason = "cancelled";
        cancelled.game_seq = lastSeenGameSeq;
        finish(cancelled, null, context.lastGameView(), false);
        return cancelled;
    }

    public void finishWithDeliveryError(String error) {
        if (result.isDone()) {
            return;
        }
        var deliveryError = new ActionResult();
        deliveryError.action_pending = false;
        deliveryError.stop_reason = "game_over";
        deliveryError.error = error;
        finish(deliveryError, null, context.lastGameView(), false);
    }

    public void fail(Throwable t) {
        result.completeExceptionally(t);
    }

    public ActionResult awaitResult() throws InterruptedException, ExecutionException {
        return result.get();
    }

    private boolean initialize() {
        yieldStartTurn = context.lastTurnNumber();
        if (until == null) {
            return false;
        }

        PhaseStep stepTarget = STEP_PHASES.get(until);
        if (stepTarget != null) {
            targetStep = stepTarget;
            return true;
        }
        if (!CLIENT_SIDE_YIELDS.contains(until)) {
            var invalid = new ActionResult();
            var allValues = new ArrayList<>(STEP_PHASES.keySet());
            allValues.addAll(CLIENT_SIDE_YIELDS);
            invalid.error = "Invalid until value: " + until + ". Valid values: " + String.join(", ", allValues);
            finish(invalid, null, context.lastGameView(), false);
            return false;
        }

        UUID gameId = context.currentGameId();
        if (gameId == null) {
            var noGame = new ActionResult();
            noGame.error = "No active game for yield";
            finish(noGame, null, context.lastGameView(), false);
            return false;
        }

        PendingAction currentAction = context.currentDecisionAction();
        if (currentAction != null && currentAction.method() != ClientCallbackMethod.GAME_SELECT) {
            logBlockedByPending(currentAction);
            finish(
                context.pendingActionResult(currentAction, "non_priority_action", boardCursorParam),
                currentAction,
                extractGameView(currentAction),
                true
            );
            return false;
        }

        boolean armedClientSideYield = false;
        if ("stack_resolved".equals(until)) {
            UUID lowestStackObject = context.lowestStackObjectId(context.lastGameView());
            if (lowestStackObject != null) {
                yieldUntilStackResolved = true;
                yieldUntilStackResolvedObjectId = lowestStackObject;
                armedClientSideYield = true;
            }
        } else if ("my_turn".equals(until)) {
            yieldUntilMyTurn = true;
            armedClientSideYield = true;
        } else if ("end_of_turn".equals(until)) {
            yieldUntilEndOfTurn = true;
            armedClientSideYield = true;
        }

        if (armedClientSideYield && currentAction != null) {
            lastSeenGameSeq = currentAction.gameSeq();
            context.clearPendingActionIfCurrent(currentAction);
            context.sendBooleanOrDie(gameId, false, "passPriority:yield_arm");
            actionsPassed++;
        }
        return armedClientSideYield;
    }

    private boolean hasPlayableCards(GameView gameView) {
        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        if (playable == null || playable.isEmpty()) {
            return false;
        }
        for (Map.Entry<UUID, PlayableObjectStats> entry : playable.getObjects().entrySet()) {
            if (context.failedManaCast(entry.getKey())) {
                continue;
            }
            PlayableObjectStats stats = entry.getValue();
            List<String> abilityNames = stats.getPlayableAbilityNames();
            List<String> manaNames = stats.getAllManaAbilityNames();
            boolean allMana = !abilityNames.isEmpty() && manaNames.size() == abilityNames.size();
            if (!allMana) {
                return true;
            }
        }
        return false;
    }

    private GameView extractGameView(PendingAction action) {
        if (action.data() instanceof GameClientMessage gameClientMessage) {
            return gameClientMessage.getGameView();
        }
        return context.lastGameView();
    }

    private void maybeFinishGameOver() {
        if (context.lastActionableCallbackAt() > 0) {
            long absoluteIdle = System.currentTimeMillis() - context.lastActionableCallbackAt();
            if (absoluteIdle > ZOMBIE_GAME_TIMEOUT_MS) {
                context.declareZombieGame(absoluteIdle);
            }
        }
        if (context.superseded() || context.playerDead() || (context.activeGamesSize() == 0 && context.gameEverStarted()) || !context.clientRunning()) {
            long elapsed = System.currentTimeMillis() - startTimeMs;
            loggerGameOver(elapsed);
            var gameOver = new ActionResult();
            gameOver.action_pending = false;
            gameOver.stop_reason = "game_over";
            gameOver.game_seq = lastSeenGameSeq;
            finish(gameOver, null, context.lastGameView(), false);
        }
    }

    private void finish(ActionResult actionResult, PendingAction action, GameView view, boolean actionPending) {
        context.finalizePassPriorityResult(this, until, actionsPassed, action, view, actionResult, actionPending);
        result.complete(actionResult);
    }

    private void logEnter(boolean yieldActive) {
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).info(
            "[" + context.username() + "] passPriority ENTER: until=" + until
                + " yieldActive=" + yieldActive
                + " pendingAction=" + (context.currentPendingAction() != null)
                + " activeGames=" + context.activeGamesSize()
                + " lastActionableCallbackAt=" + context.lastActionableCallbackAt()
        );
    }

    private void logBlockedByPending(PendingAction currentAction) {
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).info(
            "[" + context.username() + "] passPriority: until=" + until
                + " blocked by pending " + currentAction.method()
                + " — returning choices instead of auto-passing"
        );
    }

    private void logLoopDetected(ClientCallbackMethod method) {
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).warn(
            "[" + context.username() + "] Loop detected (" + context.interactionsThisTurn()
                + " interactions on turn " + context.lastTurnNumber() + "), auto-passing " + method.name()
        );
    }

    private void logPlayableCheck(PendingAction action, GameView gameView, boolean hasPlayableCards) {
        int viewSeq = gameView != null ? gameView.getGameSeq() : -1;
        String viewStep = gameView != null && gameView.getStep() != null ? gameView.getStep().toString() : "null";
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).debug(
            "[" + context.username() + "] passPriority playable check:"
                + " callback_seq=" + action.gameSeq()
                + " view_seq=" + viewSeq
                + " view_step=" + viewStep
                + " hasPlayable=" + hasPlayableCards
                + " actionsPassed=" + actionsPassed
                + " thread=" + Thread.currentThread().getName()
        );
    }

    private void loggerStillWaiting(long now) {
        long totalElapsed = now - startTimeMs;
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).warn(
            "[" + context.username() + "] passPriority STILL WAITING:"
                + " elapsed=" + totalElapsed + "ms"
                + " waitLoops=" + waitLoops
                + " actionsPassed=" + actionsPassed
                + " pendingAction=" + (context.currentPendingAction() != null)
                + " playerDead=" + context.playerDead()
                + " activeGames=" + context.activeGamesSize()
                + " gameEverStarted=" + context.gameEverStarted()
                + " lastActionableCallbackAt="
                + (context.lastActionableCallbackAt() > 0 ? (now - context.lastActionableCallbackAt()) + "ms ago" : "never")
                + " lastCallbackReceivedAt="
                + (context.lastCallbackReceivedAt() > 0 ? (now - context.lastCallbackReceivedAt()) + "ms ago" : "never")
                + " currentGameId=" + context.currentGameId()
        );
    }

    private void loggerGameOver(long elapsed) {
        org.apache.log4j.Logger.getLogger(BridgePassPriorityFlow.class).info(
            "[" + context.username() + "] passPriority EXIT game_over:"
                + " elapsed=" + elapsed + "ms"
                + " playerDead=" + context.playerDead()
                + " activeGames=" + context.activeGamesSize()
                + " actionsPassed=" + actionsPassed
        );
    }

    private static final Map<String, PhaseStep> STEP_PHASES = Map.of(
        "upkeep", PhaseStep.UPKEEP,
        "draw", PhaseStep.DRAW,
        "precombat_main", PhaseStep.PRECOMBAT_MAIN,
        "begin_combat", PhaseStep.BEGIN_COMBAT,
        "declare_attackers", PhaseStep.DECLARE_ATTACKERS,
        "declare_blockers", PhaseStep.DECLARE_BLOCKERS,
        "end_combat", PhaseStep.END_COMBAT,
        "postcombat_main", PhaseStep.POSTCOMBAT_MAIN
    );

    private static final List<String> CLIENT_SIDE_YIELDS = List.of("my_turn", "end_of_turn", "stack_resolved");
    private static final long ZOMBIE_GAME_TIMEOUT_MS = 60 * 60 * 1000;
}

package mage.client.bridge;

import mage.client.bridge.BridgeCallbackHandler.DecisionBoundaryStatus;
import mage.client.bridge.BridgeCallbackHandler.DecisionBoundaryTransition;
import mage.client.bridge.BridgeCallbackHandler.ResponseDeliveryException;
import mage.client.bridge.tools.ActionResult;
import mage.constants.PhaseStep;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.view.AbilityPickerView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class BridgePassPriorityController {

    // Cross-turn yield values handled client-side. These used to be server-side
    // yields (sendPlayerAction -> skip()), but skip() bypasses waitResponseOpen()
    // which causes stale responses to answer the wrong waitForResponse(), producing
    // nondeterministic auto-passes. Client-side handling eliminates the race.
    private static final Set<String> CLIENT_SIDE_YIELDS = Set.of(
        "end_of_turn", "stack_resolved", "my_turn"
    );

    // Mapping from "until" parameter values to PhaseStep enum constants (client-side yield).
    // Only steps where players normally receive priority are exposed.
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

    // choose_action blocks indefinitely (like pass_priority) after taking an
    // action, waiting for the next callback so the LLM always wakes up to a
    // pending decision. Terminated by game-over / zombie detection.
    private static final long ZOMBIE_GAME_TIMEOUT_MS = 60 * 60 * 1000; // no actionable callback for 60min = zombie

    private static final Logger logger = Logger.getLogger(BridgeCallbackHandler.class);

    private final BridgeCallbackHandler handler;

    BridgePassPriorityController(BridgeCallbackHandler handler) {
        this.handler = handler;
    }

    /**
     * Pass priority. Without until: passes once and returns. With until set to a
     * step name (upkeep, draw, etc.): client-side yield that auto-passes until
     * the target step is reached. With until set to a cross-turn value
     * (end_of_turn, my_turn, stack_resolved): client-side yield that auto-passes
     * each callback locally via sendPlayerBoolean(false) until the yield
     * condition is met.
     *
     * All yield modes are client-side to avoid a race condition in XMage's
     * server-side skip() which bypasses waitResponseOpen(), allowing stale
     * responses to answer the wrong waitForResponse().
     *
     * Auto-handles mechanical callbacks (GAME_PLAY_MANA auto-cancel,
     * optional GAME_TARGET with no legal targets). Returns stop_reason indicating
     * why the call returned. When action_pending=true, also includes the full
     * action choices (same data as get_action_choices) so the LLM can respond
     * immediately without a separate round-trip.
     */
    ActionResult passPriority(String until, Long boardCursorParam) {
        try {
            return passPriorityImpl(until, boardCursorParam);
        } catch (ResponseDeliveryException e) {
            var result = new ActionResult();
            result.action_pending = false;
            result.stop_reason = "game_over";
            result.error = e.getMessage();
            handler.attachUnseenChat(result);
            return result;
        }
    }

    private ActionResult passPriorityImpl(String until, Long boardCursorParam) {
        handler.interactionsThisTurn++;

        int actionsPassed = 0;
        int lastSeenGameSeq = 0; // deterministic game_seq from actionable callbacks (not lastGameView)

        // Route the "until" parameter: check step phases first, then cross-turn yields
        boolean yieldActive = false;
        PhaseStep targetStep = null;
        boolean yieldUntilMyTurn = false;
        boolean yieldUntilEndOfTurn = false;
        boolean yieldUntilStackResolved = false;
        UUID yieldUntilStackResolvedObjectId = null;
        int yieldStartTurn = handler.lastTurnNumber;
        if (until != null) {
            targetStep = STEP_PHASES.get(until);
            if (targetStep != null) {
                // Client-side step yield: do NOT sendPlayerAction.
                yieldActive = true;
            } else if (CLIENT_SIDE_YIELDS.contains(until)) {
                UUID gameId = handler.currentGameId;
                if (gameId == null) {
                    var result = new ActionResult();
                    result.error = "No active game for yield";
                    handler.logPassPriorityReturn(until, actionsPassed, null, handler.lastGameView, result, false);
                    return result;
                }
                // If a real non-priority decision is already pending, return it
                // instead of arming a yield that would auto-pass through it.
                // This guard must run BEFORE the stack_resolved fast-path below,
                // which otherwise returns early with stop_reason="stack_resolved"
                // instead of "non_priority_action" when the stack is empty.
                PendingAction currentAction = handler.currentDecisionAction();
                if (currentAction != null
                        && currentAction.method() != ClientCallbackMethod.GAME_SELECT) {
                    logger.info("[" + handler.client.getUsername()
                        + "] passPriority: until=" + until
                        + " blocked by pending " + currentAction.method()
                        + " - returning choices instead of auto-passing");
                    ActionResult result = handler.pendingActionResult(
                        currentAction,
                        "non_priority_action",
                        boardCursorParam
                    );
                    handler.logPassPriorityReturn(
                        until,
                        actionsPassed,
                        currentAction,
                        extractGameView(currentAction.data()),
                        result,
                        true);
                    return result;
                }
                // For stack_resolved: only arm the client-side yield when there
                // is actually a stack object to watch. Otherwise this falls
                // through to normal one-pass priority advancement.
                boolean armedClientSideYield = false;
                if ("stack_resolved".equals(until)) {
                    GameView gv = handler.lastGameView;
                    UUID lowestStackObjectId = lowestStackObjectId(gv);
                    if (lowestStackObjectId != null) {
                        yieldUntilStackResolved = true;
                        yieldUntilStackResolvedObjectId = lowestStackObjectId;
                        armedClientSideYield = true;
                    }
                } else if ("my_turn".equals(until)) {
                    yieldUntilMyTurn = true;
                    armedClientSideYield = true;
                } else if ("end_of_turn".equals(until)) {
                    yieldUntilEndOfTurn = true;
                    armedClientSideYield = true;
                }
                // Auto-pass the current priority locally via sendPlayerBoolean
                // instead of sendPlayerAction+skip(). This avoids the race where
                // skip() bypasses waitResponseOpen() and stale responses answer
                // the wrong waitForResponse().
                //
                // Only auto-pass if there IS a pending GAME_SELECT action.
                // Without this guard, calling pass_priority when no callback has
                // arrived yet sends a stale sendPlayerBoolean(false) that the
                // XMage server consumes for the NEXT query - creating a one-response
                // offset between bridge and server. On slow CI machines this race
                // causes golden test flakes (missing snapshots, timeouts).
                if (armedClientSideYield && currentAction != null) {
                    lastSeenGameSeq = currentAction.gameSeq();
                    synchronized (handler.actionLock) {
                        handler.pendingAction = null;
                    }
                    handler.sendBooleanOrDie(gameId, false, "passPriority:yield_arm");
                    // The yield consumed the current priority - count it as a pass.
                    actionsPassed++;
                }
                yieldActive = armedClientSideYield;
            } else {
                var allValues = new ArrayList<>(STEP_PHASES.keySet());
                allValues.addAll(CLIENT_SIDE_YIELDS);
                var result = new ActionResult();
                result.error = "Invalid until value: " + until
                    + ". Valid values: " + String.join(", ", allValues);
                handler.logPassPriorityReturn(until, actionsPassed, null, handler.lastGameView, result, false);
                return result;
            }
        }

        long startTime = System.currentTimeMillis();
        long lastProgressLogAt = startTime;
        int waitLoops = 0;
        logger.info("[" + handler.client.getUsername() + "] passPriority ENTER: until=" + until
            + " yieldActive=" + yieldActive
            + " pendingAction=" + (handler.pendingAction != null)
            + " activeGames=" + handler.activeGames.size()
            + " lastActionableCallbackAt=" + handler.lastActionableCallbackAt);

        while (true) {
            PendingAction action = handler.pendingAction;
            if (action != null) {
                lastSeenGameSeq = action.gameSeq();
                DecisionBoundaryTransition transition =
                    handler.transitionToDecisionBoundary(action, "passPriority");
                if (transition.status() == DecisionBoundaryStatus.AUTO_HANDLED) {
                    actionsPassed++;
                    continue;
                }
                if (transition.status() == DecisionBoundaryStatus.CHANGED) {
                    continue;
                }
                action = transition.action();

                ClientCallbackMethod method = action.method();

                // Update game view and reset loop counter on turn change.
                // This MUST run before the loop detection check below, otherwise
                // the `continue` in the loop detection branch skips it and the
                // counter never resets, permanently disabling the player.
                // Check any callback carrying GameView, not just GAME_SELECT -
                // a new turn can start with upkeep triggers (GAME_TARGET, GAME_ASK, etc.).
                if (action.data() instanceof GameClientMessage gcm) {
                    GameView gv = gcm.getGameView();
                    if (gv != null) {
                        handler.updateLastGameView(gv, "passPriority:" + action.method().name());
                        int turn = gv.getTurn();
                        if (turn != handler.lastTurnNumber) {
                            handler.lastTurnNumber = turn;
                            handler.failedManaCasts.clear();
                            handler.interactionsThisTurn = 0;
                            handler.poolManaAttempts = 0;
                            handler.poolManaPayingForId = null;
                            handler.manaPlan = null;
                            handler.manaPlanAbilityIndex = null;
                        }
                    }
                }

                GameView actionView = (action.data() instanceof GameClientMessage gcm2)
                    ? gcm2.getGameView() : handler.lastGameView;

                // Step-specific yield: stop on any later turn, even if the target
                // step was skipped by auto-passes or never arrived as a callback.
                if (targetStep != null && handler.lastTurnNumber != yieldStartTurn) {
                    ActionResult result = handler.stepYieldResult(action, actionView, "step_not_reached", boardCursorParam);
                    handler.logPassPriorityReturn(until, actionsPassed, action, actionView, result, true);
                    return result;
                }

                // Generic loop detection: too many interactions this turn - auto-pass everything
                if (handler.interactionsThisTurn > handler.maxInteractionsPerTurn) {
                    logger.warn("[" + handler.client.getUsername() + "] Loop detected (" + handler.interactionsThisTurn
                        + " interactions on turn " + handler.lastTurnNumber + "), auto-passing " + method.name());
                    // Not a critical error - LLM is stuck in a loop, not a code bug
                    handler.executeDefaultAction();
                    actionsPassed++;
                    continue;
                }

                // Non-GAME_SELECT always needs LLM input - return immediately
                if (method != ClientCallbackMethod.GAME_SELECT) {
                    ActionResult result = handler.pendingActionResult(
                        action,
                        "non_priority_action",
                        boardCursorParam
                    );
                    handler.logPassPriorityReturn(
                        until,
                        actionsPassed,
                        action,
                        extractGameView(action.data()),
                        result,
                        true);
                    return result;
                }

                // Combat selections (declare attackers/blockers) always need LLM input
                String combatType = handler.detectCombatSelect(action);
                if (combatType != null) {
                    ActionResult result = handler.pendingActionResult(
                        action,
                        "combat",
                        boardCursorParam,
                        built -> built.combat_phase = combatType
                    );
                    handler.logPassPriorityReturn(
                        until,
                        actionsPassed,
                        action,
                        extractGameView(action.data()),
                        result,
                        true);
                    return result;
                }

                // Client-side cross-turn yield: my_turn
                // Auto-pass all callbacks during the opponent's turn. Once it's
                // our turn, clear the flag and fall through to the playable-cards
                // check (which will return if there are meaningful choices).
                if (yieldUntilMyTurn) {
                    GameView gv = actionView;
                    if (gv != null && handler.client.getUsername().equals(gv.getActivePlayerName())) {
                        // We've become the active player - stop yielding
                        yieldUntilMyTurn = false;
                        // Fall through to playable-cards check below
                    } else {
                        // Not our turn - auto-pass
                        synchronized (handler.actionLock) {
                            if (handler.pendingAction == action) {
                                handler.pendingAction = null;
                            }
                        }
                        handler.sendBooleanOrDie(action.gameId(), false, "passPriority:yield_my_turn");
                        actionsPassed++;
                        continue;
                    }
                }

                // Client-side yield: end_of_turn
                // Auto-pass all callbacks until the end of turn step is reached.
                if (yieldUntilEndOfTurn) {
                    GameView gv = actionView;
                    PhaseStep step = gv != null ? gv.getStep() : null;
                    int turnNum = gv != null ? gv.getTurn() : yieldStartTurn;
                    if (step == PhaseStep.END_TURN || step == PhaseStep.CLEANUP
                            || turnNum > yieldStartTurn) {
                        // End of turn reached (or turn advanced past END_TURN/CLEANUP
                        // due to server-side skip settings) - return immediately so we
                        // don't fall through to the playable-cards check, which loops
                        // forever for players with no playable non-mana cards.
                        String reason = (turnNum > yieldStartTurn)
                            ? "turn_advanced" : "end_of_turn";
                        ActionResult result = handler.pendingActionResult(
                            action, reason, boardCursorParam);
                        handler.logPassPriorityReturn(
                            until, actionsPassed, action, actionView, result, true);
                        return result;
                    } else {
                        // Not end of turn yet - auto-pass
                        synchronized (handler.actionLock) {
                            if (handler.pendingAction == action) {
                                handler.pendingAction = null;
                            }
                        }
                        handler.sendBooleanOrDie(action.gameId(), false, "passPriority:yield_end_of_turn");
                        actionsPassed++;
                        continue;
                    }
                }

                // Client-side cross-turn yield: stack_resolved
                // Watch the stack objects that existed when the yield started.
                // Once the lowest of those objects is gone, the next actionable
                // callback should wake the model instead of auto-passing again.
                if (yieldUntilStackResolved) {
                    GameView gv = actionView;
                    if (!stackContains(gv, yieldUntilStackResolvedObjectId)) {
                        ActionResult result = handler.stackResolvedResult(action, boardCursorParam);
                        handler.logPassPriorityReturn(until, actionsPassed, action, gv, result, true);
                        return result;
                    }
                    // A watched stack object is still present - keep auto-passing.
                }

                // Step-specific yield: check if we've reached the target step
                // Use the action's own GameView - lastGameView can be clobbered by GAME_UPDATE.
                if (targetStep != null) {
                    GameView gv = actionView;
                    if (gv != null && gv.getStep() != null
                            && (gv.getStep() == targetStep || gv.getStep().isAfter(targetStep))) {
                        // If a later same-turn callback overtook the target-step
                        // priority, stop immediately instead of auto-passing into
                        // an even later prompt.
                        ActionResult result = handler.stepYieldResult(action, gv, "reached_step", boardCursorParam);
                        handler.logPassPriorityReturn(until, actionsPassed, action, gv, result, true);
                        return result;
                    }
                    // Not at target step: auto-pass (skip playable-cards check)
                    synchronized (handler.actionLock) {
                        if (handler.pendingAction == action) {
                            handler.pendingAction = null;
                        }
                    }
                    handler.sendBooleanOrDie(action.gameId(), false, "passPriority:step_yield");
                    actionsPassed++;
                    continue;
                }

                // Check if there are playable cards (non-mana-only, excluding failed casts)
                // Use the action's own GameView, not lastGameView - a concurrent GAME_UPDATE
                // can overwrite lastGameView with a view from a different phase (forward overwrite).
                GameView viewForPlayableCheck = ((GameClientMessage) action.data()).getGameView();
                PlayableObjectsList playable = viewForPlayableCheck != null ? viewForPlayableCheck.getCanPlayObjects() : null;
                boolean hasPlayableCards = false;
                if (playable != null && !playable.isEmpty()) {
                    for (Map.Entry<UUID, PlayableObjectStats> entry : playable.getObjects().entrySet()) {
                        if (handler.failedManaCasts.contains(entry.getKey())) {
                            continue;
                        }
                        PlayableObjectStats stats = entry.getValue();
                        List<String> abilityNames = stats.getPlayableAbilityNames();
                        List<String> manaNames = stats.getAllManaAbilityNames();
                        boolean allMana = !abilityNames.isEmpty() && manaNames.size() == abilityNames.size();
                        if (!allMana) {
                            hasPlayableCards = true;
                            break;
                        }
                    }
                }

                // Determinism debugging: always log the playable-cards check result
                // to diagnose both Mode 1 (game_seq drift) and Mode 2 (phase divergence).
                {
                    int cbSeq = action.gameSeq();
                    int viewSeq = viewForPlayableCheck != null ? viewForPlayableCheck.getGameSeq() : -1;
                    String viewStep = viewForPlayableCheck != null && viewForPlayableCheck.getStep() != null
                        ? viewForPlayableCheck.getStep().toString() : "null";
                    logger.debug("[" + handler.client.getUsername() + "] passPriority playable check:"
                        + " callback_seq=" + cbSeq
                        + " view_seq=" + viewSeq
                        + " view_step=" + viewStep
                        + " hasPlayable=" + hasPlayableCards
                        + " actionsPassed=" + actionsPassed
                        + " thread=" + Thread.currentThread().getName());
                }

                if (hasPlayableCards && actionsPassed > 0) {
                    // Already passed at least once - return so LLM can decide
                    ActionResult result = handler.pendingActionResult(
                        action,
                        "playable_cards",
                        boardCursorParam,
                        built -> built.has_playable_cards = true
                    );
                    handler.logPassPriorityReturn(until, actionsPassed, action, viewForPlayableCheck, result, true);
                    return result;
                }
                // If we found playable cards on the first pass, intentionally
                // fall through and auto-pass once so the game advances.

                // No playable cards - auto-pass this priority
                synchronized (handler.actionLock) {
                    if (handler.pendingAction == action) {
                        handler.pendingAction = null;
                    }
                }
                handler.sendBooleanOrDie(action.gameId(), false, "passPriority:auto_pass");
                actionsPassed++;

                // Continue waiting for the server to send us the next callback
            }

            synchronized (handler.actionLock) {
                try {
                    handler.actionLock.wait(200);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            waitLoops++;

            // Periodic progress log: every 30s when the loop is spinning without returning
            {
                long now = System.currentTimeMillis();
                if (now - lastProgressLogAt >= 30_000) {
                    lastProgressLogAt = now;
                    long totalElapsed = now - startTime;
                    logger.warn("[" + handler.client.getUsername() + "] passPriority STILL WAITING:"
                        + " elapsed=" + totalElapsed + "ms"
                        + " waitLoops=" + waitLoops
                        + " actionsPassed=" + actionsPassed
                        + " pendingAction=" + (handler.pendingAction != null)
                        + " playerDead=" + handler.playerDead
                        + " activeGames=" + handler.activeGames.size()
                        + " gameEverStarted=" + handler.gameEverStarted
                        + " lastActionableCallbackAt=" + (handler.lastActionableCallbackAt > 0 ? (now - handler.lastActionableCallbackAt) + "ms ago" : "never")
                        + " lastCallbackReceivedAt=" + (handler.lastCallbackReceivedAt > 0 ? (now - handler.lastCallbackReceivedAt) + "ms ago" : "never")
                        + " currentGameId=" + handler.currentGameId);
                }
            }

            // Game over bail-out: don't block forever if the game ended
            if (handler.superseded
                    || handler.playerDead
                    || (handler.activeGames.isEmpty() && handler.gameEverStarted)
                    || !handler.client.isRunning()) {
                long elapsed = System.currentTimeMillis() - startTime;
                logger.info("[" + handler.client.getUsername() + "] passPriority EXIT game_over:"
                    + " elapsed=" + elapsed + "ms"
                    + " playerDead=" + handler.playerDead
                    + " activeGames=" + handler.activeGames.size()
                    + " actionsPassed=" + actionsPassed);
                var result = new ActionResult();
                result.action_pending = false;
                result.stop_reason = "game_over";
                // Use the last actionable callback's game_seq, not lastGameView which
                // races with GAME_OVER / END_GAME_INFO callback ordering.
                result.game_seq = lastSeenGameSeq;
                GameView gvSnap = handler.lastGameView;
                handler.attachUnseenChat(result);
                handler.logPassPriorityReturn(until, actionsPassed, null, gvSnap, result, false);
                return result;
            }

            // Zombie game detection: no actionable callback for too long means the
            // server game thread is dead. Declare the game over so the pilot exits.
            if (handler.lastActionableCallbackAt > 0) {
                long absoluteIdle = System.currentTimeMillis() - handler.lastActionableCallbackAt;
                if (absoluteIdle > ZOMBIE_GAME_TIMEOUT_MS) {
                    logger.error("[" + handler.client.getUsername() + "] Zombie game detected: "
                            + "no actionable callback for " + absoluteIdle + "ms, declaring game dead");
                    handler.logError("Zombie game detected: no actionable callback for " + absoluteIdle + "ms");
                    handler.playerDead = true;
                }
            }
        }

        // InterruptedException break
        var result = new ActionResult();
        result.action_pending = false;
        result.stop_reason = "interrupted";
        result.game_seq = lastSeenGameSeq;
        GameView gvSnap = handler.lastGameView;
        handler.attachUnseenChat(result);
        handler.logPassPriorityReturn(until, actionsPassed, null, gvSnap, result, false);
        return result;
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

    private static UUID lowestStackObjectId(GameView gameView) {
        if (gameView == null || gameView.getStack() == null || gameView.getStack().isEmpty()) {
            return null;
        }
        // SpellStack iterates top-first and CardsView preserves insertion order,
        // so the last key is the lowest stack object present when the yield starts.
        UUID lowest = null;
        for (UUID stackObjectId : gameView.getStack().keySet()) {
            lowest = stackObjectId;
        }
        return lowest;
    }

    private static boolean stackContains(GameView gameView, UUID stackObjectId) {
        return gameView != null
            && gameView.getStack() != null
            && stackObjectId != null
            && gameView.getStack().containsKey(stackObjectId);
    }
}

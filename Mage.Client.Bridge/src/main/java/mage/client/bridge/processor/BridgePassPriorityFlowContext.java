package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.constants.PhaseStep;
import mage.view.GameView;

import java.util.UUID;
import java.util.function.Consumer;

public interface BridgePassPriorityFlowContext {
    String username();

    PendingAction currentPendingAction();

    PendingAction currentDecisionAction();

    PendingAction resolvePassPriorityAction(PendingAction action);

    GameView preparePassPriorityActionView(PendingAction action);

    int interactionsThisTurn();

    int maxInteractionsPerTurn();

    void executeDefaultAction();

    String detectCombatSelect(PendingAction action);

    ActionResult pendingActionResult(PendingAction action, String stopReason, Long boardCursorParam);

    ActionResult pendingActionResult(
        PendingAction action,
        String stopReason,
        Long boardCursorParam,
        Consumer<ActionResult> customizer
    );

    ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam);

    ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam);

    UUID lowestStackObjectId(GameView gameView);

    boolean stackContains(GameView gameView, UUID stackObjectId);

    boolean clearPendingActionIfCurrent(PendingAction action);

    void sendBooleanOrDie(UUID gameId, boolean data, String sendContext);

    UUID currentGameId();

    GameView lastGameView();

    int lastTurnNumber();

    boolean hasActiveGame();

    boolean superseded();

    boolean playerDead();

    boolean gameEverStarted();

    boolean clientRunning();

    long lastActionableCallbackAt();

    long lastCallbackReceivedAt();

    void declareZombieGame(long absoluteIdleMs);

    boolean failedManaCast(UUID objectId);

    void finalizePassPriorityResult(
        BridgePassPriorityFlow flow,
        String until,
        int actionsPassed,
        PendingAction action,
        GameView view,
        ActionResult result,
        boolean actionPending
    );
}

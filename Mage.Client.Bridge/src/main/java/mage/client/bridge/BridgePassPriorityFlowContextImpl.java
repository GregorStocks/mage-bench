package mage.client.bridge;

import mage.client.bridge.processor.BridgeDecisionState;
import mage.client.bridge.processor.BridgeGameState;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowContext;
import mage.client.bridge.tools.ActionResult;
import mage.view.GameView;

import java.util.UUID;
import java.util.function.Consumer;

final class BridgePassPriorityFlowContextImpl implements BridgePassPriorityFlowContext {
    private final BridgeCallbackHandler handler;
    private final BridgeDecisionState decisionState;
    private final BridgeGameState gameState;

    BridgePassPriorityFlowContextImpl(
            BridgeCallbackHandler handler,
            BridgeDecisionState decisionState,
            BridgeGameState gameState) {
        this.handler = handler;
        this.decisionState = decisionState;
        this.gameState = gameState;
    }

    @Override
    public String username() {
        return handler.username();
    }

    @Override
    public PendingAction currentPendingAction() {
        return decisionState.pendingAction();
    }

    @Override
    public PendingAction currentDecisionAction() {
        return handler.currentDecisionAction();
    }

    @Override
    public PendingAction resolvePassPriorityAction(PendingAction action) {
        return handler.resolvePassPriorityAction(action);
    }

    @Override
    public GameView preparePassPriorityActionView(PendingAction action) {
        return handler.preparePassPriorityActionView(action);
    }

    @Override
    public int interactionsThisTurn() {
        return handler.interactionsThisTurn();
    }

    @Override
    public int maxInteractionsPerTurn() {
        return handler.maxInteractionsPerTurn();
    }

    @Override
    public void executeDefaultAction() {
        handler.executeDefaultAction();
    }

    @Override
    public String detectCombatSelect(PendingAction action) {
        return handler.detectCombatSelect(action);
    }

    @Override
    public ActionResult pendingActionResult(PendingAction action, String stopReason, Long boardCursorParam) {
        return handler.pendingActionResult(action, stopReason, boardCursorParam);
    }

    @Override
    public ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam,
            Consumer<ActionResult> customizer) {
        return handler.pendingActionResult(action, stopReason, boardCursorParam, customizer);
    }

    @Override
    public ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam) {
        return handler.stepYieldResult(action, gameView, stopReason, boardCursorParam);
    }

    @Override
    public ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
        return handler.stackResolvedResult(action, boardCursorParam);
    }

    @Override
    public UUID lowestStackObjectId(GameView gameView) {
        return handler.lowestStackObjectId(gameView);
    }

    @Override
    public boolean stackContains(GameView gameView, UUID stackObjectId) {
        return handler.stackContains(gameView, stackObjectId);
    }

    @Override
    public boolean clearPendingActionIfCurrent(PendingAction action) {
        return handler.clearPendingActionIfCurrent(action);
    }

    @Override
    public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
        handler.sendBooleanOrDie(gameId, data, sendContext);
    }

    @Override
    public UUID currentGameId() {
        return gameState.currentGameId();
    }

    @Override
    public GameView lastGameView() {
        return gameState.lastGameView();
    }

    @Override
    public int lastTurnNumber() {
        return handler.lastTurnNumber();
    }

    @Override
    public boolean hasActiveGame() {
        return gameState.hasActiveGame();
    }

    @Override
    public boolean superseded() {
        return gameState.superseded();
    }

    @Override
    public boolean playerDead() {
        return gameState.playerDead();
    }

    @Override
    public boolean gameEverStarted() {
        return gameState.gameEverStarted();
    }

    @Override
    public boolean clientRunning() {
        return handler.clientRunning();
    }

    @Override
    public long lastActionableCallbackAt() {
        return gameState.lastActionableCallbackAt();
    }

    @Override
    public long lastCallbackReceivedAt() {
        return gameState.lastCallbackReceivedAt();
    }

    @Override
    public void declareZombieGame(long absoluteIdleMs) {
        handler.declareZombieGame(absoluteIdleMs);
    }

    @Override
    public boolean failedManaCast(UUID objectId) {
        return handler.failedManaCast(objectId);
    }

    @Override
    public void finalizePassPriorityResult(
            BridgePassPriorityFlow flow,
            String until,
            int actionsPassed,
            PendingAction action,
            GameView view,
            ActionResult result,
            boolean actionPending) {
        handler.finalizePassPriorityResult(flow, until, actionsPassed, action, view, result, actionPending);
    }
}

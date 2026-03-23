package mage.client.bridge;

import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowContext;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.tools.ActionResult;
import mage.view.GameView;

import java.util.UUID;
import java.util.function.Consumer;

final class BridgePassPriorityFlowContextImpl implements BridgePassPriorityFlowContext {
    private final BridgeCallbackHandler handler;
    private final BridgeProcessorState processorState;

    BridgePassPriorityFlowContextImpl(
            BridgeCallbackHandler handler,
            BridgeProcessorState processorState) {
        this.handler = handler;
        this.processorState = processorState;
    }

    @Override
    public String username() {
        return handler.username();
    }

    @Override
    public PendingAction currentPendingAction() {
        return processorState.decisionState().pendingAction();
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
        return processorState.gameState().currentGameId();
    }

    @Override
    public GameView lastGameView() {
        return processorState.gameState().lastGameView();
    }

    @Override
    public int lastTurnNumber() {
        return handler.lastTurnNumber();
    }

    @Override
    public boolean hasActiveGame() {
        return processorState.gameState().hasActiveGame();
    }

    @Override
    public boolean superseded() {
        return processorState.gameState().superseded();
    }

    @Override
    public boolean playerDead() {
        return processorState.gameState().playerDead();
    }

    @Override
    public boolean gameEverStarted() {
        return processorState.gameState().gameEverStarted();
    }

    @Override
    public boolean clientRunning() {
        return handler.clientRunning();
    }

    @Override
    public long lastActionableCallbackAt() {
        return processorState.gameState().lastActionableCallbackAt();
    }

    @Override
    public long lastCallbackReceivedAt() {
        return processorState.gameState().lastCallbackReceivedAt();
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

package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.view.GameView;

import java.util.UUID;
import java.util.function.Consumer;

public final class BridgePassPriorityFlowContextImpl implements BridgePassPriorityFlowContext {
    private final BridgeDecisionFlowService decisionFlowService;
    private final BridgeProcessorState processorState;

    public BridgePassPriorityFlowContextImpl(
            BridgeDecisionFlowService decisionFlowService,
            BridgeProcessorState processorState) {
        this.decisionFlowService = decisionFlowService;
        this.processorState = processorState;
    }

    @Override
    public String username() {
        return decisionFlowService.username();
    }

    @Override
    public PendingAction currentPendingAction() {
        return processorState.decisionState().pendingAction();
    }

    @Override
    public PendingAction currentDecisionAction() {
        return decisionFlowService.currentDecisionAction();
    }

    @Override
    public PendingAction resolvePassPriorityAction(PendingAction action) {
        return decisionFlowService.resolvePassPriorityAction(action);
    }

    @Override
    public GameView preparePassPriorityActionView(PendingAction action) {
        return decisionFlowService.preparePassPriorityActionView(action);
    }

    @Override
    public int interactionsThisTurn() {
        return decisionFlowService.interactionsThisTurn();
    }

    @Override
    public int maxInteractionsPerTurn() {
        return decisionFlowService.maxInteractionsPerTurn();
    }

    @Override
    public void executeDefaultAction() {
        decisionFlowService.executeDefaultAction();
    }

    @Override
    public String detectCombatSelect(PendingAction action) {
        return decisionFlowService.detectCombatSelect(action);
    }

    @Override
    public ActionResult pendingActionResult(PendingAction action, String stopReason, Long boardCursorParam) {
        return decisionFlowService.pendingActionResult(action, stopReason, boardCursorParam);
    }

    @Override
    public ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam,
            Consumer<ActionResult> customizer) {
        return decisionFlowService.pendingActionResult(action, stopReason, boardCursorParam, customizer);
    }

    @Override
    public ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam) {
        return decisionFlowService.stepYieldResult(action, gameView, stopReason, boardCursorParam);
    }

    @Override
    public ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
        return decisionFlowService.stackResolvedResult(action, boardCursorParam);
    }

    @Override
    public UUID lowestStackObjectId(GameView gameView) {
        return decisionFlowService.lowestStackObjectId(gameView);
    }

    @Override
    public boolean stackContains(GameView gameView, UUID stackObjectId) {
        return decisionFlowService.stackContains(gameView, stackObjectId);
    }

    @Override
    public boolean clearPendingActionIfCurrent(PendingAction action) {
        return decisionFlowService.clearPendingActionIfCurrent(action);
    }

    @Override
    public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
        decisionFlowService.sendBooleanOrDie(gameId, data, sendContext);
    }

    @Override
    public UUID currentGameId() {
        return decisionFlowService.currentGameId();
    }

    @Override
    public GameView lastGameView() {
        return decisionFlowService.lastGameView();
    }

    @Override
    public int lastTurnNumber() {
        return decisionFlowService.lastTurnNumber();
    }

    @Override
    public boolean hasActiveGame() {
        return decisionFlowService.hasActiveGame();
    }

    @Override
    public boolean superseded() {
        return decisionFlowService.superseded();
    }

    @Override
    public boolean playerDead() {
        return decisionFlowService.playerDead();
    }

    @Override
    public boolean gameEverStarted() {
        return decisionFlowService.gameEverStarted();
    }

    @Override
    public boolean clientRunning() {
        return decisionFlowService.clientRunning();
    }

    @Override
    public long lastActionableCallbackAt() {
        return decisionFlowService.lastActionableCallbackAt();
    }

    @Override
    public long lastCallbackReceivedAt() {
        return decisionFlowService.lastCallbackReceivedAt();
    }

    @Override
    public void declareZombieGame(long absoluteIdleMs) {
        decisionFlowService.declareZombieGame(absoluteIdleMs);
    }

    @Override
    public boolean failedManaCast(UUID objectId) {
        return decisionFlowService.failedManaCast(objectId);
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
        decisionFlowService.finalizePassPriorityResult(
            flow,
            until,
            actionsPassed,
            action,
            view,
            result,
            actionPending
        );
    }
}

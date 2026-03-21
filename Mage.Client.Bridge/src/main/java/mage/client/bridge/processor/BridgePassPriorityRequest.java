package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;
import mage.constants.PhaseStep;

import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public final class BridgePassPriorityRequest {
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

    public BridgePassPriorityRequest(String until, Long boardCursorParam) {
        this.until = until;
        this.boardCursorParam = boardCursorParam;
    }

    public String until() {
        return until;
    }

    public Long boardCursorParam() {
        return boardCursorParam;
    }

    public int actionsPassed() {
        return actionsPassed;
    }

    public void incrementActionsPassed() {
        actionsPassed++;
    }

    public int lastSeenGameSeq() {
        return lastSeenGameSeq;
    }

    public void setLastSeenGameSeq(int lastSeenGameSeq) {
        this.lastSeenGameSeq = lastSeenGameSeq;
    }

    public PhaseStep targetStep() {
        return targetStep;
    }

    public void setTargetStep(PhaseStep targetStep) {
        this.targetStep = targetStep;
    }

    public boolean yieldUntilMyTurn() {
        return yieldUntilMyTurn;
    }

    public void setYieldUntilMyTurn(boolean yieldUntilMyTurn) {
        this.yieldUntilMyTurn = yieldUntilMyTurn;
    }

    public boolean yieldUntilEndOfTurn() {
        return yieldUntilEndOfTurn;
    }

    public void setYieldUntilEndOfTurn(boolean yieldUntilEndOfTurn) {
        this.yieldUntilEndOfTurn = yieldUntilEndOfTurn;
    }

    public boolean yieldUntilStackResolved() {
        return yieldUntilStackResolved;
    }

    public void setYieldUntilStackResolved(boolean yieldUntilStackResolved) {
        this.yieldUntilStackResolved = yieldUntilStackResolved;
    }

    public UUID yieldUntilStackResolvedObjectId() {
        return yieldUntilStackResolvedObjectId;
    }

    public void setYieldUntilStackResolvedObjectId(UUID yieldUntilStackResolvedObjectId) {
        this.yieldUntilStackResolvedObjectId = yieldUntilStackResolvedObjectId;
    }

    public int yieldStartTurn() {
        return yieldStartTurn;
    }

    public void setYieldStartTurn(int yieldStartTurn) {
        this.yieldStartTurn = yieldStartTurn;
    }

    public long startTimeMs() {
        return startTimeMs;
    }

    public void setStartTimeMs(long startTimeMs) {
        this.startTimeMs = startTimeMs;
    }

    public long lastProgressLogAtMs() {
        return lastProgressLogAtMs;
    }

    public void setLastProgressLogAtMs(long lastProgressLogAtMs) {
        this.lastProgressLogAtMs = lastProgressLogAtMs;
    }

    public int waitLoops() {
        return waitLoops;
    }

    public void incrementWaitLoops() {
        waitLoops++;
    }

    public boolean isDone() {
        return result.isDone();
    }

    public void complete(ActionResult value) {
        result.complete(value);
    }

    public void completeExceptionally(Throwable t) {
        result.completeExceptionally(t);
    }

    public ActionResult awaitResult() {
        try {
            return result.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for pass_priority", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("pass_priority request failed", cause);
        }
    }

    public ActionResult awaitResult(long timeoutMs) throws InterruptedException, ExecutionException, TimeoutException {
        return result.get(timeoutMs, TimeUnit.MILLISECONDS);
    }
}

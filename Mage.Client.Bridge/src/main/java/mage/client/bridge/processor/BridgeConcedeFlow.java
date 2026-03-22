package mage.client.bridge.processor;

import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public final class BridgeConcedeFlow {
    private final UUID gameId;
    private final CompletableFuture<Boolean> result = new CompletableFuture<>();

    public BridgeConcedeFlow(UUID gameId) {
        this.gameId = gameId;
    }

    public static BridgeConcedeFlow completed(UUID gameId, boolean value) {
        BridgeConcedeFlow flow = new BridgeConcedeFlow(gameId);
        flow.complete(value);
        return flow;
    }

    public UUID gameId() {
        return gameId;
    }

    public boolean isDone() {
        return result.isDone();
    }

    public boolean awaitResult() throws InterruptedException, ExecutionException {
        return result.get();
    }

    public void complete(boolean value) {
        result.complete(value);
    }

    public void fail(Throwable t) {
        result.completeExceptionally(t);
    }
}

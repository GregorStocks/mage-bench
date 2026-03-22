package mage.client.bridge.processor;

import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public final class BridgeStartGameFlow {
    private volatile UUID expectedTableId;
    private final CompletableFuture<Boolean> result = new CompletableFuture<>();

    public BridgeStartGameFlow(UUID expectedTableId) {
        this.expectedTableId = expectedTableId;
    }

    public UUID expectedTableId() {
        return expectedTableId;
    }

    public void setExpectedTableId(UUID expectedTableId) {
        this.expectedTableId = expectedTableId;
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

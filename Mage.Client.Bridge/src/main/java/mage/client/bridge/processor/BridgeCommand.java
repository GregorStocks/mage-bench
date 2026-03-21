package mage.client.bridge.processor;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public abstract class BridgeCommand<T> implements BridgeProcessorMessage {
    private final CompletableFuture<T> result = new CompletableFuture<>();

    public abstract T execute();

    public final T awaitResult() {
        try {
            return result.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for bridge processor", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("Bridge processor command failed", cause);
        }
    }

    final void complete(T value) {
        result.complete(value);
    }

    final void completeExceptionally(Throwable t) {
        result.completeExceptionally(t);
    }
}

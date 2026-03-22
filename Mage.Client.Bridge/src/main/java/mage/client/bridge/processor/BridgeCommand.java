package mage.client.bridge.processor;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.function.Supplier;

public abstract class BridgeCommand<T> implements BridgeProcessorMessage {
    private final CompletableFuture<T> result = new CompletableFuture<>();

    public abstract T execute();

    public static <T> BridgeCommand<T> of(Supplier<T> supplier) {
        return new BridgeCommand<>() {
            @Override
            public T execute() {
                return supplier.get();
            }
        };
    }

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

    public final T awaitResultPreservingInterrupt() {
        boolean interrupted = false;
        try {
            while (true) {
                try {
                    return result.get();
                } catch (InterruptedException e) {
                    interrupted = true;
                } catch (ExecutionException e) {
                    Throwable cause = e.getCause();
                    if (cause instanceof RuntimeException runtimeException) {
                        throw runtimeException;
                    }
                    throw new IllegalStateException("Bridge processor command failed", cause);
                }
            }
        } finally {
            if (interrupted) {
                Thread.currentThread().interrupt();
            }
        }
    }

    final void complete(T value) {
        result.complete(value);
    }

    final void completeExceptionally(Throwable t) {
        result.completeExceptionally(t);
    }
}

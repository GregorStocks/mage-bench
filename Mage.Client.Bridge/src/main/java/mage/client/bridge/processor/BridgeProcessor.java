package mage.client.bridge.processor;

import org.apache.log4j.Logger;

import java.util.Objects;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.function.Consumer;

public final class BridgeProcessor {
    private final BlockingQueue<BridgeProcessorMessage> mailbox = new LinkedBlockingQueue<>();
    private final Thread thread;
    private final Logger logger;
    private final String username;
    private final Consumer<BridgeCallbackEvent> callbackHandler;
    private Consumer<BridgeProcessorMessage> afterMessageHook = message -> {};
    private volatile boolean closed = false;

    public BridgeProcessor(String username, Logger logger, Consumer<BridgeCallbackEvent> callbackHandler) {
        this.username = username;
        this.logger = logger;
        this.callbackHandler = callbackHandler;
        this.thread = new Thread(this::runLoop, "bridge-processor-" + username);
        this.thread.setDaemon(true);
    }

    public void start() {
        thread.start();
    }

    public void setAfterMessageHook(Consumer<BridgeProcessorMessage> afterMessageHook) {
        this.afterMessageHook = Objects.requireNonNull(afterMessageHook);
    }

    public void enqueueCallback(BridgeCallbackEvent event) {
        if (closed) {
            logger.warn("[" + username + "] Dropping callback after processor shutdown: "
                + event.method());
            return;
        }
        mailbox.offer(event);
    }

    public <T> T submit(BridgeCommand<T> command) {
        if (Thread.currentThread() == thread) {
            return command.execute();
        }
        if (closed) {
            throw new IllegalStateException("Bridge processor is shut down");
        }
        mailbox.offer(command);
        return command.awaitResult();
    }

    public <T> T submitPreservingInterrupt(BridgeCommand<T> command) {
        if (Thread.currentThread() == thread) {
            return command.execute();
        }
        if (closed) {
            throw new IllegalStateException("Bridge processor is shut down");
        }
        mailbox.offer(command);
        return command.awaitResultPreservingInterrupt();
    }

    public boolean isProcessorThread() {
        return Thread.currentThread() == thread;
    }

    public void shutdown(String reason) {
        if (closed) {
            return;
        }
        closed = true;
        mailbox.offer(new BridgeProcessorShutdown(reason));
    }

    private void runLoop() {
        while (true) {
            BridgeProcessorMessage message;
            try {
                message = mailbox.take();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }

            if (message instanceof BridgeProcessorShutdown shutdown) {
                logger.info("[" + username + "] Bridge processor stopped: " + shutdown.reason());
                return;
            }
            if (message instanceof BridgeCallbackEvent event) {
                callbackHandler.accept(event);
                runAfterMessageHook(message, "callback");
                continue;
            }
            if (message instanceof BridgeCommand<?> command) {
                executeCommand(command);
            }
        }
    }

    private void runAfterMessageHook(BridgeProcessorMessage message, String context) {
        try {
            afterMessageHook.accept(message);
        } catch (Throwable hookFailure) {
            logger.error("[" + username + "] Bridge processor after-message hook failed on " + context, hookFailure);
        }
    }

    private <T> void executeCommand(BridgeCommand<T> command) {
        T value = null;
        Throwable failure = null;
        try {
            value = command.execute();
        } catch (Throwable t) {
            failure = t;
        }

        try {
            afterMessageHook.accept(command);
        } catch (Throwable hookFailure) {
            if (failure == null) {
                failure = hookFailure;
            } else {
                failure.addSuppressed(hookFailure);
            }
        }

        if (failure == null) {
            command.complete(value);
        } else {
            command.completeExceptionally(failure);
        }
    }
}

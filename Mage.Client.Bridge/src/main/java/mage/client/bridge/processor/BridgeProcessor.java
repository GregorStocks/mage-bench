package mage.client.bridge.processor;

import org.apache.log4j.Logger;

import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.function.Consumer;

public final class BridgeProcessor {
    private final BlockingQueue<BridgeProcessorMessage> mailbox = new LinkedBlockingQueue<>();
    private final Thread thread;
    private final Logger logger;
    private final String username;
    private final Consumer<BridgeCallbackEvent> callbackHandler;
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
                continue;
            }
            if (message instanceof BridgeCommand<?> command) {
                executeCommand(command);
            }
        }
    }

    private <T> void executeCommand(BridgeCommand<T> command) {
        try {
            T value = command.execute();
            command.complete(value);
        } catch (Throwable t) {
            command.completeExceptionally(t);
        }
    }
}

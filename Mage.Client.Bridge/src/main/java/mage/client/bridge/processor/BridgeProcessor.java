package mage.client.bridge.processor;

import org.apache.log4j.Logger;

import java.util.ArrayDeque;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

public final class BridgeProcessor {
    private final BlockingQueue<BridgeProcessorMessage> mailbox = new LinkedBlockingQueue<>();
    private final ArrayDeque<BridgeCommand<?>> deferredCommands = new ArrayDeque<>();
    private final Thread thread;
    private final Logger logger;
    private final String username;
    private final Consumer<BridgeCallbackEvent> callbackHandler;
    private BridgeProcessorShutdown pendingShutdown = null;
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

    public boolean isProcessorThread() {
        return Thread.currentThread() == thread;
    }

    /**
     * While a command is running on the processor thread, callbacks still need
     * to make progress. This pumps at most one callback from the shared mailbox
     * and defers any nested commands until the active command returns.
     */
    public boolean processNextCallback(long timeoutMs) {
        if (Thread.currentThread() != thread) {
            throw new IllegalStateException("processNextCallback requires the bridge processor thread");
        }
        long deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        while (true) {
            long remainingNanos = deadlineNanos - System.nanoTime();
            if (remainingNanos <= 0) {
                return false;
            }

            BridgeProcessorMessage message;
            try {
                message = mailbox.poll(remainingNanos, TimeUnit.NANOSECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }

            if (message == null) {
                return false;
            }
            if (message instanceof BridgeCallbackEvent event) {
                callbackHandler.accept(event);
                return true;
            }
            if (message instanceof BridgeCommand<?> command) {
                deferredCommands.addLast(command);
                continue;
            }
            if (message instanceof BridgeProcessorShutdown shutdown) {
                pendingShutdown = shutdown;
                return false;
            }
        }
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
                message = takeNextMessage();
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

    private BridgeProcessorMessage takeNextMessage() throws InterruptedException {
        if (pendingShutdown != null) {
            BridgeProcessorShutdown shutdown = pendingShutdown;
            pendingShutdown = null;
            return shutdown;
        }
        BridgeCommand<?> deferredCommand = deferredCommands.pollFirst();
        if (deferredCommand != null) {
            return deferredCommand;
        }
        return mailbox.take();
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

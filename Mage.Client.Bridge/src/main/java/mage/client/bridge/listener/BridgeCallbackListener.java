package mage.client.bridge.listener;

import mage.client.bridge.BridgeCallbackHandler;
import mage.interfaces.callback.ClientCallback;
import org.apache.log4j.Logger;

import java.util.Objects;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;

public final class BridgeCallbackListener {
    private final BlockingQueue<BridgeCallbackListenerMessage> mailbox = new LinkedBlockingQueue<>();
    private final Logger logger;
    private final String username;
    private final Thread thread;
    private volatile boolean closed = false;

    public BridgeCallbackListener(String username, Logger logger) {
        this.username = Objects.requireNonNull(username);
        this.logger = Objects.requireNonNull(logger);
        this.thread = new Thread(this::runLoop, "bridge-listener-" + username);
        this.thread.setDaemon(true);
    }

    public void start() {
        thread.start();
    }

    public void enqueue(BridgeCallbackHandler handler, ClientCallback callback) {
        Objects.requireNonNull(handler);
        Objects.requireNonNull(callback);
        if (closed) {
            logger.warn("[" + username + "] Dropping callback after listener shutdown: "
                    + callback.getMethod());
            return;
        }
        mailbox.offer(new BridgeIncomingCallback(handler, callback));
    }

    public void awaitIdle() throws InterruptedException {
        if (closed) {
            return;
        }
        BridgeCallbackListenerBarrier barrier = new BridgeCallbackListenerBarrier();
        mailbox.offer(barrier);
        barrier.await();
    }

    public void shutdown(String reason) {
        if (closed) {
            return;
        }
        closed = true;
        mailbox.offer(new BridgeCallbackListenerShutdown(reason));
    }

    private void runLoop() {
        while (true) {
            BridgeCallbackListenerMessage message;
            try {
                message = mailbox.take();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }

            if (message instanceof BridgeCallbackListenerShutdown shutdown) {
                logger.info("[" + username + "] Bridge callback listener stopped: " + shutdown.reason());
                return;
            }
            if (message instanceof BridgeCallbackListenerBarrier barrier) {
                barrier.complete();
                continue;
            }
            if (message instanceof BridgeIncomingCallback incoming) {
                incoming.handler().handleCallback(incoming.callback());
                continue;
            }
            throw new IllegalStateException("Unknown bridge callback listener message: " + message.getClass().getName());
        }
    }
}

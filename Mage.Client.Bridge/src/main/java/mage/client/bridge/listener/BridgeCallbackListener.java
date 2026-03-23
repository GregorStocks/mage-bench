package mage.client.bridge.listener;

import mage.client.bridge.BridgeCallbackHandler;
import mage.interfaces.callback.ClientCallback;
import org.apache.log4j.Logger;

import java.util.Objects;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;

public final class BridgeCallbackListener {
    private final BlockingQueue<BridgeCallbackListenerMessage> mailbox = new LinkedBlockingQueue<>();
    private final Object lifecycleLock = new Object();
    private final CountDownLatch stopped = new CountDownLatch(1);
    private final Logger logger;
    private final String username;
    private final Thread thread;
    private volatile boolean closed = false;
    private volatile String shutdownReason = "shutdown";

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
        synchronized (lifecycleLock) {
            if (closed) {
                logger.warn("[" + username + "] Dropping callback after listener shutdown: "
                        + callback.getMethod());
                return;
            }
            mailbox.offer(new BridgeIncomingCallback(handler, callback));
        }
    }

    public void awaitIdle() throws InterruptedException {
        BridgeCallbackListenerBarrier barrier = null;
        synchronized (lifecycleLock) {
            if (!closed) {
                barrier = new BridgeCallbackListenerBarrier();
                mailbox.offer(barrier);
            }
        }
        if (barrier != null) {
            barrier.await();
            return;
        }
        stopped.await();
    }

    public void shutdown(String reason) {
        synchronized (lifecycleLock) {
            if (closed) {
                return;
            }
            shutdownReason = Objects.requireNonNull(reason);
            closed = true;
            mailbox.offer(new BridgeCallbackListenerWakeup(reason));
        }
    }

    private void runLoop() {
        try {
            while (true) {
                BridgeCallbackListenerMessage message;
                try {
                    message = mailbox.take();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }

                if (message instanceof BridgeCallbackListenerBarrier barrier) {
                    barrier.complete();
                } else if (message instanceof BridgeIncomingCallback incoming) {
                    incoming.handler().handleCallback(incoming.callback());
                } else if (!(message instanceof BridgeCallbackListenerWakeup)) {
                    throw new IllegalStateException("Unknown bridge callback listener message: " + message.getClass().getName());
                }

                synchronized (lifecycleLock) {
                    if (closed && mailbox.isEmpty()) {
                        logger.info("[" + username + "] Bridge callback listener stopped: " + shutdownReason);
                        return;
                    }
                }
            }
        } finally {
            stopped.countDown();
        }
    }
}

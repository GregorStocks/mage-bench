package mage.client.bridge.listener;

import java.util.concurrent.CountDownLatch;

final class BridgeCallbackListenerBarrier implements BridgeCallbackListenerMessage {
    private final CountDownLatch complete = new CountDownLatch(1);

    void complete() {
        complete.countDown();
    }

    void await() throws InterruptedException {
        complete.await();
    }
}

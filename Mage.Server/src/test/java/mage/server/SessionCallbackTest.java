package mage.server;

import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.server.managers.ManagerFactory;
import mage.server.managers.SessionManager;
import org.jboss.remoting.callback.AsynchInvokerCallbackHandler;
import org.jboss.remoting.callback.Callback;
import org.jboss.remoting.callback.HandleCallbackException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Proxy;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Session.fireCallback must never drop callbacks under contention: it enqueues onto a
 * per-session writer thread that delivers in order (see issue
 * firecallback-50ms-silent-drop — the old implementation dropped callbacks after a
 * 50ms lock timeout).
 */
public class SessionCallbackTest {

    /**
     * Callback handler stub: records delivered callbacks, can hold deliveries at a gate to
     * simulate a slow client, can throw to simulate a broken connection.
     */
    private static class RecordingCallbackHandler implements AsynchInvokerCallbackHandler {

        final List<ClientCallback> delivered = new CopyOnWriteArrayList<>();
        final CountDownLatch deliveryGate = new CountDownLatch(1);
        volatile boolean deliveriesWaitForGate = false;
        volatile boolean failAllCallbacks = false;

        @Override
        public void handleCallbackOneway(Callback callback, boolean serverSide) throws HandleCallbackException {
            if (failAllCallbacks) {
                throw new HandleCallbackException("simulated connection failure");
            }
            if (deliveriesWaitForGate) {
                try {
                    if (!deliveryGate.await(10, TimeUnit.SECONDS)) {
                        throw new HandleCallbackException("delivery gate never opened");
                    }
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                    throw new HandleCallbackException("interrupted", ex);
                }
            }
            delivered.add((ClientCallback) callback.getParameter());
        }

        @Override
        public void handleCallbackOneway(Callback callback) throws HandleCallbackException {
            handleCallbackOneway(callback, false);
        }

        @Override
        public void handleCallback(Callback callback, boolean asynch, boolean serverSide) throws HandleCallbackException {
            handleCallbackOneway(callback, serverSide);
        }

        @Override
        public void handleCallback(Callback callback) throws HandleCallbackException {
            handleCallbackOneway(callback, false);
        }
    }

    /**
     * Records sessionManager().disconnect calls; any other manager access fails the test.
     */
    private static class StubManagerFactory {

        final AtomicReference<DisconnectReason> disconnectReason = new AtomicReference<>();
        final CountDownLatch disconnected = new CountDownLatch(1);

        ManagerFactory create() {
            SessionManager sessionManager = (SessionManager) Proxy.newProxyInstance(
                    getClass().getClassLoader(), new Class<?>[]{SessionManager.class},
                    (proxy, method, args) -> {
                        if (method.getName().equals("disconnect")) {
                            disconnectReason.set((DisconnectReason) args[1]);
                            disconnected.countDown();
                            return null;
                        }
                        throw new UnsupportedOperationException(method.getName());
                    });
            return (ManagerFactory) Proxy.newProxyInstance(
                    getClass().getClassLoader(), new Class<?>[]{ManagerFactory.class},
                    (proxy, method, args) -> {
                        if (method.getName().equals("sessionManager")) {
                            return sessionManager;
                        }
                        throw new UnsupportedOperationException(method.getName());
                    });
        }
    }

    private static ClientCallback newCallback() {
        return new ClientCallback(ClientCallbackMethod.GAME_UPDATE, null, null);
    }

    private static void awaitDelivered(RecordingCallbackHandler handler, int count) throws InterruptedException {
        long deadline = System.currentTimeMillis() + 10_000;
        while (handler.delivered.size() < count) {
            assertThat(System.currentTimeMillis())
                    .as("timed out waiting for %d callbacks, got %d", count, handler.delivered.size())
                    .isLessThan(deadline);
            Thread.sleep(5);
        }
    }

    @Test
    @DisplayName("callbacks queued behind a blocked delivery must not be dropped (old code dropped after 50ms lock timeout)")
    void blockedDeliveryDoesNotDropCallbacks() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.deliveriesWaitForGate = true;
        Session session = new Session(new StubManagerFactory().create(), "test-session", handler);
        try {
            int total = 5;
            for (int i = 0; i < total; i++) {
                session.fireCallback(newCallback());
            }
            // all 5 are enqueued while the writer is blocked on the first delivery
            handler.deliveryGate.countDown();
            awaitDelivered(handler, total);
            assertThat(handler.delivered)
                    .extracting(ClientCallback::getMessageId)
                    .containsExactly(1, 2, 3, 4, 5);
        } finally {
            session.shutdown();
        }
    }

    @Test
    @DisplayName("a callback broadcast to several sessions is copied per session, not mutated shared")
    void broadcastCallbackIsCopiedPerSession() throws InterruptedException {
        RecordingCallbackHandler handlerA = new RecordingCallbackHandler();
        RecordingCallbackHandler handlerB = new RecordingCallbackHandler();
        Session sessionA = new Session(new StubManagerFactory().create(), "test-session-a", handlerA);
        Session sessionB = new Session(new StubManagerFactory().create(), "test-session-b", handlerB);
        try {
            // like ChatSession: one shared instance fired at every session
            ClientCallback shared = newCallback();
            sessionA.fireCallback(shared);
            sessionA.fireCallback(shared);
            sessionB.fireCallback(shared);
            awaitDelivered(handlerA, 2);
            awaitDelivered(handlerB, 1);

            assertThat(shared.getMessageId()).as("shared instance must not be mutated").isZero();
            assertThat(handlerA.delivered).extracting(ClientCallback::getMessageId).containsExactly(1, 2);
            assertThat(handlerB.delivered).extracting(ClientCallback::getMessageId).containsExactly(1);
        } finally {
            sessionA.shutdown();
            sessionB.shutdown();
        }
    }

    @Test
    @DisplayName("callbacks queued before shutdown still drain, callbacks after shutdown are rejected")
    void shutdownDrainsQueueAndRejectsNewCallbacks() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.deliveriesWaitForGate = true;
        Session session = new Session(new StubManagerFactory().create(), "test-session", handler);

        int total = 3;
        for (int i = 0; i < total; i++) {
            session.fireCallback(newCallback());
        }
        session.shutdown();
        handler.deliveryGate.countDown();
        awaitDelivered(handler, total);

        // rejection is synchronous: the executor is shut down, so nothing was enqueued
        session.fireCallback(newCallback());
        assertThat(handler.delivered).hasSize(total);
    }

    @Test
    @DisplayName("delivery failure invalidates the session and disconnects it")
    void deliveryFailureDisconnectsSession() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.failAllCallbacks = true;
        StubManagerFactory managerFactory = new StubManagerFactory();
        Session session = new Session(managerFactory.create(), "test-session", handler);
        try {
            session.fireCallback(newCallback());
            assertThat(managerFactory.disconnected.await(10, TimeUnit.SECONDS)).isTrue();
            assertThat(managerFactory.disconnectReason.get()).isEqualTo(DisconnectReason.LostConnection);

            // the session is invalid now (set before the disconnect call), so further
            // callbacks are dropped synchronously and never reach the handler
            handler.failAllCallbacks = false;
            session.fireCallback(newCallback());
            assertThat(handler.delivered).isEmpty();
        } finally {
            session.shutdown();
        }
    }
}

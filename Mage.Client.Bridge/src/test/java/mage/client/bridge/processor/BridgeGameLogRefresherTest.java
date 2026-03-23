package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import org.apache.log4j.Logger;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Proxy;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameLogRefresherTest {

    @Test
    void callbackTriggerDoesNotPollAgainAfterEmptyFetch() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch firstFetch = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                fetchCalls.incrementAndGet();
                firstFetch.countDown();
                return List.of();
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId = UUID.randomUUID();
            UUID playerId = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId, playerId);
                return null;
            }));

            processor.enqueueCallback(new BridgeCallbackEvent(gameId, ClientCallbackMethod.GAME_UPDATE, null));

            assertThat(firstFetch.await(1, TimeUnit.SECONDS)).isTrue();
            Thread.sleep(200);
            assertThat(fetchCalls.get()).isEqualTo(1);
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    @Test
    void callbackTriggerDrainsFollowupFetchesUntilServerIsCaughtUp() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch secondFetch = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                int call = fetchCalls.incrementAndGet();
                if (call == 1) {
                    return List.of(
                        bridgeLogEntry(5, "BEGIN_TURN", 1, "Alice", "Alice", null, null),
                        bridgeLogEntry(6, "LAND_PLAYED", 1, "Alice", "Alice", "Island", null)
                    );
                }
                secondFetch.countDown();
                return List.of();
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId = UUID.randomUUID();
            UUID playerId = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId, playerId);
                return null;
            }));

            processor.enqueueCallback(new BridgeCallbackEvent(gameId, ClientCallbackMethod.GAME_UPDATE, null));

            assertThat(secondFetch.await(1, TimeUnit.SECONDS)).isTrue();

            BridgePublishedGameLog publishedLog = processor.submit(BridgeCommand.of(gameLogState::publishedGameLog));
            assertThat(fetchCalls.get()).isEqualTo(2);
            assertThat(publishedLog.nextCursor()).isEqualTo(2);
            assertThat(publishedLog.entries()).extracting(BridgePublishedLogEntry::seq)
                .containsExactly(0, 1);
            assertThat(publishedLog.entries()).extracting(entry -> entry.bridgeEvent().type())
                .containsExactly("BEGIN_TURN", "LAND_PLAYED");
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    @Test
    void syncBarrierWaitsForTriggeredRefreshChainToDrain() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch firstFetchStarted = new CountDownLatch(1);
        CountDownLatch releaseFirstFetch = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                int call = fetchCalls.incrementAndGet();
                if (call == 1) {
                    firstFetchStarted.countDown();
                    if (!releaseFirstFetch.await(1, TimeUnit.SECONDS)) {
                        throw new AssertionError("Timed out waiting to release first fetch");
                    }
                    return List.of(bridgeLogEntry(5, "LAND_PLAYED", 1, "Alice", "Alice", "Mountain", null));
                }
                return List.of();
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId = UUID.randomUUID();
            UUID playerId = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId, playerId);
                return null;
            }));

            processor.enqueueCallback(new BridgeCallbackEvent(gameId, ClientCallbackMethod.GAME_UPDATE, null));
            assertThat(firstFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();

            CountDownLatch waiterFinished = new CountDownLatch(1);
            AtomicReference<BridgePublishedGameLog> published = new AtomicReference<>();
            AtomicReference<Throwable> waiterFailure = new AtomicReference<>();
            Thread waiter = new Thread(() -> {
                try {
                    long syncEpoch = processor.submit(BridgeCommand.of(refresher::captureSyncBarrierEpoch));
                    refresher.awaitSyncThrough(syncEpoch);
                    published.set(processor.submit(BridgeCommand.of(gameLogState::publishedGameLog)));
                } catch (Throwable t) {
                    waiterFailure.set(t);
                } finally {
                    waiterFinished.countDown();
                }
            }, "bridge-log-sync-waiter");
            waiter.start();

            Thread.sleep(100);
            assertThat(waiterFinished.getCount()).isEqualTo(1);

            releaseFirstFetch.countDown();

            assertThat(waiterFinished.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(waiterFailure.get()).isNull();
            assertThat(fetchCalls.get()).isEqualTo(2);
            assertThat(published.get()).isNotNull();
            assertThat(published.get().entries()).extracting(BridgePublishedLogEntry::seq)
                .containsExactly(0);
            assertThat(published.get().entries()).extracting(entry -> entry.bridgeEvent().cardName())
                .containsExactly("Mountain");
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    @Test
    void fetchFailureCompletesSyncBarrierAndRetriesAfterCooldown() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch firstFailure = new CountDownLatch(1);
        CountDownLatch secondFetchStarted = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                int call = fetchCalls.incrementAndGet();
                if (call == 1) {
                    firstFailure.countDown();
                    throw new IllegalStateException("boom");
                }
                secondFetchStarted.countDown();
                return List.of(bridgeLogEntry(7, "LAND_PLAYED", 1, "Alice", "Alice", "Swamp", null));
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId = UUID.randomUUID();
            UUID playerId = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId, playerId);
                return null;
            }));

            processor.enqueueCallback(new BridgeCallbackEvent(gameId, ClientCallbackMethod.GAME_UPDATE, null));
            assertThat(firstFailure.await(1, TimeUnit.SECONDS)).isTrue();

            CountDownLatch waiterFinished = new CountDownLatch(1);
            AtomicReference<Throwable> waiterFailure = new AtomicReference<>();
            Thread waiter = new Thread(() -> {
                try {
                    long syncEpoch = processor.submit(BridgeCommand.of(refresher::captureSyncBarrierEpoch));
                    refresher.awaitSyncThrough(syncEpoch);
                } catch (Throwable t) {
                    waiterFailure.set(t);
                } finally {
                    waiterFinished.countDown();
                }
            }, "bridge-log-failure-waiter");
            waiter.start();

            assertThat(waiterFinished.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(waiterFailure.get()).isNull();

            Thread.sleep(100);
            assertThat(fetchCalls.get()).isEqualTo(1);

            assertThat(secondFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(fetchCalls.get()).isEqualTo(2);
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    @Test
    void staleFetchFailureStartsQueuedCurrentGameRefresh() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch firstFetchStarted = new CountDownLatch(1);
        CountDownLatch releaseFirstFetch = new CountDownLatch(1);
        CountDownLatch secondFetchStarted = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                int call = fetchCalls.incrementAndGet();
                if (call == 1) {
                    firstFetchStarted.countDown();
                    if (!releaseFirstFetch.await(1, TimeUnit.SECONDS)) {
                        throw new AssertionError("Timed out waiting to release first fetch");
                    }
                    throw new IllegalStateException("stale failure");
                }
                secondFetchStarted.countDown();
                return List.of();
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId1 = UUID.randomUUID();
            UUID playerId1 = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId1, playerId1);
                return null;
            }));
            processor.enqueueCallback(new BridgeCallbackEvent(gameId1, ClientCallbackMethod.GAME_UPDATE, null));
            assertThat(firstFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();

            UUID gameId2 = UUID.randomUUID();
            UUID playerId2 = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.clearActiveGame(gameId1);
                gameState.activateGame(gameId2, playerId2);
                return null;
            }));
            processor.enqueueCallback(new BridgeCallbackEvent(gameId2, ClientCallbackMethod.GAME_UPDATE, null));

            releaseFirstFetch.countDown();

            assertThat(secondFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(fetchCalls.get()).isEqualTo(2);
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    @Test
    void hungStaleFetchDoesNotBlockCurrentGameSyncBarrier() throws Exception {
        AtomicInteger fetchCalls = new AtomicInteger();
        CountDownLatch firstFetchStarted = new CountDownLatch(1);
        CountDownLatch releaseFirstFetch = new CountDownLatch(1);
        CountDownLatch secondFetchStarted = new CountDownLatch(1);
        Session session = sessionProxy((proxy, method, args) -> {
            if ("getBridgeEvents".equals(method.getName())) {
                int call = fetchCalls.incrementAndGet();
                if (call == 1) {
                    firstFetchStarted.countDown();
                    if (!releaseFirstFetch.await(2, TimeUnit.SECONDS)) {
                        throw new AssertionError("Timed out waiting to release stale fetch");
                    }
                    return List.of();
                }
                secondFetchStarted.countDown();
                return List.of();
            }
            return defaultReturnValue(method.getReturnType());
        });

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            event -> {}
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        BridgeGameLogState gameLogState = processorState.gameLogState();
        BridgeGameLogRefresher refresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeGameLogRefresherTest.class),
            "TestPlayer"
        );
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent) {
                refresher.afterCallbackProcessed();
            }
        });
        processor.start();

        try {
            UUID gameId1 = UUID.randomUUID();
            UUID playerId1 = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.activateGame(gameId1, playerId1);
                return null;
            }));
            processor.enqueueCallback(new BridgeCallbackEvent(gameId1, ClientCallbackMethod.GAME_UPDATE, null));
            assertThat(firstFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();

            UUID gameId2 = UUID.randomUUID();
            UUID playerId2 = UUID.randomUUID();
            processor.submit(BridgeCommand.of(() -> {
                gameState.clearActiveGame(gameId1);
                gameState.activateGame(gameId2, playerId2);
                return null;
            }));
            processor.enqueueCallback(new BridgeCallbackEvent(gameId2, ClientCallbackMethod.GAME_UPDATE, null));

            CountDownLatch waiterFinished = new CountDownLatch(1);
            AtomicReference<Throwable> waiterFailure = new AtomicReference<>();
            Thread waiter = new Thread(() -> {
                try {
                    long syncEpoch = processor.submit(BridgeCommand.of(refresher::captureSyncBarrierEpoch));
                    refresher.awaitSyncThrough(syncEpoch);
                } catch (Throwable t) {
                    waiterFailure.set(t);
                } finally {
                    waiterFinished.countDown();
                }
            }, "bridge-log-stale-hang-waiter");
            waiter.start();

            assertThat(secondFetchStarted.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(waiterFinished.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(waiterFailure.get()).isNull();

            releaseFirstFetch.countDown();
            assertThat(fetchCalls.get()).isEqualTo(2);
        } finally {
            refresher.shutdown();
            processor.shutdown("test");
        }
    }

    private static Session sessionProxy(java.lang.reflect.InvocationHandler handler) {
        return (Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            handler
        );
    }

    private static Object defaultReturnValue(Class<?> returnType) {
        if (!returnType.isPrimitive()) {
            return null;
        }
        if (returnType == boolean.class) {
            return false;
        }
        if (returnType == int.class) {
            return 0;
        }
        if (returnType == long.class) {
            return 0L;
        }
        if (returnType == double.class) {
            return 0d;
        }
        if (returnType == float.class) {
            return 0f;
        }
        if (returnType == short.class) {
            return (short) 0;
        }
        if (returnType == byte.class) {
            return (byte) 0;
        }
        if (returnType == char.class) {
            return '\0';
        }
        return null;
    }

    private static BridgeLogEntry bridgeLogEntry(
            int index,
            String type,
            int turn,
            String activePlayer,
            String player,
            String cardName,
            String targetName) {
        return new BridgeLogEntry(
            index,
            index,
            type,
            turn,
            "PRECOMBAT_MAIN",
            "PRECOMBAT_MAIN",
            activePlayer,
            player,
            cardName,
            targetName,
            0,
            true
        );
    }
}

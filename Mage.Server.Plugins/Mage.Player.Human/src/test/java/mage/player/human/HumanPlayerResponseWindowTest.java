package mage.player.human;

import mage.game.Game;
import mage.game.GameState;
import mage.constants.RangeOfInfluence;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.UUID;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class HumanPlayerResponseWindowTest {

    @Test
    void acceptsResponseThatArrivesBeforeWaitForResponseStarts() throws Exception {
        TestHumanPlayer player = new TestHumanPlayer();
        setIntField(player, "RESPONSE_WAITING_TIME_SECS", 0);

        GameState state = new GameState();
        state.setPriorityPlayerId(player.getId());
        Game game = gameProxy(state);

        player.prepareForResponseForTest(game);
        player.setResponseBoolean(Boolean.TRUE);

        FutureTask<Void> waitTask = new FutureTask<>(() -> {
            player.waitForResponseForTest(game);
            return null;
        });
        Thread waitThread = new Thread(waitTask, "GAME HumanPlayerResponseWindowTest");
        waitThread.start();
        waitTask.get(1, TimeUnit.SECONDS);

        assertThat(player.currentBooleanResponse()).isTrue();
    }

    @Test
    void rejectsStaleResponseInsteadOfLeakingIntoNextWindow() throws Exception {
        TestHumanPlayer player = new TestHumanPlayer();
        setIntField(player, "RESPONSE_WAITING_TIME_SECS", 1);

        GameState state = new GameState();
        state.setPriorityPlayerId(player.getId());
        Game game = gameProxy(state);

        AtomicReference<Boolean> sendFinished = new AtomicReference<>(Boolean.FALSE);
        Thread staleResponseThread = new Thread(() -> {
            player.setResponseBoolean(Boolean.TRUE);
            sendFinished.set(Boolean.TRUE);
        }, "CALL stale-response");
        staleResponseThread.start();

        Thread.sleep(150);
        player.prepareForResponseForTest(game);
        staleResponseThread.join(1_000);

        assertThat(sendFinished.get()).isTrue();
        assertThat(player.currentBooleanResponse()).isNull();
    }

    @Test
    void acceptsResponseThatWaitsForReopenedWindowOfSamePrompt() throws Exception {
        TestHumanPlayer player = new TestHumanPlayer();
        setIntField(player, "RESPONSE_WAITING_TIME_SECS", 1);

        GameState state = new GameState();
        state.setPriorityPlayerId(player.getId());
        Game game = gameProxy(state);

        player.prepareForResponseForTest(game);
        setBooleanField(player, "responseOpenedForAnswer", false);

        AtomicReference<Boolean> sendFinished = new AtomicReference<>(Boolean.FALSE);
        Thread retryResponseThread = new Thread(() -> {
            player.setResponseBoolean(Boolean.TRUE);
            sendFinished.set(Boolean.TRUE);
        }, "CALL retry-response");
        retryResponseThread.start();

        Thread.sleep(150);
        reopenResponseWindow(player, game, "retry");
        retryResponseThread.join(1_000);

        assertThat(sendFinished.get()).isTrue();
        assertThat(player.currentBooleanResponse()).isTrue();
    }

    private static void setIntField(Object target, String fieldName, int value) throws Exception {
        Field field = HumanPlayer.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        field.setInt(target, value);
    }

    private static void setBooleanField(Object target, String fieldName, boolean value) throws Exception {
        Field field = HumanPlayer.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        field.setBoolean(target, value);
    }

    private static void reopenResponseWindow(TestHumanPlayer player, Game game, String actionName)
            throws NoSuchMethodException, IllegalAccessException, InvocationTargetException {
        Method method = HumanPlayer.class.getDeclaredMethod(
            "openResponseWindow",
            Game.class,
            String.class,
            boolean.class
        );
        method.setAccessible(true);
        method.invoke(player, game, actionName, false);
    }

    private static Game gameProxy(GameState state) {
        UUID gameId = UUID.randomUUID();
        InvocationHandler handler = (Object proxy, Method method, Object[] args) -> switch (method.getName()) {
            case "getState" -> state;
            case "getId" -> gameId;
            case "resumeTimer", "pauseTimer" -> null;
            case "toString" -> "HumanPlayerResponseWindowTest";
            case "hashCode" -> System.identityHashCode(proxy);
            case "equals" -> proxy == args[0];
            default -> defaultValue(method.getReturnType());
        };
        return (Game) Proxy.newProxyInstance(
            Game.class.getClassLoader(),
            new Class<?>[]{Game.class},
            handler
        );
    }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) {
            return null;
        }
        if (type == boolean.class) {
            return false;
        }
        if (type == byte.class) {
            return (byte) 0;
        }
        if (type == short.class) {
            return (short) 0;
        }
        if (type == int.class) {
            return 0;
        }
        if (type == long.class) {
            return 0L;
        }
        if (type == float.class) {
            return 0f;
        }
        if (type == double.class) {
            return 0d;
        }
        if (type == char.class) {
            return '\0';
        }
        return null;
    }

    private static final class TestHumanPlayer extends HumanPlayer {
        private TestHumanPlayer() {
            super("TestPlayer", RangeOfInfluence.ALL, 0);
        }

        private void prepareForResponseForTest(Game game) {
            prepareForResponse(game);
        }

        private void waitForResponseForTest(Game game) {
            waitForResponse(game);
        }

        private Boolean currentBooleanResponse() {
            try {
                Field responseField = HumanPlayer.class.getDeclaredField("response");
                responseField.setAccessible(true);
                PlayerResponse playerResponse = (PlayerResponse) responseField.get(this);
                return playerResponse.getBoolean();
            } catch (ReflectiveOperationException e) {
                throw new AssertionError("Failed to read player response", e);
            }
        }
    }
}

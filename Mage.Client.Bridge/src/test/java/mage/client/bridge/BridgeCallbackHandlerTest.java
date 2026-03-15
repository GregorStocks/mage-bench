package mage.client.bridge;

import mage.choices.ChoiceImpl;
import mage.client.bridge.tools.ActionResult;
import mage.game.BridgeLogEntry;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import mage.util.MultiAmountMessage;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.PlayerView;
import mage.view.StackAbilityView;
import org.junit.jupiter.api.Test;
import sun.misc.Unsafe;

import java.io.Serializable;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BridgeCallbackHandlerTest {

    @Test
    void acceptsValidMultiAmountInput() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("First", 0, 2),
            new MultiAmountMessage("Second", 0, 2)
        ), 2, 2);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{1, 1})).isNull();
    }

    @Test
    void rejectsWrongItemCount() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("Only", 0, 9)
        ), 3, 9);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{3, 6}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: expected 1 entry, got 2. "
                + "Expected 1 amount and total 3-9.");
    }

    @Test
    void rejectsPerItemRangeViolations() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("Only", 1, 3)
        ), 1, 3);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{0}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: amounts[0]=0 is outside item range 1-3. "
                + "Expected 1 amount and total 1-3.");
    }

    @Test
    void rejectsTotalRangeViolations() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("First", 0, 3),
            new MultiAmountMessage("Second", 0, 3)
        ), 2, 2);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{2, 1}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: total 3 is outside allowed range 2. "
                + "Expected 2 amounts and total 2.");
    }

    @Test
    void failsFastWhenPendingActionLacksItemMetadata() {
        GameClientMessage message = new GameClientMessage(
            null,
            Collections.<String, Serializable>emptyMap(),
            (List<MultiAmountMessage>) null,
            1,
            2
        );

        assertThatThrownBy(() -> BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{1}))
            .isInstanceOf(IllegalStateException.class)
            .hasMessage("GAME_GET_MULTI_AMOUNT is missing item metadata");
    }

    @Test
    void stripsMatchingAbilityPickerOrdinalPrefix() {
        assertThat(BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix("1. {T}: Add {G}.", 0))
            .isEqualTo("{T}: Add {G}.");
        assertThat(BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix(
            "2. {2}, {T}: Thespian's Stage becomes a copy of target land, except it has this ability.",
            1
        )).isEqualTo("{2}, {T}: Thespian's Stage becomes a copy of target land, except it has this ability.");
    }

    @Test
    void leavesUnmatchedLeadingNumbersAlone() {
        assertThat(BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix("10 damage to any target.", 0))
            .isEqualTo("10 damage to any target.");
        assertThat(BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix("10. {T}: Add {G}.", 0))
            .isEqualTo("10. {T}: Add {G}.");
    }

    @Test
    void stripsMultiDigitAbilityPickerOrdinalPrefix() {
        assertThat(BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix("10. {T}: Add one mana of any color.", 9))
            .isEqualTo("{T}: Add one mana of any color.");
    }

    @Test
    void returnsStackResolvedOnNextActionAfterPassiveUpdateClearsStack() throws Exception {
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession(sessionProxy(autoPassSent, sendPlayerBooleanCalls));
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID watchedStackObjectId = UUID.randomUUID();
        GameView stackOccupied = gameView(7, watchedStackObjectId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", stackOccupied);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(stackOccupied, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            7
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority("stack_resolved", null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();

            GameView stackCleared = gameView(8);
            setField(handler, "lastGameView", stackCleared);
            notifyActionLock(handler);

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            setField(handler, "pendingAction", new PendingAction(
                gameId,
                ClientCallbackMethod.GAME_SELECT,
                new GameClientMessage(stackCleared, Collections.<String, Serializable>emptyMap(), "Pass after resolve"),
                "Pass after resolve",
                8
            ));
            notifyActionLock(handler);

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("stack_resolved");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.game_seq).isEqualTo(8);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void treatsEmptyStackResolvedAsSinglePass() throws Exception {
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession(sessionProxy(autoPassSent, sendPlayerBooleanCalls));
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        GameView emptyStack = gameView(7);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", emptyStack);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(emptyStack, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            7
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority("stack_resolved", null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            GameView nextActionView = gameView(8);
            setField(handler, "pendingAction", new PendingAction(
                gameId,
                ClientCallbackMethod.GAME_ASK,
                new GameClientMessage(nextActionView, Collections.<String, Serializable>emptyMap(), "Mulligan hand?"),
                "Mulligan hand?",
                8
            ));
            notifyActionLock(handler);

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("non_priority_action");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_ASK");
            assertThat(result.game_seq).isEqualTo(8);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void gameOverKeepsPostgameHistoryAvailableWithoutBlockingCallbackThread() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID chatId = UUID.randomUUID();
        AtomicInteger getBridgeEventsCalls = new AtomicInteger();
        AtomicInteger leaveChatCalls = new AtomicInteger();
        List<BridgeLogEntry> bridgeEvents = List.of(
            new BridgeLogEntry(
                0, 9, "LAND_PLAYED", 3, "PRECOMBAT_MAIN", "PRECOMBAT_MAIN",
                "TestPlayer", "TestPlayer", "Island", null, 0, true
            )
        );

        InvocationHandler sessionHandler = (proxy, method, args) -> {
            switch (method.getName()) {
                case "getBridgeEvents" -> {
                    getBridgeEventsCalls.incrementAndGet();
                    assertThat(args[0]).isEqualTo(gameId);
                    assertThat(args[1]).isEqualTo(playerId);
                    assertThat(args[2]).isEqualTo(0);
                    return bridgeEvents;
                }
                case "leaveChat" -> {
                    leaveChatCalls.incrementAndGet();
                    assertThat(args[0]).isEqualTo(chatId);
                    return true;
                }
                default -> {
                    return defaultReturnValue(method.getReturnType());
                }
            }
        };

        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            sessionHandler
        ));
        BridgeCallbackHandler handler = client.getCallbackHandler();
        handler.setKeepAliveAfterGame(true);

        @SuppressWarnings("unchecked")
        Map<UUID, UUID> activeGames = (Map<UUID, UUID>) getField(handler, "activeGames");
        @SuppressWarnings("unchecked")
        Map<UUID, UUID> gameChatIds = (Map<UUID, UUID>) getField(handler, "gameChatIds");
        activeGames.put(gameId, playerId);
        gameChatIds.put(gameId, chatId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "currentPlayerId", playerId);

        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_OVER,
            gameId,
            new GameClientMessage(gameView(9), Collections.<String, Serializable>emptyMap(), "Player Opponent is the winner"),
            false
        );
        handler.handleCallback(callback);

        assertThat(handler.awaitGameFinished(100)).isTrue();
        assertThat(getBridgeEventsCalls.get()).isZero();
        assertThat(activeGames).doesNotContainKey(gameId);
        assertThat(leaveChatCalls.get()).isEqualTo(1);

        var history = handler.getGameHistory(null, null);
        assertThat(getBridgeEventsCalls.get()).isEqualTo(1);
        assertThat(history.cursor).isEqualTo(1);
        assertThat(history.event_count).isEqualTo(1);
        assertThat(history.history).contains("Turn 3 (TestPlayer):");
        assertThat(history.history).contains("TestPlayer played Island");
    }

    @Test
    void stackAbilitySummaryIncludesSourceCardAbilityTextAndReadableTargets() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID stackObjectId = UUID.randomUUID();
        CardView sourceCard = cardView(UUID.randomUUID(), "p11", "Emancipation Angel");
        StackAbilityView stackAbility = stackAbilityView(
            stackObjectId,
            sourceCard,
            "When Emancipation Angel enters, return a permanent you control to its owner's hand.",
            playerId
        );

        CardsView stack = new CardsView();
        stack.put(stackObjectId, stackAbility);
        GameView view = gameView(7, List.of(playerView(playerId, "TestPlayer", "p2")), stack);
        setField(handler, "currentGameId", gameId);
        @SuppressWarnings("unchecked")
        Map<UUID, UUID> activeGames = (Map<UUID, UUID>) getField(handler, "activeGames");
        activeGames.put(gameId, playerId);

        Map<String, Object> stackItem = invokeBuildStackItem(handler, stackAbility, view, false, false);
        assertThat(stackItem)
            .containsEntry("name", "Emancipation Angel")
            .containsEntry("source_card", "Emancipation Angel")
            .containsEntry(
                "ability_text",
                "When Emancipation Angel enters, return a permanent you control to its owner's hand."
            );
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> targets = (List<Map<String, Object>>) stackItem.get("targets");
        assertThat(targets).singleElement().satisfies(target ->
            assertThat((Map<String, Object>) target).containsEntry("name", "TestPlayer (you)")
        );
    }

    @Test
    void gameChooseChoiceReturnsStructuredErrorForNamedChoiceParam() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        ChoiceImpl choice = new ChoiceImpl(true);
        choice.setMessage("Choose color");
        choice.setChoices(new LinkedHashSet<>(List.of("White", "Blue", "Black")));

        UUID gameId = UUID.randomUUID();
        GameView view = gameView(7);
        setField(handler, "lastGameView", view);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_CHOOSE_CHOICE,
            new GameClientMessage(view, Collections.<String, Serializable>emptyMap(), choice),
            "Choose color",
            7
        ));

        var result = handler.chooseAction(
            null, "Black", null, null, null, null, null, null, null, null, null
        );

        assertThat(result.success).isFalse();
        assertThat(result.error_code).isEqualTo("invalid_choice");
        assertThat(result.retryable).isTrue();
        assertThat(result.error)
            .contains("choice=\"Black\"")
            .contains("text=\"Black\"")
            .contains("choice=N")
            .doesNotContain("Unknown short ID");
        assertThat(result.choices)
            .extracting(entry -> entry.get("description"))
            .containsExactly("White", "Blue", "Black");
    }

    private static GameClientMessage multiAmountMessage(List<MultiAmountMessage> items, int min, int max) {
        return new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), items, min, max);
    }

    @SuppressWarnings("removal")
    private static final Unsafe UNSAFE = initUnsafe();

    @SuppressWarnings("removal")
    private static Unsafe initUnsafe() {
        try {
            Field field = Unsafe.class.getDeclaredField("theUnsafe");
            field.setAccessible(true);
            return (Unsafe) field.get(null);
        } catch (ReflectiveOperationException e) {
            throw new RuntimeException("Failed to access Unsafe", e);
        }
    }

    private static GameView gameView(int gameSeq, UUID... stackObjectIds) throws Exception {
        GameView view = (GameView) UNSAFE.allocateInstance(GameView.class);
        CardsView stack = new CardsView();
        for (UUID stackObjectId : stackObjectIds) {
            stack.put(stackObjectId, null);
        }
        setField(view, "players", List.of());
        setField(view, "lookedAt", List.of());
        setField(view, "stack", stack);
        setField(view, "activePlayerName", "TestPlayer");
        setIntField(view, "turn", 1);
        setIntField(view, "gameSeq", gameSeq);
        return view;
    }

    private static GameView gameView(int gameSeq, List<PlayerView> players, CardsView stack) throws Exception {
        GameView view = (GameView) UNSAFE.allocateInstance(GameView.class);
        setField(view, "players", players);
        setField(view, "myHand", new CardsView());
        setField(view, "stack", stack);
        setField(view, "exiles", List.of());
        setField(view, "lookedAt", List.of());
        setField(view, "activePlayerName", "TestPlayer");
        setField(view, "priorityPlayerName", "TestPlayer");
        setIntField(view, "turn", 1);
        setIntField(view, "gameSeq", gameSeq);
        return view;
    }

    private static PlayerView playerView(UUID playerId, String name, String shortId) throws Exception {
        PlayerView view = (PlayerView) UNSAFE.allocateInstance(PlayerView.class);
        setField(view, "playerId", playerId);
        setField(view, "name", name);
        setField(view, "shortId", shortId);
        setField(view, "life", 20);
        setField(view, "libraryCount", 53);
        setField(view, "handCount", 0);
        setField(view, "isActive", true);
        setField(view, "battlefield", new LinkedHashMap<UUID, Object>());
        setField(view, "graveyard", new CardsView());
        setField(view, "exile", new CardsView());
        setField(view, "commandList", List.of());
        setField(view, "counters", List.of());
        setField(view, "manaPool", null);
        return view;
    }

    private static CardView cardView(UUID id, String shortId, String name) throws Exception {
        CardView view = (CardView) UNSAFE.allocateInstance(CardView.class);
        setField(view, "id", id);
        setField(view, "shortId", shortId);
        setField(view, "name", name);
        setField(view, "displayName", name);
        setField(view, "rules", List.of());
        setField(view, "cardTypes", List.of());
        return view;
    }

    private static StackAbilityView stackAbilityView(UUID id, CardView sourceCard, String rule, UUID targetId)
            throws Exception {
        StackAbilityView view = (StackAbilityView) UNSAFE.allocateInstance(StackAbilityView.class);
        setField(view, "id", id);
        setField(view, "name", "Ability");
        setField(view, "displayName", "Ability");
        setField(view, "sourceCard", sourceCard);
        setField(view, "rules", List.of(rule));
        setField(view, "targets", List.of(targetId));
        setField(view, "cardTypes", List.of());
        return view;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> invokeBuildStackItem(
            BridgeCallbackHandler handler,
            CardView card,
            GameView view,
            boolean includeId,
            boolean includeRules
    ) throws Exception {
        Method method = BridgeCallbackHandler.class.getDeclaredMethod(
            "buildStackItem",
            CardView.class,
            GameView.class,
            boolean.class,
            boolean.class
        );
        method.setAccessible(true);
        return (Map<String, Object>) method.invoke(handler, card, view, includeId, includeRules);
    }

    private static Session sessionProxy(CountDownLatch autoPassSent, AtomicInteger sendPlayerBooleanCalls) {
        InvocationHandler handler = (proxy, method, args) -> {
            if ("sendPlayerBoolean".equals(method.getName())) {
                sendPlayerBooleanCalls.incrementAndGet();
                autoPassSent.countDown();
                return true;
            }
            return defaultReturnValue(method.getReturnType());
        };
        return (Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            handler
        );
    }

    private static Object defaultReturnValue(Class<?> returnType) {
        if (returnType == Optional.class) {
            return Optional.empty();
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

    private static void notifyActionLock(BridgeCallbackHandler handler) throws Exception {
        Object actionLock = getField(handler, "actionLock");
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
    }

    private static Object getField(Object target, String name) throws Exception {
        Field field = findField(target.getClass(), name);
        field.setAccessible(true);
        return field.get(target);
    }

    private static void setField(Object target, String name, Object value) throws Exception {
        Field field = findField(target.getClass(), name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static void setIntField(Object target, String name, int value) throws Exception {
        Field field = findField(target.getClass(), name);
        field.setAccessible(true);
        field.setInt(target, value);
    }

    private static Field findField(Class<?> type, String name) throws NoSuchFieldException {
        Class<?> current = type;
        while (current != null) {
            try {
                return current.getDeclaredField(name);
            } catch (NoSuchFieldException ignored) {
                current = current.getSuperclass();
            }
        }
        throw new NoSuchFieldException(name);
    }
}

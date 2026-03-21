package mage.client.bridge;

import mage.cards.repository.CardInfo;
import mage.choices.ChoiceImpl;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.constants.CardType;
import mage.game.BridgeLogEntry;
import mage.constants.PhaseStep;
import mage.constants.SubType;
import mage.constants.SuperType;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.remote.Session;
import mage.util.MultiAmountMessage;
import mage.util.ShortIdRegistry;
import mage.util.SubTypes;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
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
import java.util.Set;
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
    void getGameLogChunkUsesAbsolutePerPlayerTurnsForCursorSlices() throws Exception {
        BridgeMageClient client = new BridgeMageClient("Alice");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        var result = handler.getGameLogChunk(0, 4);

        assertThat(result.log).isEqualTo("Alice turn 2:\nAlice cast Lightning Bolt targeting Bob");
        assertThat(result.total_length).isNull();
        assertThat(result.truncated).isFalse();
        assertThat(result.cursor).isEqualTo(6);
        assertThat(result.cursor_reset).isNull();
    }

    @Test
    void getGameLogChunkReportsFullLengthBeforeTruncating() throws Exception {
        BridgeMageClient client = new BridgeMageClient("Alice");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        String lastLine = "Alice cast Lightning Bolt targeting Bob";
        var result = handler.getGameLogChunk(lastLine.length(), null);

        assertThat(result.log).isEqualTo(lastLine);
        assertThat(result.total_length).isEqualTo(sampleBridgeLogText().length());
        assertThat(result.truncated).isTrue();
        assertThat(result.cursor).isEqualTo(6);
    }

    @Test
    void getGameLogSinceTurnDefaultsToClientPlayer() throws Exception {
        BridgeMageClient client = new BridgeMageClient("Alice");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        var result = handler.getGameLogSinceTurn(null, 2);

        assertThat(result.log).isEqualTo("Alice turn 2:\nAlice cast Lightning Bolt targeting Bob");
        assertThat(result.total_length).isEqualTo(sampleBridgeLogText().length());
        assertThat(result.truncated).isFalse();
        assertThat(result.cursor).isEqualTo(6);
        assertThat(result.since_turn).isEqualTo(2);
        assertThat(result.since_player).isEqualTo("Alice");
    }

    @Test
    void stepYieldStopsWhenCallbackOvershootsTargetStepInSameTurn() throws Exception {
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession(sessionProxy(autoPassSent, sendPlayerBooleanCalls));
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        GameView upkeepView = gameView(7, 3, PhaseStep.UPKEEP);
        GameView postcombatMainView = gameView(8, 3, PhaseStep.POSTCOMBAT_MAIN);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", upkeepView);
        setField(handler, "lastTurnNumber", 3);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(upkeepView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            7
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority("precombat_main", null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            setField(handler, "pendingAction", new PendingAction(
                gameId,
                ClientCallbackMethod.GAME_SELECT,
                new GameClientMessage(postcombatMainView, Collections.<String, Serializable>emptyMap(), "Pass after overshoot"),
                "Pass after overshoot",
                8
            ));
            notifyActionLock(handler);

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("reached_step");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.game_seq).isEqualTo(8);
            assertThat(result.current_step).isEqualTo("Postcombat Main");
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void stripsHtmlNoiseFromMultiAmountDescriptions() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage(
                "<font color='#F0E68C' object_id='12345678-1234-1234-1234-123456789abc'>"
                    + "Savannah Lions</font> [7e2], P/T: 2/1",
                0,
                2
            )
        ), 2, 2);

        setField(handler, "pendingAction", new PendingAction(
            UUID.randomUUID(),
            ClientCallbackMethod.GAME_GET_MULTI_AMOUNT,
            message,
            "",
            7
        ));

        ActionResult result = handler.getActionChoices(null);

        assertThat(result.response_type).isEqualTo("multi_amount");
        assertThat(result.items).singleElement().satisfies(item ->
            assertThat(item).containsEntry("description", "Savannah Lions, P/T: 2/1")
        );
    }

    @Test
    void populateCardFieldsMapForCardViewPreservesSharedOracleFields() throws Exception {
        CardView secondFace = cardView(UUID.randomUUID(), "p2", "Awakened Insight");
        setField(secondFace, "manaCostLeftStr", List.of());
        setField(secondFace, "manaCostRightStr", List.of());
        setField(secondFace, "rules", List.of("<i>Flying</i>"));
        setField(secondFace, "cardTypes", List.of(CardType.PLANESWALKER));
        setField(secondFace, "subTypes", subTypes(SubType.JACE));
        setField(secondFace, "superTypes", List.of(SuperType.LEGENDARY));
        setField(secondFace, "startingLoyalty", "5");

        CardView frontFace = cardView(UUID.randomUUID(), "p1", "Test Front");
        setField(frontFace, "manaCostLeftStr", List.of("{2}", "{U}"));
        setField(frontFace, "manaCostRightStr", List.of());
        setField(frontFace, "rules", List.of("Flying", "<i>Ward</i> {2}"));
        setField(frontFace, "cardTypes", List.of(CardType.CREATURE));
        setField(frontFace, "subTypes", subTypes(SubType.HUMAN, SubType.WIZARD));
        setField(frontFace, "superTypes", List.of(SuperType.LEGENDARY));
        setField(frontFace, "power", "3");
        setField(frontFace, "toughness", "4");
        setField(frontFace, "secondCardFace", secondFace);

        Map<String, Object> entry = new LinkedHashMap<>();
        oracleTextService().populateCardFields(entry, frontFace);

        assertThat(entry)
            .containsEntry("name", "Test Front")
            .containsEntry("mana_cost", "{2}{U}")
            .containsEntry("type", "Legendary Creature  - Human Wizard")
            .containsEntry("rules", List.of("Flying", "Ward {2}"))
            .containsEntry("power", "3")
            .containsEntry("toughness", "4");
        @SuppressWarnings("unchecked")
        Map<String, Object> secondFaceEntry = (Map<String, Object>) entry.get("second_face");
        assertThat(secondFaceEntry)
            .containsEntry("name", "Awakened Insight")
            .containsEntry("type", "Legendary Planeswalker  - Jace")
            .containsEntry("rules", List.of("Flying"))
            .containsEntry("starting_loyalty", "5")
            .doesNotContainKeys("mana_cost", "power", "toughness", "starting_defense");
    }

    @Test
    void populateCardFieldsResultForCardInfoPreservesSharedOracleFields() throws Exception {
        CardInfo battle = new CardInfo();
        setField(battle, "name", "Test Invasion");
        battle.setManaCosts(List.of("{1}", "{W}"));
        battle.setTypes(List.of(CardType.BATTLE));
        battle.setSubtypes(List.of("Siege"));
        battle.setSuperTypes(List.of(SuperType.LEGENDARY));
        battle.setRules(List.of(
            "<i>Front</i> rule one",
            "Front rule two"
        ));
        setField(battle, "startingDefense", "4");

        GetOracleTextTool.Result result = new GetOracleTextTool.Result();
        oracleTextService().populateCardFields(result, battle);

        assertThat(result.name).isEqualTo("Test Invasion");
        assertThat(result.mana_cost).isEqualTo("{1}{W}");
        assertThat(result.type).isEqualTo("Legendary Battle — Siege");
        assertThat(result.rules).containsExactly(
            "Front rule one",
            "Front rule two"
        );
        assertThat(result.starting_defense).isEqualTo("4");
        assertThat(result.second_face).isNull();
    }

    @Test
    void stepYieldStopsOnNewTurnRegardlessOfCurrentStep() throws Exception {
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession(sessionProxy(autoPassSent, sendPlayerBooleanCalls));
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        GameView upkeepView = gameView(7, 3, PhaseStep.UPKEEP);
        GameView nextTurnUntapView = gameView(8, 4, PhaseStep.UNTAP);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", upkeepView);
        setField(handler, "lastTurnNumber", 3);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(upkeepView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            7
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority("postcombat_main", null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            setField(handler, "pendingAction", new PendingAction(
                gameId,
                ClientCallbackMethod.GAME_SELECT,
                new GameClientMessage(nextTurnUntapView, Collections.<String, Serializable>emptyMap(), "Pass on next turn"),
                "Pass on next turn",
                8
            ));
            notifyActionLock(handler);

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("step_not_reached");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.game_seq).isEqualTo(8);
            assertThat(result.current_step).isEqualTo("Untap");
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
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
    void endGameInfoCleansUpWhenGameOverMissed() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID chatId = UUID.randomUUID();
        AtomicInteger leaveChatCalls = new AtomicInteger();

        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("leaveChat".equals(method.getName())) {
                    leaveChatCalls.incrementAndGet();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
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
        setField(handler, "gameEverStarted", true);

        // Send END_GAME_INFO without prior GAME_OVER — simulates dropped callback
        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.END_GAME_INFO,
            gameId,
            null,
            false
        );
        handler.handleCallback(callback);

        assertThat(activeGames).doesNotContainKey(gameId);
        assertThat(handler.awaitGameFinished(100)).isTrue();
        assertThat(leaveChatCalls.get()).isEqualTo(1);
    }

    @Test
    void endGameInfoIsNoOpAfterGameOver() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID chatId = UUID.randomUUID();
        AtomicInteger leaveChatCalls = new AtomicInteger();

        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("leaveChat".equals(method.getName())) {
                    leaveChatCalls.incrementAndGet();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
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

        // Send GAME_OVER first
        ClientCallback gameOverCallback = new ClientCallback(
            ClientCallbackMethod.GAME_OVER,
            gameId,
            new GameClientMessage(gameView(9), Collections.<String, Serializable>emptyMap(), "Player Opponent is the winner"),
            false
        );
        handler.handleCallback(gameOverCallback);
        assertThat(leaveChatCalls.get()).isEqualTo(1);

        // Send END_GAME_INFO second — should be a no-op
        ClientCallback endGameInfoCallback = new ClientCallback(
            ClientCallbackMethod.END_GAME_INFO,
            gameId,
            null,
            false
        );
        handler.handleCallback(endGameInfoCallback);

        // leaveChat should NOT have been called a second time
        assertThat(leaveChatCalls.get()).isEqualTo(1);
    }

    @Test
    void stackAbilitySummaryIncludesSourceCardAbilityTextAndReadableTargets() throws Exception {
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
        Map<String, Object> stackItem = cardFormatter(view, gameId, playerId).buildStackItem(
            stackAbility,
            view,
            false,
            false
        );
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

    @Test
    void chooseActionReturnsStructuredErrorForUnknownShortId() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        GameView view = gameView(7);
        setField(handler, "lastGameView", view);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(view, Collections.<String, Serializable>emptyMap(), "Play spells and abilities"),
            "Play spells and abilities",
            7
        ));

        var result = handler.chooseAction(
            null, "p", null, null, null, null, null, null, null, null, null
        );

        assertThat(result.success).isFalse();
        assertThat(result.error_code).isEqualTo("invalid_choice");
        assertThat(result.retryable).isTrue();
        assertThat(result.error)
            .contains("Unknown short ID: p")
            .contains("get_action_choices");
    }

    @Test
    void chooseActionWaitsForNextDecisionInsteadOfReturningSingleTargetFollowup() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID onlyTarget = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();

        GameView askView = gameView(10);
        GameView targetView = gameView(11);
        GameView nextDecisionView = gameView(12);
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose a creature to copy",
            new CardsView(),
            Set.of(onlyTarget),
            true
        );
        GameClientMessage nextDecisionMessage = new GameClientMessage(
            nextDecisionView,
            Collections.<String, Serializable>emptyMap(),
            "Play spells and abilities"
        );

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        setField(handler, "pendingAction", new PendingAction(
                            gameId,
                            ClientCallbackMethod.GAME_TARGET,
                            targetMessage,
                            "Choose a creature to copy",
                            11
                        ));
                        notifyActionLock(handler);
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(onlyTarget);
                        setField(handler, "pendingAction", new PendingAction(
                            gameId,
                            ClientCallbackMethod.GAME_SELECT,
                            nextDecisionMessage,
                            "Play spells and abilities",
                            12
                        ));
                        notifyActionLock(handler);
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(askView, Collections.<String, Serializable>emptyMap(), "Use effect of Clone?"),
            "Use effect of Clone?",
            10
        ));

        var result = handler.chooseAction(
            null, null, true, null, null, null, null, null, null, null, null
        );

        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
        assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
        assertThat(result.success).isTrue();
        assertThat(result.action_taken).isEqualTo("yes");
        assertThat(result.warning).isNull();
        assertThat(result.game_seq).isEqualTo(12);
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_SELECT");
        assertThat(result.response_type).isEqualTo("boolean");
        assertThat(result.message).isEqualTo("Play spells and abilities");
    }

    @Test
    void passPriorityAutoHandlesSingleTargetFollowupBeforeReturningDecision() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID onlyTarget = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();

        GameView initialView = gameView(30);
        GameView targetView = gameView(31);
        GameView nextDecisionView = gameView(32);
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose a creature to copy",
            new CardsView(),
            Set.of(onlyTarget),
            true
        );
        GameClientMessage nextDecisionMessage = new GameClientMessage(
            nextDecisionView,
            Collections.<String, Serializable>emptyMap(),
            "Mulligan hand?"
        );

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(false);
                        setField(handler, "pendingAction", new PendingAction(
                            gameId,
                            ClientCallbackMethod.GAME_TARGET,
                            targetMessage,
                            "Choose a creature to copy",
                            31
                        ));
                        notifyActionLock(handler);
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(onlyTarget);
                        setField(handler, "pendingAction", new PendingAction(
                            gameId,
                            ClientCallbackMethod.GAME_ASK,
                            nextDecisionMessage,
                            "Mulligan hand?",
                            32
                        ));
                        notifyActionLock(handler);
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            30
        ));

        ActionResult result = handler.passPriority("stack_resolved", null);

        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
        assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
        assertThat(result.stop_reason).isEqualTo("non_priority_action");
        assertThat(result.warning).isNull();
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_ASK");
        assertThat(result.game_seq).isEqualTo(32);
        assertThat(result.response_type).isEqualTo("boolean");
        assertThat(result.message).isEqualTo("Mulligan hand?");
    }

    @Test
    void passPriorityReturnsCombatDecisionAfterAutoPass() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(50);
        GameView combatView = gameView(51);
        var combatOptions = new java.util.HashMap<String, Serializable>();
        combatOptions.put("possibleAttackers", new java.util.ArrayList<>(List.of(UUID.randomUUID())));
        GameClientMessage combatMessage = new GameClientMessage(
            combatView,
            combatOptions,
            "Declare attackers"
        );

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    assertThat(args[0]).isEqualTo(gameId);
                    assertThat(args[1]).isEqualTo(false);
                    setField(handler, "pendingAction", new PendingAction(
                        gameId,
                        ClientCallbackMethod.GAME_SELECT,
                        combatMessage,
                        "Declare attackers",
                        51
                    ));
                    notifyActionLock(handler);
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            50
        ));

        ActionResult result = handler.passPriority(null, null);

        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
        assertThat(result.stop_reason).isEqualTo("combat");
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_SELECT");
        assertThat(result.game_seq).isEqualTo(51);
        assertThat(result.combat_phase).isEqualTo("attackers");
    }

    @Test
    void handleCallbackStoresSingleTargetAsPendingAction() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID onlyTarget = UUID.randomUUID();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        GameView targetView = gameView(33);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerUUID".equals(method.getName())) {
                    sendPlayerUuidCalls.incrementAndGet();
                    assertThat(args[0]).isEqualTo(gameId);
                    assertThat(args[1]).isEqualTo(onlyTarget);
                    return false;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        @SuppressWarnings("unchecked")
        Map<UUID, UUID> activeGames = (Map<UUID, UUID>) getField(handler, "activeGames");
        activeGames.put(gameId, playerId);
        setField(handler, "currentGameId", gameId);

        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_TARGET,
            gameId,
            new GameClientMessage(
                targetView,
                Collections.<String, Serializable>emptyMap(),
                "Choose a creature to copy",
                new CardsView(),
                Set.of(onlyTarget),
                true
            ),
            false
        );

        handler.handleCallback(callback);

        PendingAction pendingAction = (PendingAction) getField(handler, "pendingAction");
        assertThat(sendPlayerUuidCalls.get()).isZero();
        assertThat(pendingAction).isNotNull();
        assertThat(pendingAction.gameId()).isEqualTo(gameId);
        assertThat(pendingAction.method()).isEqualTo(ClientCallbackMethod.GAME_TARGET);
        assertThat(pendingAction.message()).isEqualTo("Choose a creature to copy");
        assertThat(pendingAction.gameSeq()).isEqualTo(33);
        assertThat(((GameClientMessage) pendingAction.data()).getGameView()).isSameAs(targetView);
        assertThat(getField(handler, "lastGameView")).isSameAs(targetView);
        assertThat(getField(handler, "playerDead")).isEqualTo(false);
    }

    @Test
    void transitionToDecisionBoundaryTreatsReplacedTargetActionAsChanged() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID onlyTarget = UUID.randomUUID();
        GameView targetView = gameView(40);
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose a creature to copy",
            new CardsView(),
            Set.of(onlyTarget),
            true
        );
        PendingAction staleTargetAction = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_TARGET,
            targetMessage,
            "Choose a creature to copy",
            40
        );
        PendingAction replacementAction = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(gameView(41), Collections.<String, Serializable>emptyMap(), "Play spells and abilities"),
            "Play spells and abilities",
            41
        );

        setField(handler, "pendingAction", replacementAction);

        assertThat(invokeDecisionBoundaryStatus(handler, staleTargetAction, "test"))
            .isEqualTo("CHANGED");
    }

    @Test
    void handleCallbackStoresPendingManaAction() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerManaTypeCalls = new AtomicInteger();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        return true;
                    }
                    case "sendPlayerManaType" -> {
                        sendPlayerManaTypeCalls.incrementAndGet();
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        @SuppressWarnings("unchecked")
        Map<UUID, UUID> activeGames = (Map<UUID, UUID>) getField(handler, "activeGames");
        activeGames.put(gameId, playerId);
        setField(handler, "currentGameId", gameId);

        GameView manaView = gameView(77);
        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_PLAY_MANA,
            gameId,
            new GameClientMessage(manaView, Collections.<String, Serializable>emptyMap(), "Pay {1}"),
            false
        );

        handler.handleCallback(callback);

        PendingAction pending = (PendingAction) getField(handler, "pendingAction");
        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(0);
        assertThat(sendPlayerUuidCalls.get()).isEqualTo(0);
        assertThat(sendPlayerManaTypeCalls.get()).isEqualTo(0);
        assertThat(pending).isNotNull();
        assertThat(pending.method()).isEqualTo(ClientCallbackMethod.GAME_PLAY_MANA);
        assertThat(pending.message()).isEqualTo("Pay {1}");
        assertThat(pending.gameSeq()).isEqualTo(77);
    }

    @Test
    void transitionToDecisionBoundaryAutoHandlesStoredManaAction() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID forestId = UUID.randomUUID();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerUUID".equals(method.getName())) {
                    sendPlayerUuidCalls.incrementAndGet();
                    assertThat(args[0]).isEqualTo(gameId);
                    assertThat(args[1]).isEqualTo(forestId);
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        PermanentView forest = permanentView(forestId, "p1", "Forest", false);
        @SuppressWarnings("unchecked")
        Map<UUID, Object> battlefield = (Map<UUID, Object>) getField(player, "battlefield");
        battlefield.put(forestId, forest);

        GameView manaView = gameView(60, List.of(player), new CardsView());
        setField(manaView, "myPlayerId", playerId);
        setField(manaView, "canPlayObjects", playableObjects(Map.of(
            forestId, manaStats("{T}: Add {G}.")
        )));

        PendingAction action = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_PLAY_MANA,
            new GameClientMessage(manaView, Collections.<String, Serializable>emptyMap(), "Pay {G}"),
            "Pay {G}",
            60
        );
        setField(handler, "pendingAction", action);

        assertThat(invokeDecisionBoundaryStatus(handler, action, "test"))
            .isEqualTo("AUTO_HANDLED");
        assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
        assertThat(getField(handler, "pendingAction")).isNull();
    }

    @Test
    void passPriorityReturnsManualManaChoiceWhenPoolSelectionIsAmbiguous() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        AtomicInteger sendPlayerManaTypeCalls = new AtomicInteger();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        return true;
                    }
                    case "sendPlayerManaType" -> {
                        sendPlayerManaTypeCalls.incrementAndGet();
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        setField(player, "manaPool", manaPoolView(1, 0, 1, 0, 0, 0));

        GameView manaView = gameView(61, List.of(player), new CardsView());
        setField(manaView, "myPlayerId", playerId);
        setField(manaView, "canPlayObjects", new PlayableObjectsList());

        PendingAction action = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_PLAY_MANA,
            new GameClientMessage(manaView, Collections.<String, Serializable>emptyMap(), "Pay 1 mana"),
            "Pay 1 mana",
            61
        );
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", manaView);
        setField(handler, "pendingAction", action);

        ActionResult result = handler.passPriority(null, null);

        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(0);
        assertThat(sendPlayerManaTypeCalls.get()).isEqualTo(0);
        assertThat(result.stop_reason).isEqualTo("non_priority_action");
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_PLAY_MANA");
        assertThat(result.response_type).isEqualTo("select");
        assertThat(result.choices).hasSize(2);
        assertThat(result.choices)
            .extracting(choice -> choice.get("choice_type"))
            .containsExactly("pool_mana", "pool_mana");
        assertThat(result.choices)
            .extracting(choice -> choice.get("name"))
            .containsExactly("Blue", "Red");
    }

    private static GameClientMessage multiAmountMessage(List<MultiAmountMessage> items, int min, int max) {
        return new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), items, min, max);
    }

    private static List<BridgeLogEntry> sampleBridgeLogEvents() {
        return List.of(
            bridgeLogEntry(0, "BEGIN_TURN", 1, "Alice", "Alice", null, null),
            bridgeLogEntry(1, "LAND_PLAYED", 1, "Alice", "Alice", "Island", null),
            bridgeLogEntry(2, "BEGIN_TURN", 2, "Bob", "Bob", null, null),
            bridgeLogEntry(3, "LAND_PLAYED", 2, "Bob", "Bob", "Swamp", null),
            bridgeLogEntry(4, "BEGIN_TURN", 3, "Alice", "Alice", null, null),
            bridgeLogEntry(5, "SPELL_CAST", 3, "Alice", "Alice", "Lightning Bolt", "Bob")
        );
    }

    private static String sampleBridgeLogText() {
        return String.join("\n",
            "Alice turn 1:",
            "Alice played Island",
            "Bob turn 1:",
            "Bob played Swamp",
            "Alice turn 2:",
            "Alice cast Lightning Bolt targeting Bob"
        );
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
        return gameView(gameSeq, 1, null, stackObjectIds);
    }

    private static GameView gameView(int gameSeq, int turn, PhaseStep step, UUID... stackObjectIds) throws Exception {
        GameView view = (GameView) UNSAFE.allocateInstance(GameView.class);
        CardsView stack = new CardsView();
        for (UUID stackObjectId : stackObjectIds) {
            stack.put(stackObjectId, null);
        }
        setField(view, "players", List.of());
        setField(view, "myHand", new CardsView());
        setField(view, "exiles", List.of());
        setField(view, "lookedAt", List.of());
        setField(view, "stack", stack);
        setField(view, "activePlayerName", "TestPlayer");
        setField(view, "step", step);
        setField(view, "priorityPlayerName", "TestPlayer");
        setIntField(view, "turn", turn);
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

    private static PermanentView permanentView(UUID id, String shortId, String name, boolean tapped) throws Exception {
        PermanentView view = (PermanentView) UNSAFE.allocateInstance(PermanentView.class);
        setField(view, "id", id);
        setField(view, "shortId", shortId);
        setField(view, "name", name);
        setField(view, "displayName", name);
        setField(view, "rules", List.of());
        setField(view, "cardTypes", List.of(CardType.LAND));
        setField(view, "tapped", tapped);
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

    private static ManaPoolView manaPoolView(int red, int green, int blue, int white, int black, int colorless)
            throws Exception {
        ManaPoolView view = (ManaPoolView) UNSAFE.allocateInstance(ManaPoolView.class);
        setField(view, "red", red);
        setField(view, "green", green);
        setField(view, "blue", blue);
        setField(view, "white", white);
        setField(view, "black", black);
        setField(view, "colorless", colorless);
        return view;
    }

    private static PlayableObjectsList playableObjects(Map<UUID, PlayableObjectStats> objects) throws Exception {
        PlayableObjectsList list = new PlayableObjectsList();
        setField(list, "objects", new LinkedHashMap<>(objects));
        return list;
    }

    private static PlayableObjectStats manaStats(String... manaAbilities) throws Exception {
        PlayableObjectStats stats = new PlayableObjectStats();
        List<Object> records = new java.util.ArrayList<>();
        for (int i = 0; i < manaAbilities.length; i++) {
            records.add(playableObjectRecord(UUID.randomUUID(), manaAbilities[i]));
        }
        setField(stats, "allManaAbilities", records);
        setField(stats, "basicManaAbilities", records);
        return stats;
    }

    private static Object playableObjectRecord(UUID id, String value) throws Exception {
        Class<?> recordClass = Class.forName("mage.players.PlayableObjectRecord");
        var ctor = recordClass.getDeclaredConstructor(UUID.class, String.class);
        ctor.setAccessible(true);
        return ctor.newInstance(id, value);
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

    private static BridgeCardFormatter cardFormatter(GameView lastGameView, UUID currentGameId, UUID playerId) {
        ShortIdRegistry shortIds = new ShortIdRegistry("l");
        BridgeViewLocator viewLocator = new BridgeViewLocator(shortIds, () -> lastGameView, ignored -> {
        });
        return new BridgeCardFormatter(viewLocator, () -> currentGameId, ignored -> playerId);
    }

    private static BridgeOracleTextService oracleTextService() {
        ShortIdRegistry shortIds = new ShortIdRegistry("l");
        BridgeViewLocator viewLocator = new BridgeViewLocator(shortIds, () -> null, ignored -> {
        });
        return new BridgeOracleTextService(shortIds, viewLocator);
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

    private static String invokeDecisionBoundaryStatus(
            BridgeCallbackHandler handler,
            PendingAction action,
            String source
    ) throws Exception {
        Method method = BridgeCallbackHandler.class.getDeclaredMethod(
            "transitionToDecisionBoundary",
            PendingAction.class,
            String.class
        );
        method.setAccessible(true);
        Object transition = method.invoke(handler, action, source);
        Method statusMethod = transition.getClass().getDeclaredMethod("status");
        statusMethod.setAccessible(true);
        Object status = statusMethod.invoke(transition);
        return status.toString();
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

    @SuppressWarnings("unchecked")
    private static void setCachedBridgeEvents(BridgeCallbackHandler handler, List<BridgeLogEntry> events)
            throws Exception {
        List<BridgeLogEntry> cached = (List<BridgeLogEntry>) getField(handler, "cachedBridgeEvents");
        cached.clear();
        cached.addAll(events);
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

    private static SubTypes subTypes(SubType... values) {
        SubTypes subTypes = new SubTypes();
        for (SubType value : values) {
            subTypes.add(value);
        }
        return subTypes;
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

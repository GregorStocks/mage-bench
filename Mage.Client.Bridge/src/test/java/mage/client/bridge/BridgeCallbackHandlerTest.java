package mage.client.bridge;

import mage.client.bridge.processor.BridgeChooseActionFlow;
import mage.client.bridge.processor.BridgeChooseActionFlowContext;
import mage.client.bridge.processor.BridgeChooseActionFlowManager;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeChooseActionStartResult;
import mage.client.bridge.processor.BridgeCallbackEvent;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeConcedeFlow;
import mage.client.bridge.processor.BridgeConcedeFlowManager;
import mage.client.bridge.processor.BridgeDecisionState;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeGameLogState;
import mage.client.bridge.processor.BridgeGameState;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowContext;
import mage.client.bridge.processor.BridgePassPriorityFlowManager;
import mage.client.bridge.processor.BridgePublishedQueryState;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.cards.repository.CardInfo;
import mage.choices.ChoiceImpl;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameStateTool;
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
import mage.view.AbilityPickerView;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.StackAbilityView;
import mage.view.TableClientMessage;
import org.junit.jupiter.api.Test;
import org.apache.log4j.Logger;
import sun.misc.Unsafe;

import java.io.Serializable;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.file.Path;
import java.util.ArrayList;
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
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

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
        addActiveGame(handler, gameId);
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

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(postcombatMainView, Collections.<String, Serializable>emptyMap(), "Pass after overshoot")
            );

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
        publishProcessorState(handler);

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
        addActiveGame(handler, gameId);
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

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(nextTurnUntapView, Collections.<String, Serializable>emptyMap(), "Pass on next turn")
            );

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
        addActiveGame(handler, gameId);
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

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(stackCleared, Collections.<String, Serializable>emptyMap(), "Pass after resolve")
            );

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
        addActiveGame(handler, gameId);
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
            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_ASK,
                gameId,
                new GameClientMessage(nextActionView, Collections.<String, Serializable>emptyMap(), "Mulligan hand?")
            );

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
    void passPriorityUsesLatestPassiveGameUpdateWhenNextSelectLacksGameView() throws Exception {
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession(sessionProxy(autoPassSent, sendPlayerBooleanCalls));
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID landId = UUID.randomUUID();
        CardView land = cardView(landId, "p11", "Plains");
        setField(land, "cardTypes", List.of(CardType.LAND));
        setField(land, "rules", List.of("{T}: Add {W}."));
        setField(land, "manaCostLeftStr", List.of());
        setField(land, "manaCostRightStr", List.of());

        PlayerView player = playerView(playerId, "TestPlayer", "p2");
        GameView initialView = gameView(21, List.of(player), new CardsView());
        setField(initialView, "myPlayerId", playerId);

        CardsView updatedHand = new CardsView();
        updatedHand.put(landId, land);
        GameView updatedView = gameView(44, List.of(player), new CardsView());
        setField(updatedView, "myPlayerId", playerId);
        setField(updatedView, "myHand", updatedHand);
        setField(updatedView, "step", PhaseStep.PRECOMBAT_MAIN);
        setField(updatedView, "canPlayObjects", playableObjects(Map.of(landId, playStats("Play land"))));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Play instants and activated abilities"),
            "Play instants and activated abilities",
            21
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority(null, null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            enqueueCallback(handler, ClientCallbackMethod.GAME_UPDATE, gameId, updatedView);
            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage((GameView) null, Collections.<String, Serializable>emptyMap(), "Play spells and abilities")
            );

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("playable_cards");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.board).isNotNull();
            assertThat(result.choices).singleElement().satisfies(choice ->
                assertThat(choice).containsEntry("name", "Plains")
            );
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
                    int cursor = (Integer) args[2];
                    if (cursor == 0) {
                        return bridgeEvents;
                    }
                    assertThat(cursor).isEqualTo(1);
                    return List.of();
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

        addActiveGame(handler, gameId, playerId);
        setCurrentChatId(handler, gameId, chatId);

        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_OVER,
            gameId,
            new GameClientMessage(gameView(9), Collections.<String, Serializable>emptyMap(), "Player Opponent is the winner"),
            false
        );
        handler.handleCallback(callback);

        handler.awaitProcessorIdle();
        waitForCondition(() -> getBridgeEventsCalls.get() >= 2
            && handler.getGameHistory(null, null).event_count == 1);
        assertThat(hasActiveGame(handler, gameId)).isFalse();
        assertThat(leaveChatCalls.get()).isEqualTo(1);

        int callsBeforeHistory = getBridgeEventsCalls.get();
        var history = handler.getGameHistory(null, null);
        assertThat(getBridgeEventsCalls.get()).isEqualTo(callsBeforeHistory);
        assertThat(history.cursor).isEqualTo(1);
        assertThat(history.event_count).isEqualTo(1);
        assertThat(history.history).contains("Turn 3 (TestPlayer):");
        assertThat(history.history).contains("TestPlayer played Island");
    }

    @Test
    void gameLogUsesProcessorLocalCursorInsteadOfServerEventIndex() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, List.of(
            new BridgeLogEntry(50, 50, "BEGIN_TURN", 3, "PRECOMBAT_MAIN", "PRECOMBAT_MAIN",
                "TestPlayer", "TestPlayer", null, null, 0, true),
            new BridgeLogEntry(51, 51, "LAND_PLAYED", 3, "PRECOMBAT_MAIN", "PRECOMBAT_MAIN",
                "TestPlayer", "TestPlayer", "Island", null, 0, true),
            new BridgeLogEntry(71, 71, "SPELL_CAST", 3, "PRECOMBAT_MAIN", "PRECOMBAT_MAIN",
                "TestPlayer", "TestPlayer", "Opt", null, 0, true)
        ));

        var log = handler.getGameLogChunk(0, null);
        var history = handler.getGameHistory(null, null);

        assertThat(log.cursor).isEqualTo(3);
        assertThat(history.cursor).isEqualTo(3);
        assertThat(log.log).contains("TestPlayer turn 1:");
        assertThat(history.event_count).isEqualTo(3);
    }

    @Test
    void getGameLogResetsStaleCursorToFirstPublishedEntry() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        handler.reset();
        setCachedBridgeEvents(handler, List.of(
            bridgeLogEntry(9, "BEGIN_TURN", 4, "Alice", "Alice", null, null),
            bridgeLogEntry(10, "SPELL_CAST", 4, "Alice", "Alice", "Shock", "Bob")
        ));

        var result = handler.getGameLogChunk(0, 0);

        assertThat(result.cursor_reset).isTrue();
        assertThat(result.cursor).isEqualTo(8);
        assertThat(result.log).contains("Alice turn 1:");
        assertThat(result.log).contains("Alice cast Shock targeting Bob");
    }

    @Test
    void getGameHistoryKeepsFutureCursorAtPublishedNextCursor() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        var result = handler.getGameHistory(null, 999);

        assertThat(result.cursor).isEqualTo(6);
        assertThat(result.event_count).isZero();
        assertThat(result.history).isEqualTo("No game events recorded yet.");
    }

    @Test
    void getGameHistoryReadsPublishedSnapshotWithoutFetchingServerBridgeEvents() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        AtomicInteger getBridgeEventsCalls = new AtomicInteger();
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("getBridgeEvents".equals(method.getName())) {
                    getBridgeEventsCalls.incrementAndGet();
                    throw new AssertionError("MCP reads should not fetch bridge events");
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");
        setCachedBridgeEvents(handler, sampleBridgeLogEvents());

        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<?> historyFuture = executor.submit(() -> handler.getGameHistory(null, null));
            Future<Void> processorFuture = executor.submit(() -> processor.submit(BridgeCommand.of(() -> null)));
            processorFuture.get(1, TimeUnit.SECONDS);

            historyFuture.get(1, TimeUnit.SECONDS);
            assertThat(getBridgeEventsCalls.get()).isZero();
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void getGameHistoryWaitsForPublishedLogAfterRefreshSyncBarrier() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        AtomicInteger getBridgeEventsCalls = new AtomicInteger();
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("getBridgeEvents".equals(method.getName())) {
                    int cursor = (Integer) args[2];
                    getBridgeEventsCalls.incrementAndGet();
                    if (cursor == 0) {
                        return List.of(bridgeLogEntry(9, "LAND_PLAYED", 4, "Alice", "Alice", "Shock", "Bob"));
                    }
                    return List.of();
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        BridgeGameLogState gameLogState = (BridgeGameLogState) getProcessorStateField(handler, "gameLogState");
        BridgeGameLogRefresher gameLogRefresher = (BridgeGameLogRefresher) getDirectField(handler, "gameLogRefresher");
        BridgePublishedQueryState publishedQueryState = (BridgePublishedQueryState) getDirectField(handler, "publishedQueryState");

        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        addActiveGame(handler, gameId, playerId);

        CountDownLatch afterHookBlocked = new CountDownLatch(1);
        CountDownLatch releaseAfterHook = new CountDownLatch(1);
        AtomicBoolean delayedPublish = new AtomicBoolean(false);
        processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent event
                    && gameState.currentGameId() != null
                    && gameState.currentGameId().equals(event.objectId())) {
                gameLogRefresher.afterCallbackProcessed();
            }
            if (message instanceof BridgeCommand<?>
                    && gameLogState.publishedGameLog().nextCursor() > 0
                    && delayedPublish.compareAndSet(false, true)) {
                afterHookBlocked.countDown();
                try {
                    assertThat(releaseAfterHook.await(1, TimeUnit.SECONDS)).isTrue();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException("Interrupted while blocking afterMessageHook", e);
                }
            }
            publishedQueryState.publishProcessorState();
        });

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            enqueueCallback(handler, ClientCallbackMethod.GAME_UPDATE, gameId, null);

            Future<GetGameHistoryTool.Result> future = executor.submit(() -> handler.getGameHistory(null, null));

            assertThat(afterHookBlocked.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            releaseAfterHook.countDown();

            GetGameHistoryTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(getBridgeEventsCalls.get()).isGreaterThanOrEqualTo(1);
            assertThat(result.event_count).isEqualTo(1);
            assertThat(result.cursor).isEqualTo(1);
            assertThat(result.history).contains("Alice played Shock");
        } finally {
            releaseAfterHook.countDown();
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
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

        addActiveGame(handler, gameId, playerId);
        setCurrentChatId(handler, gameId, chatId);
        setField(handler, "gameEverStarted", true);

        // Send END_GAME_INFO without prior GAME_OVER — simulates dropped callback
        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.END_GAME_INFO,
            gameId,
            null,
            false
        );
        handler.handleCallback(callback);

        handler.awaitProcessorIdle();
        assertThat(hasActiveGame(handler, gameId)).isFalse();
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

        addActiveGame(handler, gameId, playerId);
        setCurrentChatId(handler, gameId, chatId);

        // Send GAME_OVER first
        ClientCallback gameOverCallback = new ClientCallback(
            ClientCallbackMethod.GAME_OVER,
            gameId,
            new GameClientMessage(gameView(9), Collections.<String, Serializable>emptyMap(), "Player Opponent is the winner"),
            false
        );
        handler.handleCallback(gameOverCallback);
        handler.awaitProcessorIdle();
        assertThat(leaveChatCalls.get()).isEqualTo(1);

        // Send END_GAME_INFO second — should be a no-op
        ClientCallback endGameInfoCallback = new ClientCallback(
            ClientCallbackMethod.END_GAME_INFO,
            gameId,
            null,
            false
        );
        handler.handleCallback(endGameInfoCallback);
        handler.awaitProcessorIdle();

        // leaveChat should NOT have been called a second time
        assertThat(leaveChatCalls.get()).isEqualTo(1);
    }

    @Test
    void concedeWaitsForGameOverOnProcessorFlow() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID chatId = UUID.randomUUID();
        CountDownLatch concedeSent = new CountDownLatch(1);
        AtomicReference<String> concedeThreadName = new AtomicReference<>();
        AtomicInteger leaveChatCalls = new AtomicInteger();

        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerAction" -> {
                        concedeThreadName.set(Thread.currentThread().getName());
                        assertThat(args[0]).isEqualTo(mage.constants.PlayerAction.CONCEDE);
                        assertThat(args[1]).isEqualTo(gameId);
                        assertThat(args[2]).isNull();
                        concedeSent.countDown();
                        return defaultReturnValue(method.getReturnType());
                    }
                    case "leaveChat" -> {
                        leaveChatCalls.incrementAndGet();
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));
        BridgeCallbackHandler handler = client.getCallbackHandler();
        handler.setKeepAliveAfterGame(true);

        addActiveGame(handler, gameId, playerId);
        setCurrentChatId(handler, gameId, chatId);

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> future = executor.submit(handler::concede);

            assertThat(concedeSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            handler.handleCallback(new ClientCallback(
                ClientCallbackMethod.GAME_OVER,
                gameId,
                new GameClientMessage(
                    gameView(9),
                    Collections.<String, Serializable>emptyMap(),
                    "Player Opponent is the winner"
                ),
                false
            ));

            assertThat(future.get(1, TimeUnit.SECONDS)).isTrue();
            handler.awaitProcessorIdle();
            assertThat(concedeThreadName.get()).startsWith("bridge-processor-TestPlayer");
            assertThat(hasActiveGame(handler, gameId)).isFalse();
            assertThat(leaveChatCalls.get()).isEqualTo(1);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void concedeReturnsFalseWhenProcessorStopsWhileWaitingForGameOver() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        CountDownLatch concedeSent = new CountDownLatch(1);

        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerAction".equals(method.getName())) {
                    concedeSent.countDown();
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));
        BridgeCallbackHandler handler = client.getCallbackHandler();
        handler.setKeepAliveAfterGame(true);

        addActiveGame(handler, gameId, playerId);

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> future = executor.submit(handler::concede);

            assertThat(concedeSent.await(1, TimeUnit.SECONDS)).isTrue();
            client.stop();

            assertThat(future.get(1, TimeUnit.SECONDS)).isFalse();
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
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

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", askView);
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_TARGET, gameId, targetMessage);
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(onlyTarget);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
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
    void chooseActionWaitsThroughMultipleAutoResolvedFollowups() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID onlyTarget = UUID.randomUUID();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();

        GameView askView = gameView(20);
        GameView targetView = gameView(21);
        GameView abilityView = gameView(22);
        GameView nextDecisionView = gameView(23);
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose a creature to copy",
            new CardsView(),
            Set.of(onlyTarget),
            true
        );
        AbilityPickerView emptyAbilityPicker = new AbilityPickerView(
            abilityView,
            new LinkedHashMap<>(),
            "Choose spell or ability"
        );
        GameClientMessage nextDecisionMessage = new GameClientMessage(
            nextDecisionView,
            Collections.<String, Serializable>emptyMap(),
            "Play spells and abilities"
        );

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", askView);
        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_TARGET, gameId, targetMessage);
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        if (sendPlayerUuidCalls.get() == 1) {
                            assertThat(args[1]).isEqualTo(onlyTarget);
                            enqueueCallback(handler, ClientCallbackMethod.GAME_CHOOSE_ABILITY, gameId, emptyAbilityPicker);
                        } else {
                            assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
                            assertThat(args[1]).isNull();
                            enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
                        }
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
            20
        ));

        var result = handler.chooseAction(
            null, null, true, null, null, null, null, null, null, null, null
        );

        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
        assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
        assertThat(result.success).isTrue();
        assertThat(result.action_taken).isEqualTo("yes");
        assertThat(result.game_seq).isEqualTo(23);
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_SELECT");
        assertThat(result.response_type).isEqualTo("boolean");
        assertThat(result.message).isEqualTo("Play spells and abilities");
    }

    @Test
    void chooseActionDoesNotMonopolizeProcessorThreadWhileWaiting() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch sendPlayerBooleanCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(40);
        GameView nextDecisionView = gameView(41);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    sendPlayerBooleanCalled.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Use effect of Clone?"),
            "Use effect of Clone?",
            40
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, true, null, null, null, null, null, null, null, null
            ));

            assertThat(sendPlayerBooleanCalled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            handler.awaitProcessorIdle();

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(nextDecisionView, Collections.<String, Serializable>emptyMap(), "Play spells and abilities")
            );

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("yes");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.game_seq).isEqualTo(41);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void batchChooseActionAttackersDoesNotMonopolizeProcessorThreadWhileWaiting() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID attackerUuid = UUID.randomUUID();
        CountDownLatch sendPlayerUuidCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView combatView = gameView(45);
        GameView confirmView = gameView(46);
        GameView nextDecisionView = gameView(47);

        registerShortId(handler, attackerUuid, "p1");

        var combatOptions = new LinkedHashMap<String, Serializable>();
        combatOptions.put("possibleAttackers", new ArrayList<>(List.of(attackerUuid)));
        GameClientMessage combatMessage = new GameClientMessage(combatView, combatOptions, "Declare attackers");
        GameClientMessage confirmMessage = new GameClientMessage(confirmView, Collections.<String, Serializable>emptyMap(), "Declare attackers");
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
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        sendPlayerUuidCalled.countDown();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(attackerUuid);
                        return true;
                    }
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", combatView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            combatMessage,
            "Declare attackers",
            45
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, null, null, null, null, null, null, null, new String[]{"p1"}, null
            ));

            assertThat(sendPlayerUuidCalled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            handler.awaitProcessorIdle();

            enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, confirmMessage);

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("batch_attack");
            assertThat(result.declared).containsExactly(Map.of("id", "p1"));
            assertThat(result.action_pending).isTrue();
            assertThat(result.game_seq).isEqualTo(47);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void batchChooseActionAttackersAllWithSingleAttackerUsesDirectUuidPath() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID attackerUuid = UUID.randomUUID();
        CountDownLatch sendPlayerUuidCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView combatView = gameView(145);
        GameView confirmView = gameView(146);
        GameView nextDecisionView = gameView(147);

        registerShortId(handler, attackerUuid, "p1");

        var combatOptions = new LinkedHashMap<String, Serializable>();
        combatOptions.put("possibleAttackers", new ArrayList<>(List.of(attackerUuid)));
        GameClientMessage combatMessage = new GameClientMessage(combatView, combatOptions, "Declare attackers");
        GameClientMessage confirmMessage = new GameClientMessage(confirmView, Collections.<String, Serializable>emptyMap(), "Declare attackers");
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
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        sendPlayerUuidCalled.countDown();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(attackerUuid);
                        return true;
                    }
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
                        return true;
                    }
                    case "sendPlayerString" -> throw new AssertionError("single-attacker attackers=all should not use special path");
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", combatView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            combatMessage,
            "Declare attackers",
            145
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, null, null, null, null, null, null, null, new String[]{"all"}, null
            ));

            assertThat(sendPlayerUuidCalled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            handler.awaitProcessorIdle();
            enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, confirmMessage);

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("batch_attack");
            assertThat(result.declared).containsExactly(Map.of("id", "all"));
            assertThat(result.action_pending).isTrue();
            assertThat(result.game_seq).isEqualTo(147);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void batchChooseActionAttackersAllWithSingleAttackerReturnsImmediateNextDecisionWithoutConfirm() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID attackerUuid = UUID.randomUUID();
        CountDownLatch sendPlayerUuidCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView combatView = gameView(182);
        GameView nextDecisionView = gameView(187, 5, PhaseStep.DECLARE_ATTACKERS);

        registerShortId(handler, attackerUuid, "p1");

        var combatOptions = new LinkedHashMap<String, Serializable>();
        combatOptions.put("possibleAttackers", new ArrayList<>(List.of(attackerUuid)));
        GameClientMessage combatMessage = new GameClientMessage(combatView, combatOptions, "Declare attackers");
        GameClientMessage nextDecisionMessage = new GameClientMessage(
            nextDecisionView,
            Collections.<String, Serializable>emptyMap(),
            "Play instants and activated abilities"
        );

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        sendPlayerUuidCalled.countDown();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(attackerUuid);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
                        return true;
                    }
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        throw new AssertionError("single-attacker attackers=all should not confirm when XMage already moved to the next decision");
                    }
                    case "sendPlayerString" -> throw new AssertionError("single-attacker attackers=all should not use special path");
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", combatView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            combatMessage,
            "Declare attackers",
            182
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, null, null, null, null, null, null, null, new String[]{"all"}, null
            ));

            assertThat(sendPlayerUuidCalled.await(1, TimeUnit.SECONDS)).isTrue();

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerUuidCalls.get()).isEqualTo(1);
            assertThat(sendPlayerBooleanCalls.get()).isZero();
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("batch_attack");
            assertThat(result.declared).containsExactly(Map.of("id", "all"));
            assertThat(result.action_pending).isTrue();
            assertThat(result.message).isEqualTo("Play instants and activated abilities");
            assertThat(result.game_seq).isEqualTo(187);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void batchChooseActionBlockersHandlesTargetPromptAndReturnsNextDecision() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID blockerUuid = UUID.randomUUID();
        UUID attackerUuid = UUID.randomUUID();
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView combatView = gameView(48);
        GameView targetView = gameView(49);
        GameView returnSelectView = gameView(50);
        GameView nextDecisionView = gameView(51);

        registerShortId(handler, blockerUuid, "p5");
        registerShortId(handler, attackerUuid, "p1");

        var combatOptions = new LinkedHashMap<String, Serializable>();
        combatOptions.put("possibleBlockers", new ArrayList<>(List.of(blockerUuid)));
        GameClientMessage combatMessage = new GameClientMessage(combatView, combatOptions, "Declare blockers");
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose attacker to block",
            new CardsView(),
            Set.of(attackerUuid),
            true
        );
        GameClientMessage returnSelectMessage = new GameClientMessage(
            returnSelectView,
            Collections.<String, Serializable>emptyMap(),
            "Declare blockers"
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
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        if (sendPlayerUuidCalls.get() == 1) {
                            assertThat(args[1]).isEqualTo(blockerUuid);
                            enqueueCallback(handler, ClientCallbackMethod.GAME_TARGET, gameId, targetMessage);
                        } else {
                            assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
                            assertThat(args[1]).isEqualTo(attackerUuid);
                            enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, returnSelectMessage);
                        }
                        return true;
                    }
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(true);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, nextDecisionMessage);
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", combatView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            combatMessage,
            "Declare blockers",
            48
        ));

        ChooseActionTool.Result result = handler.chooseAction(
            null, null, null, null, null, null, null, null, null, null, new String[]{"p5:p1"}
        );

        assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
        assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
        assertThat(result.success).isTrue();
        assertThat(result.action_taken).isEqualTo("batch_block");
        assertThat(result.declared).containsExactly(Map.of("id", "p5", "blocks", "p1"));
        assertThat(result.action_pending).isTrue();
        assertThat(result.game_seq).isEqualTo(51);
    }

    @Test
    void chooseActionReturnsAfterClientStopWithoutFollowupCallback() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch sendPlayerBooleanCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(50);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    sendPlayerBooleanCalled.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Use effect of Clone?"),
            "Use effect of Clone?",
            50
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, true, null, null, null, null, null, null, null, null
            ));

            assertThat(sendPlayerBooleanCalled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            client.stop();

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("yes");
            assertThat(result.action_pending).isNull();
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void chooseActionReturnsAfterClientDisconnectWithoutFollowupCallback() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch sendPlayerBooleanCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(55);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    sendPlayerBooleanCalled.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Use effect of Clone?"),
            "Use effect of Clone?",
            55
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, true, null, null, null, null, null, null, null, null
            ));

            assertThat(sendPlayerBooleanCalled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            client.disconnected(false, false);

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.success).isTrue();
            assertThat(result.action_taken).isEqualTo("yes");
            assertThat(result.action_pending).isNull();
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void chooseActionReturnsAfterClientStopWithBatchTargetCallbackPending() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID blockerUuid = UUID.randomUUID();
        UUID attackerUuid = UUID.randomUUID();
        CountDownLatch targetCallbackQueued = new CountDownLatch(1);
        AtomicInteger sendPlayerUuidCalls = new AtomicInteger();
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView combatView = gameView(56);
        GameView targetView = gameView(57);

        registerShortId(handler, blockerUuid, "p5");
        registerShortId(handler, attackerUuid, "p1");

        var combatOptions = new LinkedHashMap<String, Serializable>();
        combatOptions.put("possibleBlockers", new ArrayList<>(List.of(blockerUuid)));
        GameClientMessage combatMessage = new GameClientMessage(combatView, combatOptions, "Declare blockers");
        GameClientMessage targetMessage = new GameClientMessage(
            targetView,
            Collections.<String, Serializable>emptyMap(),
            "Choose attacker to block",
            new CardsView(),
            Set.of(attackerUuid),
            true
        );

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        if (sendPlayerUuidCalls.get() == 1) {
                            assertThat(args[1]).isEqualTo(blockerUuid);
                            enqueueCallback(handler, ClientCallbackMethod.GAME_TARGET, gameId, targetMessage);
                            targetCallbackQueued.countDown();
                        } else {
                            assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
                            assertThat(args[1]).isEqualTo(attackerUuid);
                        }
                        return true;
                    }
                    case "sendPlayerBoolean" -> {
                        sendPlayerBooleanCalls.incrementAndGet();
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", combatView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            combatMessage,
            "Declare blockers",
            56
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ChooseActionTool.Result> future = executor.submit(() -> handler.chooseAction(
                null, null, null, null, null, null, null, null, null, null, new String[]{"p5:p1"}
            ));

            assertThat(targetCallbackQueued.await(1, TimeUnit.SECONDS)).isTrue();
            BridgeDecisionState decisionState = (BridgeDecisionState) getField(handler, "decisionState");
            long deadlineNanos = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
            while (System.nanoTime() < deadlineNanos) {
                PendingAction pendingAction = decisionState.pendingAction();
                if (pendingAction != null && pendingAction.method() == ClientCallbackMethod.GAME_TARGET) {
                    break;
                }
                Thread.sleep(10);
            }

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            client.stop();

            ChooseActionTool.Result result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerUuidCalls.get()).isEqualTo(2);
            assertThat(sendPlayerBooleanCalls.get()).isZero();
            assertThat(result.success).isFalse();
            assertThat(result.interrupted).isTrue();
            assertThat(result.action_taken).isEqualTo("batch_block");
            assertThat(result.declared).containsExactly(Map.of("id", "p5", "blocks", "p1"));
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void chooseActionReturnsCancelledResultWhenCallerThreadIsInterrupted() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch sendPlayerBooleanCalled = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(60);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    sendPlayerBooleanCalled.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Use effect of Clone?"),
            "Use effect of Clone?",
            60
        ));

        AtomicReference<ChooseActionTool.Result> resultRef = new AtomicReference<>();
        AtomicReference<Throwable> errorRef = new AtomicReference<>();
        AtomicBoolean interruptFlagAfterReturn = new AtomicBoolean(false);
        CountDownLatch done = new CountDownLatch(1);

        Thread worker = new Thread(() -> {
            try {
                resultRef.set(handler.chooseAction(
                    null, null, true, null, null, null, null, null, null, null, null
                ));
                interruptFlagAfterReturn.set(Thread.currentThread().isInterrupted());
            } catch (Throwable t) {
                errorRef.set(t);
            } finally {
                done.countDown();
            }
        }, "choose-action-interrupt-test");

        worker.start();
        try {
            assertThat(sendPlayerBooleanCalled.await(1, TimeUnit.SECONDS)).isTrue();

            worker.interrupt();

            assertThat(done.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(errorRef.get()).isNull();
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(resultRef.get()).isNotNull();
            assertThat(resultRef.get().success).isFalse();
            assertThat(resultRef.get().error_code).isEqualTo("cancelled");
            assertThat(interruptFlagAfterReturn.get()).isTrue();
        } finally {
            worker.join(1000);
        }
    }

    @Test
    void chooseActionTickerFailsFlowWhenTickCommandThrowsIllegalStateException() throws Exception {
        AtomicBoolean failOnDecisionRead = new AtomicBoolean(false);
        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeChooseActionFlowManager.class),
            event -> { }
        );
        BridgeDecisionState decisionState = new BridgeDecisionState();
        BridgeChooseActionFlowContext context = new BridgeChooseActionFlowContext() {
            @Override
            public PendingAction currentPendingAction() {
                return null;
            }

            @Override
            public PendingAction currentDecisionAction() {
                if (failOnDecisionRead.get()) {
                    throw new IllegalStateException("choose_action tick failed");
                }
                return null;
            }

            @Override
            public boolean requestCannotContinue() {
                return false;
            }

            @Override
            public ChooseActionTool.Result noPendingActionResult() {
                return new ChooseActionTool.Result();
            }

            @Override
            public BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action) {
                throw new AssertionError("applyChooseAction should not run");
            }

            @Override
            public String detectCombatSelect(PendingAction action) {
                throw new AssertionError("detectCombatSelect should not run");
            }

            @Override
            public UUID resolveShortId(String shortId) {
                throw new AssertionError("resolveShortId should not run");
            }

            @Override
            public Set<UUID> validTargets(PendingAction action) {
                throw new AssertionError("validTargets should not run");
            }

            @Override
            public boolean clearPendingActionIfCurrent(PendingAction action) {
                throw new AssertionError("clearPendingActionIfCurrent should not run");
            }

            @Override
            public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
                throw new AssertionError("sendBooleanOrDie should not run");
            }

            @Override
            public void sendUuidOrDie(UUID gameId, UUID data, String sendContext) {
                throw new AssertionError("sendUuidOrDie should not run");
            }

            @Override
            public void sendStringOrDie(UUID gameId, String data, String sendContext) {
                throw new AssertionError("sendStringOrDie should not run");
            }

            @Override
            public void clearLastChoices() {
            }

            @Override
            public ChooseActionTool.Result buildChooseActionError(
                    ChooseActionTool.Result result,
                    String errorCode,
                    String message,
                    boolean retryable,
                    PendingAction action) {
                throw new AssertionError("buildChooseActionError should not run");
            }

            @Override
            public void finishChooseActionWithNextDecision(
                    ChooseActionTool.Result result,
                    PendingAction previousAction,
                    PendingAction nextAction) {
                throw new AssertionError("finishChooseActionWithNextDecision should not run");
            }

            @Override
            public void finishChooseActionWithoutNextDecision(
                    ChooseActionTool.Result result,
                    PendingAction previousAction) {
                throw new AssertionError("finishChooseActionWithoutNextDecision should not run");
            }

            @Override
            public void finishBatchChooseActionWithNextDecision(
                    ChooseActionTool.Result result,
                    PendingAction nextAction) {
                throw new AssertionError("finishBatchChooseActionWithNextDecision should not run");
            }

            @Override
            public void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result) {
                throw new AssertionError("finishBatchChooseActionWithoutNextDecision should not run");
            }

            @Override
            public ChooseActionTool.Result cancelledChooseActionResult(
                    PendingAction previousAction,
                    ChooseActionTool.Result partialResult) {
                throw new AssertionError("cancelledChooseActionResult should not run");
            }
        };
        BridgeChooseActionFlowManager manager = new BridgeChooseActionFlowManager(
            processor,
            "TestPlayer",
            decisionState,
            context,
            message -> {
                var result = new ChooseActionTool.Result();
                result.error = message;
                return result;
            }
        );
        processor.start();

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            BridgeChooseActionFlow flow = processor.submit(BridgeCommand.of(() -> manager.startPendingFlow(
                new BridgeChooseActionInput(null, null, null, null, null, null, null, null, null, null, null)
            )));

            Future<ChooseActionTool.Result> future = executor.submit(flow::awaitResult);
            failOnDecisionRead.set(true);

            assertThatThrownBy(() -> future.get(1, TimeUnit.SECONDS))
                .isInstanceOf(ExecutionException.class)
                .satisfies(error -> {
                    Throwable outerCause = ((ExecutionException) error).getCause();
                    assertThat(outerCause).isInstanceOf(ExecutionException.class);
                    assertThat(outerCause.getCause())
                        .isInstanceOf(IllegalStateException.class)
                        .hasMessage("choose_action tick failed");
                });
            assertThat(decisionState.pendingChooseActionFlow()).isNull();
        } finally {
            manager.shutdown();
            processor.shutdown("test done");
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
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
                        enqueueCallback(handler, ClientCallbackMethod.GAME_TARGET, gameId, targetMessage);
                        return true;
                    }
                    case "sendPlayerUUID" -> {
                        sendPlayerUuidCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(gameId);
                        assertThat(args[1]).isEqualTo(onlyTarget);
                        enqueueCallback(handler, ClientCallbackMethod.GAME_ASK, gameId, nextDecisionMessage);
                        return true;
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        addActiveGame(handler, gameId);
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
                    enqueueCallback(handler, ClientCallbackMethod.GAME_SELECT, gameId, combatMessage);
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
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

        addActiveGame(handler, gameId, playerId);

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
        handler.awaitProcessorIdle();

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
        assertThat(handler.isActionPending()).isTrue();
    }

    @Test
    void getGameStateReadsPublishedProcessorSnapshot() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID tableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        GameView gameView = gameView(12, List.of(playerView(playerId, "TestPlayer", "p2")), new CardsView());

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                return switch (method.getName()) {
                    case "joinGame" -> true;
                    case "getGameChatId" -> Optional.empty();
                    default -> defaultReturnValue(method.getReturnType());
                };
            }
        ));

        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.START_GAME,
            gameId,
            new TableClientMessage().withTable(tableId, null).withPlayer(playerId),
            false
        ));
        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.GAME_INIT,
            gameId,
            gameView,
            false
        ));
        handler.awaitProcessorIdle();

        var result = handler.getGameState(null);

        assertThat(result.available).isTrue();
        assertThat(result.snapshot_id).isEqualTo(publishedGameStateSnapshotId(handler));
        assertThat(result.game_seq).isEqualTo(12);
        assertThat(result.turn).isEqualTo(1);
        assertThat(result.active_player).isEqualTo("TestPlayer");
        assertThat(result.priority_player).isEqualTo("TestPlayer");
    }

    @Test
    void publishesGameStateSnapshotIdBeforeFirstMcpRead() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID tableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        GameView gameView = gameView(12, List.of(playerView(playerId, "TestPlayer", "p2")), new CardsView());

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                return switch (method.getName()) {
                    case "joinGame" -> true;
                    case "getGameChatId" -> Optional.empty();
                    default -> defaultReturnValue(method.getReturnType());
                };
            }
        ));

        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.START_GAME,
            gameId,
            new TableClientMessage().withTable(tableId, null).withPlayer(playerId),
            false
        ));
        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.GAME_INIT,
            gameId,
            gameView,
            false
        ));
        handler.awaitProcessorIdle();

        assertThat(publishedGameStateSnapshotId(handler)).isEqualTo(handler.getGameState(null).snapshot_id);
    }

    @Test
    void getGameStateWithSnapshotIdWaitsForQueuedCallbacksBeforeReportingUnchanged() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID tableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        GameView initialView = gameView(12, List.of(playerView(playerId, "TestPlayer", "p2")), new CardsView());
        GameView queuedView = gameView(13, List.of(playerView(playerId, "TestPlayer", "p2")), new CardsView());

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                return switch (method.getName()) {
                    case "joinGame" -> true;
                    case "getGameChatId" -> Optional.empty();
                    default -> defaultReturnValue(method.getReturnType());
                };
            }
        ));

        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.START_GAME,
            gameId,
            new TableClientMessage().withTable(tableId, null).withPlayer(playerId),
            false
        ));
        handler.handleCallback(new ClientCallback(
            ClientCallbackMethod.GAME_INIT,
            gameId,
            initialView,
            false
        ));
        handler.awaitProcessorIdle();

        long initialSnapshotId = handler.getGameState(null).snapshot_id;
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");

        CountDownLatch blockerEntered = new CountDownLatch(1);
        CountDownLatch releaseBlocker = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<?> blockerFuture = executor.submit(() -> processor.submit(new BridgeCommand<Void>() {
                @Override
                public Void execute() {
                    blockerEntered.countDown();
                    try {
                        assertThat(releaseBlocker.await(1, TimeUnit.SECONDS)).isTrue();
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new IllegalStateException("Interrupted while blocking processor", e);
                    }
                    return null;
                }
            }));

            assertThat(blockerEntered.await(1, TimeUnit.SECONDS)).isTrue();

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(queuedView, Collections.<String, Serializable>emptyMap(), "Pass")
            );

            Future<GetGameStateTool.Result> future = executor.submit(() -> handler.getGameState(initialSnapshotId));

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            releaseBlocker.countDown();

            GetGameStateTool.Result result = future.get(1, TimeUnit.SECONDS);
            blockerFuture.get(1, TimeUnit.SECONDS);

            assertThat(result.available).isTrue();
            assertThat(result.unchanged).isNull();
            assertThat(result.game_seq).isEqualTo(13);
            assertThat(result.snapshot_id).isNotEqualTo(initialSnapshotId);
        } finally {
            releaseBlocker.countDown();
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void getActionChoicesWaitsForQueuedCallbacksBeforeReturningPublishedChoices() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");

        UUID gameId = UUID.randomUUID();
        GameView queuedView = gameView(13, 3, PhaseStep.PRECOMBAT_MAIN);
        addActiveGame(handler, gameId);

        CountDownLatch blockerEntered = new CountDownLatch(1);
        CountDownLatch releaseBlocker = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<?> blockerFuture = executor.submit(() -> processor.submit(new BridgeCommand<Void>() {
                @Override
                public Void execute() {
                    blockerEntered.countDown();
                    try {
                        assertThat(releaseBlocker.await(1, TimeUnit.SECONDS)).isTrue();
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new IllegalStateException("Interrupted while blocking processor", e);
                    }
                    return null;
                }
            }));

            assertThat(blockerEntered.await(1, TimeUnit.SECONDS)).isTrue();

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_SELECT,
                gameId,
                new GameClientMessage(queuedView, Collections.<String, Serializable>emptyMap(), "Pass")
            );

            Future<ActionResult> future = executor.submit(() -> handler.getActionChoices(null));

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            releaseBlocker.countDown();

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            blockerFuture.get(1, TimeUnit.SECONDS);

            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_SELECT");
            assertThat(result.response_type).isEqualTo("boolean");
            assertThat(result.game_seq).isEqualTo(13);
        } finally {
            releaseBlocker.countDown();
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void failedProcessorCommandStillPublishesUpdatedActionPendingSnapshot() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");
        BridgeDecisionState decisionState = (BridgeDecisionState) getProcessorStateField(handler, "decisionState");
        UUID gameId = UUID.randomUUID();
        PendingAction pendingAction = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage((GameView) null, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            7
        );

        processor.submit(BridgeCommand.of(() -> {
            decisionState.replacePendingAction(pendingAction);
            return null;
        }));
        assertThat(handler.isActionPending()).isTrue();

        assertThatThrownBy(() -> processor.submit(new BridgeCommand<Void>() {
            @Override
            public Void execute() {
                decisionState.clearPendingActionIfCurrent(pendingAction);
                throw new IllegalStateException("boom");
            }
        })).isInstanceOf(IllegalStateException.class)
            .hasMessage("boom");

        assertThat(handler.isActionPending()).isFalse();
    }

    @Test
    void callbackHookFailureDoesNotKillProcessorThread() throws Exception {
        AtomicInteger hookCalls = new AtomicInteger();
        CountDownLatch callbackHandled = new CountDownLatch(1);
        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeCallbackHandlerTest.class),
            event -> callbackHandled.countDown()
        );
        processor.setAfterMessageHook(message -> {
            if (hookCalls.getAndIncrement() == 0) {
                throw new IllegalStateException("hook failed");
            }
        });
        processor.start();

        try {
            processor.enqueueCallback(new BridgeCallbackEvent(
                UUID.randomUUID(),
                ClientCallbackMethod.GAME_SELECT,
                null
            ));

            assertThat(callbackHandled.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(processor.submit(BridgeCommand.of(() -> "ok"))).isEqualTo("ok");
            assertThat(hookCalls.get()).isGreaterThanOrEqualTo(2);
        } finally {
            processor.shutdown("test");
        }
    }

    @Test
    void handleCallbackProcessesStartGameOnProcessorThread() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        UUID tableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicReference<String> joinGameThreadName = new AtomicReference<>();
        AtomicReference<String> listenerThreadName = new AtomicReference<>();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("joinGame".equals(method.getName())) {
                    joinGameThreadName.set(Thread.currentThread().getName());
                    assertThat(args[0]).isEqualTo(gameId);
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.START_GAME,
            gameId,
            new TableClientMessage().withTable(tableId, null).withPlayer(playerId),
            false
        ) {
            @Override
            public void decompressData() {
                listenerThreadName.set(Thread.currentThread().getName());
                super.decompressData();
            }
        };

        String callbackCallerThreadName = Thread.currentThread().getName();
        client.onCallback(callback);
        client.awaitCallbackListenerIdle();
        handler.awaitProcessorIdle();

        assertThat(joinGameThreadName.get()).startsWith("bridge-processor-TestPlayer");
        assertThat(listenerThreadName.get()).startsWith("bridge-listener-TestPlayer");
        assertThat(listenerThreadName.get()).isNotEqualTo(callbackCallerThreadName);
        assertThat(joinGameThreadName.get()).isNotEqualTo(listenerThreadName.get());
        assertThat(getField(handler, "currentGameId")).isEqualTo(gameId);
        assertThat(getField(handler, "currentPlayerId")).isEqualTo(playerId);
    }

    @Test
    void clientOnCallbackSerializesConcurrentIngressOnSingleListenerThread() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        Set<String> listenerThreads = Collections.synchronizedSet(new LinkedHashSet<>());

        ClientCallback first = new ClientCallback(
            ClientCallbackMethod.GAME_SELECT,
            gameId,
            new GameClientMessage((GameView) null, Collections.<String, Serializable>emptyMap(), "Pass"),
            false
        ) {
            @Override
            public void decompressData() {
                listenerThreads.add(Thread.currentThread().getName());
                super.decompressData();
            }
        };
        ClientCallback second = new ClientCallback(
            ClientCallbackMethod.GAME_SELECT,
            gameId,
            new GameClientMessage((GameView) null, Collections.<String, Serializable>emptyMap(), "Pass"),
            false
        ) {
            @Override
            public void decompressData() {
                listenerThreads.add(Thread.currentThread().getName());
                super.decompressData();
            }
        };

        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<?> firstFuture = executor.submit(() -> client.onCallback(first));
            Future<?> secondFuture = executor.submit(() -> client.onCallback(second));

            firstFuture.get(1, TimeUnit.SECONDS);
            secondFuture.get(1, TimeUnit.SECONDS);
            client.awaitCallbackListenerIdle();
            handler.awaitProcessorIdle();
        } finally {
            executor.shutdownNow();
        }

        assertThat(listenerThreads).containsExactly("bridge-listener-TestPlayer");
    }

    @Test
    void awaitCallbackListenerIdleAfterShutdownWaitsForInFlightCallback() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        CountDownLatch callbackStarted = new CountDownLatch(1);
        CountDownLatch releaseCallback = new CountDownLatch(1);

        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_SELECT,
            UUID.randomUUID(),
            new GameClientMessage((GameView) null, Collections.<String, Serializable>emptyMap(), "Pass"),
            false
        ) {
            @Override
            public void decompressData() {
                callbackStarted.countDown();
                try {
                    assertThat(releaseCallback.await(1, TimeUnit.SECONDS)).isTrue();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new AssertionError(e);
                }
                super.decompressData();
            }
        };

        client.onCallback(callback);
        assertThat(callbackStarted.await(1, TimeUnit.SECONDS)).isTrue();
        client.stop();

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<?> future = executor.submit(() -> {
                client.awaitCallbackListenerIdle();
                return null;
            });

            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            releaseCallback.countDown();
            future.get(1, TimeUnit.SECONDS);
        } finally {
            releaseCallback.countDown();
            executor.shutdownNow();
        }
    }

    @Test
    void joinNextTableWaitsForStartGameOnProcessorFlow() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        gameState.setKeepAliveAfterGame(true);

        UUID gameId = UUID.randomUUID();
        UUID tableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicReference<String> joinGameThreadName = new AtomicReference<>();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "joinGame" -> {
                        joinGameThreadName.set(Thread.currentThread().getName());
                        assertThat(args[0]).isEqualTo(gameId);
                        return true;
                    }
                    case "getGameChatId" -> {
                        return Optional.empty();
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        handler.setJoinHandler((deckPath, requestedTableId) -> {
            assertThat(requestedTableId).isEqualTo(tableId);
            Thread callbackThread = new Thread(() -> {
                try {
                    Thread.sleep(50);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
                client.getCallbackHandler().handleCallback(new ClientCallback(
                    ClientCallbackMethod.START_GAME,
                    gameId,
                    new TableClientMessage().withTable(tableId, null).withPlayer(playerId),
                    false
                ));
            }, "join-table-start-game-test");
            callbackThread.setDaemon(true);
            callbackThread.start();
            return tableId;
        });

        try {
            handler.joinNextTable(joinTableDeckPath(), tableId);

            BridgeCallbackHandler fresh = client.getCallbackHandler();
            fresh.awaitProcessorIdle();
            assertThat(fresh).isNotSameAs(handler);
            assertThat(joinGameThreadName.get()).startsWith("bridge-processor-TestPlayer");
            assertThat(getField(fresh, "currentGameId")).isEqualTo(gameId);
            assertThat(getField(fresh, "currentPlayerId")).isEqualTo(playerId);
        } finally {
            client.stop();
        }
    }

    @Test
    void joinNextTableIgnoresWrongStartGameTableWhileWaiting() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        gameState.setKeepAliveAfterGame(true);

        UUID wrongGameId = UUID.randomUUID();
        UUID rightGameId = UUID.randomUUID();
        UUID wrongTableId = UUID.randomUUID();
        UUID rightTableId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicInteger joinGameCalls = new AtomicInteger();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                switch (method.getName()) {
                    case "joinGame" -> {
                        joinGameCalls.incrementAndGet();
                        assertThat(args[0]).isEqualTo(rightGameId);
                        return true;
                    }
                    case "getGameChatId" -> {
                        return Optional.empty();
                    }
                    default -> {
                        return defaultReturnValue(method.getReturnType());
                    }
                }
            }
        ));

        handler.setJoinHandler((deckPath, requestedTableId) -> {
            assertThat(requestedTableId).isEqualTo(rightTableId);
            Thread callbackThread = new Thread(() -> {
                client.getCallbackHandler().handleCallback(new ClientCallback(
                    ClientCallbackMethod.START_GAME,
                    wrongGameId,
                    new TableClientMessage().withTable(wrongTableId, null).withPlayer(playerId),
                    false
                ));
                client.getCallbackHandler().handleCallback(new ClientCallback(
                    ClientCallbackMethod.START_GAME,
                    rightGameId,
                    new TableClientMessage().withTable(rightTableId, null).withPlayer(playerId),
                    false
                ));
            }, "join-table-wrong-table-test");
            callbackThread.setDaemon(true);
            callbackThread.start();
            return rightTableId;
        });

        try {
            handler.joinNextTable(joinTableDeckPath(), rightTableId);

            BridgeCallbackHandler fresh = client.getCallbackHandler();
            fresh.awaitProcessorIdle();
            assertThat(joinGameCalls.get()).isEqualTo(1);
            assertThat(getField(fresh, "currentGameId")).isEqualTo(rightGameId);
            assertThat(getField(fresh, "currentPlayerId")).isEqualTo(playerId);
        } finally {
            client.stop();
        }
    }

    @Test
    void joinNextTableReturnsWhenProcessorStopsBeforeStartGame() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        gameState.setKeepAliveAfterGame(true);

        UUID tableId = UUID.randomUUID();
        CountDownLatch joinReturned = new CountDownLatch(1);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("getGameChatId".equals(method.getName())) {
                    return Optional.empty();
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        handler.setJoinHandler((deckPath, requestedTableId) -> {
            assertThat(requestedTableId).isEqualTo(tableId);
            joinReturned.countDown();
            return tableId;
        });

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<?> future = executor.submit(() -> {
                try {
                    handler.joinNextTable(joinTableDeckPath(), tableId);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            });

            assertThat(joinReturned.await(1, TimeUnit.SECONDS)).isTrue();
            BridgeCallbackHandler fresh = client.getCallbackHandler();
            fresh.awaitProcessorIdle();
            client.stop();

            assertThatThrownBy(() -> future.get(1, TimeUnit.SECONDS))
                .isInstanceOf(ExecutionException.class)
                .hasCauseInstanceOf(AssertionError.class)
                .hasMessageContaining("Game did not start within 60s after joining table");
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void queuedStartGameFlowFailsAfterShutdownInsteadOfCreatingOrphanedWaiter() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        BridgeCallbackHandler fresh = handler.createFreshForNextGame();
        BridgeProcessor processor = (BridgeProcessor) getDirectField(fresh, "processor");
        UUID tableId = UUID.randomUUID();

        CountDownLatch blockerEntered = new CountDownLatch(1);
        CountDownLatch releaseBlocker = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(3);
        try {
            Future<?> blockerFuture = executor.submit(() -> processor.submit(new BridgeCommand<Void>() {
                @Override
                public Void execute() {
                    blockerEntered.countDown();
                    try {
                        assertThat(releaseBlocker.await(1, TimeUnit.SECONDS)).isTrue();
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new IllegalStateException("Interrupted while blocking processor", e);
                    }
                    return null;
                }
            }));

            assertThat(blockerEntered.await(1, TimeUnit.SECONDS)).isTrue();

            Future<?> shutdownFuture = executor.submit(() -> {
                fresh.shutdownProcessor("test shutdown");
                return null;
            });
            Thread.sleep(50);
            assertThat(shutdownFuture.isDone()).isFalse();

            Future<?> startFuture = executor.submit(() -> invokeStartPendingStartGameFlow(fresh, tableId));

            releaseBlocker.countDown();

            ExecutionException failure = null;
            try {
                startFuture.get(1, TimeUnit.SECONDS);
            } catch (ExecutionException e) {
                failure = e;
            }
            assertThat(failure).isNotNull();
            assertThat(failure).hasCauseInstanceOf(IllegalStateException.class);
            assertThat(failure.getCause().getMessage()).isIn(
                "START_GAME flow manager is shut down",
                "Bridge processor is shut down"
            );

            blockerFuture.get(1, TimeUnit.SECONDS);
            shutdownFuture.get(1, TimeUnit.SECONDS);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void executeDefaultActionRunsOnProcessorThread() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        AtomicReference<String> sendThreadName = new AtomicReference<>();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendThreadName.set(Thread.currentThread().getName());
                    assertThat(args[0]).isEqualTo(gameId);
                    assertThat(args[1]).isEqualTo(false);
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        GameView view = gameView(7);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(view, Collections.<String, Serializable>emptyMap(), "Play spells and abilities"),
            "Play spells and abilities",
            7
        ));

        String callerThreadName = Thread.currentThread().getName();
        Map<String, Object> result = handler.executeDefaultAction();

        assertThat(result).containsEntry("success", true);
        assertThat(result).containsEntry("action_type", "GAME_SELECT");
        assertThat(result).containsEntry("action_taken", "passed_priority");
        assertThat(sendThreadName.get()).startsWith("bridge-processor-TestPlayer");
        assertThat(sendThreadName.get()).isNotEqualTo(callerThreadName);
    }

    @Test
    void passPriorityDoesNotMonopolizeProcessorThreadWhileWaiting() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch autoPassSent = new CountDownLatch(1);
        GameView initialView = gameView(90);
        GameView nextDecisionView = gameView(91);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    autoPassSent.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            90
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority(null, null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            handler.awaitProcessorIdle();

            enqueueCallback(
                handler,
                ClientCallbackMethod.GAME_ASK,
                gameId,
                new GameClientMessage(nextDecisionView, Collections.<String, Serializable>emptyMap(), "Mulligan hand?")
            );

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("non_priority_action");
            assertThat(result.action_pending).isTrue();
            assertThat(result.action_type).isEqualTo("GAME_ASK");
            assertThat(result.game_seq).isEqualTo(91);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void passPriorityReturnsAfterClientStopWithoutFollowupCallback() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(92);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    autoPassSent.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            92
        ));

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ActionResult> future = executor.submit(() -> handler.passPriority(null, null));

            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> future.get(200, TimeUnit.MILLISECONDS))
                .isInstanceOf(TimeoutException.class);

            client.stop();

            ActionResult result = future.get(1, TimeUnit.SECONDS);
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(result.stop_reason).isEqualTo("game_over");
            assertThat(result.action_pending).isFalse();
            assertThat(result.game_seq).isEqualTo(92);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
    }

    @Test
    void queryApisFailFastAfterProcessorShutdown() {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        DeckCardLists deck = new DeckCardLists();
        deck.setCards(List.of(new DeckCardInfo("Lightning Bolt", "150", "lea", 4)));
        handler.setDeckList(deck);

        handler.shutdownProcessor("test shutdown");

        assertThatThrownBy(handler::getMyDecklist)
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("Bridge processor is shut down");
        assertThatThrownBy(() -> handler.getOracleText("Lightning Bolt", null, null, null))
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("Bridge processor is shut down");
    }

    @Test
    void passPriorityReturnsCancelledResultWhenCallerThreadIsInterrupted() throws Exception {
        BridgeMageClient client = new BridgeMageClient("TestPlayer");
        BridgeCallbackHandler handler = client.getCallbackHandler();

        UUID gameId = UUID.randomUUID();
        CountDownLatch autoPassSent = new CountDownLatch(1);
        AtomicInteger sendPlayerBooleanCalls = new AtomicInteger();
        GameView initialView = gameView(93);

        client.setSession((Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerBoolean".equals(method.getName())) {
                    sendPlayerBooleanCalls.incrementAndGet();
                    autoPassSent.countDown();
                    return true;
                }
                return defaultReturnValue(method.getReturnType());
            }
        ));

        addActiveGame(handler, gameId);
        setField(handler, "currentGameId", gameId);
        setField(handler, "lastGameView", initialView);
        setField(handler, "pendingAction", new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(initialView, Collections.<String, Serializable>emptyMap(), "Pass"),
            "Pass",
            93
        ));

        AtomicReference<ActionResult> resultRef = new AtomicReference<>();
        AtomicReference<Throwable> errorRef = new AtomicReference<>();
        AtomicBoolean interruptFlagAfterReturn = new AtomicBoolean(false);
        CountDownLatch done = new CountDownLatch(1);

        Thread worker = new Thread(() -> {
            try {
                resultRef.set(handler.passPriority(null, null));
                interruptFlagAfterReturn.set(Thread.currentThread().isInterrupted());
            } catch (Throwable t) {
                errorRef.set(t);
            } finally {
                done.countDown();
            }
        }, "pass-priority-interrupt-test");

        worker.start();
        try {
            assertThat(autoPassSent.await(1, TimeUnit.SECONDS)).isTrue();

            worker.interrupt();

            assertThat(done.await(1, TimeUnit.SECONDS)).isTrue();
            assertThat(errorRef.get()).isNull();
            assertThat(sendPlayerBooleanCalls.get()).isEqualTo(1);
            assertThat(resultRef.get()).isNotNull();
            assertThat(resultRef.get().stop_reason).isEqualTo("cancelled");
            assertThat(resultRef.get().action_pending).isFalse();
            assertThat(interruptFlagAfterReturn.get()).isTrue();
        } finally {
            worker.join(1000);
        }
    }

    @Test
    void passPriorityTickerFailsFlowWhenTickCommandThrowsIllegalStateException() throws Exception {
        AtomicBoolean failOnPendingRead = new AtomicBoolean(false);
        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgePassPriorityFlowManager.class),
            event -> { }
        );
        BridgeDecisionState decisionState = new BridgeDecisionState();
        BridgePassPriorityFlowContext context = new BridgePassPriorityFlowContext() {
            @Override
            public String username() {
                return "TestPlayer";
            }

            @Override
            public PendingAction currentPendingAction() {
                if (failOnPendingRead.get()) {
                    throw new IllegalStateException("pass_priority tick failed");
                }
                return null;
            }

            @Override
            public PendingAction currentDecisionAction() {
                return null;
            }

            @Override
            public PendingAction resolvePassPriorityAction(PendingAction action) {
                return action;
            }

            @Override
            public GameView preparePassPriorityActionView(PendingAction action) {
                return null;
            }

            @Override
            public int interactionsThisTurn() {
                return 0;
            }

            @Override
            public int maxInteractionsPerTurn() {
                return 10;
            }

            @Override
            public void executeDefaultAction() {
                throw new AssertionError("executeDefaultAction should not run");
            }

            @Override
            public String detectCombatSelect(PendingAction action) {
                throw new AssertionError("detectCombatSelect should not run");
            }

            @Override
            public ActionResult pendingActionResult(PendingAction action, String stopReason, Long boardCursorParam) {
                throw new AssertionError("pendingActionResult should not run");
            }

            @Override
            public ActionResult pendingActionResult(
                    PendingAction action,
                    String stopReason,
                    Long boardCursorParam,
                    java.util.function.Consumer<ActionResult> customizer) {
                throw new AssertionError("pendingActionResult should not run");
            }

            @Override
            public ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam) {
                throw new AssertionError("stepYieldResult should not run");
            }

            @Override
            public ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
                throw new AssertionError("stackResolvedResult should not run");
            }

            @Override
            public UUID lowestStackObjectId(GameView gameView) {
                return null;
            }

            @Override
            public boolean stackContains(GameView gameView, UUID stackObjectId) {
                return false;
            }

            @Override
            public boolean clearPendingActionIfCurrent(PendingAction action) {
                throw new AssertionError("clearPendingActionIfCurrent should not run");
            }

            @Override
            public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
                throw new AssertionError("sendBooleanOrDie should not run");
            }

            @Override
            public UUID currentGameId() {
                return UUID.randomUUID();
            }

            @Override
            public GameView lastGameView() {
                return null;
            }

            @Override
            public int lastTurnNumber() {
                return 0;
            }

            @Override
            public boolean hasActiveGame() {
                return true;
            }

            @Override
            public boolean superseded() {
                return false;
            }

            @Override
            public boolean playerDead() {
                return false;
            }

            @Override
            public boolean gameEverStarted() {
                return false;
            }

            @Override
            public boolean clientRunning() {
                return true;
            }

            @Override
            public long lastActionableCallbackAt() {
                return 0;
            }

            @Override
            public long lastCallbackReceivedAt() {
                return 0;
            }

            @Override
            public void declareZombieGame(long absoluteIdleMs) {
                throw new AssertionError("declareZombieGame should not run");
            }

            @Override
            public boolean failedManaCast(UUID objectId) {
                return false;
            }

            @Override
            public void finalizePassPriorityResult(
                    BridgePassPriorityFlow flow,
                    String until,
                    int actionsPassed,
                    PendingAction action,
                    GameView view,
                    ActionResult result,
                    boolean actionPending) {
                decisionState.clearPendingPassPriorityFlowIfCurrent(flow);
            }
        };
        BridgePassPriorityFlowManager manager = new BridgePassPriorityFlowManager(
            processor,
            "TestPlayer",
            decisionState,
            context
        );
        processor.start();

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            BridgePassPriorityFlow flow = processor.submit(BridgeCommand.of(() -> manager.startPendingFlow(null, null)));

            Future<ActionResult> future = executor.submit(flow::awaitResult);
            failOnPendingRead.set(true);

            assertThatThrownBy(() -> future.get(1, TimeUnit.SECONDS))
                .isInstanceOf(ExecutionException.class)
                .satisfies(error -> {
                    Throwable outerCause = ((ExecutionException) error).getCause();
                    assertThat(outerCause).isInstanceOf(ExecutionException.class);
                    assertThat(outerCause.getCause())
                        .isInstanceOf(IllegalStateException.class)
                        .hasMessage("pass_priority tick failed");
                });
            assertThat(decisionState.pendingPassPriorityFlow()).isNull();
        } finally {
            manager.shutdown();
            processor.shutdown("test done");
            executor.shutdownNow();
            executor.awaitTermination(1, TimeUnit.SECONDS);
        }
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
    void concedeFlowManagerReturnsTrueAfterKeepAliveTimeout() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        AtomicReference<String> concedeThreadName = new AtomicReference<>();

        BridgeProcessor processor = new BridgeProcessor(
            "TestPlayer",
            Logger.getLogger(BridgeCallbackHandlerTest.class),
            ignored -> { }
        );
        BridgeProcessorState processorState = new BridgeProcessorState();
        BridgeGameState gameState = processorState.gameState();
        Session session = (Session) Proxy.newProxyInstance(
            Session.class.getClassLoader(),
            new Class<?>[]{Session.class},
            (proxy, method, args) -> {
                if ("sendPlayerAction".equals(method.getName())) {
                    concedeThreadName.set(Thread.currentThread().getName());
                }
                return defaultReturnValue(method.getReturnType());
            }
        );
        BridgeConcedeFlowManager manager = new BridgeConcedeFlowManager(
            processor,
            processorState,
            () -> session,
            Logger.getLogger(BridgeCallbackHandlerTest.class),
            "TestPlayer",
            0
        );
        processor.start();

        try {
            BridgeConcedeFlow flow = processor.submit(BridgeCommand.of(() -> {
                gameState.setKeepAliveAfterGame(true);
                gameState.activateGame(gameId, playerId);
                return manager.startPendingFlow();
            }));

            assertThat(flow.awaitResult()).isTrue();
            assertThat(concedeThreadName.get()).startsWith("bridge-processor-TestPlayer");
        } finally {
            manager.shutdown();
            processor.shutdown("test done");
        }
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

        addActiveGame(handler, gameId, playerId);

        GameView manaView = gameView(77);
        ClientCallback callback = new ClientCallback(
            ClientCallbackMethod.GAME_PLAY_MANA,
            gameId,
            new GameClientMessage(manaView, Collections.<String, Serializable>emptyMap(), "Pay {1}"),
            false
        );

        handler.handleCallback(callback);
        handler.awaitProcessorIdle();

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

    private static void addActiveGame(BridgeCallbackHandler handler, UUID gameId) throws Exception {
        addActiveGame(handler, gameId, UUID.randomUUID());
    }

    private static void addActiveGame(BridgeCallbackHandler handler, UUID gameId, UUID playerId) throws Exception {
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        gameState.activateGame(gameId, playerId);
    }

    private static void setCurrentChatId(BridgeCallbackHandler handler, UUID gameId, UUID chatId) throws Exception {
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        gameState.setCurrentChatId(gameId, chatId);
    }

    private static boolean hasActiveGame(BridgeCallbackHandler handler, UUID gameId) throws Exception {
        BridgeGameState gameState = (BridgeGameState) getProcessorStateField(handler, "gameState");
        return gameState.isCurrentActiveGame(gameId);
    }

    private static void registerShortId(BridgeCallbackHandler handler, UUID uuid, String shortId) throws Exception {
        ShortIdRegistry shortIds = (ShortIdRegistry) getField(handler, "shortIds");
        shortIds.register(uuid, shortId);
    }

    private static void enqueueCallback(
            BridgeCallbackHandler handler,
            ClientCallbackMethod method,
            UUID gameId,
            Object data) {
        handler.handleCallback(new ClientCallback(method, gameId, data, false));
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

    private static PlayableObjectStats playStats(String... playAbilities) throws Exception {
        PlayableObjectStats stats = new PlayableObjectStats();
        List<Object> records = new java.util.ArrayList<>();
        for (int i = 0; i < playAbilities.length; i++) {
            records.add(playableObjectRecord(UUID.randomUUID(), playAbilities[i]));
        }
        setField(stats, "basicPlayAbilities", records);
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
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");
        BridgeGameLogState gameLogState = (BridgeGameLogState) getProcessorStateField(handler, "gameLogState");
        BridgePublishedQueryState publishedQueryState = (BridgePublishedQueryState) getDirectField(handler, "publishedQueryState");
        processor.submit(BridgeCommand.of(() -> {
            gameLogState.recordFetchedBridgeEvents(events);
            publishedQueryState.publishProcessorState();
            return null;
        }));
    }

    private static void publishProcessorState(BridgeCallbackHandler handler) throws Exception {
        BridgeProcessor processor = (BridgeProcessor) getDirectField(handler, "processor");
        BridgePublishedQueryState publishedQueryState = (BridgePublishedQueryState) getDirectField(handler, "publishedQueryState");
        processor.submit(BridgeCommand.of(() -> {
            publishedQueryState.publishProcessorState();
            return null;
        }));
    }

    private static Long publishedGameStateSnapshotId(BridgeCallbackHandler handler) throws Exception {
        Object publishedQueryState = getDirectField(handler, "publishedQueryState");
        Object snapshot = invokeNoArg(publishedQueryState, "snapshot");
        Object gameState = invokeNoArg(snapshot, "gameState");
        return (Long) invokeNoArg(gameState, "snapshotId");
    }

    private static void waitForCondition(BooleanSupplier condition) throws Exception {
        long deadline = System.currentTimeMillis() + 1_000;
        while (System.currentTimeMillis() < deadline) {
            if (condition.getAsBoolean()) {
                return;
            }
            Thread.sleep(10);
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }

    private static String joinTableDeckPath() {
        return Path.of("..", "tests", "decks", "filler_opponent.dck")
            .toAbsolutePath()
            .normalize()
            .toString();
    }

    private static Object invokeStartPendingStartGameFlow(BridgeCallbackHandler handler, UUID expectedTableId) {
        try {
            Method method = BridgeCallbackHandler.class.getDeclaredMethod("startPendingStartGameFlow", UUID.class);
            method.setAccessible(true);
            return method.invoke(handler, expectedTableId);
        } catch (ReflectiveOperationException e) {
            Throwable cause = e instanceof java.lang.reflect.InvocationTargetException invocationTargetException
                ? invocationTargetException.getCause()
                : e;
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("Failed to invoke startPendingStartGameFlow", cause);
        }
    }

    private static Object getField(Object target, String name) throws Exception {
        Object owner = resolveFieldOwner(target, name);
        Field field = findField(owner.getClass(), name);
        field.setAccessible(true);
        return field.get(owner);
    }

    private static void setField(Object target, String name, Object value) throws Exception {
        Object owner = resolveFieldOwner(target, name);
        Field field = findField(owner.getClass(), name);
        field.setAccessible(true);
        field.set(owner, value);
    }

    private static void setIntField(Object target, String name, int value) throws Exception {
        Object owner = resolveFieldOwner(target, name);
        Field field = findField(owner.getClass(), name);
        field.setAccessible(true);
        field.setInt(owner, value);
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

    private static Object resolveFieldOwner(Object target, String name) throws Exception {
        try {
            findField(target.getClass(), name);
            return target;
        } catch (NoSuchFieldException ignored) {
            if (!(target instanceof BridgeCallbackHandler handler)) {
                throw ignored;
            }
            Object processorState = getDirectField(handler, "processorState");
            try {
                findField(processorState.getClass(), name);
                return processorState;
            } catch (NoSuchFieldException ignoredProcessorState) {
                // Keep searching through the extracted state holders below.
            }
            Object decisionState = getDirectField(processorState, "decisionState");
            try {
                findField(decisionState.getClass(), name);
                return decisionState;
            } catch (NoSuchFieldException ignoredDecisionState) {
                for (String ownerField : List.of("gameState", "interactionState", "gameLogState", "cursorState")) {
                    Object owner = getDirectField(processorState, ownerField);
                    try {
                        findField(owner.getClass(), name);
                        return owner;
                    } catch (NoSuchFieldException ignoredOwner) {
                        // Keep searching the remaining extracted state holders.
                    }
                }
                throw ignoredDecisionState;
            }
        }
    }

    private static Object getProcessorStateField(BridgeCallbackHandler handler, String name) throws Exception {
        Object processorState = getDirectField(handler, "processorState");
        return getDirectField(processorState, name);
    }

    private static Object getDirectField(Object target, String name) throws Exception {
        Field field = findField(target.getClass(), name);
        field.setAccessible(true);
        return field.get(target);
    }

    private static Object invokeNoArg(Object target, String name) throws Exception {
        Method method = target.getClass().getDeclaredMethod(name);
        method.setAccessible(true);
        return method.invoke(target);
    }
}

package mage.client.bridge;

import mage.constants.CardType;
import mage.constants.ManaType;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.util.ShortIdRegistry;
import mage.view.AbilityPickerView;
import mage.view.CardsView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import org.junit.jupiter.api.Test;
import sun.misc.Unsafe;

import java.io.Serializable;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BridgeManaHandlerTest {

    @Test
    void storeManaPlanRejectsUnknownPermanentIds() {
        Harness harness = harness(null, UUID.randomUUID());

        assertThatThrownBy(() -> harness.handler().storeManaPlan(new String[]{"p1"}, true))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Mana plan references unknown permanent 'p1'. Check the board state for correct permanent IDs.");
    }

    @Test
    void buildManualChoiceSetSortsSourcesAndPoolChoicesDeterministically() throws Exception {
        UUID playerId = UUID.randomUUID();
        UUID islandId = UUID.randomUUID();
        UUID forestId = UUID.randomUUID();

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        setField(player, "manaPool", manaPoolView(1, 0, 1, 0, 0, 0));
        @SuppressWarnings("unchecked")
        Map<UUID, Object> battlefield = (Map<UUID, Object>) getField(player, "battlefield");
        battlefield.put(islandId, permanentView(islandId, "p2", "Island"));
        battlefield.put(forestId, permanentView(forestId, "p1", "Forest"));

        GameView gameView = gameView(17, List.of(player));
        setField(gameView, "myPlayerId", playerId);
        setField(gameView, "canPlayObjects", playableObjects(orderedMap(
            islandId, manaStats("{T}: Add {U}."),
            forestId, manaStats("{T}: Add {G}.")
        )));

        Harness harness = harness(gameView, playerId);

        BridgeManaHandler.ManualChoiceSet result = harness.handler().buildManualChoiceSet(gameView, "Pay 1 mana");

        assertThat(result.choices())
            .extracting(choice -> choice.get("name"))
            .containsExactly("Forest", "Island", "Blue", "Red");
        assertThat(result.choices())
            .extracting(choice -> choice.get("choice_type"))
            .containsExactly("tap_source", "tap_source", "pool_mana", "pool_mana");
    }

    @Test
    void autoHandleGamePlayManaCancelsWhenPlanExhaustsWithoutAutoTap() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID spellId = UUID.randomUUID();

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        setField(player, "manaPool", manaPoolView(1, 0, 0, 0, 0, 0));

        GameView gameView = gameView(21, List.of(player));
        setField(gameView, "myPlayerId", playerId);

        Harness harness = harness(gameView, playerId);
        assertThat(harness.handler().storeManaPlan(new String[]{"RED"}, false)).isEqualTo(1);

        GameClientMessage message = new GameClientMessage(
            gameView,
            Collections.<String, Serializable>emptyMap(),
            "Pay {R} object_id='" + spellId + "'"
        );

        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        assertThat(harness.responseSink().manaTypes()).containsExactly(ManaType.RED);
        assertThat(harness.responseSink().booleans()).isEmpty();

        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        assertThat(harness.responseSink().booleans()).containsExactly(false);
        assertThat(harness.systemChat())
            .containsExactly("[System] Spell cancelled — mana plan was incorrect or incomplete.");
        assertThat(harness.bridgeEvents())
            .containsExactly("SPELL_CANCELLED:mana plan was incorrect or incomplete");
    }

    @Test
    void autoHandleGamePlayManaFallsBackToAutoTapWhenPlanExhausts() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID spellId = UUID.randomUUID();
        UUID forestId = UUID.randomUUID();

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        setField(player, "manaPool", manaPoolView(1, 0, 0, 0, 0, 0));
        @SuppressWarnings("unchecked")
        Map<UUID, Object> battlefield = (Map<UUID, Object>) getField(player, "battlefield");
        battlefield.put(forestId, permanentView(forestId, "p1", "Forest"));

        GameView gameView = gameView(22, List.of(player));
        setField(gameView, "myPlayerId", playerId);
        setField(gameView, "canPlayObjects", playableObjects(Map.of(
            forestId, manaStats("{T}: Add {G}.")
        )));

        Harness harness = harness(gameView, playerId);
        assertThat(harness.handler().storeManaPlan(new String[]{"RED"}, true)).isEqualTo(1);

        GameClientMessage message = new GameClientMessage(
            gameView,
            Collections.<String, Serializable>emptyMap(),
            "Pay {R} object_id='" + spellId + "'"
        );

        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        assertThat(harness.responseSink().manaTypes()).containsExactly(ManaType.RED);

        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        assertThat(harness.responseSink().uuids()).containsExactly(forestId);
        assertThat(harness.responseSink().booleans()).isEmpty();
    }

    @Test
    void autoHandleChooseAbilityCancelsBadManaPlanAbilityIndex() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID landId = UUID.randomUUID();
        UUID abilityId = UUID.randomUUID();
        UUID spellId = UUID.randomUUID();

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        @SuppressWarnings("unchecked")
        Map<UUID, Object> battlefield = (Map<UUID, Object>) getField(player, "battlefield");
        battlefield.put(landId, permanentView(landId, "p1", "Forest"));

        GameView gameView = gameView(23, List.of(player));
        setField(gameView, "myPlayerId", playerId);
        setField(gameView, "canPlayObjects", playableObjects(Map.of(
            landId, manaStats("{T}: Add {G}.")
        )));

        Harness harness = harness(gameView, playerId);
        harness.shortIds().register(landId, "p1");
        harness.handler().storeManaPlan(new String[]{"p1:2"}, true);

        GameClientMessage message = new GameClientMessage(
            gameView,
            Collections.<String, Serializable>emptyMap(),
            "Pay {G} object_id='" + spellId + "'"
        );
        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();

        Map<UUID, String> choices = new LinkedHashMap<>();
        choices.put(abilityId, "1. {T}: Add {G}.");
        AbilityPickerView picker = new AbilityPickerView(gameView, choices, "Choose mana ability");

        assertThat(harness.handler().autoHandleChooseAbility(gameId, picker, "test")).isTrue();
        assertThat(harness.responseSink().uuids()).containsExactly(landId, null);
        assertThat(harness.systemChat())
            .containsExactly("[System] Spell cancelled — mana plan ability index was incorrect.");
        assertThat(harness.bridgeEvents())
            .containsExactly("SPELL_CANCELLED:mana plan ability index out of range");
    }

    @Test
    void autoHandleGamePlayManaCancelsOnPoolLoop() throws Exception {
        UUID gameId = UUID.randomUUID();
        UUID playerId = UUID.randomUUID();
        UUID spellId = UUID.randomUUID();

        PlayerView player = playerView(playerId, "TestPlayer", "p99");
        setField(player, "manaPool", manaPoolView(1, 0, 0, 0, 0, 0));

        GameView gameView = gameView(24, List.of(player));
        setField(gameView, "myPlayerId", playerId);
        setField(gameView, "canPlayObjects", new PlayableObjectsList());

        Harness harness = harness(gameView, playerId);
        GameClientMessage message = new GameClientMessage(
            gameView,
            Collections.<String, Serializable>emptyMap(),
            "Pay {R} object_id='" + spellId + "'"
        );

        for (int i = 0; i < 10; i++) {
            assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        }
        assertThat(harness.responseSink().manaTypes()).hasSize(10);
        assertThat(harness.responseSink().booleans()).isEmpty();

        assertThat(harness.handler().autoHandleGamePlayMana(gameId, message)).isTrue();
        assertThat(harness.responseSink().booleans()).containsExactly(false);
        assertThat(harness.systemChat())
            .containsExactly("[System] Spell cancelled — not enough mana to complete payment.");
        assertThat(harness.bridgeEvents())
            .containsExactly("SPELL_CANCELLED:not enough mana to complete payment");
    }

    private static Harness harness(GameView initialGameView, UUID playerId) {
        AtomicReference<GameView> lastGameView = new AtomicReference<>(initialGameView);
        ShortIdRegistry shortIds = new ShortIdRegistry("l");
        BridgeViewLocator viewLocator = new BridgeViewLocator(shortIds, lastGameView::get, ignored -> {
        });
        BridgeCardFormatter cardFormatter = new BridgeCardFormatter(viewLocator, () -> UUID.randomUUID(), ignored -> playerId);
        var systemChat = new ArrayList<String>();
        var bridgeEvents = new ArrayList<String>();
        var responseSink = new RecordingResponseSink();

        BridgeManaHandler handler = new BridgeManaHandler(
            "TestPlayer",
            shortIds,
            viewLocator,
            cardFormatter,
            ignored -> playerId,
            systemChat::add,
            (method, summary) -> bridgeEvents.add(method + ":" + summary),
            responseSink
        );

        return new Harness(handler, shortIds, responseSink, systemChat, bridgeEvents);
    }

    private record Harness(
        BridgeManaHandler handler,
        ShortIdRegistry shortIds,
        RecordingResponseSink responseSink,
        List<String> systemChat,
        List<String> bridgeEvents
    ) {
    }

    private static final class RecordingResponseSink implements BridgeManaHandler.ResponseSink {
        private final List<Boolean> booleans = new ArrayList<>();
        private final List<UUID> uuids = new ArrayList<>();
        private final List<ManaType> manaTypes = new ArrayList<>();

        @Override
        public void sendBooleanOrDie(UUID gameId, boolean data, String context) {
            booleans.add(data);
        }

        @Override
        public void sendUuidOrDie(UUID gameId, UUID data, String context) {
            uuids.add(data);
        }

        @Override
        public void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType data, String context) {
            manaTypes.add(data);
        }

        private List<Boolean> booleans() {
            return booleans;
        }

        private List<UUID> uuids() {
            return uuids;
        }

        private List<ManaType> manaTypes() {
            return manaTypes;
        }
    }

    @SafeVarargs
    private static <K, V> Map<K, V> orderedMap(Object... keyValues) {
        var map = new LinkedHashMap<K, V>();
        for (int i = 0; i < keyValues.length; i += 2) {
            @SuppressWarnings("unchecked")
            K key = (K) keyValues[i];
            @SuppressWarnings("unchecked")
            V value = (V) keyValues[i + 1];
            map.put(key, value);
        }
        return map;
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

    private static GameView gameView(int gameSeq, List<PlayerView> players) throws Exception {
        GameView view = (GameView) UNSAFE.allocateInstance(GameView.class);
        setField(view, "players", players);
        setField(view, "myHand", new CardsView());
        setField(view, "stack", new CardsView());
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

    private static PermanentView permanentView(UUID id, String shortId, String name) throws Exception {
        PermanentView view = (PermanentView) UNSAFE.allocateInstance(PermanentView.class);
        setField(view, "id", id);
        setField(view, "shortId", shortId);
        setField(view, "name", name);
        setField(view, "displayName", name);
        setField(view, "rules", List.of());
        setField(view, "cardTypes", List.of(CardType.LAND));
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
        List<Object> records = new ArrayList<>();
        for (String manaAbility : manaAbilities) {
            records.add(playableObjectRecord(UUID.randomUUID(), manaAbility));
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

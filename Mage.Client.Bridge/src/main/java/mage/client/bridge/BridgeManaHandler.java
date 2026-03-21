package mage.client.bridge;

import mage.constants.ManaType;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.util.ShortIdRegistry;
import mage.view.AbilityPickerView;
import mage.view.CardView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PlayerView;
import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.BiConsumer;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.regex.Pattern;

final class BridgeManaHandler {

    interface ResponseSink {
        void sendBooleanOrDie(UUID gameId, boolean data, String context);

        void sendUuidOrDie(UUID gameId, UUID data, String context);

        void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType data, String context);
    }

    record ManualChoiceSet(List<Map<String, Object>> choices, List<Object> indexToChoice) {
    }

    private record ManaPlanEntry(String type, String value, Integer abilityIndex) {
        ManaPlanEntry(String type, String value) {
            this(type, value, null);
        }
    }

    private static final Logger logger = Logger.getLogger(BridgeManaHandler.class);

    // Same regex approach as ManaUtil.java. Match explicit colored symbols,
    // including hybrid/phyrexian variants, when we need deterministic pool choice ordering.
    private static final Pattern REGEX_WHITE = Pattern.compile("\\x7b.{0,2}W.{0,2}\\x7d");
    private static final Pattern REGEX_BLUE = Pattern.compile("\\x7b.{0,2}U.{0,2}\\x7d");
    private static final Pattern REGEX_BLACK = Pattern.compile("\\x7b.{0,2}B.{0,2}\\x7d");
    private static final Pattern REGEX_RED = Pattern.compile("\\x7b.{0,2}R.{0,2}\\x7d");
    private static final Pattern REGEX_GREEN = Pattern.compile("\\x7b.{0,2}G.{0,2}\\x7d");
    private static final Pattern REGEX_COLORLESS = Pattern.compile("\\x7b.{0,2}C.{0,2}\\x7d");
    private static final int MAX_POOL_MANA_ATTEMPTS = 10;

    private final String username;
    private final ShortIdRegistry shortIds;
    private final BridgeViewLocator viewLocator;
    private final BridgeCardFormatter cardFormatter;
    private final Function<UUID, UUID> playerIdForGame;
    private final Consumer<String> systemChatSink;
    private final BiConsumer<String, String> bridgeEventLogger;
    private final ResponseSink responseSink;

    private final Set<UUID> failedManaCasts = ConcurrentHashMap.newKeySet();
    private volatile UUID poolManaPayingForId = null;
    private volatile int poolManaAttempts = 0;
    private volatile CopyOnWriteArrayList<ManaPlanEntry> manaPlan = null;
    private volatile Integer manaPlanAbilityIndex = null;
    private volatile boolean manaPlanAutoTapFallback = true;

    BridgeManaHandler(
            String username,
            ShortIdRegistry shortIds,
            BridgeViewLocator viewLocator,
            BridgeCardFormatter cardFormatter,
            Function<UUID, UUID> playerIdForGame,
            Consumer<String> systemChatSink,
            BiConsumer<String, String> bridgeEventLogger,
            ResponseSink responseSink
    ) {
        this.username = username;
        this.shortIds = shortIds;
        this.viewLocator = viewLocator;
        this.cardFormatter = cardFormatter;
        this.playerIdForGame = playerIdForGame;
        this.systemChatSink = systemChatSink;
        this.bridgeEventLogger = bridgeEventLogger;
        this.responseSink = responseSink;
    }

    void resetForTurnChange() {
        failedManaCasts.clear();
        poolManaAttempts = 0;
        poolManaPayingForId = null;
        clearStoredPlan();
    }

    boolean hasFailedManaCast(UUID objectId) {
        return failedManaCasts.contains(objectId);
    }

    boolean hasActiveManaPlan() {
        return manaPlan != null;
    }

    int storeManaPlan(String[] rawEntries, boolean autoTapFallback) {
        CopyOnWriteArrayList<ManaPlanEntry> parsedPlan = parseManaPlan(rawEntries);
        for (ManaPlanEntry entry : parsedPlan) {
            if ("tap".equals(entry.type()) && shortIds.tryResolve(entry.value()) == null) {
                throw new IllegalArgumentException(
                    "Mana plan references unknown permanent '" + entry.value()
                        + "'. Check the board state for correct permanent IDs."
                );
            }
        }
        manaPlan = parsedPlan;
        manaPlanAbilityIndex = null;
        manaPlanAutoTapFallback = autoTapFallback;
        return parsedPlan.size();
    }

    void clearForExplicitAutoTap() {
        clearStoredPlan();
        manaPlanAutoTapFallback = true;
    }

    void recordManualCancel(String promptText) {
        UUID payingForId = extractPayingForId(promptText);
        if (payingForId != null) {
            failedManaCasts.add(payingForId);
        }
        clearStoredPlan();
    }

    ManualChoiceSet buildManualChoiceSet(GameView gameView, String promptText) {
        var choiceList = new ArrayList<Map<String, Object>>();
        var indexToChoice = new ArrayList<Object>();
        UUID payingForId = extractPayingForId(promptText);
        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;

        if (playable != null) {
            var sortedManaEntries = new ArrayList<>(playable.getObjects().entrySet());
            sortedManaEntries.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(entry -> {
                CardView cardView = viewLocator.findCardViewById(entry.getKey(), gameView);
                return cardView != null ? cardFormatter.safeDisplayName(cardView) : "";
            }).thenComparingInt(entry ->
                viewLocator.getStableShortIdSequence(entry.getKey(), viewLocator.findCardViewById(entry.getKey(), gameView))
            ));

            int idx = 0;
            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedManaEntries) {
                UUID manaObjectId = entry.getKey();
                if (manaObjectId.equals(payingForId)) {
                    continue;
                }

                List<String> manaAbilities = entry.getValue().getAllManaAbilityNames();
                if (manaAbilities.isEmpty()) {
                    continue;
                }

                CardView cardView = viewLocator.findCardViewById(manaObjectId, gameView);
                String cardName = cardView != null
                    ? cardView.getDisplayName()
                    : "Unknown (" + manaObjectId.toString().substring(0, 8) + ")";

                for (String manaAbilityText : manaAbilities) {
                    var choiceEntry = new HashMap<String, Object>();
                    choiceEntry.put("index", idx);
                    choiceEntry.put("id", viewLocator.getStableShortId(manaObjectId, cardView));
                    choiceEntry.put("choice_type", manaAbilityText.contains("{T}") ? "tap_source" : "mana_source");
                    choiceEntry.put("name", cardName);
                    choiceEntry.put("ability", manaAbilityText);
                    choiceList.add(choiceEntry);
                    indexToChoice.add(manaObjectId);
                    idx++;
                }
            }
        }

        List<ManaType> poolChoices = getPoolManaChoices(gameView, promptText);
        if (!poolChoices.isEmpty()) {
            int idx = choiceList.size();
            ManaPoolView manaPool = getMyManaPoolView(gameView);
            for (ManaType manaType : poolChoices) {
                var choiceEntry = new HashMap<String, Object>();
                choiceEntry.put("index", idx);
                choiceEntry.put("choice_type", "pool_mana");
                choiceEntry.put("name", prettyManaType(manaType));
                choiceEntry.put("count", getManaPoolCount(manaPool, manaType));
                choiceList.add(choiceEntry);
                indexToChoice.add(manaType);
                idx++;
            }
        }

        return new ManualChoiceSet(choiceList, indexToChoice);
    }

    String applyManualChoice(UUID gameId, Object manaChoice, GameView gameView, int resolvedIndex) {
        if (manaChoice instanceof UUID manaUuid) {
            responseSink.sendUuidOrDie(gameId, manaUuid, "chooseAction:GAME_PLAY_MANA");
            return "tapped_mana_" + resolvedIndex;
        }
        if (manaChoice instanceof ManaType manaType) {
            UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
            if (manaPlayerId == null) {
                throw new IllegalStateException("Could not resolve player ID for mana pool selection");
            }
            responseSink.sendManaTypeOrDie(gameId, manaPlayerId, manaType, "chooseAction:GAME_PLAY_MANA_pool");
            return "used_pool_" + manaType;
        }
        throw new IllegalStateException("Unsupported mana choice type at index " + resolvedIndex);
    }

    boolean autoHandleChooseAbility(UUID gameId, AbilityPickerView picker, String source) {
        CopyOnWriteArrayList<ManaPlanEntry> plan = manaPlan;
        if (plan == null) {
            return false;
        }

        Map<UUID, String> choices = picker.getChoices();
        Integer abilityIdx = manaPlanAbilityIndex;
        manaPlanAbilityIndex = null;

        UUID selected;
        if (abilityIdx != null) {
            List<UUID> abilityIds = new ArrayList<>(choices.keySet());
            if (abilityIdx >= 0 && abilityIdx < abilityIds.size()) {
                selected = abilityIds.get(abilityIdx);
                logger.info("[" + username + "] " + source
                    + ": mana plan selecting ability " + abilityIdx + ": \""
                    + picker.getMessage() + "\" -> " + choices.get(selected));
            } else {
                logger.warn("[" + username + "] " + source
                    + ": mana plan ability index " + abilityIdx
                    + " out of range (0-" + (abilityIds.size() - 1) + ") for \""
                    + picker.getMessage() + "\", cancelling spell");
                clearStoredPlan();
                systemChatSink.accept("[System] Spell cancelled — mana plan ability index was incorrect.");
                bridgeEventLogger.accept("SPELL_CANCELLED", "mana plan ability index out of range");
                responseSink.sendUuidOrDie(gameId, null, "auto GAME_CHOOSE_ABILITY bad_mana_plan");
                return true;
            }
        } else {
            selected = choices.keySet().iterator().next();
            if (choices.size() == 1) {
                logger.info("[" + username + "] " + source
                    + ": mana plan auto-selecting sole ability: \""
                    + picker.getMessage() + "\" -> " + choices.get(selected));
            } else {
                logger.info("[" + username + "] " + source
                    + ": mana plan no ability index, picking first of "
                    + choices.size() + ": \"" + picker.getMessage()
                    + "\" -> " + choices.get(selected));
            }
        }

        responseSink.sendUuidOrDie(gameId, selected, "auto GAME_CHOOSE_ABILITY mana_plan");
        return true;
    }

    boolean autoHandleGamePlayMana(UUID gameId, GameClientMessage message) {
        GameView gameView = message.getGameView();
        String promptText = message.getMessage();
        UUID payingForId = extractPayingForId(promptText);

        CopyOnWriteArrayList<ManaPlanEntry> plan = manaPlan;
        if (plan != null && !plan.isEmpty()) {
            ManaPlanEntry entry = plan.remove(0);

            if ("tap".equals(entry.type())) {
                manaPlanAbilityIndex = entry.abilityIndex();
                UUID targetId = shortIds.tryResolve(entry.value());
                if (targetId == null) {
                    logger.warn("[" + username + "] Mana plan: unknown short ID '" + entry.value() + "', cancelling spell");
                    return cancelSpellFromBadManaPlan(gameId, payingForId);
                }

                PlayableObjectsList playableForPlan = gameView != null ? gameView.getCanPlayObjects() : null;
                if (playableForPlan != null) {
                    PlayableObjectStats stats = playableForPlan.getObjects().get(targetId);
                    if (stats != null && !targetId.equals(payingForId) && !failedManaCasts.contains(targetId)) {
                        logger.info("[" + username + "] Mana plan: \"" + promptText + "\" -> tapping " + entry.value());
                        poolManaAttempts = 0;
                        responseSink.sendUuidOrDie(gameId, targetId, "manaAuto:plan_tap");
                        return true;
                    }
                }

                logger.warn("[" + username + "] Mana plan: tap target " + entry.value() + " not available, cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            if ("pool".equals(entry.type())) {
                ManaType manaType = ManaType.valueOf(entry.value());
                UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
                if (manaPlayerId != null) {
                    logger.info("[" + username + "] Mana plan: \"" + promptText + "\" -> using pool " + manaType);
                    responseSink.sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:plan_pool");
                    return true;
                }
                logger.warn("[" + username + "] Mana plan: pool entry failed (no player ID), cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            logger.warn("[" + username + "] Mana plan: unknown entry type '" + entry.type() + "', cancelling spell");
            return cancelSpellFromBadManaPlan(gameId, payingForId);
        }

        if (plan != null) {
            if (manaPlanAutoTapFallback) {
                logger.info("[" + username + "] Mana plan: exhausted, falling through to auto-tap for remaining pips");
                clearStoredPlan();
            } else {
                logger.warn("[" + username + "] Mana plan: exhausted with pips remaining, cancelling spell (auto_tap=false)");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }
        }

        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        if (playable != null && !playable.isEmpty()) {
            var battlefieldOrder = new HashMap<UUID, Integer>();
            if (gameView != null) {
                int order = 0;
                for (PlayerView player : gameView.getPlayers()) {
                    for (UUID permanentId : player.getBattlefield().keySet()) {
                        battlefieldOrder.put(permanentId, order++);
                    }
                }
            }

            var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
            sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>>comparingInt(entry -> {
                Integer idx = battlefieldOrder.get(entry.getKey());
                return idx != null ? idx : Integer.MAX_VALUE;
            }).thenComparing(entry -> {
                CardView cardView = viewLocator.findCardViewById(entry.getKey(), gameView);
                return cardView != null ? cardFormatter.safeDisplayName(cardView) : "";
            }).thenComparingInt(entry ->
                viewLocator.getStableShortIdSequence(entry.getKey(), viewLocator.findCardViewById(entry.getKey(), gameView))
            ));

            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                UUID objectId = entry.getKey();
                if (objectId.equals(payingForId)) {
                    continue;
                }
                if (failedManaCasts.contains(objectId)) {
                    continue;
                }

                boolean hasTapManaAbility = false;
                for (String name : entry.getValue().getAllManaAbilityNames()) {
                    if (!name.contains("{T}")) {
                        continue;
                    }
                    int colonPos = name.indexOf(':');
                    if (colonPos > 0) {
                        String costPart = name.substring(0, colonPos);
                        if (costPart.matches(".*\\{[0-9WUBRGC]\\}.*")) {
                            continue;
                        }
                    }
                    hasTapManaAbility = true;
                    break;
                }

                if (hasTapManaAbility) {
                    logger.info("[" + username + "] Mana: \"" + promptText + "\" -> tapping " + objectId.toString().substring(0, 8));
                    poolManaAttempts = 0;
                    responseSink.sendUuidOrDie(gameId, objectId, "manaAuto:tap");
                    return true;
                }
            }
        }

        List<ManaType> poolChoices = getPoolManaChoices(gameView, promptText);
        if (!poolChoices.isEmpty()) {
            UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
            boolean canAutoSelectPoolType = poolChoices.size() == 1 || hasExplicitManaSymbol(promptText);
            if (manaPlayerId != null) {
                if (payingForId != null && payingForId.equals(poolManaPayingForId)) {
                    poolManaAttempts++;
                } else {
                    poolManaPayingForId = payingForId;
                    poolManaAttempts = 1;
                }

                if (poolManaAttempts > MAX_POOL_MANA_ATTEMPTS) {
                    logger.warn("[" + username + "] Mana: \"" + promptText + "\" -> pool payment not progressing after "
                        + poolManaAttempts + " attempts, cancelling spell");
                    poolManaAttempts = 0;
                    poolManaPayingForId = null;
                    return cancelSpell(
                        gameId,
                        payingForId,
                        "[System] Spell cancelled — not enough mana to complete payment.",
                        "not enough mana to complete payment",
                        "manaAuto:pool_loop_cancel"
                    );
                }

                if (!canAutoSelectPoolType) {
                    logger.info("[" + username + "] Mana: \"" + promptText + "\" -> pool has multiple options, waiting for manual choice");
                    return false;
                }

                ManaType manaType = poolChoices.get(0);
                logger.info("[" + username + "] Mana: \"" + promptText + "\" -> using pool " + manaType);
                responseSink.sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:pool");
                return true;
            }
            logger.warn("[" + username + "] Mana: couldn't resolve player ID for mana pool payment");
        }

        logger.info("[" + username + "] Mana: \"" + promptText + "\" -> no mana source available, cancelling spell");
        return cancelSpell(
            gameId,
            payingForId,
            "[System] Spell cancelled — not enough mana to complete payment.",
            "not enough mana to complete payment",
            "manaAuto:no_source_cancel"
        );
    }

    private CopyOnWriteArrayList<ManaPlanEntry> parseManaPlan(String[] entries) {
        var plan = new CopyOnWriteArrayList<ManaPlanEntry>();
        for (int i = 0; i < entries.length; i++) {
            String entry = entries[i];
            if (entry == null || entry.isBlank()) {
                throw new IllegalArgumentException("Mana plan entry " + i + " must not be empty");
            }

            if (isPoolColor(entry)) {
                plan.add(new ManaPlanEntry("pool", entry));
                continue;
            }

            int colonIdx = entry.indexOf(':');
            if (colonIdx >= 0) {
                String shortId = entry.substring(0, colonIdx);
                String rawAbilityIndex = entry.substring(colonIdx + 1);
                if (shortId.isEmpty()) {
                    throw new IllegalArgumentException("Mana plan entry '" + entry + "' is missing a permanent ID");
                }
                if (rawAbilityIndex.isEmpty()) {
                    throw new IllegalArgumentException("Mana plan entry '" + entry + "' has empty ability index");
                }
                try {
                    plan.add(new ManaPlanEntry("tap", shortId, Integer.parseInt(rawAbilityIndex)));
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("Mana plan entry '" + entry + "' has invalid ability index");
                }
            } else {
                plan.add(new ManaPlanEntry("tap", entry));
            }
        }
        return plan;
    }

    private void clearStoredPlan() {
        manaPlan = null;
        manaPlanAbilityIndex = null;
    }

    private static boolean isPoolColor(String entry) {
        try {
            ManaType.valueOf(entry);
            return true;
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    private boolean cancelSpellFromBadManaPlan(UUID gameId, UUID payingForId) {
        return cancelSpell(
            gameId,
            payingForId,
            "[System] Spell cancelled — mana plan was incorrect or incomplete.",
            "mana plan was incorrect or incomplete",
            "cancelSpellFromBadManaPlan"
        );
    }

    private boolean cancelSpell(
            UUID gameId,
            UUID payingForId,
            String systemMessage,
            String bridgeSummary,
            String sendContext
    ) {
        if (payingForId != null) {
            failedManaCasts.add(payingForId);
        }
        clearStoredPlan();
        systemChatSink.accept(systemMessage);
        bridgeEventLogger.accept("SPELL_CANCELLED", bridgeSummary);
        responseSink.sendBooleanOrDie(gameId, false, sendContext);
        return true;
    }

    private UUID getManaPoolPlayerId(UUID gameId, GameView gameView) {
        if (gameView != null) {
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getPlayerId() != null) {
                return myPlayer.getPlayerId();
            }
        }
        return playerIdForGame.apply(gameId);
    }

    private UUID extractPayingForId(String message) {
        if (message == null) {
            return null;
        }
        int idx = message.indexOf("object_id='");
        if (idx < 0) {
            return null;
        }
        int start = idx + "object_id='".length();
        int end = message.indexOf("'", start);
        if (end <= start) {
            return null;
        }
        try {
            return UUID.fromString(message.substring(start, end));
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private ManaPoolView getMyManaPoolView(GameView gameView) {
        if (gameView == null) {
            return null;
        }
        PlayerView myPlayer = gameView.getMyPlayer();
        if (myPlayer == null) {
            return null;
        }
        return myPlayer.getManaPool();
    }

    private int getManaPoolCount(ManaPoolView manaPool, ManaType manaType) {
        if (manaPool == null) {
            return 0;
        }
        return switch (manaType) {
            case WHITE -> manaPool.getWhite();
            case BLUE -> manaPool.getBlue();
            case BLACK -> manaPool.getBlack();
            case RED -> manaPool.getRed();
            case GREEN -> manaPool.getGreen();
            case COLORLESS -> manaPool.getColorless();
            case GENERIC -> 0;
        };
    }

    private String prettyManaType(ManaType manaType) {
        return switch (manaType) {
            case WHITE -> "White";
            case BLUE -> "Blue";
            case BLACK -> "Black";
            case RED -> "Red";
            case GREEN -> "Green";
            case COLORLESS -> "Colorless";
            case GENERIC -> "Generic";
        };
    }

    private void addPreferredPoolManaChoice(List<ManaType> orderedChoices, ManaPoolView manaPool, ManaType manaType) {
        if (getManaPoolCount(manaPool, manaType) > 0 && !orderedChoices.contains(manaType)) {
            orderedChoices.add(manaType);
        }
    }

    private boolean hasExplicitManaSymbol(String promptText) {
        if (promptText == null) {
            return false;
        }
        return REGEX_WHITE.matcher(promptText).find()
            || REGEX_BLUE.matcher(promptText).find()
            || REGEX_BLACK.matcher(promptText).find()
            || REGEX_RED.matcher(promptText).find()
            || REGEX_GREEN.matcher(promptText).find()
            || REGEX_COLORLESS.matcher(promptText).find();
    }

    private boolean addExplicitPoolChoices(List<ManaType> orderedChoices, ManaPoolView manaPool, String promptText) {
        if (promptText == null) {
            return false;
        }
        boolean hasExplicitSymbols = false;
        if (REGEX_WHITE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        }
        if (REGEX_BLUE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        }
        if (REGEX_BLACK.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        }
        if (REGEX_RED.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        }
        if (REGEX_GREEN.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        }
        if (REGEX_COLORLESS.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);
        }
        return hasExplicitSymbols;
    }

    private List<ManaType> getPoolManaChoices(GameView gameView, String promptText) {
        ManaPoolView manaPool = getMyManaPoolView(gameView);
        if (manaPool == null) {
            return new ArrayList<>();
        }

        var orderedChoices = new ArrayList<ManaType>();
        boolean hasExplicitSymbols = addExplicitPoolChoices(orderedChoices, manaPool, promptText);
        if (hasExplicitSymbols) {
            return orderedChoices;
        }

        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);
        return orderedChoices;
    }
}

package mage.client.bridge;

import mage.choices.Choice;
import mage.constants.ManaType;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectsList;
import mage.players.PlayableObjectStats;
import mage.util.MultiAmountMessage;
import mage.view.AbilityPickerView;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.CombatGroupView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;

import mage.client.bridge.tools.ActionResult;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.BiFunction;
import java.util.function.BooleanSupplier;
import java.util.function.Function;
import java.util.function.IntConsumer;
import java.util.function.Predicate;
import java.util.function.Supplier;

final class BridgeActionChoicesBuilder {

    record BuildResult(ActionResult result, List<Object> choiceMapping) {
    }

    @FunctionalInterface
    interface OptionalTargetAutoCanceler {
        void cancel(PendingAction action);
    }

    private record TargetChoice(UUID targetId, Map<String, Object> entry, CardView cardView) {
    }

    private final BridgeMageClient client;
    private final RoundTracker roundTracker;
    private final BridgeGameStateBuilder gameStateBuilder;
    private final BridgeCardFormatter cardFormatter;
    private final BridgeViewLocator viewLocator;
    private final Supplier<GameView> lastGameViewSupplier;
    private final Supplier<UUID> currentGameIdSupplier;
    private final Function<UUID, UUID> playerIdForGame;
    private final IntConsumer observeTurn;
    private final Predicate<UUID> failedManaCastPredicate;
    private final Function<List<Map<String, Object>>, Long> updateBoardCursor;
    private final Function<GameClientMessage, Set<UUID>> findValidTargets;
    private final BiFunction<GameView, String, List<ManaType>> getPoolManaChoices;
    private final BiFunction<ManaPoolView, ManaType, Integer> getManaPoolCount;
    private final Function<ManaType, String> prettyManaType;
    private final BooleanSupplier hasDeckList;
    private final Supplier<Set<String>> deckCreatureTypesSupplier;
    private final OptionalTargetAutoCanceler optionalTargetAutoCanceler;

    BridgeActionChoicesBuilder(
            BridgeMageClient client,
            RoundTracker roundTracker,
            BridgeGameStateBuilder gameStateBuilder,
            BridgeCardFormatter cardFormatter,
            BridgeViewLocator viewLocator,
            Supplier<GameView> lastGameViewSupplier,
            Supplier<UUID> currentGameIdSupplier,
            Function<UUID, UUID> playerIdForGame,
            IntConsumer observeTurn,
            Predicate<UUID> failedManaCastPredicate,
            Function<List<Map<String, Object>>, Long> updateBoardCursor,
            Function<GameClientMessage, Set<UUID>> findValidTargets,
            BiFunction<GameView, String, List<ManaType>> getPoolManaChoices,
            BiFunction<ManaPoolView, ManaType, Integer> getManaPoolCount,
            Function<ManaType, String> prettyManaType,
            BooleanSupplier hasDeckList,
            Supplier<Set<String>> deckCreatureTypesSupplier,
            OptionalTargetAutoCanceler optionalTargetAutoCanceler
    ) {
        this.client = client;
        this.roundTracker = roundTracker;
        this.gameStateBuilder = gameStateBuilder;
        this.cardFormatter = cardFormatter;
        this.viewLocator = viewLocator;
        this.lastGameViewSupplier = lastGameViewSupplier;
        this.currentGameIdSupplier = currentGameIdSupplier;
        this.playerIdForGame = playerIdForGame;
        this.observeTurn = observeTurn;
        this.failedManaCastPredicate = failedManaCastPredicate;
        this.updateBoardCursor = updateBoardCursor;
        this.findValidTargets = findValidTargets;
        this.getPoolManaChoices = getPoolManaChoices;
        this.getManaPoolCount = getManaPoolCount;
        this.prettyManaType = prettyManaType;
        this.hasDeckList = hasDeckList;
        this.deckCreatureTypesSupplier = deckCreatureTypesSupplier;
        this.optionalTargetAutoCanceler = optionalTargetAutoCanceler;
    }

    @SuppressWarnings("unchecked")
    BuildResult build(PendingAction action, Long boardCursorParam, boolean allowAutoResolve) {
        var result = new ActionResult();
        List<Object> choiceMapping = null;

        GameView gameView = null;
        if (action != null && action.data() instanceof GameClientMessage gcm) {
            gameView = gcm.getGameView();
        }
        if (gameView == null) {
            gameView = lastGameViewSupplier.get();
        }
        final GameView gv = gameView;
        if (action != null) {
            result.game_seq = action.gameSeq();
        }

        if (action == null) {
            result.action_pending = false;
            return new BuildResult(result, null);
        }

        result.action_pending = true;
        result.action_type = action.method().name();
        result.message = BridgePromptFormatting.stripHtml(action.message());

        if (gameView != null) {
            populateGameContext(result, gameView, boardCursorParam);
        }

        ClientCallbackMethod method = action.method();
        Object data = action.data();

        switch (method) {
            case GAME_ASK -> {
                result.response_type = "boolean";
                result.respond_with = "choice=yes or choice=no";

                String askMsg = action.message();
                if (askMsg != null && askMsg.toLowerCase().contains("mulligan") && gameView != null) {
                    CardsView hand = gameView.getMyHand();
                    if (hand != null && !hand.isEmpty()) {
                        var sortedHand = new ArrayList<>(hand.values());
                        sortedHand.sort(Comparator.comparing(cardFormatter::safeDisplayName));

                        var handCards = new ArrayList<Map<String, Object>>();
                        for (CardView card : sortedHand) {
                            handCards.add(cardFormatter.buildCardInfoMap(card));
                        }
                        result.your_hand = handCards;
                    }
                }
            }

            case GAME_SELECT -> {
                var selectChoices = buildSelectChoices((GameClientMessage) data, gameView, gv, result);
                boolean hasSelectChoices =
                    selectChoices.choiceMapping() != null && !selectChoices.choiceMapping().isEmpty();
                result.response_type = hasSelectChoices ? "select" : "boolean";
                if ("declare_attackers".equals(result.combat_phase)) {
                    result.respond_with = "attackers=p1,p2,... or choice=yes (confirm) or choice=no (skip)";
                } else if ("declare_blockers".equals(result.combat_phase)) {
                    result.respond_with = "blockers=p5:p1,p6:p2 (blocker:attacker) or choice=yes (confirm) or choice=no (skip)";
                } else if (hasSelectChoices) {
                    result.respond_with = "choice=pN to play, or choice=no to pass";
                } else {
                    result.respond_with = "choice=yes (confirm) or choice=no (pass)";
                }
                if (hasSelectChoices) {
                    result.choices = selectChoices.choices();
                    choiceMapping = selectChoices.choiceMapping();
                }
            }

            case GAME_PLAY_MANA, GAME_PLAY_XMANA -> {
                var manaChoices = buildManaChoices((GameClientMessage) data, gameView, gv);
                if (!manaChoices.choices().isEmpty()) {
                    result.response_type = "select";
                    result.respond_with = "choice=pN to tap, or choice=no to cancel";
                    result.choices = manaChoices.choices();
                    choiceMapping = manaChoices.choiceMapping();
                } else {
                    result.response_type = "boolean";
                    result.respond_with = "choice=no to cancel";
                }
            }

            case GAME_TARGET -> {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "index";
                boolean required = msg.isFlag();
                result.required = required;
                result.can_cancel = !required;
                result.respond_with = required
                    ? "choice=pN — must pick a target"
                    : "choice=pN, or choice=no to cancel";

                Set<UUID> targets = findValidTargets.apply(msg);
                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                if (targets != null) {
                    CardsView cardsView = msg.getCardsView1();
                    GameView targetGameView = msg.getGameView() != null ? msg.getGameView() : lastGameViewSupplier.get();
                    UUID gameId = currentGameIdSupplier.get();
                    UUID myPlayerId = playerIdForGame.apply(gameId);
                    var targetChoices = new ArrayList<TargetChoice>();
                    for (UUID targetId : targets) {
                        var choiceEntry = new HashMap<String, Object>();
                        CardView resolvedCv = cardFormatter.buildTargetInfo(
                            choiceEntry,
                            targetId,
                            cardsView,
                            targetGameView,
                            myPlayerId
                        );
                        targetChoices.add(new TargetChoice(targetId, choiceEntry, resolvedCv));
                    }

                    targetChoices.sort((a, b) -> {
                        boolean aIsYou = Boolean.TRUE.equals(a.entry().get("is_you"));
                        boolean bIsYou = Boolean.TRUE.equals(b.entry().get("is_you"));
                        int youCmp = Boolean.compare(bIsYou, aIsYou);
                        if (youCmp != 0) {
                            return youCmp;
                        }
                        String aName = Objects.toString(a.entry().get("name"), "");
                        String bName = Objects.toString(b.entry().get("name"), "");
                        int nameCmp = String.CASE_INSENSITIVE_ORDER.compare(aName, bName);
                        if (nameCmp != 0) {
                            return nameCmp;
                        }
                        return Integer.compare(
                            viewLocator.getStableShortIdSequence(a.targetId(), a.cardView()),
                            viewLocator.getStableShortIdSequence(b.targetId(), b.cardView())
                        );
                    });

                    int idx = 0;
                    for (TargetChoice tc : targetChoices) {
                        tc.entry().put("id", viewLocator.getStableShortId(tc.targetId(), tc.cardView()));
                        tc.entry().put("index", idx);
                        choiceList.add(tc.entry());
                        indexToUuid.add(tc.targetId());
                        idx++;
                    }
                }

                if (choiceList.isEmpty() && !required && allowAutoResolve) {
                    optionalTargetAutoCanceler.cancel(action);
                    result.action_pending = false;
                    result.action_taken = "auto_cancelled_no_targets";
                    result.message = BridgePromptFormatting.stripHtml(msg.getMessage());
                    return new BuildResult(result, null);
                }

                result.choices = choiceList;
                choiceMapping = indexToUuid;
            }

            case GAME_CHOOSE_ABILITY -> {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> choices = picker.getChoices();
                result.response_type = "index";
                result.respond_with = "choice=0, choice=1, etc. (not yes/no)";

                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                boolean allManaAbilities = choices != null && !choices.isEmpty();
                if (choices != null) {
                    int idx = 0;
                    for (Map.Entry<UUID, String> entry : choices.entrySet()) {
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        String desc = BridgeCallbackHandler.stripAbilityPickerOrdinalPrefix(
                            BridgePromptFormatting.stripHtml(entry.getValue()),
                            idx
                        );
                        choiceEntry.put("description", desc);
                        choiceList.add(choiceEntry);
                        indexToUuid.add(entry.getKey());
                        idx++;
                        if (!desc.contains("Add {")) {
                            allManaAbilities = false;
                        }
                    }
                }

                if (allManaAbilities) {
                    String msg = result.message;
                    if (msg != null && msg.startsWith("Choose spell or ability")) {
                        int colonIdx = msg.indexOf(": ");
                        String cardName = colonIdx >= 0 ? msg.substring(colonIdx + 2).trim() : "";
                        if (!cardName.isEmpty()) {
                            result.message = "Choose which mana to produce from " + cardName
                                + " (tapping to pay for a spell)";
                        }
                    }
                }

                result.choices = choiceList;
                choiceMapping = indexToUuid;
            }

            case GAME_CHOOSE_CHOICE -> {
                GameClientMessage msg = (GameClientMessage) data;
                Choice choice = msg.getChoice();
                result.response_type = "index";
                result.respond_with = "choice=0, choice=1, etc. or text=Name (not yes/no)";

                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToKey = new ArrayList<Object>();

                if (choice != null) {
                    if (choice.isKeyChoice()) {
                        Map<String, String> keyChoices = choice.getKeyChoices();
                        if (keyChoices != null) {
                            int idx = 0;
                            for (Map.Entry<String, String> entry : keyChoices.entrySet()) {
                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("description", BridgePromptFormatting.stripHtml(entry.getValue()));
                                choiceList.add(choiceEntry);
                                indexToKey.add(entry.getKey());
                                idx++;
                            }
                        }
                    } else {
                        Set<String> choices = choice.getChoices();
                        if (choices != null) {
                            int idx = 0;
                            for (String c : choices) {
                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("description", c);
                                choiceList.add(choiceEntry);
                                indexToKey.add(c);
                                idx++;
                            }
                        }
                    }
                }

                int totalChoices = choiceList.size();
                if (totalChoices >= 50 && hasDeckList.getAsBoolean()) {
                    Set<String> deckTypes = deckCreatureTypesSupplier.get();
                    if (!deckTypes.isEmpty()) {
                        var filtered = new ArrayList<Map<String, Object>>();
                        var filteredKeys = new ArrayList<Object>();
                        int idx = 0;
                        for (int i = 0; i < choiceList.size(); i++) {
                            String desc = (String) choiceList.get(i).get("description");
                            if (deckTypes.contains(desc)) {
                                var entry = new HashMap<String, Object>();
                                entry.put("index", idx);
                                entry.put("description", desc);
                                filtered.add(entry);
                                filteredKeys.add(indexToKey.get(i));
                                idx++;
                            }
                        }
                        if (!filtered.isEmpty()) {
                            choiceList = filtered;
                            indexToKey = filteredKeys;
                            result.note = "Showing " + filtered.size()
                                + " types from your deck (" + totalChoices
                                + " total available). Use choose_action(text='TypeName') for any other type.";
                        }
                    }
                }

                result.choices = choiceList;
                choiceMapping = indexToKey;
            }

            case GAME_CHOOSE_PILE -> {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "pile";
                result.respond_with = "pile=1 or pile=2";

                var pile1 = new ArrayList<Map<String, Object>>();
                var pile2 = new ArrayList<Map<String, Object>>();
                if (msg.getCardsView1() != null) {
                    for (CardView card : msg.getCardsView1().values()) {
                        pile1.add(cardFormatter.buildCardInfoMap(card));
                    }
                }
                if (msg.getCardsView2() != null) {
                    for (CardView card : msg.getCardsView2().values()) {
                        pile2.add(cardFormatter.buildCardInfoMap(card));
                    }
                }
                result.pile1 = pile1;
                result.pile2 = pile2;
            }

            case GAME_GET_AMOUNT -> {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "amount";
                result.respond_with = "amount=N (min=" + msg.getMin() + ", max=" + msg.getMax() + ")";
                result.min = msg.getMin();
                result.max = msg.getMax();
            }

            case GAME_GET_MULTI_AMOUNT -> {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "multi_amount";
                result.respond_with = "amounts=[N,N,...] — one per item, sum between total_min and total_max";
                result.total_min = msg.getMin();
                result.total_max = msg.getMax();

                var items = new ArrayList<Map<String, Object>>();
                if (msg.getMessages() != null) {
                    for (MultiAmountMessage mam : msg.getMessages()) {
                        var item = new HashMap<String, Object>();
                        item.put("description", BridgePromptFormatting.stripHtml(mam.message));
                        item.put("min", mam.min);
                        item.put("max", mam.max);
                        item.put("default", mam.defaultValue);
                        items.add(item);
                    }
                }
                result.items = items;
                if ((result.message == null || result.message.isEmpty()) && msg.getOptions() != null) {
                    Object header = msg.getOptions().get("header");
                    if (header instanceof String) {
                        result.message = BridgePromptFormatting.stripHtml((String) header);
                    }
                }
            }

            default -> {
                result.response_type = "unknown";
                result.error = "Unhandled action type: " + method;
            }
        }

        return new BuildResult(result, choiceMapping);
    }

    private void populateGameContext(ActionResult result, GameView gameView, Long boardCursorParam) {
        int turn = roundTracker.update(gameView);
        boolean isMyTurn = client.getUsername().equals(gameView.getActivePlayerName());
        boolean isMainPhase = gameView.getPhase() != null && gameView.getPhase().isMain();

        var ctx = new StringBuilder();
        ctx.append("T").append(turn);
        if (gameView.getPhase() != null) {
            ctx.append(" ").append(gameView.getPhase());
        }
        if (gameView.getStep() != null) {
            ctx.append("/").append(gameView.getStep());
        }
        ctx.append(" (").append(gameView.getActivePlayerName()).append(")");
        if (isMyTurn && isMainPhase) {
            ctx.append(" YOUR_MAIN");
        }
        result.context = ctx.toString();

        List<Map<String, Object>> players = gameStateBuilder.buildPlayersArray(gameView);
        long currentBoardCursor = updateBoardCursor.apply(players);
        result.board_cursor = currentBoardCursor;
        if (boardCursorParam != null && boardCursorParam.longValue() == currentBoardCursor) {
            result.board_unchanged = true;
        } else {
            result.board = players;
        }

        PlayerView myPlayer = gameView.getMyPlayer();
        if (myPlayer != null && myPlayer.getBattlefield() != null) {
            int untappedLands = 0;
            for (PermanentView perm : myPlayer.getBattlefield().values()) {
                if (perm.isLand() && !perm.isTapped()) {
                    untappedLands++;
                }
            }
            if (untappedLands > 0) {
                result.untapped_lands = untappedLands;
            }
        }
        if (isMyTurn && isMainPhase && myPlayer != null) {
            result.land_drops_used = myPlayer.getLandsPlayed();
        }

        List<Map<String, Object>> stackSummary = cardFormatter.buildStackItems(gameView, false, false);
        if (!stackSummary.isEmpty()) {
            result.stack = stackSummary;
        }

        List<Map<String, Object>> combatGroups = gameStateBuilder.buildCombatGroups(gameView);
        if (combatGroups != null) {
            result.combat = combatGroups;
        }
    }

    private ChoiceList buildSelectChoices(
            GameClientMessage gcm,
            GameView gameView,
            GameView gv,
            ActionResult result
    ) {
        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        var choiceList = new ArrayList<Map<String, Object>>();
        var indexToChoice = new ArrayList<Object>();

        if (playable != null && !playable.isEmpty()) {
            if (gameView != null) {
                observeTurn.accept(gameView.getTurn());
            }

            var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
            sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(e -> {
                CardView cv = viewLocator.findCardViewById(e.getKey(), gv);
                return cv != null ? cardFormatter.safeDisplayName(cv) : "";
            }).thenComparingInt(e -> viewLocator.getStableShortIdSequence(
                e.getKey(),
                viewLocator.findCardViewById(e.getKey(), gv)
            )));

            int idx = 0;
            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                UUID objectId = entry.getKey();
                PlayableObjectStats stats = entry.getValue();

                if (failedManaCastPredicate.test(objectId)) {
                    continue;
                }

                List<String> abilityNames = stats.getPlayableAbilityNames();
                List<String> manaNames = stats.getAllManaAbilityNames();
                if (!abilityNames.isEmpty() && manaNames.size() == abilityNames.size()) {
                    continue;
                }

                CardView cardView = viewLocator.findCardViewById(objectId, gameView);
                var choiceEntry = new HashMap<String, Object>();
                choiceEntry.put("index", idx);
                choiceEntry.put("id", viewLocator.getStableShortId(objectId, cardView));

                boolean isOnBattlefield = false;
                if (cardView == null) {
                    isOnBattlefield = true;
                } else if (gameView.getMyHand().get(objectId) == null
                    && gameView.getStack().get(objectId) == null) {
                    isOnBattlefield = true;
                }

                if (cardView != null) {
                    choiceEntry.put("name", cardFormatter.safeDisplayName(cardView));
                    if (isOnBattlefield) {
                        choiceEntry.put("action", "activate");
                        var manaNameSet = new HashSet<>(stats.getAllManaAbilityNames());
                        var nonManaAbilities = new ArrayList<String>();
                        for (String name : abilityNames) {
                            if (!manaNameSet.contains(name)) {
                                nonManaAbilities.add(name);
                            }
                        }
                        if (!nonManaAbilities.isEmpty()) {
                            choiceEntry.put("playable_abilities", nonManaAbilities);
                        }
                    } else {
                        choiceEntry.put("action", cardView.isLand() ? "land" : "cast");
                        String manaCost = cardView.getManaCostStr();
                        if (manaCost != null && !manaCost.isEmpty()) {
                            choiceEntry.put("mana_cost", manaCost);
                        }
                        if (cardView.isCreature() && cardView.getPower() != null) {
                            choiceEntry.put("power", cardView.getPower());
                            choiceEntry.put("toughness", cardView.getToughness());
                        }
                    }
                } else {
                    choiceEntry.put("name", "Unknown (" + objectId.toString().substring(0, 8) + ")");
                }

                choiceList.add(choiceEntry);
                indexToChoice.add(objectId);
                idx++;
            }
        }

        Map<String, Serializable> options = gcm.getOptions();
        if (options != null) {
            @SuppressWarnings("unchecked")
            List<UUID> possibleAttackerIds = (List<UUID>) options.get("possibleAttackers");
            @SuppressWarnings("unchecked")
            List<UUID> possibleBlockerIds = (List<UUID>) options.get("possibleBlockers");

            if (possibleAttackerIds != null && !possibleAttackerIds.isEmpty()) {
                result.combat_phase = "declare_attackers";

                var alreadyAttacking = new ArrayList<Map<String, Object>>();
                if (gameView != null && gameView.getCombat() != null) {
                    for (CombatGroupView group : gameView.getCombat()) {
                        for (CardView attacker : group.getAttackers().values()) {
                            var attackerInfo = new HashMap<String, Object>();
                            if (attacker.getId() != null) {
                                attackerInfo.put("id", viewLocator.getStableShortId(attacker.getId(), attacker));
                            }
                            attackerInfo.put("name", cardFormatter.safeDisplayName(attacker));
                            if (attacker.getPower() != null) {
                                attackerInfo.put("power", attacker.getPower());
                                attackerInfo.put("toughness", attacker.getToughness());
                            }
                            alreadyAttacking.add(attackerInfo);
                        }
                    }
                }
                if (!alreadyAttacking.isEmpty()) {
                    result.already_attacking = alreadyAttacking;
                }

                int idx = choiceList.size();
                for (UUID attackerId : possibleAttackerIds) {
                    PermanentView perm = viewLocator.findPermanentViewById(attackerId, gameView);
                    if (perm == null) {
                        continue;
                    }

                    var choiceEntry = new HashMap<String, Object>();
                    choiceEntry.put("index", idx);
                    choiceEntry.put("id", viewLocator.getStableShortId(attackerId, perm));
                    choiceEntry.put("name", cardFormatter.safeDisplayName(perm));
                    if (perm.getPower() != null) {
                        choiceEntry.put("power", perm.getPower());
                        choiceEntry.put("toughness", perm.getToughness());
                    }
                    choiceEntry.put("choice_type", "attacker");
                    choiceList.add(choiceEntry);
                    indexToChoice.add(attackerId);
                    idx++;
                }

                if (options.containsKey("specialButton")) {
                    var allAttackEntry = new HashMap<String, Object>();
                    allAttackEntry.put("index", idx);
                    allAttackEntry.put("id", "all");
                    allAttackEntry.put("name", "All attack");
                    allAttackEntry.put("choice_type", "special");
                    choiceList.add(allAttackEntry);
                    indexToChoice.add("special");
                }
            }

            if (possibleBlockerIds != null && !possibleBlockerIds.isEmpty()) {
                result.combat_phase = "declare_blockers";

                var incomingAttackers = new ArrayList<Map<String, Object>>();
                if (gameView != null && gameView.getCombat() != null) {
                    for (CombatGroupView group : gameView.getCombat()) {
                        for (CardView attacker : group.getAttackers().values()) {
                            var attackerInfo = new HashMap<String, Object>();
                            if (attacker.getId() != null) {
                                attackerInfo.put("id", viewLocator.getStableShortId(attacker.getId(), attacker));
                            }
                            attackerInfo.put("name", attacker.getDisplayName());
                            if (attacker.getPower() != null) {
                                attackerInfo.put("power", attacker.getPower());
                                attackerInfo.put("toughness", attacker.getToughness());
                            }
                            incomingAttackers.add(attackerInfo);
                        }
                    }
                }
                if (!incomingAttackers.isEmpty()) {
                    result.incoming_attackers = incomingAttackers;
                }

                int idx = choiceList.size();
                for (UUID blockerId : possibleBlockerIds) {
                    PermanentView perm = viewLocator.findPermanentViewById(blockerId, gameView);
                    if (perm == null) {
                        continue;
                    }

                    var choiceEntry = new HashMap<String, Object>();
                    choiceEntry.put("index", idx);
                    choiceEntry.put("id", viewLocator.getStableShortId(blockerId, perm));
                    choiceEntry.put("name", cardFormatter.safeDisplayName(perm));
                    if (perm.getPower() != null) {
                        choiceEntry.put("power", perm.getPower());
                        choiceEntry.put("toughness", perm.getToughness());
                    }
                    choiceEntry.put("choice_type", "blocker");
                    choiceList.add(choiceEntry);
                    indexToChoice.add(blockerId);
                    idx++;
                }
            }
        }

        return new ChoiceList(choiceList, indexToChoice.isEmpty() ? null : indexToChoice);
    }

    private ChoiceList buildManaChoices(GameClientMessage manaMsg, GameView gameView, GameView gv) {
        PlayableObjectsList manaPlayable = gameView != null ? gameView.getCanPlayObjects() : null;
        var manaChoiceList = new ArrayList<Map<String, Object>>();
        var manaIndexToChoice = new ArrayList<Object>();
        UUID payingForId = BridgeDecisionSupport.extractPayingForId(manaMsg.getMessage());

        if (manaPlayable != null) {
            var sortedManaEntries = new ArrayList<>(manaPlayable.getObjects().entrySet());
            sortedManaEntries.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(e -> {
                CardView cv = viewLocator.findCardViewById(e.getKey(), gv);
                return cv != null ? cardFormatter.safeDisplayName(cv) : "";
            }).thenComparingInt(e -> viewLocator.getStableShortIdSequence(
                e.getKey(),
                viewLocator.findCardViewById(e.getKey(), gv)
            )));

            int idx = 0;
            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedManaEntries) {
                UUID manaObjectId = entry.getKey();
                if (manaObjectId.equals(payingForId)) {
                    continue;
                }
                PlayableObjectStats stats = entry.getValue();
                List<String> manaAbilities = stats.getAllManaAbilityNames();
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
                    boolean isTap = manaAbilityText.contains("{T}");
                    choiceEntry.put("choice_type", isTap ? "tap_source" : "mana_source");
                    choiceEntry.put("name", cardName);
                    choiceEntry.put("ability", manaAbilityText);
                    manaChoiceList.add(choiceEntry);
                    manaIndexToChoice.add(manaObjectId);
                    idx++;
                }
            }
        }

        List<ManaType> poolChoices = getPoolManaChoices.apply(gameView, manaMsg.getMessage());
        if (!poolChoices.isEmpty()) {
            int idx = manaChoiceList.size();
            ManaPoolView manaPool = BridgeDecisionSupport.getMyManaPoolView(gameView);
            for (ManaType manaType : poolChoices) {
                var choiceEntry = new HashMap<String, Object>();
                choiceEntry.put("index", idx);
                choiceEntry.put("choice_type", "pool_mana");
                choiceEntry.put("name", prettyManaType.apply(manaType));
                choiceEntry.put("count", getManaPoolCount.apply(manaPool, manaType));
                manaChoiceList.add(choiceEntry);
                manaIndexToChoice.add(manaType);
                idx++;
            }
        }

        return new ChoiceList(manaChoiceList, manaIndexToChoice);
    }

    private record ChoiceList(List<Map<String, Object>> choices, List<Object> choiceMapping) {
    }
}

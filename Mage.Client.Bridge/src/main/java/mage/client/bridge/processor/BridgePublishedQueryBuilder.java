package mage.client.bridge.processor;

import mage.choices.Choice;
import mage.client.bridge.BridgeOracleTextService;
import mage.client.bridge.BridgePromptFormatting;
import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.McpToolRegistry;
import mage.constants.ManaType;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.PlayableObjectStats;
import mage.players.PlayableObjectsList;
import mage.util.MultiAmountMessage;
import mage.view.AbilityPickerView;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.CommandObjectView;
import mage.view.CombatGroupView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import java.util.function.ToLongFunction;
import java.util.regex.Pattern;

public final class BridgePublishedQueryBuilder {
    private static final Pattern REGEX_WHITE = Pattern.compile("\\x7b.{0,2}W.{0,2}\\x7d");
    private static final Pattern REGEX_BLUE = Pattern.compile("\\x7b.{0,2}U.{0,2}\\x7d");
    private static final Pattern REGEX_BLACK = Pattern.compile("\\x7b.{0,2}B.{0,2}\\x7d");
    private static final Pattern REGEX_RED = Pattern.compile("\\x7b.{0,2}R.{0,2}\\x7d");
    private static final Pattern REGEX_GREEN = Pattern.compile("\\x7b.{0,2}G.{0,2}\\x7d");
    private static final Pattern REGEX_COLORLESS = Pattern.compile("\\x7b.{0,2}C.{0,2}\\x7d");

    private record TargetChoice(UUID targetId, Map<String, Object> entry, CardView cardView) {
    }

    private final String username;
    private final BridgeProcessorServices processorServices;
    private final Supplier<Set<String>> deckCreatureTypesSupplier;
    private final ToLongFunction<Map<String, Object>> snapshotIdAllocator;

    public BridgePublishedQueryBuilder(
            String username,
            BridgeProcessorServices processorServices,
            Supplier<Set<String>> deckCreatureTypesSupplier,
            ToLongFunction<Map<String, Object>> snapshotIdAllocator) {
        this.username = Objects.requireNonNull(username);
        this.processorServices = Objects.requireNonNull(processorServices);
        this.deckCreatureTypesSupplier = Objects.requireNonNull(deckCreatureTypesSupplier);
        this.snapshotIdAllocator = Objects.requireNonNull(snapshotIdAllocator);
    }

    public BridgeBuiltActionChoices buildPublishedActionChoices(
            PendingAction action,
            BridgeProjectedActionContext projectedActionContext,
            GameView fallbackGameView,
            BridgeProjectionInputs projectionInputs
    ) {
        var result = new ActionResult();
        GameView gameView = extractActionGameView(action, fallbackGameView);
        if (action != null) {
            result.game_seq = action.gameSeq();
        }

        if (action == null) {
            result.action_pending = false;
            return new BridgeBuiltActionChoices(result, List.of());
        }

        result.action_pending = true;
        result.action_type = action.method().name();
        result.message = BridgePromptFormatting.stripHtml(action.message());

        if (fallbackGameView != null && !projectedActionContext.available()) {
            throw new IllegalStateException("Published action context missing for projected game view");
        }
        if (projectedActionContext.available()) {
            result.context = projectedActionContext.context();
            result.board = projectedActionContext.board();
            result.stack = projectedActionContext.stack();
            result.combat = projectedActionContext.combat();
            result.untapped_lands = projectedActionContext.untappedLands();
            result.land_drops_used = projectedActionContext.landDropsUsed();
        }

        ClientCallbackMethod method = action.method();
        Object data = action.data();

        List<Object> backingChoices;
        switch (method) {
            case GAME_ASK -> backingChoices = buildAskChoices(result, action, gameView);
            case GAME_SELECT -> backingChoices = buildSelectChoices(result, data, gameView, projectionInputs);
            case GAME_PLAY_MANA, GAME_PLAY_XMANA -> backingChoices = buildManaChoices(result, data, gameView);
            case GAME_TARGET -> backingChoices = buildTargetChoices(result, data, gameView, projectionInputs.currentPlayerId());
            case GAME_CHOOSE_ABILITY -> backingChoices = buildAbilityChoices(result, data);
            case GAME_CHOOSE_CHOICE -> backingChoices = buildChoiceChoices(result, data);
            case GAME_CHOOSE_PILE -> backingChoices = buildPileChoices(result, data);
            case GAME_GET_AMOUNT -> backingChoices = buildAmountChoices(result, data);
            case GAME_GET_MULTI_AMOUNT -> backingChoices = buildMultiAmountChoices(result, data);
            default -> {
                result.response_type = "unknown";
                result.error = "Unhandled action type: " + method;
                backingChoices = List.of();
            }
        }

        return new BridgeBuiltActionChoices(result, backingChoices);
    }

    public BridgeProjectedActionContext buildProjectedActionContext(
            GameView gameView,
            BridgePublishedGameState projectedGameState,
            int currentRound
    ) {
        if (gameView == null || !projectedGameState.available()) {
            return BridgeProjectedActionContext.empty();
        }

        boolean isMyTurn = username.equals(gameView.getActivePlayerName());
        boolean isMainPhase = gameView.getPhase() != null && gameView.getPhase().isMain();
        var ctx = new StringBuilder();
        ctx.append("T").append(currentRound);
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

        Integer untappedLands = null;
        Integer landDropsUsed = null;
        PlayerView myPlayer = gameView.getMyPlayer();
        if (myPlayer != null && myPlayer.getBattlefield() != null) {
            int count = 0;
            for (PermanentView perm : myPlayer.getBattlefield().values()) {
                if (perm.isLand() && !perm.isTapped()) {
                    count++;
                }
            }
            if (count > 0) {
                untappedLands = count;
            }
        }
        if (isMyTurn && isMainPhase && myPlayer != null) {
            landDropsUsed = myPlayer.getLandsPlayed();
        }

        return new BridgeProjectedActionContext(
            true,
            ctx.toString(),
            projectedGameState.players(),
            summarizeProjectedStack(projectedGameState.stack()),
            projectedGameState.combat(),
            untappedLands,
            landDropsUsed,
            projectedGameState.gameSeq()
        );
    }

    public BridgePublishedOracleIndex buildPublishedOracleIndex(GameView gameView) {
        if (gameView == null) {
            return BridgePublishedOracleIndex.empty();
        }

        var cardsByObjectId = new LinkedHashMap<String, Map<String, Object>>();
        var cardsByUuid = new LinkedHashMap<UUID, Map<String, Object>>();

        for (CardView card : gameView.getMyHand().values()) {
            addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
        }
        for (CardView card : gameView.getStack().values()) {
            addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
        }
        for (PlayerView player : gameView.getPlayers()) {
            for (PermanentView permanent : player.getBattlefield().values()) {
                addOracleCard(cardsByObjectId, cardsByUuid, permanent, gameView);
            }
            for (CardView card : player.getGraveyard().values()) {
                addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
            }
            for (CardView card : player.getExile().values()) {
                addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
            }
            for (CommandObjectView commandObject : player.getCommandObjectList()) {
                if (commandObject instanceof CardView card) {
                    addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
                }
            }
        }
        for (var exileZone : gameView.getExile()) {
            for (CardView card : exileZone.values()) {
                addOracleCard(cardsByObjectId, cardsByUuid, card, gameView);
            }
        }

        for (String shortId : processorServices.shortIds().snapshotShortIds()) {
            UUID objectId = processorServices.shortIds().tryResolve(shortId);
            if (objectId == null) {
                continue;
            }
            Map<String, Object> fields = cardsByUuid.get(objectId);
            if (fields != null) {
                cardsByObjectId.putIfAbsent(shortId, fields);
            }
        }

        return new BridgePublishedOracleIndex(
            cardsByObjectId,
            processorServices.shortIds().snapshotShortIds()
        );
    }

    BridgePublishedGameStateBuild buildPublishedGameState(
            GameView gameView,
            int currentRound,
            UUID currentPlayerId) {
        UUID myPlayerId = resolveMyPlayerId(gameView, currentPlayerId);
        List<Map<String, Object>> players = freezeMapList(
            processorServices.gameStateBuilder().buildPlayersArray(gameView, myPlayerId)
        );
        List<Map<String, Object>> stack = freezeMapList(
            processorServices.cardFormatter().buildStackItems(gameView, myPlayerId, true, true)
        );
        List<Map<String, Object>> combat = freezeMapList(processorServices.gameStateBuilder().buildCombatGroups(gameView));

        var state = new GetGameStateTool.Result();
        state.available = true;
        state.game_seq = gameView.getGameSeq();
        state.turn = currentRound;
        state.phase = gameView.getPhase() != null ? gameView.getPhase().toString() : null;
        state.step = gameView.getStep() != null ? gameView.getStep().toString() : null;
        state.active_player = gameView.getActivePlayerName();
        state.priority_player = gameView.getPriorityPlayerName();
        state.players = players;
        state.stack = stack;
        state.combat = combat;

        Map<String, Object> stateMap = McpToolRegistry.resultToMap(state);
        long snapshotId = updateGameStateSnapshotId(stateMap);

        return new BridgePublishedGameStateBuild(
            new BridgePublishedGameState(
                true,
                null,
                snapshotId,
                state.turn,
                state.phase,
                state.step,
                state.active_player,
                state.priority_player,
                players,
                stack,
                combat,
                state.game_seq
            ),
            stateMap.toString()
        );
    }

    record BridgePublishedGameStateBuild(
            BridgePublishedGameState state,
            String payload
    ) {
    }

    private void addOracleCard(
            Map<String, Map<String, Object>> cardsByObjectId,
            Map<UUID, Map<String, Object>> cardsByUuid,
            CardView card,
            GameView gameView) {
        if (card == null || card.getId() == null) {
            return;
        }
        Map<String, Object> fields = BridgeOracleTextService.buildCardFieldsMap(card);
        cardsByUuid.putIfAbsent(card.getId(), fields);
        cardsByObjectId.putIfAbsent(
            processorServices.viewLocator().getStableShortId(card.getId(), card, gameView),
            fields
        );
        CardView secondFace = card.getSecondCardFace();
        if (secondFace != null && secondFace.getId() != null) {
            Map<String, Object> secondFaceFields = BridgeOracleTextService.buildCardFieldsMap(secondFace);
            cardsByUuid.putIfAbsent(secondFace.getId(), secondFaceFields);
            cardsByObjectId.putIfAbsent(
                processorServices.viewLocator().getStableShortId(secondFace.getId(), secondFace, gameView),
                secondFaceFields
            );
        }
    }

    private List<Object> buildAskChoices(ActionResult result, PendingAction action, GameView gameView) {
        result.response_type = "boolean";
        result.respond_with = "choice=yes or choice=no";

        String askMsg = action.message();
        if (askMsg != null && askMsg.toLowerCase().contains("mulligan") && gameView != null) {
            CardsView hand = gameView.getMyHand();
            if (hand != null && !hand.isEmpty()) {
                var sortedHand = new ArrayList<>(hand.values());
                sortedHand.sort(Comparator.comparing(processorServices.cardFormatter()::safeDisplayName));

                var handCards = new ArrayList<Map<String, Object>>();
                for (CardView card : sortedHand) {
                    handCards.add(processorServices.cardFormatter().buildCardInfoMap(card));
                }
                result.your_hand = handCards;
            }
        }
        return List.of();
    }

    private List<Object> buildSelectChoices(
            ActionResult result,
            Object data,
            GameView gameView,
            BridgeProjectionInputs projectionInputs) {
        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        var choiceList = new ArrayList<Map<String, Object>>();
        var indexToUuid = new ArrayList<Object>();

        if (playable != null && !playable.isEmpty()) {
            var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
            sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(entry -> {
                CardView cardView = processorServices.viewLocator().findCardViewById(entry.getKey(), gameView);
                return cardView != null ? processorServices.cardFormatter().safeDisplayName(cardView) : "";
            }).thenComparingInt(entry -> processorServices.viewLocator().getStableShortIdSequence(
                entry.getKey(),
                processorServices.viewLocator().findCardViewById(entry.getKey(), gameView),
                gameView
            )));

            int idx = 0;
            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                UUID objectId = entry.getKey();
                PlayableObjectStats stats = entry.getValue();
                if (projectionInputs.failedManaCast(objectId)) {
                    continue;
                }

                List<String> abilityNames = stats.getPlayableAbilityNames();
                List<String> manaNames = stats.getAllManaAbilityNames();
                if (!abilityNames.isEmpty() && manaNames.size() == abilityNames.size()) {
                    continue;
                }

                CardView cardView = processorServices.viewLocator().findCardViewById(objectId, gameView);
                var choiceEntry = new HashMap<String, Object>();
                choiceEntry.put("index", idx);
                choiceEntry.put("id", processorServices.viewLocator().getStableShortId(objectId, cardView, gameView));

                boolean isOnBattlefield = cardView == null
                    || (gameView.getMyHand().get(objectId) == null && gameView.getStack().get(objectId) == null);

                if (cardView != null) {
                    choiceEntry.put("name", processorServices.cardFormatter().safeDisplayName(cardView));
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
                indexToUuid.add(objectId);
                idx++;
            }
        }

        if (data instanceof GameClientMessage gcm) {
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
                                    attackerInfo.put("id", processorServices.viewLocator().getStableShortId(attacker.getId(), attacker, gameView));
                                }
                                attackerInfo.put("name", processorServices.cardFormatter().safeDisplayName(attacker));
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
                        PermanentView permanent = processorServices.viewLocator().findPermanentViewById(attackerId, gameView);
                        if (permanent == null) {
                            continue;
                        }

                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        choiceEntry.put("id", processorServices.viewLocator().getStableShortId(attackerId, permanent, gameView));
                        choiceEntry.put("name", processorServices.cardFormatter().safeDisplayName(permanent));
                        if (permanent.getPower() != null) {
                            choiceEntry.put("power", permanent.getPower());
                            choiceEntry.put("toughness", permanent.getToughness());
                        }
                        choiceEntry.put("choice_type", "attacker");
                        choiceList.add(choiceEntry);
                        indexToUuid.add(attackerId);
                        idx++;
                    }

                    if (options.containsKey("specialButton")) {
                        var allAttackEntry = new HashMap<String, Object>();
                        allAttackEntry.put("index", idx);
                        allAttackEntry.put("id", "all");
                        allAttackEntry.put("name", "All attack");
                        allAttackEntry.put("choice_type", "special");
                        choiceList.add(allAttackEntry);
                        indexToUuid.add("special");
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
                                    attackerInfo.put("id", processorServices.viewLocator().getStableShortId(attacker.getId(), attacker, gameView));
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
                        PermanentView permanent = processorServices.viewLocator().findPermanentViewById(blockerId, gameView);
                        if (permanent == null) {
                            continue;
                        }

                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        choiceEntry.put("id", processorServices.viewLocator().getStableShortId(blockerId, permanent, gameView));
                        choiceEntry.put("name", processorServices.cardFormatter().safeDisplayName(permanent));
                        if (permanent.getPower() != null) {
                            choiceEntry.put("power", permanent.getPower());
                            choiceEntry.put("toughness", permanent.getToughness());
                        }
                        choiceEntry.put("choice_type", "blocker");
                        choiceList.add(choiceEntry);
                        indexToUuid.add(blockerId);
                        idx++;
                    }
                }
            }
        }

        if (!choiceList.isEmpty()) {
            result.response_type = "select";
            result.choices = choiceList;
            String combatPhase = result.combat_phase;
            if ("declare_attackers".equals(combatPhase)) {
                result.respond_with = "attackers=p1,p2,... or choice=yes (confirm) or choice=no (skip)";
            } else if ("declare_blockers".equals(combatPhase)) {
                result.respond_with = "blockers=p5:p1,p6:p2 (blocker:attacker) or choice=yes (confirm) or choice=no (skip)";
            } else {
                result.respond_with = "choice=pN to play, or choice=no to pass";
            }
            return indexToUuid;
        }

        result.response_type = "boolean";
        result.respond_with = "choice=yes (confirm) or choice=no (pass)";
        return List.of();
    }

    private List<Object> buildManaChoices(ActionResult result, Object data, GameView gameView) {
        GameClientMessage manaMsg = (GameClientMessage) data;
        PlayableObjectsList manaPlayable = gameView != null ? gameView.getCanPlayObjects() : null;
        var manaChoiceList = new ArrayList<Map<String, Object>>();
        var manaIndexToChoice = new ArrayList<Object>();
        UUID payingForId = extractPayingForId(manaMsg.getMessage());

        if (manaPlayable != null) {
            var sortedManaEntries = new ArrayList<>(manaPlayable.getObjects().entrySet());
            sortedManaEntries.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(entry -> {
                CardView cardView = processorServices.viewLocator().findCardViewById(entry.getKey(), gameView);
                return cardView != null ? processorServices.cardFormatter().safeDisplayName(cardView) : "";
            }).thenComparingInt(entry -> processorServices.viewLocator().getStableShortIdSequence(
                entry.getKey(),
                processorServices.viewLocator().findCardViewById(entry.getKey(), gameView),
                gameView
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

                CardView cardView = processorServices.viewLocator().findCardViewById(manaObjectId, gameView);
                String cardName = cardView != null
                    ? cardView.getDisplayName()
                    : "Unknown (" + manaObjectId.toString().substring(0, 8) + ")";

                for (String manaAbilityText : manaAbilities) {
                    var choiceEntry = new HashMap<String, Object>();
                    choiceEntry.put("index", idx);
                    choiceEntry.put("id", processorServices.viewLocator().getStableShortId(manaObjectId, cardView, gameView));
                    choiceEntry.put("choice_type", manaAbilityText.contains("{T}") ? "tap_source" : "mana_source");
                    choiceEntry.put("name", cardName);
                    choiceEntry.put("ability", manaAbilityText);
                    manaChoiceList.add(choiceEntry);
                    manaIndexToChoice.add(manaObjectId);
                    idx++;
                }
            }
        }

        List<ManaType> poolChoices = getPoolManaChoices(gameView, manaMsg.getMessage());
        if (!poolChoices.isEmpty()) {
            int idx = manaChoiceList.size();
            ManaPoolView manaPool = getMyManaPoolView(gameView);
            for (ManaType manaType : poolChoices) {
                var choiceEntry = new HashMap<String, Object>();
                choiceEntry.put("index", idx);
                choiceEntry.put("choice_type", "pool_mana");
                choiceEntry.put("name", prettyManaType(manaType));
                choiceEntry.put("count", getManaPoolCount(manaPool, manaType));
                manaChoiceList.add(choiceEntry);
                manaIndexToChoice.add(manaType);
                idx++;
            }
        }

        if (!manaChoiceList.isEmpty()) {
            result.response_type = "select";
            result.respond_with = "choice=pN to tap, or choice=no to cancel";
            result.choices = manaChoiceList;
            return manaIndexToChoice;
        }

        result.response_type = "boolean";
        result.respond_with = "choice=no to cancel";
        return List.of();
    }

    private List<Object> buildTargetChoices(
            ActionResult result,
            Object data,
            GameView targetGameView,
            UUID currentPlayerId) {
        GameClientMessage msg = (GameClientMessage) data;
        result.response_type = "index";
        boolean required = msg.isFlag();
        result.required = required;
        result.can_cancel = !required;
        result.respond_with = required
            ? "choice=pN — must pick a target"
            : "choice=pN, or choice=no to cancel";

        Set<UUID> targets = findValidTargets(msg);
        var choiceList = new ArrayList<Map<String, Object>>();
        var indexToUuid = new ArrayList<Object>();

        if (targets != null) {
            CardsView cardsView = msg.getCardsView1();
            UUID myPlayerId = resolveMyPlayerId(targetGameView, currentPlayerId);
            var targetChoices = new ArrayList<TargetChoice>();
            for (UUID targetId : targets) {
                var choiceEntry = new HashMap<String, Object>();
                CardView resolvedCardView = processorServices.cardFormatter().buildTargetInfo(choiceEntry, targetId, cardsView, targetGameView, myPlayerId);
                targetChoices.add(new TargetChoice(targetId, choiceEntry, resolvedCardView));
            }

            targetChoices.sort((left, right) -> {
                boolean leftIsYou = Boolean.TRUE.equals(left.entry().get("is_you"));
                boolean rightIsYou = Boolean.TRUE.equals(right.entry().get("is_you"));
                int youCmp = Boolean.compare(rightIsYou, leftIsYou);
                if (youCmp != 0) {
                    return youCmp;
                }
                String leftName = Objects.toString(left.entry().get("name"), "");
                String rightName = Objects.toString(right.entry().get("name"), "");
                int nameCmp = String.CASE_INSENSITIVE_ORDER.compare(leftName, rightName);
                if (nameCmp != 0) {
                    return nameCmp;
                }
                return Integer.compare(
                    processorServices.viewLocator().getStableShortIdSequence(left.targetId(), left.cardView(), targetGameView),
                    processorServices.viewLocator().getStableShortIdSequence(right.targetId(), right.cardView(), targetGameView)
                );
            });

            int idx = 0;
            for (TargetChoice choice : targetChoices) {
                choice.entry().put("id", processorServices.viewLocator().getStableShortId(
                    choice.targetId(),
                    choice.cardView(),
                    targetGameView
                ));
                choice.entry().put("index", idx);
                choiceList.add(choice.entry());
                indexToUuid.add(choice.targetId());
                idx++;
            }
        }

        result.choices = choiceList;
        return indexToUuid;
    }

    private List<Object> buildAbilityChoices(ActionResult result, Object data) {
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
                String desc = BridgePromptFormatting.stripAbilityPickerOrdinalPrefix(
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
            String message = result.message;
            if (message != null && message.startsWith("Choose spell or ability")) {
                int colonIdx = message.indexOf(": ");
                String cardName = colonIdx >= 0 ? message.substring(colonIdx + 2).trim() : "";
                if (!cardName.isEmpty()) {
                    result.message = "Choose which mana to produce from " + cardName
                        + " (tapping to pay for a spell)";
                }
            }
        }

        result.choices = choiceList;
        return indexToUuid;
    }

    private List<Object> buildChoiceChoices(ActionResult result, Object data) {
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
                    for (String choiceValue : choices) {
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        choiceEntry.put("description", choiceValue);
                        choiceList.add(choiceEntry);
                        indexToKey.add(choiceValue);
                        idx++;
                    }
                }
            }
        }

        int totalChoices = choiceList.size();
        if (totalChoices >= 50) {
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
        return indexToKey;
    }

    private List<Object> buildPileChoices(ActionResult result, Object data) {
        GameClientMessage msg = (GameClientMessage) data;
        result.response_type = "pile";
        result.respond_with = "pile=1 or pile=2";

        var pile1 = new ArrayList<Map<String, Object>>();
        var pile2 = new ArrayList<Map<String, Object>>();
        if (msg.getCardsView1() != null) {
            for (CardView card : msg.getCardsView1().values()) {
                pile1.add(processorServices.cardFormatter().buildCardInfoMap(card));
            }
        }
        if (msg.getCardsView2() != null) {
            for (CardView card : msg.getCardsView2().values()) {
                pile2.add(processorServices.cardFormatter().buildCardInfoMap(card));
            }
        }
        result.pile1 = pile1;
        result.pile2 = pile2;
        return List.of();
    }

    private List<Object> buildAmountChoices(ActionResult result, Object data) {
        GameClientMessage msg = (GameClientMessage) data;
        result.response_type = "amount";
        result.respond_with = "amount=N (min=" + msg.getMin() + ", max=" + msg.getMax() + ")";
        result.min = msg.getMin();
        result.max = msg.getMax();
        return List.of();
    }

    private List<Object> buildMultiAmountChoices(ActionResult result, Object data) {
        GameClientMessage msg = (GameClientMessage) data;
        result.response_type = "multi_amount";
        result.respond_with = "amounts=[N,N,...] — one per item, sum between total_min and total_max";
        result.total_min = msg.getMin();
        result.total_max = msg.getMax();

        var items = new ArrayList<Map<String, Object>>();
        if (msg.getMessages() != null) {
            for (MultiAmountMessage item : msg.getMessages()) {
                var itemInfo = new HashMap<String, Object>();
                itemInfo.put("description", BridgePromptFormatting.stripHtml(item.message));
                itemInfo.put("min", item.min);
                itemInfo.put("max", item.max);
                itemInfo.put("default", item.defaultValue);
                items.add(itemInfo);
            }
        }
        result.items = items;
        if ((result.message == null || result.message.isEmpty()) && msg.getOptions() != null) {
            Object header = msg.getOptions().get("header");
            if (header instanceof String headerText) {
                result.message = BridgePromptFormatting.stripHtml(headerText);
            }
        }
        return List.of();
    }

    private GameView extractActionGameView(PendingAction action, GameView fallbackGameView) {
        if (action != null && action.data() instanceof GameClientMessage gameClientMessage) {
            GameView gameView = gameClientMessage.getGameView();
            if (gameView != null) {
                return gameView;
            }
        }
        return fallbackGameView;
    }

    private long updateGameStateSnapshotId(Map<String, Object> state) {
        return snapshotIdAllocator.applyAsLong(state);
    }

    @SuppressWarnings("unchecked")
    private Set<UUID> findValidTargets(GameClientMessage message) {
        Set<UUID> targets = message.getTargets();
        if (targets != null && !targets.isEmpty()) {
            return targets;
        }

        Map<String, Serializable> options = message.getOptions();
        if (options != null) {
            Object possibleTargets = options.get("possibleTargets");
            if (possibleTargets instanceof Set<?> possibleSet) {
                Set<UUID> possible = (Set<UUID>) possibleSet;
                if (!possible.isEmpty()) {
                    return possible;
                }
            }
        }

        CardsView cardsView = message.getCardsView1();
        if (cardsView != null && !cardsView.isEmpty()) {
            return cardsView.keySet();
        }

        return null;
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
        return myPlayer != null ? myPlayer.getManaPool() : null;
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

    private UUID resolveMyPlayerId(GameView gameView, UUID currentPlayerId) {
        if (gameView != null) {
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getPlayerId() != null) {
                return myPlayer.getPlayerId();
            }
        }
        return currentPlayerId;
    }

    private static List<Map<String, Object>> summarizeProjectedStack(List<Map<String, Object>> stack) {
        if (stack == null || stack.isEmpty()) {
            return null;
        }
        var summarized = new ArrayList<Map<String, Object>>(stack.size());
        for (Map<String, Object> item : stack) {
            var summary = new LinkedHashMap<String, Object>();
            for (Map.Entry<String, Object> entry : item.entrySet()) {
                if ("id".equals(entry.getKey()) || "rules".equals(entry.getKey())) {
                    continue;
                }
                summary.put(entry.getKey(), freezeJsonLike(entry.getValue()));
            }
            summarized.add(Collections.unmodifiableMap(summary));
        }
        return Collections.unmodifiableList(summarized);
    }

    private static List<Map<String, Object>> freezeMapList(List<Map<String, Object>> values) {
        if (values == null) {
            return null;
        }
        var frozen = new ArrayList<Map<String, Object>>(values.size());
        for (Map<String, Object> value : values) {
            frozen.add(freezeMap(value));
        }
        return Collections.unmodifiableList(frozen);
    }

    private static Map<String, Object> freezeMap(Map<String, Object> value) {
        var frozen = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> entry : value.entrySet()) {
            frozen.put(entry.getKey(), freezeJsonLike(entry.getValue()));
        }
        return Collections.unmodifiableMap(frozen);
    }

    private static Object freezeJsonLike(Object value) {
        if (value instanceof Map<?, ?> map) {
            var frozen = new LinkedHashMap<String, Object>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                frozen.put((String) entry.getKey(), freezeJsonLike(entry.getValue()));
            }
            return Collections.unmodifiableMap(frozen);
        }
        if (value instanceof List<?> list) {
            var frozen = new ArrayList<>(list.size());
            for (Object entry : list) {
                frozen.add(freezeJsonLike(entry));
            }
            return Collections.unmodifiableList(frozen);
        }
        return value;
    }
}

package mage.client.bridge;

import mage.players.PlayableObjectsList;
import mage.view.CardView;
import mage.view.CombatGroupView;
import mage.view.CommandObjectView;
import mage.view.CounterView;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import java.util.UUID;
import java.util.function.Function;
import java.util.function.Supplier;

public final class BridgeGameStateBuilder {

    private final BridgeCardFormatter cardFormatter;
    private final BridgeViewLocator viewLocator;
    private final Supplier<UUID> currentGameIdSupplier;
    private final Function<UUID, UUID> playerIdForGame;

    public BridgeGameStateBuilder(
            BridgeCardFormatter cardFormatter,
            BridgeViewLocator viewLocator,
            Supplier<UUID> currentGameIdSupplier,
            Function<UUID, UUID> playerIdForGame
    ) {
        this.cardFormatter = cardFormatter;
        this.viewLocator = viewLocator;
        this.currentGameIdSupplier = currentGameIdSupplier;
        this.playerIdForGame = playerIdForGame;
    }

    public List<Map<String, Object>> buildPlayersArray(GameView gameView) {
        var players = new ArrayList<Map<String, Object>>();
        UUID myPlayerId = playerIdForGame.apply(currentGameIdSupplier.get());

        for (PlayerView player : getStablePlayers(gameView, myPlayerId)) {
            var playerInfo = new HashMap<String, Object>();
            playerInfo.put("name", player.getName());
            playerInfo.put("life", player.getLife());
            playerInfo.put("library_size", player.getLibraryCount());
            playerInfo.put("hand_size", player.getHandCount());
            playerInfo.put("is_active", player.isActive());

            boolean isMe = player.getPlayerId().equals(myPlayerId);
            playerInfo.put("is_you", isMe);

            if (isMe && gameView.getMyHand() != null) {
                var handCards = new ArrayList<Map<String, Object>>();
                PlayableObjectsList playable = gameView.getCanPlayObjects();

                var sortedHand = new ArrayList<>(gameView.getMyHand().entrySet());
                sortedHand.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> cardFormatter.safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> viewLocator.getStableShortIdSequence(e.getKey(), e.getValue())));

                for (Map.Entry<UUID, CardView> handEntry : sortedHand) {
                    var cardInfo = cardFormatter.buildCardInfoMap(handEntry.getValue());
                    cardInfo.put("id", viewLocator.getStableShortId(handEntry.getKey(), handEntry.getValue()));
                    if (playable != null && playable.containsObject(handEntry.getKey())) {
                        cardInfo.put("playable", true);
                    }
                    handCards.add(cardInfo);
                }
                playerInfo.put("hand", handCards);
            }

            var battlefield = new ArrayList<Map<String, Object>>();
            if (player.getBattlefield() != null) {
                var sortedBattlefield = new ArrayList<>(player.getBattlefield().values());
                sortedBattlefield.sort(Comparator.<PermanentView, String>comparing(cardFormatter::safeDisplayName)
                    .thenComparingInt(p -> viewLocator.getStableShortIdSequence(p.getId(), p)));
                for (PermanentView perm : sortedBattlefield) {
                    var permInfo = new HashMap<String, Object>();
                    permInfo.put("id", viewLocator.getStableShortId(perm.getId(), perm));
                    permInfo.put("name", cardFormatter.safeDisplayName(perm));
                    permInfo.put("tapped", perm.isTapped());

                    if (perm.isCreature()) {
                        permInfo.put("power", perm.getPower());
                        permInfo.put("toughness", perm.getToughness());
                    }
                    if (perm.isPlaneswalker()) {
                        permInfo.put("loyalty", perm.getLoyalty());
                    }
                    if (perm.getCounters() != null && !perm.getCounters().isEmpty()) {
                        var counters = new HashMap<String, Integer>();
                        for (CounterView counter : perm.getCounters()) {
                            counters.put(counter.getName(), counter.getCount());
                        }
                        permInfo.put("counters", counters);
                    }
                    if (perm.isCreature()) {
                        permInfo.put("summoning_sick", perm.hasSummoningSickness());
                    }
                    if (perm.isToken()) {
                        permInfo.put("token", true);
                    }

                    boolean modified = false;
                    CardView orig = perm.getOriginal();
                    if (orig != null) {
                        modified = !Objects.equals(
                            BridgePromptFormatting.stripHtmlList(perm.getRules()),
                            BridgePromptFormatting.stripHtmlList(orig.getRules()));
                    }
                    if (modified) {
                        permInfo.put("modified", true);
                    }

                    List<String> rules = BridgePromptFormatting.stripHtmlList(perm.getRules());
                    if (rules != null && !rules.isEmpty()) {
                        permInfo.put("rules", rules);
                    }

                    String altName = perm.getAlternateName();
                    if (altName != null && !altName.isEmpty()) {
                        permInfo.put("original_card", altName);
                    }
                    if (perm.isCopy()) {
                        permInfo.put("copy", true);
                    }
                    if (perm.isMorphed() || perm.isManifested()) {
                        permInfo.put("face_down", true);
                    }

                    battlefield.add(permInfo);
                }
            }
            if (!battlefield.isEmpty()) {
                playerInfo.put("battlefield", battlefield);
            }

            var graveyard = new ArrayList<Map<String, Object>>();
            if (player.getGraveyard() != null) {
                var sortedGraveyard = new ArrayList<>(player.getGraveyard().entrySet());
                sortedGraveyard.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> cardFormatter.safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> viewLocator.getStableShortIdSequence(e.getKey(), e.getValue())));
                for (Map.Entry<UUID, CardView> entry : sortedGraveyard) {
                    var cardInfo = new HashMap<String, Object>();
                    cardInfo.put("id", viewLocator.getStableShortId(entry.getKey(), entry.getValue()));
                    cardInfo.put("name", cardFormatter.safeDisplayName(entry.getValue()));
                    List<String> gyRules = BridgePromptFormatting.stripHtmlList(entry.getValue().getRules());
                    if (gyRules != null && !gyRules.isEmpty()) {
                        cardInfo.put("rules", gyRules);
                    }
                    graveyard.add(cardInfo);
                }
            }
            if (!graveyard.isEmpty()) {
                playerInfo.put("graveyard", graveyard);
            }

            var exileCards = new ArrayList<Map<String, Object>>();
            if (player.getExile() != null) {
                var sortedExile = new ArrayList<>(player.getExile().entrySet());
                sortedExile.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> cardFormatter.safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> viewLocator.getStableShortIdSequence(e.getKey(), e.getValue())));
                for (Map.Entry<UUID, CardView> entry : sortedExile) {
                    var cardInfo = new HashMap<String, Object>();
                    cardInfo.put("id", viewLocator.getStableShortId(entry.getKey(), entry.getValue()));
                    cardInfo.put("name", cardFormatter.safeDisplayName(entry.getValue()));
                    List<String> exileRules = BridgePromptFormatting.stripHtmlList(entry.getValue().getRules());
                    if (exileRules != null && !exileRules.isEmpty()) {
                        cardInfo.put("rules", exileRules);
                    }
                    exileCards.add(cardInfo);
                }
            }
            if (!exileCards.isEmpty()) {
                playerInfo.put("exile", exileCards);
            }

            ManaPoolView pool = player.getManaPool();
            if (pool != null) {
                int total = pool.getRed() + pool.getGreen() + pool.getBlue()
                          + pool.getWhite() + pool.getBlack() + pool.getColorless();
                if (total > 0) {
                    var mana = new HashMap<String, Integer>();
                    if (pool.getRed() > 0) {
                        mana.put("R", pool.getRed());
                    }
                    if (pool.getGreen() > 0) {
                        mana.put("G", pool.getGreen());
                    }
                    if (pool.getBlue() > 0) {
                        mana.put("U", pool.getBlue());
                    }
                    if (pool.getWhite() > 0) {
                        mana.put("W", pool.getWhite());
                    }
                    if (pool.getBlack() > 0) {
                        mana.put("B", pool.getBlack());
                    }
                    if (pool.getColorless() > 0) {
                        mana.put("C", pool.getColorless());
                    }
                    playerInfo.put("mana_pool", mana);
                }
            }

            if (player.getCounters() != null && !player.getCounters().isEmpty()) {
                var counters = new HashMap<String, Integer>();
                for (CounterView counter : player.getCounters()) {
                    counters.put(counter.getName(), counter.getCount());
                }
                playerInfo.put("counters", counters);
            }

            if (player.getCommandObjectList() != null && !player.getCommandObjectList().isEmpty()) {
                var commanders = new ArrayList<String>();
                for (CommandObjectView cmd : player.getCommandObjectList()) {
                    commanders.add(cmd.getName());
                }
                playerInfo.put("commanders", commanders);
            }

            players.add(playerInfo);
        }
        return players;
    }

    public List<Map<String, Object>> buildCombatGroups(GameView gameView) {
        if (gameView == null || gameView.getCombat() == null || gameView.getCombat().isEmpty()) {
            return null;
        }
        var combatGroups = new ArrayList<Map<String, Object>>();
        for (CombatGroupView group : gameView.getCombat()) {
            var groupInfo = new HashMap<String, Object>();
            var attackers = new ArrayList<Map<String, Object>>();
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
                attackers.add(attackerInfo);
            }
            groupInfo.put("attackers", attackers);

            var blockers = new ArrayList<Map<String, Object>>();
            for (CardView blocker : group.getBlockers().values()) {
                var blockerInfo = new HashMap<String, Object>();
                if (blocker.getId() != null) {
                    blockerInfo.put("id", viewLocator.getStableShortId(blocker.getId(), blocker));
                }
                blockerInfo.put("name", cardFormatter.safeDisplayName(blocker));
                if (blocker.getPower() != null) {
                    blockerInfo.put("power", blocker.getPower());
                    blockerInfo.put("toughness", blocker.getToughness());
                }
                blockers.add(blockerInfo);
            }
            if (!blockers.isEmpty()) {
                groupInfo.put("blockers", blockers);
            }
            groupInfo.put("blocked", group.isBlocked());
            groupInfo.put("defending", group.getDefenderName());
            combatGroups.add(groupInfo);
        }
        return combatGroups;
    }

    public static String buildStateSignature(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map<?, ?> map) {
            var sorted = new TreeMap<String, Object>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                sorted.put(String.valueOf(entry.getKey()), entry.getValue());
            }
            var sb = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<String, Object> entry : sorted.entrySet()) {
                if (!first) {
                    sb.append(",");
                }
                sb.append(entry.getKey()).append(":").append(buildStateSignature(entry.getValue()));
                first = false;
            }
            sb.append("}");
            return sb.toString();
        }
        if (value instanceof List<?> list) {
            var sb = new StringBuilder("[");
            boolean first = true;
            for (Object item : list) {
                if (!first) {
                    sb.append(",");
                }
                sb.append(buildStateSignature(item));
                first = false;
            }
            sb.append("]");
            return sb.toString();
        }
        return String.valueOf(value);
    }

    private List<PlayerView> getStablePlayers(GameView gameView, UUID myPlayerId) {
        var players = new ArrayList<PlayerView>(gameView.getPlayers());
        players.sort((a, b) -> {
            boolean aIsYou = myPlayerId != null && myPlayerId.equals(a.getPlayerId());
            boolean bIsYou = myPlayerId != null && myPlayerId.equals(b.getPlayerId());
            int youCmp = Boolean.compare(bIsYou, aIsYou);
            if (youCmp != 0) {
                return youCmp;
            }
            String aName = a.getName() != null ? a.getName() : "";
            String bName = b.getName() != null ? b.getName() : "";
            int nameCmp = String.CASE_INSENSITIVE_ORDER.compare(aName, bName);
            if (nameCmp != 0) {
                return nameCmp;
            }
            return a.getPlayerId().toString().compareTo(b.getPlayerId().toString());
        });
        return players;
    }
}

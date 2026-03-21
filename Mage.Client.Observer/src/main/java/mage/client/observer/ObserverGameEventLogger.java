package mage.client.observer;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import mage.cards.Card;
import mage.view.CardView;
import mage.view.CombatGroupView;
import mage.view.CommandObjectView;
import mage.view.CommanderView;
import mage.view.CounterView;
import mage.view.ExileView;
import mage.view.GameView;
import mage.view.LookedAtView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.RevealedView;
import mage.view.SimpleCardView;
import mage.view.StackAbilityView;
import org.apache.log4j.Logger;

import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.file.Path;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

final class ObserverGameEventLogger {

    private static final Logger logger = Logger.getLogger(ObserverGameEventLogger.class);
    private static final ZoneId LOG_TZ = ZoneId.of("America/Los_Angeles");
    private static final DateTimeFormatter LOG_TS_FMT = DateTimeFormatter.ISO_OFFSET_DATE_TIME;

    private PrintWriter gameEventWriter;
    private int gameEventSeq = 0;
    private int lastServerGameSeq = 0;
    private String lastSnapshotKey = "";

    void init(Path gameDirPath) {
        if (gameEventWriter != null || gameDirPath == null) {
            return;
        }
        try {
            gameEventWriter = new PrintWriter(new FileWriter(gameDirPath.resolve("game_events.jsonl").toString(), true));
        } catch (IOException e) {
            logger.warn("Failed to open game_events.jsonl", e);
        }
    }

    void writeStateSnapshotIfChanged(GameView game, RoundTracker roundTracker, Map<String, Card> loadedCards) {
        if (gameEventWriter == null || game == null) {
            return;
        }
        Map<String, mage.view.SimpleCardsView> watchedHands = game.getWatchedHands();
        if (watchedHands == null || watchedHands.isEmpty()) {
            return;
        }

        var keyBuilder = new StringBuilder();
        keyBuilder.append(roundTracker.getGameRound()).append("|");
        keyBuilder.append(game.getPhase()).append("|");
        keyBuilder.append(game.getStep()).append("|");
        for (PlayerView p : game.getPlayers()) {
            keyBuilder.append(p.getName()).append(":").append(p.getLife()).append(":")
                    .append(p.getHandCount()).append(":")
                    .append(p.getBattlefield() != null ? p.getBattlefield().size() : 0).append(":")
                    .append(p.getGraveyard() != null ? p.getGraveyard().size() : 0).append(":")
                    .append(p.getExile() != null ? p.getExile().size() : 0).append(":")
                    .append(p.isMonarch() ? "M" : "").append(":")
                    .append(p.isInitiative() ? "I" : "").append(",");
            ManaPoolView mp = p.getManaPool();
            if (mp != null) {
                keyBuilder.append("mp:").append(mp.getWhite()).append(mp.getBlue())
                        .append(mp.getBlack()).append(mp.getRed())
                        .append(mp.getGreen()).append(mp.getColorless()).append(",");
            }
        }
        if (game.getCombat() != null) {
            keyBuilder.append("combat:");
            for (CombatGroupView group : game.getCombat()) {
                for (CardView attacker : group.getAttackers().values()) {
                    keyBuilder.append(safe(attacker.getDisplayName())).append(">");
                }
                for (CardView blocker : group.getBlockers().values()) {
                    keyBuilder.append(safe(blocker.getDisplayName())).append("<");
                }
                keyBuilder.append(group.isBlocked() ? "B" : "U").append(",");
            }
        }
        if (game.getStack() != null) {
            keyBuilder.append("stack:").append(game.getStack().size()).append(",");
        }
        if (game.getRevealed() != null) {
            keyBuilder.append("rev:").append(game.getRevealed().size()).append(",");
        }
        String key = keyBuilder.toString();
        if (key.equals(lastSnapshotKey)) {
            return;
        }
        lastSnapshotKey = key;

        lastServerGameSeq = game.getGameSeq();

        var event = new JsonObject();
        event.addProperty("turn", roundTracker.getGameRound());
        event.addProperty("phase", game.getPhase() != null ? game.getPhase().name() : "");
        event.addProperty("step", game.getStep() != null ? game.getStep().name() : "");
        event.addProperty("active_player", safe(game.getActivePlayerName()));
        event.addProperty("priority_player", safe(game.getPriorityPlayerName()));

        var playersArray = new JsonArray();

        var uuidToShortId = new HashMap<UUID, String>();
        for (PlayerView player : game.getPlayers()) {
            if (player.getBattlefield() != null) {
                for (PermanentView permanent : player.getBattlefield().values()) {
                    if (permanent.getShortId() != null) {
                        uuidToShortId.put(permanent.getId(), permanent.getShortId());
                    }
                }
            }
        }

        for (PlayerView player : game.getPlayers()) {
            var playerJson = new JsonObject();
            playerJson.addProperty("name", safe(player.getName()));
            playerJson.addProperty("life", player.getLife());
            playerJson.addProperty("library_count", player.getLibraryCount());
            playerJson.addProperty("hand_count", player.getHandCount());
            playerJson.addProperty("is_active", player.isActive());
            playerJson.addProperty("has_left", player.hasLeft());
            if (player.isMonarch()) {
                playerJson.addProperty("monarch", true);
            }
            if (player.isInitiative()) {
                playerJson.addProperty("initiative", true);
            }
            playerJson.add("counters", countersToJson(player));

            ManaPoolView manaPool = player.getManaPool();
            if (manaPool != null) {
                var manaJson = new JsonObject();
                if (manaPool.getWhite() > 0) {
                    manaJson.addProperty("W", manaPool.getWhite());
                }
                if (manaPool.getBlue() > 0) {
                    manaJson.addProperty("U", manaPool.getBlue());
                }
                if (manaPool.getBlack() > 0) {
                    manaJson.addProperty("B", manaPool.getBlack());
                }
                if (manaPool.getRed() > 0) {
                    manaJson.addProperty("R", manaPool.getRed());
                }
                if (manaPool.getGreen() > 0) {
                    manaJson.addProperty("G", manaPool.getGreen());
                }
                if (manaPool.getColorless() > 0) {
                    manaJson.addProperty("C", manaPool.getColorless());
                }
                if (manaJson.size() > 0) {
                    playerJson.add("mana_pool", manaJson);
                }
            }

            List<String> designations = player.getDesignationNames();
            if (designations != null && !designations.isEmpty()) {
                var desArr = new JsonArray();
                for (String designation : designations) {
                    desArr.add(designation);
                }
                playerJson.add("designations", desArr);
            }

            var bfArray = new JsonArray();
            if (player.getBattlefield() != null) {
                for (PermanentView perm : player.getBattlefield().values()) {
                    var permJson = new JsonObject();
                    permJson.addProperty("id", Objects.requireNonNull(
                            perm.getShortId(),
                            "battlefield permanent missing shortId: " + perm.getDisplayName()
                    ));
                    permJson.addProperty("name", safe(perm.getDisplayName()));
                    permJson.addProperty("tapped", perm.isTapped());
                    permJson.addProperty("typeLine", formatTypeLine(perm));
                    if (perm.isCreature()) {
                        permJson.addProperty("power", safe(perm.getPower()));
                        permJson.addProperty("toughness", safe(perm.getToughness()));
                        if (perm.hasSummoningSickness()) {
                            permJson.addProperty("summoning_sick", true);
                        }
                    }
                    if (perm.getCounters() != null && !perm.getCounters().isEmpty()) {
                        var counters = new JsonObject();
                        for (CounterView counter : perm.getCounters()) {
                            counters.addProperty(counter.getName(), counter.getCount());
                        }
                        permJson.add("counters", counters);
                    }
                    if (perm.isToken()) {
                        permJson.addProperty("token", true);
                    }
                    if (perm.isCopy()) {
                        permJson.addProperty("copy", true);
                    }

                    boolean modified = false;
                    CardView orig = perm.getOriginal();
                    if (orig != null) {
                        modified = !Objects.equals(perm.getRules(), orig.getRules());
                    }

                    if (perm.isToken() || modified) {
                        List<String> rules = perm.getRules();
                        if (rules != null && !rules.isEmpty()) {
                            var rulesArr = new JsonArray();
                            for (String rule : rules) {
                                rulesArr.add(stripHtml(rule));
                            }
                            permJson.add("rules", rulesArr);
                        }
                    }

                    String altName = perm.getAlternateName();
                    if (altName != null && !altName.isEmpty()) {
                        permJson.addProperty("original_card", altName);
                    }
                    if (perm.isTransformed()) {
                        permJson.addProperty("back_face", true);
                    }
                    if (perm.isFaceDown()) {
                        permJson.addProperty("face_down", true);
                    }
                    if (perm.getAttachedTo() != null) {
                        String targetShortId = uuidToShortId.get(perm.getAttachedTo());
                        if (targetShortId != null) {
                            permJson.addProperty("attachedTo", targetShortId);
                        }
                    }
                    bfArray.add(permJson);
                }
            }
            playerJson.add("battlefield", bfArray);

            var cmdArray = new JsonArray();
            if (player.getCommandObjectList() != null) {
                for (CommandObjectView cmd : player.getCommandObjectList()) {
                    var cmdJson = new JsonObject();
                    cmdJson.addProperty("name", safe(cmd.getName()));
                    if (cmd instanceof CommanderView cv) {
                        cmdJson.addProperty("type", "commander");
                        if (cv.getShortId() != null) {
                            cmdJson.addProperty("id", cv.getShortId());
                        }
                    } else {
                        cmdJson.addProperty("type",
                                cmd.getClass().getSimpleName().replace("View", "").toLowerCase(Locale.ROOT));
                    }
                    List<String> rules = cmd.getRules();
                    if (rules != null && !rules.isEmpty()) {
                        var rulesArr = new JsonArray();
                        for (String rule : rules) {
                            rulesArr.add(stripHtml(rule));
                        }
                        cmdJson.add("rules", rulesArr);
                    }
                    cmdArray.add(cmdJson);
                }
            }
            playerJson.add("command_zone", cmdArray);

            CardView topCard = player.getTopCard();
            if (topCard != null) {
                var topJson = new JsonObject();
                if (topCard.getShortId() != null) {
                    topJson.addProperty("id", topCard.getShortId());
                }
                topJson.addProperty("name", safe(topCard.getDisplayName()));
                playerJson.add("top_card", topJson);
            }

            var gyArray = new JsonArray();
            if (player.getGraveyard() != null) {
                for (CardView card : player.getGraveyard().values()) {
                    var gyCard = new JsonObject();
                    gyCard.addProperty("id", Objects.requireNonNull(
                            card.getShortId(),
                            "graveyard card missing shortId: " + card.getDisplayName()
                    ));
                    gyCard.addProperty("name", safe(card.getDisplayName()));
                    gyArray.add(gyCard);
                }
            }
            playerJson.add("graveyard", gyArray);

            var exileArray = new JsonArray();
            if (player.getExile() != null) {
                for (CardView card : player.getExile().values()) {
                    var exCard = new JsonObject();
                    exCard.addProperty("id", Objects.requireNonNull(
                            card.getShortId(),
                            "exile card missing shortId: " + card.getDisplayName()
                    ));
                    exCard.addProperty("name", safe(card.getDisplayName()));
                    exileArray.add(exCard);
                }
            }
            playerJson.add("exile", exileArray);

            var handArray = new JsonArray();
            var handCards = ObserverHandManager.getHandCardsForPlayer(player, game, loadedCards);
            if (handCards != null) {
                for (CardView card : handCards.values()) {
                    var cardJson = new JsonObject();
                    cardJson.addProperty("id", Objects.requireNonNull(
                            card.getShortId(),
                            "hand card missing shortId: " + card.getDisplayName()
                    ));
                    cardJson.addProperty("name", safe(card.getDisplayName()));
                    cardJson.addProperty("mana_cost", safe(card.getManaCostStr()));
                    handArray.add(cardJson);
                }
            }
            playerJson.add("hand", handArray);

            playersArray.add(playerJson);
        }
        event.add("players", playersArray);

        var stackArray = new JsonArray();
        if (game.getStack() != null) {
            for (CardView card : game.getStack().values()) {
                var stackJson = new JsonObject();
                stackJson.addProperty("id", Objects.requireNonNull(
                        card.getShortId(),
                        "stack card missing shortId: " + stackCardName(card)
                ));
                stackJson.addProperty("name", stackCardName(card));
                if (card instanceof StackAbilityView sav) {
                    CardView source = sav.getSourceCard();
                    if (source != null) {
                        String srcName = source.getDisplayName();
                        if (srcName != null && !srcName.isEmpty()) {
                            stackJson.addProperty("source_card", srcName);
                        }
                    }
                    if (card.getRules() != null && !card.getRules().isEmpty()) {
                        stackJson.addProperty("ability_text", safe(card.getRules().get(0)));
                    }
                }
                if (card.getControllerId() != null) {
                    String owner = game.getPlayerName(card.getControllerId());
                    if (owner != null) {
                        stackJson.addProperty("owner", owner);
                    }
                }
                if (card.getTargets() != null && !card.getTargets().isEmpty()) {
                    var targetsArray = new JsonArray();
                    for (UUID targetId : card.getTargets()) {
                        targetsArray.add(resolveTargetName(targetId, game));
                    }
                    stackJson.add("targets", targetsArray);
                }
                stackArray.add(stackJson);
            }
        }
        event.add("stack", stackArray);

        if (game.getCombat() != null && !game.getCombat().isEmpty()) {
            var combatArray = new JsonArray();
            for (CombatGroupView group : game.getCombat()) {
                var groupJson = new JsonObject();
                var attackersArr = new JsonArray();
                for (CardView attacker : group.getAttackers().values()) {
                    var attackerJson = new JsonObject();
                    if (attacker.getShortId() != null) {
                        attackerJson.addProperty("id", attacker.getShortId());
                    }
                    attackerJson.addProperty("name", safe(attacker.getDisplayName()));
                    if (attacker.getPower() != null) {
                        attackerJson.addProperty("power", safe(attacker.getPower()));
                        attackerJson.addProperty("toughness", safe(attacker.getToughness()));
                    }
                    attackersArr.add(attackerJson);
                }
                groupJson.add("attackers", attackersArr);
                var blockersArr = new JsonArray();
                for (CardView blocker : group.getBlockers().values()) {
                    var blockerJson = new JsonObject();
                    if (blocker.getShortId() != null) {
                        blockerJson.addProperty("id", blocker.getShortId());
                    }
                    blockerJson.addProperty("name", safe(blocker.getDisplayName()));
                    if (blocker.getPower() != null) {
                        blockerJson.addProperty("power", safe(blocker.getPower()));
                        blockerJson.addProperty("toughness", safe(blocker.getToughness()));
                    }
                    blockersArr.add(blockerJson);
                }
                if (blockersArr.size() > 0) {
                    groupJson.add("blockers", blockersArr);
                }
                groupJson.addProperty("blocked", group.isBlocked());
                groupJson.addProperty("defending", group.getDefenderName());
                combatArray.add(groupJson);
            }
            event.add("combat", combatArray);
        }

        if (game.getRevealed() != null && !game.getRevealed().isEmpty()) {
            var revealedArray = new JsonArray();
            for (RevealedView revealedView : game.getRevealed()) {
                var rvJson = new JsonObject();
                rvJson.addProperty("name", safe(revealedView.getName()));
                var cardsArr = new JsonArray();
                for (CardView card : revealedView.getCards().values()) {
                    var cardJson = new JsonObject();
                    if (card.getShortId() != null) {
                        cardJson.addProperty("id", card.getShortId());
                    }
                    cardJson.addProperty("name", safe(card.getDisplayName()));
                    cardsArr.add(cardJson);
                }
                rvJson.add("cards", cardsArr);
                revealedArray.add(rvJson);
            }
            event.add("revealed", revealedArray);
        }

        if (game.getCompanion() != null && !game.getCompanion().isEmpty()) {
            var companionArray = new JsonArray();
            for (RevealedView companionView : game.getCompanion()) {
                var rvJson = new JsonObject();
                rvJson.addProperty("name", safe(companionView.getName()));
                var cardsArr = new JsonArray();
                for (CardView card : companionView.getCards().values()) {
                    var cardJson = new JsonObject();
                    if (card.getShortId() != null) {
                        cardJson.addProperty("id", card.getShortId());
                    }
                    cardJson.addProperty("name", safe(card.getDisplayName()));
                    cardsArr.add(cardJson);
                }
                rvJson.add("cards", cardsArr);
                companionArray.add(rvJson);
            }
            event.add("companion", companionArray);
        }

        if (game.getLookedAt() != null && !game.getLookedAt().isEmpty()) {
            var lookedAtArray = new JsonArray();
            for (LookedAtView lookedAtView : game.getLookedAt()) {
                var lvJson = new JsonObject();
                lvJson.addProperty("name", safe(lookedAtView.getName()));
                var cardsArr = new JsonArray();
                for (SimpleCardView simpleCard : lookedAtView.getCards().values()) {
                    var cardJson = new JsonObject();
                    if (simpleCard.getShortId() != null) {
                        cardJson.addProperty("id", simpleCard.getShortId());
                    }
                    cardsArr.add(cardJson);
                }
                lvJson.add("cards", cardsArr);
                lookedAtArray.add(lvJson);
            }
            event.add("looked_at", lookedAtArray);
        }

        if (game.getExile() != null && !game.getExile().isEmpty()) {
            var exileZonesArray = new JsonArray();
            for (ExileView exileView : game.getExile()) {
                var exileJson = new JsonObject();
                exileJson.addProperty("zone_name", safe(exileView.getName()));
                var cardsArr = new JsonArray();
                for (CardView card : exileView.values()) {
                    var cardJson = new JsonObject();
                    if (card.getShortId() != null) {
                        cardJson.addProperty("id", card.getShortId());
                    }
                    cardJson.addProperty("name", safe(card.getDisplayName()));
                    cardsArr.add(cardJson);
                }
                exileJson.add("cards", cardsArr);
                exileZonesArray.add(exileJson);
            }
            event.add("exile_zones", exileZonesArray);
        }

        if (game.getMyHelperEmblems() != null && !game.getMyHelperEmblems().isEmpty()) {
            var emblemsArray = new JsonArray();
            for (CardView card : game.getMyHelperEmblems().values()) {
                var emblemJson = new JsonObject();
                if (card.getShortId() != null) {
                    emblemJson.addProperty("id", card.getShortId());
                }
                emblemJson.addProperty("name", safe(card.getDisplayName()));
                List<String> rules = card.getRules();
                if (rules != null && !rules.isEmpty()) {
                    var rulesArr = new JsonArray();
                    for (String rule : rules) {
                        rulesArr.add(stripHtml(rule));
                    }
                    emblemJson.add("rules", rulesArr);
                }
                emblemsArray.add(emblemJson);
            }
            event.add("helper_emblems", emblemsArray);
        }

        writeGameEvent("state_snapshot", event);
    }

    void logChatEvent(String type, String message, String username) {
        var event = new JsonObject();
        if ("player_chat".equals(type)) {
            event.addProperty("from", username != null ? username : "");
        }
        event.addProperty("message", message != null ? message : "");
        writeGameEvent(type, event);
    }

    void logGameOver(String message) {
        if (gameEventWriter == null) {
            return;
        }
        var event = new JsonObject();
        event.addProperty("message", message != null ? message : "");
        writeGameEvent("game_over", event);
        gameEventWriter.close();
        gameEventWriter = null;
    }

    private void writeGameEvent(String type, JsonObject data) {
        if (gameEventWriter == null) {
            return;
        }
        gameEventSeq++;
        data.addProperty("ts", ZonedDateTime.now(LOG_TZ).format(LOG_TS_FMT));
        data.addProperty("seq", gameEventSeq);
        if (lastServerGameSeq > 0) {
            data.addProperty("game_seq", lastServerGameSeq);
        }
        data.addProperty("type", type);
        gameEventWriter.println(data.toString());
        gameEventWriter.flush();
    }

    private JsonArray countersToJson(PlayerView player) {
        var counters = new JsonArray();
        for (CounterView counter : player.getCounters()) {
            var counterJson = new JsonObject();
            counterJson.addProperty("name", safe(counter.getName()));
            counterJson.addProperty("count", counter.getCount());
            counters.add(counterJson);
        }
        return counters;
    }

    private static String formatTypeLine(CardView card) {
        var sb = new StringBuilder();

        if (card.getSuperTypes() != null && !card.getSuperTypes().isEmpty()) {
            for (Object superType : card.getSuperTypes()) {
                if (sb.length() > 0) {
                    sb.append(' ');
                }
                sb.append(superType.toString());
            }
        }

        if (card.getCardTypes() != null && !card.getCardTypes().isEmpty()) {
            for (Object cardType : card.getCardTypes()) {
                if (sb.length() > 0) {
                    sb.append(' ');
                }
                sb.append(cardType.toString());
            }
        }

        String subTypes = card.getSubTypes() == null ? "" : card.getSubTypes().toString();
        if (!subTypes.isEmpty()) {
            if (sb.length() > 0) {
                sb.append(" - ");
            }
            sb.append(subTypes);
        }

        return sb.toString();
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static String stripHtml(String value) {
        if (value == null || value.isEmpty()) {
            return value;
        }
        value = value.replaceAll("(?i)<br\\s*/?>", ": ");
        value = value.replaceAll("<[^>]*>", "");
        return value;
    }

    private static String stackCardName(CardView card) {
        String name = card.getDisplayName();
        if ((name == null || name.isEmpty()) && card instanceof StackAbilityView sav) {
            CardView source = sav.getSourceCard();
            if (source != null) {
                name = source.getDisplayName();
            }
        }
        if (name == null || name.isEmpty()) {
            name = card.getName();
        }
        return safe(name);
    }

    private static String resolveTargetName(UUID targetId, GameView game) {
        if (game == null || targetId == null || game.getPlayers() == null) {
            return "Unknown";
        }

        if (game.getStack() != null) {
            CardView found = game.getStack().get(targetId);
            if (found != null) {
                return safe(found.getDisplayName());
            }
        }

        for (PlayerView player : game.getPlayers()) {
            if (player.getBattlefield() != null) {
                PermanentView perm = player.getBattlefield().get(targetId);
                if (perm != null) {
                    return safe(perm.getDisplayName());
                }
            }

            CardView found = null;
            if (player.getGraveyard() != null) {
                found = player.getGraveyard().get(targetId);
                if (found != null) {
                    return safe(found.getDisplayName());
                }
            }

            if (player.getExile() != null) {
                found = player.getExile().get(targetId);
                if (found != null) {
                    return safe(found.getDisplayName());
                }
            }
        }

        for (PlayerView player : game.getPlayers()) {
            if (player.getPlayerId().equals(targetId)) {
                return player.getName();
            }
        }

        if (game.getExile() != null) {
            for (ExileView exileZone : game.getExile()) {
                for (CardView card : exileZone.values()) {
                    if (card.getId().equals(targetId)) {
                        return safe(card.getDisplayName());
                    }
                }
            }
        }

        return "Unknown";
    }
}

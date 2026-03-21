package mage.client.bridge;

import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.StackAbilityView;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.function.Supplier;

final class BridgeCardFormatter {

    private final BridgeViewLocator viewLocator;
    private final Supplier<UUID> currentGameIdSupplier;
    private final Function<UUID, UUID> playerIdForGame;

    BridgeCardFormatter(
            BridgeViewLocator viewLocator,
            Supplier<UUID> currentGameIdSupplier,
            Function<UUID, UUID> playerIdForGame
    ) {
        this.viewLocator = viewLocator;
        this.currentGameIdSupplier = currentGameIdSupplier;
        this.playerIdForGame = playerIdForGame;
    }

    String safeDisplayName(CardView cv) {
        if (cv instanceof StackAbilityView sav) {
            CardView sourceCard = sav.getSourceCard();
            if (sourceCard != null) {
                String sourceName = sourceCard.getDisplayName();
                if (sourceName == null || sourceName.isEmpty()) {
                    sourceName = sourceCard.getName();
                }
                if (sourceName != null && !sourceName.isEmpty()) {
                    return sourceName;
                }
            }
        }
        String name = cv.getDisplayName();
        if (name == null || name.isEmpty()) {
            name = cv.getName() != null ? cv.getName() : "Unknown";
        }
        return name;
    }

    Map<String, Object> buildCardInfoMap(CardView cv) {
        var info = new HashMap<String, Object>();
        info.put("name", safeDisplayName(cv));
        String manaCost = cv.getManaCostStr();
        if (manaCost != null && !manaCost.isEmpty()) {
            info.put("mana_cost", manaCost);
        }
        if (cv.isLand()) {
            info.put("is_land", true);
        }
        if (cv.isCreature() && cv.getPower() != null) {
            info.put("power", cv.getPower());
            info.put("toughness", cv.getToughness());
        }
        List<String> rules = BridgePromptFormatting.stripHtmlList(cv.getRules());
        if (rules != null && !rules.isEmpty()) {
            info.put("rules", rules);
        }
        return info;
    }

    String buildCardDescription(CardView cv) {
        String displayName = cv.getDisplayName();
        if (displayName == null) {
            displayName = cv.getName() != null ? cv.getName() : "Unknown";
        }
        var sb = new StringBuilder(displayName);
        if (cv instanceof PermanentView pv) {
            if (pv.isCreature() && cv.getPower() != null && cv.getToughness() != null) {
                sb.append(" (").append(cv.getPower()).append("/").append(cv.getToughness()).append(")");
            }
            if (pv.isTapped()) {
                sb.append(" [tapped]");
            }
        }
        return sb.toString();
    }

    String describeTarget(UUID targetId, CardsView cardsView, GameView gameView) {
        GameView view = gameView;
        if (cardsView != null) {
            CardView cv = cardsView.get(targetId);
            if (cv != null) {
                return buildCardDescription(cv) + controllerSuffix(targetId, view);
            }
        }
        CardView cv = viewLocator.findCardViewById(targetId, view);
        if (cv != null) {
            return buildCardDescription(cv) + controllerSuffix(targetId, view);
        }
        if (view != null) {
            UUID myPlayerId = playerIdForGame.apply(currentGameIdSupplier.get());
            for (PlayerView player : view.getPlayers()) {
                if (player.getPlayerId().equals(targetId)) {
                    String desc = player.getName();
                    if (player.getPlayerId().equals(myPlayerId)) {
                        desc += " (you)";
                    }
                    return desc;
                }
            }
        }
        return "Unknown (" + targetId.toString().substring(0, 8) + ")";
    }

    CardView buildTargetInfo(
            Map<String, Object> entry,
            UUID targetId,
            CardsView cardsView,
            GameView gameView,
            UUID myPlayerId
    ) {
        CardView cv = null;
        if (cardsView != null) {
            cv = cardsView.get(targetId);
        }
        if (cv == null) {
            cv = viewLocator.findCardViewById(targetId, gameView);
        }
        if (cv != null) {
            entry.put("name", safeDisplayName(cv));
            if (cv instanceof PermanentView pv) {
                entry.put("target_type", "permanent");
                if (pv.isCreature() && cv.getPower() != null) {
                    entry.put("power", cv.getPower());
                    entry.put("toughness", cv.getToughness());
                }
                if (pv.isTapped()) {
                    entry.put("tapped", true);
                }
            } else {
                entry.put("target_type", "card");
            }
            if (gameView != null) {
                for (PlayerView player : gameView.getPlayers()) {
                    if (player.getBattlefield().get(targetId) != null) {
                        if (!player.getPlayerId().equals(myPlayerId)) {
                            entry.put("controller", player.getName());
                        }
                        break;
                    }
                }
            }
            return cv;
        }
        if (gameView != null) {
            for (PlayerView player : gameView.getPlayers()) {
                if (player.getPlayerId().equals(targetId)) {
                    entry.put("name", player.getName());
                    entry.put("target_type", "player");
                    if (player.getPlayerId().equals(myPlayerId)) {
                        entry.put("is_you", true);
                    }
                    return null;
                }
            }
        }
        entry.put("name", "Unknown (" + targetId.toString().substring(0, 8) + ")");
        entry.put("target_type", "card");
        return null;
    }

    List<Map<String, Object>> buildStackItems(GameView gameView, boolean includeIds, boolean includeRules) {
        var stack = new ArrayList<Map<String, Object>>();
        if (gameView == null || gameView.getStack() == null || gameView.getStack().isEmpty()) {
            return stack;
        }
        for (CardView card : gameView.getStack().values()) {
            stack.add(buildStackItem(card, gameView, includeIds, includeRules));
        }
        return stack;
    }

    Map<String, Object> buildStackItem(CardView card, GameView gameView, boolean includeId, boolean includeRules) {
        var item = new HashMap<String, Object>();
        if (includeId && card.getId() != null) {
            item.put("id", viewLocator.getStableShortId(card.getId(), card));
        }
        item.put("name", safeDisplayName(card));
        addStackAbilityContext(item, card);
        if (includeRules) {
            item.put("rules", BridgePromptFormatting.stripHtmlList(card.getRules()));
        }
        if (card.getControllerId() != null && gameView != null) {
            String owner = gameView.getPlayerName(card.getControllerId());
            if (owner != null) {
                item.put("owner", owner);
            }
        }
        if (card.getTargets() != null && !card.getTargets().isEmpty()) {
            var targets = new ArrayList<Map<String, Object>>();
            for (UUID targetId : card.getTargets()) {
                var target = new HashMap<String, Object>();
                target.put("id", viewLocator.getStableShortId(targetId, viewLocator.findCardViewById(targetId, gameView)));
                target.put("name", describeTarget(targetId, null, gameView));
                targets.add(target);
            }
            item.put("targets", targets);
        }
        return item;
    }

    private void addStackAbilityContext(Map<String, Object> item, CardView card) {
        if (!(card instanceof StackAbilityView sav)) {
            return;
        }
        CardView sourceCard = sav.getSourceCard();
        if (sourceCard != null) {
            item.put("source_card", safeDisplayName(sourceCard));
        }
        List<String> rules = BridgePromptFormatting.stripHtmlList(card.getRules());
        if (rules != null && !rules.isEmpty()) {
            item.put("ability_text", rules.get(0));
        }
    }

    private String controllerSuffix(UUID objectId, GameView gameView) {
        if (gameView == null) {
            return "";
        }
        UUID myPlayerId = playerIdForGame.apply(currentGameIdSupplier.get());
        for (PlayerView player : gameView.getPlayers()) {
            if (player.getBattlefield().get(objectId) != null) {
                if (player.getPlayerId().equals(myPlayerId)) {
                    return " (yours)";
                }
                return " (" + player.getName() + "'s)";
            }
        }
        return "";
    }
}

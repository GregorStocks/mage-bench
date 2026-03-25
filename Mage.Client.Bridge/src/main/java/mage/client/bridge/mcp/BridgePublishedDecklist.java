package mage.client.bridge.mcp;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class BridgePublishedDecklist {
    private static final Map<String, Object> EMPTY = Map.of("error", "No deck loaded");

    private BridgePublishedDecklist() {
    }

    public static Map<String, Object> empty() {
        return EMPTY;
    }

    public static Map<String, Object> from(DeckCardLists deck) {
        if (deck == null) {
            return EMPTY;
        }

        var result = new HashMap<String, Object>();
        result.put("cards", renderDeckSection(deck.getCards()));
        if (!deck.getSideboard().isEmpty()) {
            result.put("sideboard", renderDeckSection(deck.getSideboard()));
        }
        return Map.copyOf(result);
    }

    private static String renderDeckSection(List<DeckCardInfo> cards) {
        var sb = new StringBuilder();
        for (DeckCardInfo card : cards) {
            if (sb.length() > 0) {
                sb.append("\n");
            }
            sb.append(card.getAmount()).append("x ").append(card.getCardName());
        }
        return sb.toString();
    }
}

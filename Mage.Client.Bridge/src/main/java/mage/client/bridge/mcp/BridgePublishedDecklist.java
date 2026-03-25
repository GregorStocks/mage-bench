package mage.client.bridge.mcp;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.constants.SubType;
import mage.constants.SubTypeSet;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class BridgePublishedDecklist {
    private static final BridgePublishedDecklistSnapshot EMPTY_SNAPSHOT =
        new BridgePublishedDecklistSnapshot(Map.of("error", "No deck loaded"), Set.of());

    private BridgePublishedDecklist() {
    }

    public static BridgePublishedDecklistSnapshot emptySnapshot() {
        return EMPTY_SNAPSHOT;
    }

    public static BridgePublishedDecklistSnapshot snapshot(DeckCardLists deck) {
        if (deck == null) {
            return EMPTY_SNAPSHOT;
        }
        return new BridgePublishedDecklistSnapshot(
            buildResponseMap(deck),
            computeCreatureTypes(deck)
        );
    }

    private static Map<String, Object> buildResponseMap(DeckCardLists deck) {
        var result = new HashMap<String, Object>();
        result.put("cards", renderDeckSection(deck.getCards()));
        if (!deck.getSideboard().isEmpty()) {
            result.put("sideboard", renderDeckSection(deck.getSideboard()));
        }
        return Map.copyOf(result);
    }

    private static Set<String> computeCreatureTypes(DeckCardLists deck) {
        var types = new HashSet<String>();
        for (DeckCardInfo card : deck.getCards()) {
            CardInfo info = CardRepository.instance.findCard(card.getCardName());
            if (info != null) {
                for (SubType subType : info.getSubTypes()) {
                    if (subType.getSubTypeSet() == SubTypeSet.CreatureType) {
                        types.add(subType.toString());
                    }
                }
            }
        }
        return Set.copyOf(types);
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

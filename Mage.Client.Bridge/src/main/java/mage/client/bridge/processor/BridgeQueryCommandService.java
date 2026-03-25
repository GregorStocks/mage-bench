package mage.client.bridge.processor;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.client.bridge.tools.GetOracleTextTool;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

public final class BridgeQueryCommandService {
    private final Supplier<DeckCardLists> deckListSupplier;
    private final BridgeProcessorServices processorServices;

    public BridgeQueryCommandService(
            Supplier<DeckCardLists> deckListSupplier,
            BridgeProcessorServices processorServices) {
        this.deckListSupplier = deckListSupplier;
        this.processorServices = processorServices;
    }

    public Map<String, Object> getMyDecklist() {
        var result = new HashMap<String, Object>();
        DeckCardLists deck = deckListSupplier.get();
        if (deck == null) {
            result.put("error", "No deck loaded");
            return result;
        }

        result.put("cards", renderDeckSection(deck.getCards()));
        if (!deck.getSideboard().isEmpty()) {
            result.put("sideboard", renderDeckSection(deck.getSideboard()));
        }
        return result;
    }

    public GetOracleTextTool.Result getOracleText(
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds) {
        return processorServices.getOracleText(cardName, objectId, cardNames, objectIds);
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

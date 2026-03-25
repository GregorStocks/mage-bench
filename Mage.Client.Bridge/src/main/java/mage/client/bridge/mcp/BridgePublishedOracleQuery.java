package mage.client.bridge.mcp;

import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.client.bridge.BridgeOracleTextService;
import mage.client.bridge.processor.BridgePublishedOracleIndex;
import mage.client.bridge.tools.GetOracleTextTool;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

public final class BridgePublishedOracleQuery {
    private BridgePublishedOracleQuery() {
    }

    public static GetOracleTextTool.Result getOracleText(
            BridgePublishedOracleIndex oracleIndex,
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds) {
        var result = new GetOracleTextTool.Result();

        boolean hasCardName = cardName != null && !cardName.isEmpty();
        boolean hasObjectId = objectId != null && !objectId.isEmpty();
        boolean hasCardNames = cardNames != null && cardNames.length > 0;
        boolean hasObjectIds = objectIds != null && objectIds.length > 0;

        int providedCount = (hasCardName ? 1 : 0)
            + (hasObjectId ? 1 : 0)
            + (hasCardNames ? 1 : 0)
            + (hasObjectIds ? 1 : 0);
        if (providedCount != 1) {
            result.success = false;
            result.error = "Provide exactly one of: card_name, object_id, card_names, or object_ids";
            return result;
        }

        if (hasObjectIds) {
            var cards = new ArrayList<Map<String, Object>>();
            for (String oid : objectIds) {
                var entry = new HashMap<String, Object>();
                if (oid == null) {
                    entry.put("object_id", null);
                    entry.put("error", "null object_id");
                } else {
                    entry.put("object_id", oid);
                    Map<String, Object> card = oracleIndex.card(oid);
                    if (card != null) {
                        entry.putAll(card);
                    } else if (oracleIndex.knowsObjectId(oid)) {
                        entry.put("error", "not found");
                    } else {
                        entry.put("error", "unknown short ID: " + oid);
                    }
                }
                cards.add(Collections.unmodifiableMap(new LinkedHashMap<>(entry)));
            }
            result.success = true;
            result.cards = cards;
            return result;
        }

        if (hasCardNames) {
            var cards = new ArrayList<Map<String, Object>>();
            for (String name : cardNames) {
                var entry = new HashMap<String, Object>();
                entry.put("name", name);
                CardInfo cardInfo = CardRepository.instance.findCard(name);
                if (cardInfo != null) {
                    entry.putAll(BridgeOracleTextService.buildCardFieldsMap(cardInfo));
                } else {
                    entry.put("error", "not found");
                }
                cards.add(Collections.unmodifiableMap(new LinkedHashMap<>(entry)));
            }
            result.success = true;
            result.cards = cards;
            return result;
        }

        if (hasObjectId) {
            Map<String, Object> card = oracleIndex.card(objectId);
            if (card != null) {
                populateResult(result, card);
                result.success = true;
                return result;
            }
            result.success = false;
            if (oracleIndex.knowsObjectId(objectId)) {
                result.error = "Object not found in current game state: " + objectId;
            } else {
                result.error = "Unknown short ID: " + objectId;
            }
            return result;
        }

        CardInfo cardInfo = CardRepository.instance.findCard(cardName);
        if (cardInfo != null) {
            populateResult(result, BridgeOracleTextService.buildCardFieldsMap(cardInfo));
            result.success = true;
            return result;
        }
        result.success = false;
        result.error = "Card not found in database: " + cardName;
        return result;
    }

    @SuppressWarnings("unchecked")
    private static void populateResult(GetOracleTextTool.Result result, Map<String, Object> fields) {
        result.name = (String) fields.get("name");
        result.mana_cost = (String) fields.get("mana_cost");
        result.type = (String) fields.get("type");
        result.rules = (java.util.List<String>) fields.get("rules");
        result.power = (String) fields.get("power");
        result.toughness = (String) fields.get("toughness");
        result.starting_loyalty = (String) fields.get("starting_loyalty");
        result.starting_defense = (String) fields.get("starting_defense");
        result.second_face = (Map<String, Object>) fields.get("second_face");
    }
}

package mage.client.bridge;

import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.constants.CardType;
import mage.constants.SubType;
import mage.constants.SuperType;
import mage.util.ShortIdRegistry;
import mage.view.CardView;
import mage.view.GameView;
import mage.view.StackAbilityView;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

public final class BridgeOracleTextService {

    private final ShortIdRegistry shortIds;
    private final BridgeViewLocator viewLocator;

    public BridgeOracleTextService(ShortIdRegistry shortIds, BridgeViewLocator viewLocator) {
        this.shortIds = shortIds;
        this.viewLocator = viewLocator;
    }

    public GetOracleTextTool.Result getOracleText(
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds,
            GameView gameView
    ) {
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
            var results = new ArrayList<Map<String, Object>>();
            for (String oid : objectIds) {
                var entry = new HashMap<String, Object>();
                if (oid == null) {
                    entry.put("object_id", null);
                    entry.put("error", "null object_id");
                } else {
                    entry.put("object_id", oid);
                    try {
                        UUID uuid = shortIds.resolve(oid);
                        CardView cardView = viewLocator.findCardViewById(uuid, gameView);
                        if (cardView != null) {
                            populateCardFields(entry, cardView);
                        } else {
                            entry.put("error", "not found");
                        }
                    } catch (IllegalArgumentException e) {
                        entry.put("error", "unknown short ID: " + oid);
                    }
                }
                results.add(entry);
            }
            result.success = true;
            result.cards = results;
            return result;
        }

        if (hasCardNames) {
            var results = new ArrayList<Map<String, Object>>();
            for (String name : cardNames) {
                var entry = new HashMap<String, Object>();
                entry.put("name", name);
                CardInfo cardInfo = CardRepository.instance.findCard(name);
                if (cardInfo != null) {
                    populateCardFields(entry, cardInfo);
                } else {
                    entry.put("error", "not found");
                }
                results.add(entry);
            }
            result.success = true;
            result.cards = results;
            return result;
        }

        if (hasObjectId) {
            try {
                UUID uuid = shortIds.resolve(objectId);
                CardView cardView = viewLocator.findCardViewById(uuid, gameView);
                if (cardView != null) {
                    result.success = true;
                    populateCardFields(result, cardView);
                    return result;
                }
                result.success = false;
                result.error = "Object not found in current game state: " + objectId;
                return result;
            } catch (IllegalArgumentException e) {
                result.success = false;
                result.error = "Unknown short ID: " + objectId;
                return result;
            }
        }

        CardInfo cardInfo = CardRepository.instance.findCard(cardName);
        if (cardInfo != null) {
            result.success = true;
            populateCardFields(result, cardInfo);
            return result;
        }
        result.success = false;
        result.error = "Card not found in database: " + cardName;
        return result;
    }

    public static Map<String, Object> buildCardFieldsMap(CardView cv) {
        var entry = new HashMap<String, Object>();
        extractOracleCardFields(cv).populate(entry);
        return Map.copyOf(entry);
    }

    public static Map<String, Object> buildCardFieldsMap(CardInfo ci) {
        var entry = new HashMap<String, Object>();
        extractOracleCardFields(ci, true).populate(entry);
        return Map.copyOf(entry);
    }

    void populateCardFields(Map<String, Object> entry, CardView cv) {
        extractOracleCardFields(cv).populate(entry);
    }

    void populateCardFields(Map<String, Object> entry, CardInfo ci) {
        extractOracleCardFields(ci, true).populate(entry);
    }

    void populateCardFields(GetOracleTextTool.Result result, CardView cv) {
        extractOracleCardFields(cv).populate(result);
    }

    void populateCardFields(GetOracleTextTool.Result result, CardInfo ci) {
        extractOracleCardFields(ci, true).populate(result);
    }

    private record OracleCardFields(
            String name,
            String manaCost,
            String type,
            List<String> rules,
            String power,
            String toughness,
            String startingLoyalty,
            String startingDefense,
            OracleCardFields secondFace
    ) {
        private void populate(Map<String, Object> entry) {
            entry.put("name", name);
            if (manaCost != null) {
                entry.put("mana_cost", manaCost);
            }
            if (type != null) {
                entry.put("type", type);
            }
            if (rules != null) {
                entry.put("rules", rules);
            }
            if (power != null) {
                entry.put("power", power);
            }
            if (toughness != null) {
                entry.put("toughness", toughness);
            }
            if (startingLoyalty != null) {
                entry.put("starting_loyalty", startingLoyalty);
            }
            if (startingDefense != null) {
                entry.put("starting_defense", startingDefense);
            }
            if (secondFace != null) {
                entry.put("second_face", secondFace.toMap());
            }
        }

        private Map<String, Object> toMap() {
            var face = new HashMap<String, Object>();
            populate(face);
            return face;
        }

        private void populate(GetOracleTextTool.Result result) {
            result.name = name;
            result.mana_cost = manaCost;
            result.type = type;
            result.rules = rules;
            result.power = power;
            result.toughness = toughness;
            result.starting_loyalty = startingLoyalty;
            result.starting_defense = startingDefense;
            result.second_face = secondFace != null ? secondFace.toMap() : null;
        }
    }

    private static OracleCardFields extractOracleCardFields(CardView cv) {
        CardView oracleCard = unwrapOracleCardView(cv);
        CardView secondFace = oracleCard.getSecondCardFace();
        return new OracleCardFields(
            requireOracleName(oracleCard),
            normalizeOptionalField(oracleCard.getManaCostStr()),
            normalizeOptionalType(oracleCard.getTypeText()),
            BridgePromptFormatting.stripHtmlList(oracleCard.getRules()),
            oracleCard.isCreature() && oracleCard.getPower() != null ? oracleCard.getPower() : null,
            oracleCard.isCreature() && oracleCard.getPower() != null ? oracleCard.getToughness() : null,
            oracleCard.isPlaneswalker() ? normalizeNonZeroField(oracleCard.getStartingLoyalty()) : null,
            oracleCard.isBattle() ? normalizeNonZeroField(oracleCard.getStartingDefense()) : null,
            secondFace != null ? extractOracleCardFields(secondFace) : null
        );
    }

    private static CardView unwrapOracleCardView(CardView cv) {
        if (cv instanceof StackAbilityView stackAbilityView && stackAbilityView.getSourceCard() != null) {
            return stackAbilityView.getSourceCard();
        }
        return cv;
    }

    private static String requireOracleName(CardView cv) {
        String name = cv.getDisplayName();
        if (name == null || name.isEmpty()) {
            name = cv.getName();
        }
        if (name == null || name.isEmpty()) {
            throw new IllegalStateException("Oracle card view missing name for object " + cv.getId());
        }
        return name;
    }

    private static OracleCardFields extractOracleCardFields(CardInfo ci, boolean includeSecondFace) {
        CardInfo secondFace = includeSecondFace ? findSecondFace(ci) : null;
        return new OracleCardFields(
            ci.getName(),
            joinManaCosts(ci.getManaCosts(CardInfo.ManaCostSide.ALL)),
            normalizeOptionalType(buildTypeLine(ci)),
            BridgePromptFormatting.stripHtmlList(ci.getRules()),
            ci.getTypes().contains(CardType.CREATURE) && ci.getPower() != null ? ci.getPower() : null,
            ci.getTypes().contains(CardType.CREATURE) && ci.getPower() != null ? ci.getToughness() : null,
            ci.getTypes().contains(CardType.PLANESWALKER) ? normalizeNonZeroField(ci.getStartingLoyalty()) : null,
            ci.getTypes().contains(CardType.BATTLE) ? normalizeNonZeroField(ci.getStartingDefense()) : null,
            secondFace != null ? extractOracleCardFields(secondFace, false) : null
        );
    }

    private static CardInfo findSecondFace(CardInfo ci) {
        String secondName = ci.getSecondSideName();
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getDoubleFacedSecondSideName();
        }
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getFlipCardName();
        }
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getSpellOptionCardName();
        }
        if (secondName == null || secondName.isEmpty()) {
            return null;
        }
        return CardRepository.instance.findCard(secondName);
    }

    private static String joinManaCosts(List<String> manaCosts) {
        if (manaCosts == null || manaCosts.isEmpty()) {
            return null;
        }
        return String.join("", manaCosts);
    }

    private static String normalizeOptionalField(String value) {
        if (value == null || value.isEmpty()) {
            return null;
        }
        return value;
    }

    private static String normalizeOptionalType(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private static String normalizeNonZeroField(String value) {
        if (value == null || value.isEmpty() || value.equals("0")) {
            return null;
        }
        return value;
    }

    private static String buildTypeLine(CardInfo ci) {
        StringBuilder sb = new StringBuilder();
        if (!ci.getSupertypes().isEmpty()) {
            sb.append(ci.getSupertypes().stream().map(SuperType::toString).collect(Collectors.joining(" ")));
            sb.append(" ");
        }
        if (!ci.getTypes().isEmpty()) {
            sb.append(ci.getTypes().stream().map(CardType::toString).collect(Collectors.joining(" ")));
        }
        if (!ci.getSubTypes().isEmpty()) {
            sb.append(" — ");
            sb.append(ci.getSubTypes().stream().map(SubType::toString).collect(Collectors.joining(" ")));
        }
        return sb.toString().trim();
    }
}

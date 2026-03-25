package mage.client.bridge;

import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.constants.CardType;
import mage.constants.SubType;
import mage.constants.SuperType;
import mage.view.CardView;
import mage.view.StackAbilityView;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public final class BridgeOracleTextService {

    private BridgeOracleTextService() {
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

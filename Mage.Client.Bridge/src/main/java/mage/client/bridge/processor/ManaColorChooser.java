package mage.client.bridge.processor;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Answers the engine's "Pick a mana color" prompt (any-colour sources such as
 * Command Tower / Arcane Signet) from the cost currently being paid, so the
 * auto-payer never hands a colour choice to the pilot or the human. A wrong
 * pick taps the source for unusable mana and the spell is cancelled.
 */
public final class ManaColorChooser {

    private static final Pattern PIP = Pattern.compile("\\{([WUBRGC])(?:/([WUBRGC]))?\\}");
    private static final Pattern ADDS = Pattern.compile("Add (.*)");
    private static final List<String> ALL = List.of("White", "Blue", "Black", "Red", "Green");

    private ManaColorChooser() {
    }

    static String colorName(char pip) {
        return switch (pip) {
            case 'W' -> "White";
            case 'U' -> "Blue";
            case 'B' -> "Black";
            case 'R' -> "Red";
            case 'G' -> "Green";
            default -> null;
        };
    }

    /** Colour names still needed by a "Pay {1}{U}{B}<div ...>" payment message, in cost order. */
    public static List<String> neededColors(String paymentText) {
        List<String> out = new ArrayList<>();
        if (paymentText == null) {
            return out;
        }
        int html = paymentText.indexOf('<');
        String cost = html >= 0 ? paymentText.substring(0, html) : paymentText;
        Matcher m = PIP.matcher(cost);
        while (m.find()) {
            for (int g = 1; g <= 2; g++) {
                String pip = m.group(g);
                if (pip != null) {
                    String name = colorName(pip.charAt(0));
                    if (name != null && !out.contains(name)) {
                        out.add(name);
                    }
                }
            }
        }
        return out;
    }

    /**
     * The colour to answer with, or null to leave the decision to the pilot/human:
     * the first still-needed colour that the source offers; if the remaining cost
     * has no coloured pips (generic only), any offered colour; if only one colour
     * is offered, that one.
     */
    public static String pick(String paymentText, Collection<String> offered) {
        return pick(paymentText, offered, Set.of());
    }

    /** Colours the OTHER untapped sources can still produce, from their mana ability texts
     *  ("{T}: Add {U}.", "{T}: Add {U} or {B}.", "Add one mana of any color ..."). */
    public static Set<String> producible(Collection<String> manaAbilityTexts) {
        Set<String> out = new HashSet<>();
        for (String text : manaAbilityTexts) {
            if (text == null) {
                continue;
            }
            String lower = text.toLowerCase();
            if (lower.contains("any color") || lower.contains("any type") || lower.contains("any one color")) {
                out.addAll(ALL);
                continue;
            }
            Matcher a = ADDS.matcher(text);
            String adds = a.find() ? a.group(1) : text;
            Matcher m = PIP.matcher(adds);
            while (m.find()) {
                for (int g = 1; g <= 2; g++) {
                    if (m.group(g) != null) {
                        String name = colorName(m.group(g).charAt(0));
                        if (name != null) {
                            out.add(name);
                        }
                    }
                }
            }
        }
        return out;
    }

    /**
     * The colour to answer with, or null to leave the decision to the pilot/human.
     * Prefers a needed colour that the other sources can't produce (tapping Command
     * Tower for Black when only a Swamp is left would strand the {U}); then any
     * needed colour the source offers; then the only offered colour; then, for a
     * generic-only remainder, any colour.
     */
    public static String pick(String paymentText, Collection<String> offered, Set<String> coverableByOthers) {
        if (offered == null || offered.isEmpty()) {
            return null;
        }
        List<String> needed = neededColors(paymentText);
        for (String color : needed) {
            if (offered.contains(color) && !coverableByOthers.contains(color)) {
                return color;
            }
        }
        for (String color : needed) {
            if (offered.contains(color)) {
                return color;
            }
        }
        if (offered.size() == 1) {
            return offered.iterator().next();
        }
        if (needed.isEmpty() && paymentText != null && paymentText.contains("{")) {
            return offered.iterator().next(); // generic-only remainder: any colour pays it
        }
        return null;
    }
}

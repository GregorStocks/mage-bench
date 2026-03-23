package mage.client.bridge;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

public final class BridgePromptFormatting {

    private static final Pattern HTML_TAG_PATTERN = Pattern.compile("<[^>]+>");
    private static final Pattern HEX_SUFFIX_PATTERN = Pattern.compile(" \\[[0-9a-f]{3}\\]");

    private BridgePromptFormatting() {
    }

    /**
     * Clean a string for LLM consumption: strip HTML tags and 3-char hex ID suffixes.
     * Must be applied after internal HTML parsing (cast owner tracking, mana payment extraction).
     */
    public static String stripHtml(String s) {
        if (s == null || s.isEmpty()) {
            return s;
        }
        // XMage uses <br> to separate labels from values in prompt text.
        String result = s.replaceAll("(?i)<br\\s*/?>", ": ");
        result = HTML_TAG_PATTERN.matcher(result).replaceAll("");
        result = HEX_SUFFIX_PATTERN.matcher(result).replaceAll("");
        return result;
    }

    public static String stripAbilityPickerOrdinalPrefix(String description, int zeroBasedIndex) {
        String normalized = Objects.requireNonNull(description, "Ability choice description must not be null");
        String expectedPrefix = (zeroBasedIndex + 1) + ". ";
        if (normalized.startsWith(expectedPrefix)) {
            return normalized.substring(expectedPrefix.length());
        }
        return normalized;
    }

    public static List<String> stripHtmlList(List<String> list) {
        if (list == null) {
            return null;
        }
        var result = new ArrayList<String>(list.size());
        for (String s : list) {
            result.add(stripHtml(s));
        }
        return result;
    }
}

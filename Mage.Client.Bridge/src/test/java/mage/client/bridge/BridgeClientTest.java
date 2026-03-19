package mage.client.bridge;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BridgeClientTest {

    @Test
    void normalizesSupportedPersonalityNames() {
        assertThat(BridgeClient.parsePersonality("SLEEPWALKER")).isEqualTo("sleepwalker");
        assertThat(BridgeClient.parsePersonality("StAlLeR")).isEqualTo("staller");
    }

    @Test
    void rejectsUnknownPersonality() {
        assertThatThrownBy(() -> BridgeClient.parsePersonality("wizard"))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Unknown bridge personality 'wizard'. Expected one of: potato, staller, sleepwalker");
    }

    @Test
    void rejectsMissingArgumentValue() {
        assertThatThrownBy(() -> BridgeClient.getStringSetting(
            new String[]{"--server"},
            "--server",
            "xmage.bridge.server",
            "localhost"
        ))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Missing value for --server");
    }

    @Test
    void prefersCommandLineIntegerSettingOverProperty() {
        withSystemProperty("xmage.bridge.port", "17171", () ->
            assertThat(BridgeClient.getIntSetting(
                new String[]{"--port", "18181"},
                "--port",
                "xmage.bridge.port",
                16161
            )).isEqualTo(18181)
        );
    }

    @Test
    void usesDefaultIntegerSettingWhenUnset() {
        withSystemProperty("xmage.bridge.port", null, () ->
            assertThat(BridgeClient.getIntSetting(
                new String[0],
                "--port",
                "xmage.bridge.port",
                17171
            )).isEqualTo(17171)
        );
    }

    @Test
    void rejectsInvalidIntegerArgument() {
        assertThatThrownBy(() -> BridgeClient.getIntSetting(
            new String[]{"--port", "nope"},
            "--port",
            "xmage.bridge.port",
            17171
        ))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Invalid integer for --port: nope");
    }

    @Test
    void rejectsInvalidIntegerProperty() {
        withSystemProperty("xmage.bridge.port", "nope", () ->
            assertThatThrownBy(() -> BridgeClient.getIntSetting(
                new String[0],
                "--port",
                "xmage.bridge.port",
                17171
            ))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("Invalid integer for -Dxmage.bridge.port: nope")
        );
    }

    @Test
    void loadsValidDeckFile() throws IOException {
        Path deckFile = writeDeck(
            "# main deck",
            "1 [9ED:41] Savannah Lions",
            "",
            "SB: 2 [M10:230] Plains"
        );

        DeckCardLists deck = BridgeClient.loadDeck(deckFile.toString());

        assertThat(deck.getCards()).hasSize(1);
        DeckCardInfo mainDeckCard = deck.getCards().get(0);
        assertThat(mainDeckCard.getCardName()).isEqualTo("Savannah Lions");
        assertThat(mainDeckCard.getSetCode()).isEqualTo("9ED");
        assertThat(mainDeckCard.getCardNumber()).isEqualTo("41");
        assertThat(mainDeckCard.getAmount()).isEqualTo(1);

        assertThat(deck.getSideboard()).hasSize(1);
        DeckCardInfo sideboardCard = deck.getSideboard().get(0);
        assertThat(sideboardCard.getCardName()).isEqualTo("Plains");
        assertThat(sideboardCard.getSetCode()).isEqualTo("M10");
        assertThat(sideboardCard.getCardNumber()).isEqualTo("230");
        assertThat(sideboardCard.getAmount()).isEqualTo(2);
    }

    @Test
    void requiresDeckPath() {
        assertThatThrownBy(() -> BridgeClient.loadDeck(null))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Bridge deck path is required");

        assertThatThrownBy(() -> BridgeClient.loadDeck("   "))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Bridge deck path is required");
    }

    @Test
    void requiresExistingDeckFile() throws IOException {
        Path tempDir = Files.createTempDirectory("bridge-client-missing-");
        tempDir.toFile().deleteOnExit();
        Path missingDeck = tempDir.resolve("missing.dck");

        assertThatThrownBy(() -> BridgeClient.loadDeck(missingDeck.toString()))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Deck file not found: " + missingDeck);
    }

    @Test
    void rejectsMalformedDeckLines() throws IOException {
        Path deckFile = writeDeck(
            "1 [9ED:41] Savannah Lions",
            "not a deck line"
        );

        assertThatThrownBy(() -> BridgeClient.loadDeck(deckFile.toString()))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Invalid deck line 2 in " + deckFile + ": not a deck line");
    }

    @Test
    void rejectsDecksWithoutCards() throws IOException {
        Path deckFile = writeDeck(
            "# no cards here",
            "",
            "// still no cards"
        );

        assertThatThrownBy(() -> BridgeClient.loadDeck(deckFile.toString()))
            .isInstanceOf(IllegalStateException.class)
            .hasMessage("Deck is empty after parsing: " + deckFile);
    }

    private static Path writeDeck(String... lines) throws IOException {
        Path deckFile = Files.createTempFile("bridge-client-", ".dck");
        deckFile.toFile().deleteOnExit();
        Files.write(deckFile, List.of(lines));
        return deckFile;
    }

    private static void withSystemProperty(String name, String value, Runnable assertion) {
        String originalValue = System.getProperty(name);
        if (value == null) {
            System.clearProperty(name);
        } else {
            System.setProperty(name, value);
        }
        try {
            assertion.run();
        } finally {
            if (originalValue == null) {
                System.clearProperty(name);
            } else {
                System.setProperty(name, originalValue);
            }
        }
    }
}

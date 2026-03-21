package mage.client.bridge;

import mage.client.bridge.tools.GetGameHistoryTool;
import mage.game.BridgeLogEntry;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameLogFormatterTest {

    @Test
    void renderGameLogFlatSortsSameCursorChatByMessageAndSkipsSetupEvents() {
        List<BridgeLogEntry> events = List.of(
            bridgeLogEntry(0, "LAND_PLAYED", 0, "Alice", "Alice", "Pre-game Island", null),
            bridgeLogEntry(1, "BEGIN_TURN", 1, "Alice", "Alice", null, null),
            bridgeLogEntry(2, "LAND_PLAYED", 1, "Alice", "Alice", "Island", null)
        );

        List<BridgeChatLogEntry> chats = List.of(
            new BridgeChatLogEntry(2, "zeta", "[Chat] Bob: zeta"),
            new BridgeChatLogEntry(2, "alpha", "[Chat] Zoe: alpha")
        );

        assertThat(BridgeGameLogFormatter.renderGameLogFlat(events, chats, Map.of(), 0, true))
            .isEqualTo(String.join("\n",
                "Alice turn 1:",
                "[Chat] Zoe: alpha",
                "[Chat] Bob: zeta",
                "Alice played Island"
            ));
    }

    @Test
    void buildGameHistoryResultFormatsTurnsAndPhaseHeaders() {
        List<BridgeLogEntry> events = List.of(
            new BridgeLogEntry(
                0,
                0,
                "LAND_PLAYED",
                3,
                "PRECOMBAT_MAIN",
                "PRECOMBAT_MAIN",
                "Alice",
                "Alice",
                "Island",
                null,
                0,
                true
            ),
            new BridgeLogEntry(
                1,
                1,
                "SPELL_CAST",
                3,
                "PRECOMBAT_MAIN",
                "PRECOMBAT_MAIN",
                "Alice",
                "Alice",
                "Lightning Bolt",
                "Bob",
                0,
                true
            )
        );

        GetGameHistoryTool.Result result = BridgeGameLogFormatter.buildGameHistoryResult(events, 2);

        assertThat(result.cursor).isEqualTo(2);
        assertThat(result.event_count).isEqualTo(2);
        assertThat(result.history).isEqualTo(String.join("\n",
            "Turn 3 (Alice):",
            "  Precombat Main:",
            "    - Alice played Island",
            "    - Alice cast Lightning Bolt targeting Bob",
            ""
        ));
    }

    private static BridgeLogEntry bridgeLogEntry(
            int index,
            String type,
            int turn,
            String activePlayer,
            String player,
            String cardName,
            String targetName
    ) {
        return new BridgeLogEntry(
            index,
            index,
            type,
            turn,
            "PRECOMBAT_MAIN",
            "PRECOMBAT_MAIN",
            activePlayer,
            player,
            cardName,
            targetName,
            0,
            true
        );
    }
}

package mage.client.bridge;

import mage.client.bridge.mcp.BridgeGameLogFormatter;
import mage.client.bridge.processor.BridgePublishedLogEntry;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.game.BridgeLogEntry;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameLogFormatterTest {

    @Test
    void renderGameLogFlatUsesPublishedEntryOrderAndSkipsSetupEvents() {
        List<BridgePublishedLogEntry> entries = List.of(
            new BridgePublishedLogEntry(0, bridgeLogEntry(0, "LAND_PLAYED", 0, "Alice", "Alice", "Pre-game Island", null), null),
            new BridgePublishedLogEntry(1, bridgeLogEntry(1, "BEGIN_TURN", 1, "Alice", "Alice", null, null), null),
            new BridgePublishedLogEntry(2, null, "[Chat] Zoe: alpha"),
            new BridgePublishedLogEntry(3, null, "[Chat] Bob: zeta"),
            new BridgePublishedLogEntry(4, bridgeLogEntry(2, "LAND_PLAYED", 1, "Alice", "Alice", "Island", null), null)
        );

        assertThat(BridgeGameLogFormatter.renderGameLogFlat(entries, Map.of()))
            .isEqualTo(String.join("\n",
                "Alice turn 1:",
                "[Chat] Zoe: alpha",
                "[Chat] Bob: zeta",
                "Alice played Island"
            ));
    }

    @Test
    void renderGameLogFlatKeepsPreTurnChatAndSystemLines() {
        List<BridgePublishedLogEntry> entries = List.of(
            new BridgePublishedLogEntry(0, null, "[System] Table ready"),
            new BridgePublishedLogEntry(1, null, "[Chat] Bob: glhf"),
            new BridgePublishedLogEntry(2, bridgeLogEntry(2, "LAND_PLAYED", 0, "Alice", "Alice", "Pre-game Island", null), null),
            new BridgePublishedLogEntry(3, bridgeLogEntry(3, "BEGIN_TURN", 1, "Alice", "Alice", null, null), null),
            new BridgePublishedLogEntry(4, bridgeLogEntry(4, "LAND_PLAYED", 1, "Alice", "Alice", "Island", null), null)
        );

        assertThat(BridgeGameLogFormatter.renderGameLogFlat(entries, Map.of()))
            .isEqualTo(String.join("\n",
                "[System] Table ready",
                "[Chat] Bob: glhf",
                "Alice turn 1:",
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

package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameLogStateTest {

    @Test
    void outgoingChatPublishesImmediatelyAndSuppressesEcho() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordOutgoingChatMessage("Alice", "glhf", 1_000L, 30_000L, 0L);
        state.recordTalkMessage("Alice", "Alice", "glhf", 1_100L, 30_000L);

        BridgePublishedGameLog published = state.publishedGameLog();
        assertThat(published.nextCursor()).isEqualTo(1);
        assertThat(published.entries()).hasSize(1);
        assertThat(published.entries().getFirst().rendered()).isEqualTo("[Chat] Alice: glhf");
    }

    @Test
    void outgoingChatSuppressesOnlyMatchingEchoMessage() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordOutgoingChatMessage("Alice", "first", 1_000L, 30_000L, 0L);
        state.recordOutgoingChatMessage("Alice", "second", 1_100L, 30_000L, 0L);
        state.publishedGameLog(0L);
        state.recordTalkMessage("Alice", "Alice", "first", 1_200L, 30_000L);
        state.recordTalkMessage("Alice", "Alice", "third", 1_300L, 30_000L);

        BridgePublishedGameLog published = state.publishedGameLog();
        assertThat(published.entries()).extracting(BridgePublishedLogEntry::rendered)
            .containsExactly(
                "[Chat] Alice: first",
                "[Chat] Alice: second",
                "[Chat] Alice: third"
            );
    }

    @Test
    void outgoingChatWaitsForRequestedSyncEpochBeforePublishing() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordOutgoingChatMessage("Alice", "glhf", 1_000L, 30_000L, 2L);

        assertThat(state.publishedGameLog(1L).entries()).isEmpty();
        assertThat(state.publishedGameLog(2L).entries()).extracting(BridgePublishedLogEntry::rendered)
            .containsExactly("[Chat] Alice: glhf");
    }

    @Test
    void outgoingChatPublishesAfterEarlierBridgeEventsWhenSyncCompletes() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordFetchedBridgeEvents(List.of(
            bridgeLogEntry(5, "BEGIN_TURN", 1, "Alice", "Alice", null, null),
            bridgeLogEntry(6, "LAND_PLAYED", 1, "Alice", "Alice", "Mountain", null)
        ));
        state.recordOutgoingChatMessage("Alice", "glhf", 1_000L, 30_000L, 2L);

        assertThat(state.publishedGameLog(1L).entries()).extracting(entry ->
            entry.isBridgeEvent() ? entry.bridgeEvent().type() : entry.rendered()
        ).containsExactly("BEGIN_TURN", "LAND_PLAYED");

        assertThat(state.publishedGameLog(2L).entries()).extracting(entry ->
            entry.isBridgeEvent() ? entry.bridgeEvent().type() : entry.rendered()
        ).containsExactly("BEGIN_TURN", "LAND_PLAYED", "[Chat] Alice: glhf");
    }

    private static BridgeLogEntry bridgeLogEntry(
            int index,
            String type,
            int turn,
            String activePlayer,
            String player,
            String cardName,
            String targetName) {
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

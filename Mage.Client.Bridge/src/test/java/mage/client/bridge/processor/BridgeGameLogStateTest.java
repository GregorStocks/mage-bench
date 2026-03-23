package mage.client.bridge.processor;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameLogStateTest {

    @Test
    void outgoingChatPublishesImmediatelyAndSuppressesEcho() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordOutgoingChatMessage("Alice", "glhf", 1_000L, 30_000L);
        state.recordTalkMessage("Alice", "Alice", "glhf", 1_100L, 30_000L);

        BridgePublishedGameLog published = state.publishedGameLog();
        assertThat(published.nextCursor()).isEqualTo(1);
        assertThat(published.entries()).hasSize(1);
        assertThat(published.entries().getFirst().rendered()).isEqualTo("[Chat] Alice: glhf");
    }

    @Test
    void outgoingChatSuppressesOnlyMatchingEchoMessage() {
        BridgeGameLogState state = new BridgeGameLogState();

        state.recordOutgoingChatMessage("Alice", "first", 1_000L, 30_000L);
        state.recordOutgoingChatMessage("Alice", "second", 1_100L, 30_000L);
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
}

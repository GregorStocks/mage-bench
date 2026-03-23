package mage.client.bridge.processor;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeCursorStateTest {

    @Test
    void gameStateSnapshotIdDependsOnCurrentSignatureNotTransitionCount() {
        BridgeCursorState cursorState = new BridgeCursorState();

        long alpha1 = cursorState.updateGameStateSnapshotId("alpha");
        long beta = cursorState.updateGameStateSnapshotId("beta");
        long alpha2 = cursorState.updateGameStateSnapshotId("alpha");

        assertThat(alpha1).isNotZero();
        assertThat(beta).isNotZero();
        assertThat(beta).isNotEqualTo(alpha1);
        assertThat(alpha2).isEqualTo(alpha1);
    }

    @Test
    void boardCursorStillUsesMonotonicDedupCounter() {
        BridgeCursorState cursorState = new BridgeCursorState();

        long alpha1 = cursorState.updateBoardCursor("alpha");
        long alpha2 = cursorState.updateBoardCursor("alpha");
        long beta = cursorState.updateBoardCursor("beta");

        assertThat(alpha1).isEqualTo(1L);
        assertThat(alpha2).isEqualTo(alpha1);
        assertThat(beta).isEqualTo(2L);
    }
}

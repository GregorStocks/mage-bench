package mage.client.bridge;

import mage.util.MultiAmountMessage;
import mage.view.GameClientMessage;
import org.junit.jupiter.api.Test;

import java.io.Serializable;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BridgeCallbackHandlerTest {

    @Test
    void acceptsValidMultiAmountInput() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("First", 0, 2),
            new MultiAmountMessage("Second", 0, 2)
        ), 2, 2);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{1, 1})).isNull();
    }

    @Test
    void rejectsWrongItemCount() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("Only", 0, 9)
        ), 3, 9);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{3, 6}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: expected 1 entry, got 2. "
                + "Expected 1 amount and total 3-9.");
    }

    @Test
    void rejectsPerItemRangeViolations() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("Only", 1, 3)
        ), 1, 3);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{0}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: amounts[0]=0 is outside item range 1-3. "
                + "Expected 1 amount and total 1-3.");
    }

    @Test
    void rejectsTotalRangeViolations() {
        GameClientMessage message = multiAmountMessage(List.of(
            new MultiAmountMessage("First", 0, 3),
            new MultiAmountMessage("Second", 0, 3)
        ), 2, 2);

        assertThat(BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{2, 1}))
            .isEqualTo("Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: total 3 is outside allowed range 2. "
                + "Expected 2 amounts and total 2.");
    }

    @Test
    void failsFastWhenPendingActionLacksItemMetadata() {
        GameClientMessage message = new GameClientMessage(
            null,
            Collections.<String, Serializable>emptyMap(),
            (List<MultiAmountMessage>) null,
            1,
            2
        );

        assertThatThrownBy(() -> BridgeCallbackHandler.validateMultiAmountInput(message, new int[]{1}))
            .isInstanceOf(IllegalStateException.class)
            .hasMessage("GAME_GET_MULTI_AMOUNT is missing item metadata");
    }

    private static GameClientMessage multiAmountMessage(List<MultiAmountMessage> items, int min, int max) {
        return new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), items, min, max);
    }
}

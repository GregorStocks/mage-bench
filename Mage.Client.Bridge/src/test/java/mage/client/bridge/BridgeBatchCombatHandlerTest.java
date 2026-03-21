package mage.client.bridge;

import mage.interfaces.callback.ClientCallbackMethod;
import mage.util.ShortIdRegistry;
import mage.view.GameClientMessage;

import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;

import org.junit.jupiter.api.Test;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.fail;

class BridgeBatchCombatHandlerTest {

    @Test
    void handleBatchAttackersAllAttackConfirmsAndMergesNextDecision() {
        UUID gameId = UUID.randomUUID();
        PendingAction declareAttackers = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), "Declare attackers"),
            "Declare attackers",
            11
        );
        PendingAction confirmAttackers = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), "Confirm attackers"),
            "Confirm attackers",
            12
        );
        PendingAction nextDecision = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_ASK,
            new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), "Mulligan hand?"),
            "Mulligan hand?",
            13
        );

        List<String> sends = new ArrayList<>();
        BridgeBatchCombatHandler handler = new BridgeBatchCombatHandler(
            new ShortIdRegistry("p"),
            new BridgeBatchCombatHandler.Context() {
                @Override
                public void clearPendingAction() {
                }

                @Override
                public PendingAction waitForNextCallback() {
                    return confirmAttackers;
                }

                @Override
                public PendingAction awaitDecisionAction() {
                    return nextDecision;
                }

                @Override
                public void mergeActionChoices(ActionResult result, Long boardCursorParam, PendingAction action) {
                    result.action_pending = true;
                    result.action_type = action.method().name();
                    result.response_type = "boolean";
                    result.message = action.message();
                }

                @Override
                public void attachUnseenChat(ActionResult result) {
                }

                @Override
                public ChooseActionTool.Result buildError(
                        ChooseActionTool.Result result,
                        String errorCode,
                        String message,
                        boolean retryable,
                        PendingAction action,
                        boolean attachChoices
                ) {
                    fail("All-attack flow should not hit buildError");
                    return result;
                }

                @Override
                public Set<UUID> findValidTargets(GameClientMessage message) {
                    return Set.of();
                }

                @Override
                public void sendBooleanOrDie(UUID gameId, boolean data, String context) {
                    sends.add("bool:" + data + ":" + context);
                }

                @Override
                public void sendStringOrDie(UUID gameId, String data, String context) {
                    sends.add("string:" + data + ":" + context);
                }

                @Override
                public void sendUuidOrDie(UUID gameId, UUID data, String context) {
                    fail("All-attack flow should not send UUID selections");
                }
            }
        );

        ChooseActionTool.Result result = handler.handleBatchAttackers(
            new String[]{"all"},
            declareAttackers,
            new ChooseActionTool.Result()
        );

        assertThat(result.success).isTrue();
        assertThat(result.action_taken).isEqualTo("batch_attack");
        assertThat(result.declared).containsExactly(Map.of("id", "all"));
        assertThat(result.game_seq).isEqualTo(13);
        assertThat(result.action_pending).isTrue();
        assertThat(result.action_type).isEqualTo("GAME_ASK");
        assertThat(result.response_type).isEqualTo("boolean");
        assertThat(result.message).isEqualTo("Mulligan hand?");
        assertThat(sends).containsExactly(
            "string:special:batchAttack:all",
            "bool:true:batchAttack:confirm_all"
        );
    }
}

package mage.client.bridge;

import mage.constants.ManaType;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.view.GameClientMessage;
import mage.view.GameView;

import mage.client.bridge.tools.ChooseActionTool;

import org.apache.log4j.Logger;
import org.junit.jupiter.api.Test;

import java.io.Serializable;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.fail;

class BridgeChooseActionExecutorTest {

    @Test
    void gameSelectRejectsManaPlanWithUnknownPermanentId() {
        UUID gameId = UUID.randomUUID();
        UUID selectableId = UUID.randomUUID();
        PendingAction action = new PendingAction(
            gameId,
            ClientCallbackMethod.GAME_SELECT,
            new GameClientMessage(null, Collections.<String, Serializable>emptyMap(), "Play spells and abilities"),
            "Play spells and abilities",
            7
        );

        BridgeChooseActionExecutor executor = new BridgeChooseActionExecutor(
            new BridgeMageClient("TestPlayer"),
            Logger.getLogger(BridgeChooseActionExecutorTest.class),
            new BridgeChooseActionExecutor.Context() {
                @Override
                public List<Object> lastChoices() {
                    return List.of(selectableId);
                }

                @Override
                public ChooseActionTool.Result buildError(
                        ChooseActionTool.Result result,
                        String errorCode,
                        String message,
                        boolean retryable,
                        PendingAction pendingAction,
                        boolean attachChoices
                ) {
                    result.success = false;
                    result.error_code = errorCode;
                    result.error = message;
                    result.retryable = retryable;
                    return result;
                }

                @Override
                public void logChoiceOutOfRangeDiagnostic(
                        ClientCallbackMethod method,
                        Integer index,
                        List<Object> choices
                ) {
                }

                @Override
                public CopyOnWriteArrayList<ManaPlanEntry> parseManaPlan(String[] arr) {
                    return new CopyOnWriteArrayList<>(List.of(new ManaPlanEntry("tap", arr[0])));
                }

                @Override
                public UUID tryResolveShortId(String id) {
                    return null;
                }

                @Override
                public void setManaPlan(CopyOnWriteArrayList<ManaPlanEntry> plan) {
                    fail("Mana plan should not be stored when validation fails");
                }

                @Override
                public void setManaPlanAbilityIndex(Integer abilityIndex) {
                }

                @Override
                public void setManaPlanAutoTapFallback(boolean autoTapFallback) {
                    fail("Auto-tap fallback should not be updated when validation fails");
                }

                @Override
                public void addFailedManaCast(UUID objectId) {
                    fail("Failed mana casts should not be updated during plan validation");
                }

                @Override
                public UUID extractPayingForId(String message) {
                    return null;
                }

                @Override
                public UUID getManaPoolPlayerId(UUID gameId, GameView gameView) {
                    return null;
                }

                @Override
                public GameView lastGameView() {
                    return null;
                }

                @Override
                public Set<UUID> findValidTargets(GameClientMessage message) {
                    return Set.of();
                }

                @Override
                public UUID selectDeterministicTarget(Set<UUID> targets, List<Object> choices) {
                    return null;
                }

                @Override
                public void sendBooleanOrDie(UUID gameId, boolean data, String context) {
                    fail("Should not send a boolean response when mana plan validation fails");
                }

                @Override
                public void sendUuidOrDie(UUID gameId, UUID data, String context) {
                    fail("Should not send a UUID response when mana plan validation fails");
                }

                @Override
                public void sendStringOrDie(UUID gameId, String data, String context) {
                    fail("Should not send a string response when mana plan validation fails");
                }

                @Override
                public void sendIntegerOrDie(UUID gameId, int data, String context) {
                    fail("Should not send an integer response when mana plan validation fails");
                }

                @Override
                public void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType manaType, String context) {
                    fail("Should not send pool mana when mana plan validation fails");
                }
            }
        );

        ChooseActionTool.Result result = executor.execute(
            action,
            new ChooseActionTool.Result(),
            0,
            null,
            null,
            null,
            null,
            null,
            null,
            new String[]{"p999"},
            false
        );

        assertThat(result.success).isFalse();
        assertThat(result.error_code).isEqualTo("invalid_mana_plan");
        assertThat(result.retryable).isTrue();
        assertThat(result.error).contains("unknown permanent 'p999'");
    }
}

package mage.client.bridge.processor;

import mage.client.bridge.BridgeCardFormatter;
import mage.client.bridge.BridgeGameStateBuilder;
import mage.client.bridge.BridgeOracleTextService;
import mage.client.bridge.BridgeViewLocator;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.util.ShortIdRegistry;

import java.util.Objects;
import java.util.function.Consumer;

public final class BridgeProcessorServices {
    private final ShortIdRegistry shortIds;
    private final BridgeViewLocator viewLocator;
    private final BridgeCardFormatter cardFormatter;
    private final BridgeGameStateBuilder gameStateBuilder;
    private final BridgeOracleTextService oracleTextService;

    public BridgeProcessorServices(
            BridgeProcessorState processorState,
            Consumer<String> errorLogger) {
        Objects.requireNonNull(processorState, "processorState");
        Objects.requireNonNull(errorLogger, "errorLogger");
        this.shortIds = new ShortIdRegistry("l");
        this.viewLocator = new BridgeViewLocator(
            shortIds,
            processorState.gameState()::lastGameView,
            errorLogger
        );
        this.cardFormatter = new BridgeCardFormatter(
            viewLocator,
            processorState.gameState()::currentGameId,
            processorState.gameState()::playerIdForGame
        );
        this.gameStateBuilder = new BridgeGameStateBuilder(
            cardFormatter,
            viewLocator,
            processorState.gameState()::currentGameId,
            processorState.gameState()::playerIdForGame
        );
        this.oracleTextService = new BridgeOracleTextService(shortIds, viewLocator);
    }

    public void clearShortIds() {
        shortIds.clear();
    }

    public ShortIdRegistry shortIds() {
        return shortIds;
    }

    public BridgeViewLocator viewLocator() {
        return viewLocator;
    }

    public BridgeCardFormatter cardFormatter() {
        return cardFormatter;
    }

    public BridgeGameStateBuilder gameStateBuilder() {
        return gameStateBuilder;
    }

    public GetOracleTextTool.Result getOracleText(
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds) {
        return oracleTextService.getOracleText(cardName, objectId, cardNames, objectIds);
    }
}

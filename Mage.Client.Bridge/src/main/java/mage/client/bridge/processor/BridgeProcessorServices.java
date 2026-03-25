package mage.client.bridge.processor;

import mage.client.bridge.BridgeCardFormatter;
import mage.client.bridge.BridgeGameStateBuilder;
import mage.client.bridge.BridgeViewLocator;
import mage.util.ShortIdRegistry;

import java.util.Objects;
import java.util.function.Consumer;

public final class BridgeProcessorServices {
    private final ShortIdRegistry shortIds;
    private final BridgeViewLocator viewLocator;
    private final BridgeCardFormatter cardFormatter;
    private final BridgeGameStateBuilder gameStateBuilder;

    public BridgeProcessorServices(
            Consumer<String> errorLogger) {
        Objects.requireNonNull(errorLogger, "errorLogger");
        this.shortIds = new ShortIdRegistry("l");
        this.viewLocator = new BridgeViewLocator(shortIds, errorLogger);
        this.cardFormatter = new BridgeCardFormatter(viewLocator);
        this.gameStateBuilder = new BridgeGameStateBuilder(cardFormatter, viewLocator);
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
}

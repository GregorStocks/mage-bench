package mage.client.bridge.processor;

public record BridgeManaPlanEntry(String type, String value, Integer abilityIndex) {
    public BridgeManaPlanEntry(String type, String value) {
        this(type, value, null);
    }
}

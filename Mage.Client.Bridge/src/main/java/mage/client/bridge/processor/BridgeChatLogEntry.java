package mage.client.bridge.processor;

public record BridgeChatLogEntry(int eventCursor, String message, String rendered) {
}

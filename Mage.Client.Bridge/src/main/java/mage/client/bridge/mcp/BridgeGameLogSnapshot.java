package mage.client.bridge.mcp;

import mage.game.BridgeLogEntry;
import mage.client.bridge.processor.BridgeChatLogEntry;

import java.util.List;

record BridgeGameLogSnapshot(List<BridgeLogEntry> events, List<BridgeChatLogEntry> chatEntries, int cursor) {
}

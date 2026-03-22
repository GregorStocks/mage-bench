package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgePublishedLogEntry;

import java.util.List;

record BridgeGameLogSnapshot(List<BridgePublishedLogEntry> entries, int firstCursor, int nextCursor) {
}

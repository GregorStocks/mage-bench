package mage.client.bridge.mcp;

import mage.game.BridgeLogEntry;

import java.util.List;

record BridgeGameLogSnapshot(List<BridgeLogEntry> events, int cursor) {
}

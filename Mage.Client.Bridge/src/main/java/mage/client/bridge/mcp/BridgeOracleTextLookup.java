package mage.client.bridge.mcp;

import mage.client.bridge.tools.GetOracleTextTool;

@FunctionalInterface
public interface BridgeOracleTextLookup {
    GetOracleTextTool.Result getOracleText(String cardName, String objectId, String[] cardNames, String[] objectIds);
}

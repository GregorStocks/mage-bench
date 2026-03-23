package mage.client.bridge.tools;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetGameStateTool {

    public static class Result {
        @ResultField(description = "Whether state is available")
        public Boolean available;

        @ResultField(description = "Error message")
        public String error;

        @ResultField(description = "Snapshot identifier for the current published state")
        public Long snapshot_id;

        @ResultField(description = "Snapshot matched (no changes)")
        public Boolean unchanged;

        @ResultField(description = "Turn number")
        public Integer turn;

        @ResultField(description = "Current phase")
        public String phase;

        @ResultField(description = "Current step")
        public String step;

        @ResultField(description = "Whose turn it is")
        public String active_player;

        @ResultField(description = "Who has priority")
        public String priority_player;

        @ResultField(description = "Player objects with life, hand, battlefield, graveyard, exile, mana_pool")
        public List<Map<String, Object>> players;

        @ResultField(description = "Stack: name, rules, targets")
        public List<Map<String, Object>> stack;

        @ResultField(description = "Combat groups")
        public List<Map<String, Object>> combat;

        @ResultField(description = "Sequence number")
        public Integer game_seq;
    }

    @Tool(
        name = "get_game_state",
        description = "Get full game state: turn, phase, players (life, hand, battlefield, graveyard, exile), stack, combat."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Snapshot identifier from a previous get_game_state call. Returns compact payload if unchanged.") Long snapshot_id) {
        return handler.getGameState(snapshot_id);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Mid-game state", json(
                "available", true,
                "turn", 4,
                "phase", "PRECOMBAT_MAIN",
                "step", "PRECOMBAT_MAIN",
                "active_player", "Player1",
                "priority_player", "Player1",
                "players", List.of(
                    json("name", "Player1",
                        "life", 18,
                        "library_size", 49,
                        "hand_size", 5,
                        "is_active", true,
                        "is_you", true,
                        "hand", List.of(
                            json("name", "Lightning Bolt", "mana_cost", "{R}", "playable", true),
                            json("name", "Mountain", "is_land", true, "playable", true)),
                        "battlefield", List.of(
                            json("name", "Mountain", "tapped", false),
                            json("name", "Goblin Guide", "tapped", false, "power", 2, "toughness", 2)),
                        "mana_pool", json("R", 0)),
                    json("name", "Player2",
                        "life", 20,
                        "library_size", 52,
                        "hand_size", 7,
                        "is_active", false,
                        "is_you", false,
                        "battlefield", List.of(
                            json("name", "Island", "tapped", false)))),
                "stack", Collections.emptyList())));
    }
}

package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetOracleTextTool {

    public static class Result {
        @ResultField(description = "Whether lookup succeeded")
        public Boolean success;

        @ResultField(description = "Card name")
        public String name;

        @ResultField(description = "Mana cost")
        public String mana_cost;

        @ResultField(description = "Type line")
        public String type;

        @ResultField(description = "Oracle text lines")
        public List<String> rules;

        @ResultField(description = "Power")
        public String power;

        @ResultField(description = "Toughness")
        public String toughness;

        @ResultField(description = "Starting loyalty")
        public String starting_loyalty;

        @ResultField(description = "Starting defense")
        public String starting_defense;

        @ResultField(description = "Back face (transform/MDFC)")
        public Map<String, Object> second_face;

        @ResultField(description = "Card objects (batch mode)")
        public List<Map<String, Object>> cards;

        @ResultField(description = "Error message")
        public String error;
    }

    @Tool(
        name = "get_oracle_text",
        description = "Get oracle text for cards. Use card_name/card_names for lookup by name, "
            + "or object_id/object_ids for in-game objects."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "Card name") String card_name,
            @Param(description = "Card names (batch)") String[] card_names,
            @Param(description = "In-game object ID (e.g. \"p3\")") String object_id,
            @Param(description = "In-game object IDs (batch)") String[] object_ids) {
        return handler.getOracleText(card_name, object_id, card_names, object_ids);
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("Single creature", json(
                "success", true,
                "name", "Snapcaster Mage",
                "mana_cost", "{1}{U}",
                "type", "Creature — Human Wizard",
                "rules", List.of("Flash", "When Snapcaster Mage enters the battlefield, target instant or sorcery card in your graveyard gains flashback until end of turn. The flashback cost is equal to its mana cost."),
                "power", "2",
                "toughness", "1")),
            example("Transform card with second face", json(
                "success", true,
                "name", "Delver of Secrets",
                "mana_cost", "{U}",
                "type", "Creature — Human Wizard",
                "rules", List.of("At the beginning of your upkeep, look at the top card of your library. You may reveal that card. If an instant or sorcery card is revealed this way, transform Delver of Secrets."),
                "power", "1",
                "toughness", "1",
                "second_face", json(
                    "name", "Insectile Aberration",
                    "type", "Creature — Human Insect",
                    "rules", List.of("Flying"),
                    "power", "3",
                    "toughness", "2"))),
            example("Batch lookup", json(
                "success", true,
                "cards", List.of(
                    json("name", "Lightning Bolt", "mana_cost", "{R}", "type", "Instant",
                        "rules", List.of("Lightning Bolt deals 3 damage to any target.")),
                    json("name", "Counterspell", "mana_cost", "{U}{U}", "type", "Instant",
                        "rules", List.of("Counter target spell."))))),
            example("Not found", json(
                "success", false,
                "error", "not found")));
    }
}

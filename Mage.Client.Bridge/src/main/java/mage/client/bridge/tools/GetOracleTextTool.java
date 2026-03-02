package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetOracleTextTool {
    @Tool(
        name = "get_oracle_text",
        description = "Get oracle text for cards. Use card_name/card_names for lookup by name, "
            + "or object_id/object_ids for in-game objects.",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether lookup succeeded"),
            @Tool.Field(name = "name", type = "string", description = "Card name"),
            @Tool.Field(name = "mana_cost", type = "string", description = "Mana cost"),
            @Tool.Field(name = "type", type = "string", description = "Type line"),
            @Tool.Field(name = "rules", type = "array[string]", description = "Oracle text lines"),
            @Tool.Field(name = "power", type = "string", description = "Power"),
            @Tool.Field(name = "toughness", type = "string", description = "Toughness"),
            @Tool.Field(name = "starting_loyalty", type = "string", description = "Starting loyalty"),
            @Tool.Field(name = "starting_defense", type = "string", description = "Starting defense"),
            @Tool.Field(name = "second_face", type = "object", description = "Back face (transform/MDFC)"),
            @Tool.Field(name = "cards", type = "array[object]", description = "Card objects (batch mode)"),
            @Tool.Field(name = "error", type = "string", description = "Error message")
        }
    )
    public static Map<String, Object> execute(
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

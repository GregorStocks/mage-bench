package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class GetOracleTextTool {
    @Tool(
        name = "get_oracle_text",
        description = "Get oracle text and card details. Provide exactly one of: card_name (single), "
            + "card_names (batch array), object_id (in-game object), or object_ids (batch array of in-game objects). "
            + "Returns name, mana_cost, type, rules, and where applicable: power/toughness, starting_loyalty, "
            + "starting_defense, second_face.",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether the lookup succeeded"),
            @Tool.Field(name = "name", type = "string", description = "Card name"),
            @Tool.Field(name = "mana_cost", type = "string", description = "Mana cost (e.g. \"{2}{R}\")"),
            @Tool.Field(name = "type", type = "string", description = "Type line (e.g. \"Creature — Human Wizard\")"),
            @Tool.Field(name = "rules", type = "array[string]", description = "Oracle text lines"),
            @Tool.Field(name = "power", type = "string", description = "Power (creatures only)"),
            @Tool.Field(name = "toughness", type = "string", description = "Toughness (creatures only)"),
            @Tool.Field(name = "starting_loyalty", type = "string", description = "Starting loyalty (planeswalkers only)"),
            @Tool.Field(name = "starting_defense", type = "string", description = "Starting defense (battles only)"),
            @Tool.Field(name = "second_face", type = "object", description = "Back face for transform/MDFC cards (same fields)"),
            @Tool.Field(name = "cards", type = "array[object]", description = "Array of card objects (batch mode). Each has name, mana_cost, type, rules, and optional power/toughness/starting_loyalty/starting_defense/second_face"),
            @Tool.Field(name = "error", type = "string", description = "Error message")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Single card name lookup") String card_name,
            @Param(description = "Batch card name lookup") String[] card_names,
            @Param(description = "Short ID of an in-game object (e.g. \"p3\")") String object_id,
            @Param(description = "Batch in-game object short ID lookup (e.g. [\"p1\",\"p2\"])") String[] object_ids) {
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

package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ChooseActionTool {
    @Tool(
        name = "choose_action",
        description = "Respond to pending action. Blocks until an action is pending (like pass_priority). "
            + "Use choice to select by ID (\"p3\"), index (\"0\"), or answer yes/no (\"yes\"/\"no\"). "
            + "Use attackers/blockers for batch combat. "
            + "Call get_action_choices first.",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether the action was accepted"),
            @Tool.Field(name = "action_taken", type = "string", description = "Description of what was done (e.g. \"selected_0\", \"yes\", \"passed_priority\", \"batch_attack\")"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "error_code", type = "string",
                description = "Machine-readable error code: no_pending_action, missing_param, "
                    + "index_out_of_range, invalid_choice, internal_error, unknown_action_type"),
            @Tool.Field(name = "retryable", type = "boolean",
                description = "Whether the action can be retried with different parameters"),
            @Tool.Field(name = "warning", type = "string", description = "Warning (e.g. possible game loop detected)"),
            @Tool.Field(name = "mana_plan_set", type = "boolean", description = "Whether a mana plan was stored for upcoming payment callbacks"),
            @Tool.Field(name = "mana_plan_size", type = "integer", description = "Number of entries in the stored mana plan"),
            @Tool.Field(name = "declared", type = "array", description = "IDs of successfully declared attackers/blockers (batch combat)"),
            @Tool.Field(name = "failed", type = "array", description = "Entries that failed during batch combat: {id, reason}"),
            @Tool.Field(name = "interrupted", type = "boolean", description = "Whether batch combat was interrupted by a trigger"),
            @Tool.Field(name = "game_seq", type = "integer",
                description = "Game sequence number for determinism tracking"),
            @Tool.Field(name = "next_action_pending", type = "boolean",
                description = "Whether a follow-up action arrived from the server (e.g. bestow mode selection, kicker choice). Call get_action_choices or choose_action next — not pass_priority."),
            @Tool.Field(name = "next_action_type", type = "string",
                description = "XMage callback method name of the follow-up action (e.g. GAME_SELECT, GAME_CHOOSE_CHOICE)"),
            @Tool.Field(name = "next_action_message", type = "string",
                description = "Human-readable message describing the follow-up action (e.g. the question text for GAME_ASK)"),
            @Tool.Field(name = "next_action_hint", type = "string",
                description = "Hint for handling the follow-up action: call get_action_choices/choose_action for details, or pass_priority to continue"),
            @Tool.Field(name = "choices", type = "array[object]",
                description = "Available choices (attached to error responses so the model can self-correct without a separate get_action_choices call)"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died"),
            @Tool.Field(name = "game_over", type = "boolean", description = "Whether the game ended"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "Chat messages received since last check")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Selection or response. Accepts: permanent ID (\"p3\"), "
                + "index (\"0\"), or yes/no (\"yes\", \"no\"). "
                + "Use \"yes\"/\"no\" for boolean questions, mulligans (yes=mulligan, no=keep), "
                + "pass priority (\"no\"), confirm combat (\"yes\"). "
                + "Use IDs or indices for cards, targets, abilities, mana sources.") String choice,
            @Param(description = "Amount value (for get_amount actions)") Integer amount,
            @Param(description = "Multiple amount values (for multi_amount actions)") int[] amounts,
            @Param(description = "Pile number: 1 or 2 (for pile choices)") Integer pile,
            @Param(description = "Text value for GAME_CHOOSE_CHOICE (use instead of choice to pick any option by name, "
                + "e.g. a creature type not in the filtered list)") String text,
            @Param(description = "Comma-separated mana payment instructions for casting a spell (use with choice). "
                + "Each entry is a short ID of a permanent to tap for mana (e.g. \"p1\" for a land/rock). "
                + "For multi-ability permanents (dual lands), append :N (e.g. \"p5:1\" for second ability). "
                + "IMPORTANT: Only use short IDs (\"p1\", \"p5:1\") to TAP permanents for mana. "
                + "Pool colors (WHITE, BLUE, BLACK, RED, GREEN, COLORLESS) only SPEND mana already "
                + "in your mana pool — they do NOT produce mana. Only use pool colors after something "
                + "else has already added mana to your pool (e.g. a ritual spell or a triggered ability). "
                + "Entries are consumed in order, one per mana pip. "
                + "If any entry fails (permanent not available), the spell is cancelled. "
                + "If the plan runs out before all pips are paid, auto-tap fills the rest "
                + "(unless auto_tap=false, which cancels instead). "
                + "Example: \"p1,p5:1\" for a 3-mana spell with 2 lands — auto-tap handles the 3rd pip.") String mana_plan,
            @Param(description = "Controls automatic mana tapping. Default behavior (omitted or true): "
                + "auto-tap pays mana by tapping the first available source. Used alone for full auto-tap, "
                + "or as fallback after a mana_plan runs out. "
                + "Set false WITH a mana_plan to require the plan to be complete — spell is cancelled if "
                + "the plan doesn't cover all pips. "
                + "WARNING: auto-tap has no color awareness and uses a naive heuristic for dual lands. "
                + "Prefer mana_plan for color-sensitive spells.") Boolean auto_tap,
            @Param(description = "Declare multiple attackers at once. Comma-separated short IDs (e.g. \"p1,p2\"). "
                + "Use \"all\" to declare all possible attackers. "
                + "Automatically confirms after declaring.") String attackers,
            @Param(description = "Declare multiple blockers at once. Comma-separated \"blocker_id:attacker_id\" pairs. "
                + "Example: \"p5:p1,p6:p2\" means p5 blocks p1, p6 blocks p2. "
                + "Automatically confirms after declaring.") String blockers) {
        // Parse choice into index/id/answer for the handler
        Integer index = null;
        String id = null;
        Boolean answer = null;
        if (choice != null && !choice.isBlank()) {
            String trimmed = choice.trim();
            if (trimmed.equalsIgnoreCase("yes") || trimmed.equalsIgnoreCase("true")) {
                answer = true;
            } else if (trimmed.equalsIgnoreCase("no") || trimmed.equalsIgnoreCase("false")) {
                answer = false;
            } else {
                try {
                    index = Integer.parseInt(trimmed);
                } catch (NumberFormatException e) {
                    id = trimmed;
                }
            }
        }
        // Parse comma-separated strings into arrays
        String[] manaPlanArray = splitCsv(mana_plan);
        String[] attackersArray = splitCsv(attackers);
        String[] blockersArray = splitCsv(blockers);
        // Treat empty arrays/strings as "not provided"
        if (amounts != null && amounts.length == 0) amounts = null;
        if (text != null && text.isEmpty()) text = null;
        return handler.chooseAction(index, id, answer, amount, amounts, pile, text, manaPlanArray, auto_tap, attackersArray, blockersArray);
    }

    private static String[] splitCsv(String value) {
        if (value == null || value.isBlank()) return null;
        String[] parts = value.split(",");
        for (int i = 0; i < parts.length; i++) {
            parts[i] = parts[i].trim();
        }
        return parts;
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("ID-based selection", json(
                "success", true,
                "action_taken", "selected_2")),
            example("Boolean answer", json(
                "success", true,
                "action_taken", "no")),
            example("Cast with mana plan", json(
                "success", true,
                "action_taken", "selected_2",
                "mana_plan_set", true,
                "mana_plan_size", 3)),
            example("Batch attack", json(
                "success", true,
                "action_taken", "batch_attack",
                "declared", List.of("p1", "p2", "p3"))),
            example("Batch block", json(
                "success", true,
                "action_taken", "batch_block",
                "declared", List.of(
                    json("id", "p5", "blocks", "p1"),
                    json("id", "p6", "blocks", "p2")))),
            example("Error", json(
                "success", false,
                "error", "Index 5 is out of range (valid: 0-3). Call get_action_choices to see current options.",
                "error_code", "index_out_of_range",
                "retryable", true)));
    }
}

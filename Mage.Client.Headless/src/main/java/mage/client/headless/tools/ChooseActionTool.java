package mage.client.headless.tools;

import com.google.gson.JsonArray;

import java.util.List;
import java.util.Map;

import mage.client.headless.BridgeCallbackHandler;

import static mage.client.headless.tools.McpToolRegistry.example;
import static mage.client.headless.tools.McpToolRegistry.json;

public class ChooseActionTool {
    @Tool(
        name = "choose_action",
        description = "Respond to pending action. Blocks until an action is pending (like pass_priority). "
            + "Use id or index to pick a choice (card, attacker, blocker, "
            + "target, ability, mana source). Use answer for yes/no, pass priority, or confirm "
            + "combat (true=confirm attackers/blockers). Use attackers/blockers for batch combat. "
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
            @Tool.Field(name = "next_action_pending", type = "boolean",
                description = "Whether a follow-up action arrived from the server (e.g. bestow mode selection, kicker choice). Call get_action_choices or choose_action next — not pass_priority."),
            @Tool.Field(name = "next_action_type", type = "string",
                description = "XMage callback method name of the follow-up action (e.g. GAME_SELECT, GAME_CHOOSE_CHOICE)"),
            @Tool.Field(name = "next_action_hint", type = "string",
                description = "Instruction to call get_action_choices or choose_action to handle the follow-up action")
        }
    )
    public static Map<String, Object> execute(
            BridgeCallbackHandler handler,
            @Param(description = "Choice index from get_action_choices") Integer index,
            @Param(description = "Short ID of the object to select (e.g. \"p3\"). "
                + "Alternative to index.") String id,
            @Param(description = "Yes/No response. For GAME_ASK: true means YES to the question, false means NO. "
                + "For mulligan: true = YES MULLIGAN (discard hand, draw new cards), false = NO KEEP (keep this hand). "
                + "For GAME_SELECT: false = pass priority (done playing cards this phase), "
                + "true = confirm combat (done declaring attackers/blockers). "
                + "Also false to cancel target/mana selection.") Boolean answer,
            @Param(description = "Amount value (for get_amount actions)") Integer amount,
            @Param(description = "Multiple amount values (for multi_amount actions)") int[] amounts,
            @Param(description = "Pile number: 1 or 2 (for pile choices)") Integer pile,
            @Param(description = "Text value for GAME_CHOOSE_CHOICE (use instead of index to pick any option by name, e.g. a creature type not in the filtered list)") String text,
            @Param(description = "Mana sources to tap when casting a spell. "
                + "Each entry: {\"tap\": \"p3\"} to tap a permanent by short ID, "
                + "or {\"pool\": \"RED\"} to spend mana from pool "
                + "(valid types: WHITE, BLUE, BLACK, RED, GREEN, COLORLESS). "
                + "Consumed in order as mana payment callbacks arrive. "
                + "Example: [{\"tap\": \"p1\"}, {\"tap\": \"p2\"}]. "
                + "The plan must be COMPLETE — if any entry fails or runs out, the spell is cancelled. "
                + "Avoid multi-ability permanents (filter lands, dual lands). "
                + "Mutually exclusive with auto_tap.") JsonArray mana_plan,
            @Param(description = "Set true to use the automatic mana tapper. "
                + "WARNING: The autotapper is not smart — it taps the first available source with no color "
                + "awareness and uses a naive heuristic for multi-ability lands. Prefer mana_plan for "
                + "strategic tapping. Only use auto_tap to save tokens when tapping order doesn't matter.") Boolean auto_tap,
            @Param(description = "Declare multiple attackers at once. Array of short IDs (e.g. [\"p1\",\"p2\"]). "
                + "Use [\"all\"] to declare all possible attackers. "
                + "Automatically confirms after declaring.") String[] attackers,
            @Param(description = "Declare multiple blockers at once. Array of assignments, "
                + "each with \"id\" (blocker) and \"blocks\" (attacker from incoming_attackers). "
                + "Example: [{\"id\":\"p5\",\"blocks\":\"p1\"},{\"id\":\"p6\",\"blocks\":\"p2\"}]. "
                + "Automatically confirms after declaring.") JsonArray blockers) {
        // Treat empty arrays/strings as "not provided" (some models send all params with defaults)
        if (amounts != null && amounts.length == 0) amounts = null;
        if (text != null && text.isEmpty()) text = null;
        if (mana_plan != null && mana_plan.isEmpty()) mana_plan = null;
        if (id != null && id.isBlank()) id = null;
        if (attackers != null && attackers.length == 0) attackers = null;
        if (blockers != null && blockers.isEmpty()) blockers = null;
        return handler.chooseAction(index, id, answer, amount, amounts, pile, text, mana_plan, auto_tap, attackers, blockers);
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

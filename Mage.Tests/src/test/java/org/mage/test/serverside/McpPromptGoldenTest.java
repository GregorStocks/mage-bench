package org.mage.test.serverside;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import mage.client.headless.BridgeCallbackHandler;
import mage.client.headless.BridgeMageClient;
import mage.constants.PhaseStep;
import mage.constants.Zone;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.server.game.GameSessionPlayer;
import mage.view.GameClientMessage;
import mage.view.GameView;
import org.junit.Assert;
import org.junit.Test;
import org.mage.test.serverside.base.CardTestPlayerBase;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.io.Serializable;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Java side of the golden prompt tests.  Constructs real XMage game states,
 * simulates the server callbacks BridgeCallbackHandler would receive, and
 * captures MCP tool results (pass_priority + get_game_state) as fixture files.
 *
 * The Python side (test_golden_prompts.py) reads these fixtures and assembles
 * the complete API payload sent to the LLM.
 *
 * See doc/golden-prompts.md for architecture and rationale.
 *
 *   make update-golden   # regenerate after intentional changes
 *   make test-golden     # verify golden files match
 */
public class McpPromptGoldenTest extends CardTestPlayerBase {

    private static final String GOLDEN_DIR = "golden/mcp";
    private static final boolean UPDATE_MODE = Boolean.getBoolean("updateGolden");

    // --- Helper: create a fresh handler wired to a game ---

    private BridgeCallbackHandler createHandler(UUID gameId, UUID playerId) {
        BridgeMageClient client = new BridgeMageClient("PlayerA");
        BridgeCallbackHandler handler = client.getCallbackHandler();
        handler.initForTest(gameId, playerId);
        return handler;
    }

    // --- Helper: simulate server callbacks ---

    private static void simulatePriorityCallback(BridgeCallbackHandler handler, mage.game.Game game, UUID playerId) {
        GameView gv = GameSessionPlayer.prepareGameView(game, playerId, null);
        String message = game.canPlaySorcery(playerId)
                ? "Play spells and abilities"
                : "Play instants and activated abilities";
        handler.handleCallback(new ClientCallback(
                ClientCallbackMethod.GAME_SELECT, game.getId(),
                new GameClientMessage(gv, null, message), false));
    }

    private static void simulateAskCallback(BridgeCallbackHandler handler, mage.game.Game game, UUID playerId, String message) {
        GameView gv = GameSessionPlayer.prepareGameView(game, playerId, null);
        HashMap<String, Serializable> options = new HashMap<>();
        options.put("UI.left.btn.text", "Mulligan");
        options.put("UI.right.btn.text", "Keep");
        handler.handleCallback(new ClientCallback(
                ClientCallbackMethod.GAME_ASK, game.getId(),
                new GameClientMessage(gv, options, message), false));
    }

    private static void simulateTargetCallback(BridgeCallbackHandler handler, mage.game.Game game, UUID playerId,
                                                Set<UUID> targets, String message) {
        GameView gv = GameSessionPlayer.prepareGameView(game, playerId, null);
        handler.handleCallback(new ClientCallback(
                ClientCallbackMethod.GAME_TARGET, game.getId(),
                new GameClientMessage(gv, null, message, null, targets, true), false));
    }

    // --- Helper: build pass_priority-format result ---

    /**
     * Build a result map matching the format of the pass_priority MCP tool.
     * Merges action choices (like pass_priority's mergeActionChoices does)
     * and adds stop metadata.
     */
    private static Map<String, Object> buildPassPriorityResult(BridgeCallbackHandler handler, String stopReason) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("action_pending", true);
        result.put("actions_passed", 0);
        result.put("stop_reason", stopReason);

        // Merge action choices (same as BridgeCallbackHandler.mergeActionChoices)
        Map<String, Object> choices = handler.getActionChoices();
        for (Map.Entry<String, Object> entry : choices.entrySet()) {
            result.putIfAbsent(entry.getKey(), entry.getValue());
        }
        return result;
    }

    // --- Helper: build full scenario output ---

    /**
     * Build the complete golden file content for a scenario: pass_priority result + game state.
     * This captures what the LLM would see from the two primary MCP tools.
     */
    private static String buildScenarioJson(BridgeCallbackHandler handler, String stopReason) {
        Map<String, Object> scenario = new LinkedHashMap<>();
        scenario.put("pass_priority_result", buildPassPriorityResult(handler, stopReason));
        scenario.put("game_state", handler.getGameState());
        return toSortedJson(scenario);
    }

    // --- Helper: deterministic JSON ---

    private static String toSortedJson(Map<String, Object> map) {
        Object sorted = sortDeep(map);
        String json = new GsonBuilder()
                .setPrettyPrinting()
                .disableHtmlEscaping()
                .create()
                .toJson(sorted);
        return normalizeShortIds(json);
    }

    /**
     * Re-number short IDs (p1, p2, ...) sequentially by first occurrence.
     * This makes output deterministic regardless of ShortIdRegistry assignment order.
     */
    private static String normalizeShortIds(String json) {
        java.util.regex.Pattern pattern = java.util.regex.Pattern.compile("\"p(\\d+)\"");
        // First pass: build mapping from original IDs to sequential IDs
        java.util.regex.Matcher m = pattern.matcher(json);
        LinkedHashMap<String, String> idMap = new LinkedHashMap<>();
        int nextId = 1;
        while (m.find()) {
            String original = m.group();
            if (!idMap.containsKey(original)) {
                idMap.put(original, "\"p" + nextId + "\"");
                nextId++;
            }
        }
        // Second pass: single-pass replacement (no collision risk)
        m = pattern.matcher(json);
        StringBuffer result = new StringBuffer();
        while (m.find()) {
            m.appendReplacement(result, java.util.regex.Matcher.quoteReplacement(idMap.get(m.group())));
        }
        m.appendTail(result);
        return result.toString();
    }

    @SuppressWarnings("unchecked")
    private static Object sortDeep(Object obj) {
        if (obj instanceof Map) {
            TreeMap<String, Object> sorted = new TreeMap<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) obj).entrySet()) {
                sorted.put(String.valueOf(entry.getKey()), sortDeep(entry.getValue()));
            }
            return sorted;
        } else if (obj instanceof List) {
            List<Object> sortedList = new ArrayList<>();
            for (Object item : (List<?>) obj) {
                sortedList.add(sortDeep(item));
            }
            // Sort arrays by serialized form for deterministic output.
            // After recursion, all Maps are TreeMaps so toString() is stable.
            sortedList.sort(Comparator.comparing(String::valueOf));
            return sortedList;
        }
        return obj;
    }

    // --- Helper: golden file comparison ---

    private void assertGoldenFile(String name, String actualJson) {
        // Find the resources directory relative to the test class
        Path resourceDir = findResourceDir();
        Path goldenFile = resourceDir.resolve(GOLDEN_DIR).resolve(name + ".json");

        if (UPDATE_MODE) {
            try {
                Files.createDirectories(goldenFile.getParent());
                Files.writeString(goldenFile, actualJson + "\n", StandardCharsets.UTF_8);
                System.out.println("Updated golden file: " + goldenFile);
            } catch (IOException e) {
                throw new UncheckedIOException("Failed to write golden file: " + goldenFile, e);
            }
            return;
        }

        Assert.assertTrue("Golden file not found: " + goldenFile
                        + "\nRun 'make update-golden' to generate it.",
                Files.exists(goldenFile));

        String expected;
        try {
            expected = Files.readString(goldenFile, StandardCharsets.UTF_8).stripTrailing();
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read golden file: " + goldenFile, e);
        }

        if (!expected.equals(actualJson)) {
            // Build a useful diff message
            String[] expectedLines = expected.split("\n");
            String[] actualLines = actualJson.split("\n");
            StringBuilder diff = new StringBuilder();
            diff.append("Golden file mismatch: ").append(name).append(".json\n");
            diff.append("Run 'make update-golden' to regenerate.\n\n");

            int maxLines = Math.max(expectedLines.length, actualLines.length);
            for (int i = 0; i < maxLines; i++) {
                String exp = i < expectedLines.length ? expectedLines[i] : "<missing>";
                String act = i < actualLines.length ? actualLines[i] : "<missing>";
                if (!exp.equals(act)) {
                    diff.append("Line ").append(i + 1).append(":\n");
                    diff.append("  expected: ").append(exp).append("\n");
                    diff.append("  actual:   ").append(act).append("\n");
                }
            }
            Assert.fail(diff.toString());
        }
    }

    private Path findRepoRoot() {
        // Walk up from working directory to find repo root (has puppeteer/ and website/)
        Path dir = Paths.get(System.getProperty("user.dir"));
        while (dir != null) {
            if (Files.isDirectory(dir.resolve("puppeteer")) && Files.isDirectory(dir.resolve("website"))) {
                return dir;
            }
            dir = dir.getParent();
        }
        throw new IllegalStateException("Cannot find repo root (looking for puppeteer/ and website/ dirs)");
    }

    private Path findResourceDir() {
        // The test resources are at Mage.Tests/src/test/resources/
        // Walk up from the compiled class location to find the source tree
        Path classDir = Paths.get("Mage.Tests/src/test/resources");
        if (Files.isDirectory(classDir)) {
            return classDir;
        }
        // Try absolute path from working directory
        Path abs = Paths.get(System.getProperty("user.dir")).resolve(classDir);
        if (Files.isDirectory(abs)) {
            return abs;
        }
        // Fallback: look for the resources dir relative to the project root
        // (handles running from repo root or Mage.Tests/)
        for (Path candidate : List.of(
                Paths.get("src/test/resources"),
                Paths.get("Mage.Tests/src/test/resources"))) {
            if (Files.isDirectory(candidate)) {
                return candidate;
            }
        }
        throw new IllegalStateException("Cannot find test resources directory. "
                + "Run tests from the repository root or Mage.Tests/.");
    }

    // ========== Test Cases ==========

    /**
     * Captures the static parts of the LLM prompt: system prompt and tool definitions.
     * These don't change per game state but are part of every LLM interaction.
     */
    @Test
    public void testPromptContext() {
        Path repoRoot = findRepoRoot();

        // Read system prompt from prompts.json
        Path promptsPath = repoRoot.resolve("puppeteer/prompts.json");
        String promptsJson;
        try {
            promptsJson = Files.readString(promptsPath, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read " + promptsPath, e);
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> prompts = new com.google.gson.Gson().fromJson(promptsJson, Map.class);
        String systemPrompt = (String) prompts.get("default");
        Assert.assertNotNull("prompts.json missing 'default' key", systemPrompt);

        // Read tool definitions from mcp-tools.json (generated by make mcp-tools)
        Path toolsPath = repoRoot.resolve("website/src/data/mcp-tools.json");
        String toolsJson;
        try {
            toolsJson = Files.readString(toolsPath, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read " + toolsPath
                    + "\nRun 'make mcp-tools' to generate it.", e);
        }
        JsonElement toolsElement = JsonParser.parseString(toolsJson);

        // Build the prompt context: system prompt + tool definitions
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("system_prompt", systemPrompt);
        // Parse tools as raw JSON to avoid Map<String,Object> flattening
        @SuppressWarnings("unchecked")
        List<Object> tools = new com.google.gson.Gson().fromJson(toolsElement, List.class);
        context.put("tools", tools);

        String json = toSortedJson(context);
        assertGoldenFile("prompt_context", json);
    }

    @Test
    public void testPlayOrDraw() {
        addCard(Zone.LIBRARY, playerA, "Mountain", 30);
        addCard(Zone.LIBRARY, playerB, "Island", 30);

        setStopAt(1, PhaseStep.PRECOMBAT_MAIN);
        execute();

        BridgeCallbackHandler handler = createHandler(currentGame.getId(), playerA.getId());

        // Simulate GAME_TARGET callback: choosing starting player
        Set<UUID> targets = new LinkedHashSet<>();
        targets.add(playerA.getId());
        targets.add(playerB.getId());
        simulateTargetCallback(handler, currentGame, playerA.getId(), targets, "starting player");

        assertGoldenFile("play_or_draw", buildScenarioJson(handler, "non_priority_action"));
    }

    @Test
    public void testMulliganSevenMountains() {
        addCard(Zone.HAND, playerA, "Mountain", 7);
        addCard(Zone.LIBRARY, playerA, "Mountain", 30);
        addCard(Zone.HAND, playerB, "Island", 7);
        addCard(Zone.LIBRARY, playerB, "Island", 30);

        setStopAt(1, PhaseStep.PRECOMBAT_MAIN);
        execute();

        BridgeCallbackHandler handler = createHandler(currentGame.getId(), playerA.getId());

        // Simulate GAME_ASK callback: mulligan decision
        simulateAskCallback(handler, currentGame, playerA.getId(), "Mulligan down to 6 cards?");

        assertGoldenFile("mulligan_seven_mountains", buildScenarioJson(handler, "non_priority_action"));
    }

    @Test
    public void testTurn2BoltOnStack() {
        addCard(Zone.BATTLEFIELD, playerA, "Mountain", 2);
        addCard(Zone.HAND, playerA, "Lightning Bolt", 2);
        addCard(Zone.HAND, playerA, "Mountain", 2);
        addCard(Zone.LIBRARY, playerA, "Mountain", 30);

        addCard(Zone.BATTLEFIELD, playerB, "Forest", 2);
        addCard(Zone.LIBRARY, playerB, "Forest", 30);

        // PlayerA casts Bolt targeting opponent on their T2 (game turn 3)
        castSpell(3, PhaseStep.PRECOMBAT_MAIN, playerA, "Lightning Bolt", playerB);

        // Capture state while Bolt is on the stack.
        // runCode fires during playerA's next priority pass after the castSpell.
        AtomicReference<String> captured = new AtomicReference<>();

        runCode("capture prompt", 3, PhaseStep.PRECOMBAT_MAIN, playerA,
                (info, player, game) -> {
                    BridgeCallbackHandler h = createHandler(game.getId(), player.getId());
                    simulatePriorityCallback(h, game, player.getId());
                    captured.set(buildScenarioJson(h, "playable_cards"));
                });

        setStopAt(3, PhaseStep.PRECOMBAT_MAIN);
        execute();

        Assert.assertNotNull("runCode did not execute — captured is null", captured.get());
        assertGoldenFile("t2_bolt_on_stack", captured.get());
    }
}

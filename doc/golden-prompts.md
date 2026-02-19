# Golden Prompt Tests

Golden file tests that capture the exact JSON payload we send to the LLM API
(OpenRouter). When code changes affect the prompt, the diff shows up in PRs.

## Why

The LLM's behavior depends entirely on what we send it: the system prompt,
tool definitions, conversation history, and tool results. These are assembled
across two languages (Java game engine + Python orchestration) with several
layers of formatting. A small change in any layer can silently alter what the
model receives.

Golden files make prompt changes visible and reviewable. If a refactor
accidentally drops a field from `get_game_state`, or a prompt tweak changes
the mulligan instructions, the golden file diff shows exactly what changed
in the wire-format payload.

## What the golden file captures

Each golden file is a JSON array — the complete `messages` array we pass to the
LLM API's `chat.completions.create` (system prompt, user message, assistant tool
calls, tool results). Tool definitions are always the same across scenarios
(derived from `website/src/data/mcp-tools.json`) and are not included.

This is the **wire format** — the exact JSON serialized into the HTTP request
body. Tool result `content` fields are JSON strings (with escaped quotes),
because that's what the API receives. How the provider (OpenRouter, OpenAI)
converts this into the actual token sequence the model processes is their
implementation detail — we can't capture that.

## Architecture

The tests have two layers:

### Java layer (`McpPromptGoldenTest.java`)

Sets up real XMage game states using the test framework (`CardTestPlayerBase`),
then simulates the server callbacks that `BridgeCallbackHandler` would receive
during a real game. Captures the output of `pass_priority` (merged with action
choices) and `get_game_state` — the two primary MCP tool results.

Produces fixture files in `Mage.Tests/src/test/resources/golden/mcp/`:
- `prompt_context.json` — system prompt + tool definitions
- `mulligan_seven_mountains.json` — pass_priority_result + game_state
- `play_or_draw.json`, `t2_bolt_on_stack.json` — same structure

### Python layer (`test_golden_prompts.py`)

Reads the Java fixtures and assembles the complete API payload:
1. Loads system prompt from `puppeteer/prompts.json`
2. Loads tool definitions from `website/src/data/mcp-tools.json`, converts to
   OpenAI format, filters by the default toolset
3. Builds the initial user message (replicating `_prefetch_first_action` logic)
4. Adds scripted AI tool calls + their results from the Java fixture
5. Runs `_render_context()` from `pilot.py` to assemble the final messages array

Each test defines a **scenario script** — the sequence of MCP tool calls the AI
makes before its decision point:

```python
def test_mulligan_seven_mountains():
    prompt = _assemble_prompt(
        fixture_name="mulligan_seven_mountains",
        ai_tool_calls=[
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    _assert_golden("mulligan_seven_mountains", _to_sorted_json(prompt))
```

Produces golden files in `puppeteer/tests/golden/prompts/`.

## Commands

```bash
make test-golden      # Verify golden files match (runs Java + Python)
make update-golden    # Regenerate all golden files after intentional changes
```

Both are included in `make check`.

## Adding a new scenario

1. Add a test method in `McpPromptGoldenTest.java` that sets up the game state
   and simulates the appropriate callback
2. Run `make update-golden` to generate the Java fixture
3. Add a test function in `test_golden_prompts.py` with the scenario script
4. Run `make update-golden` again to generate the Python golden file
5. Review the golden files, commit them
